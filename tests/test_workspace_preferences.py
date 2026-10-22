"""Durable folder consent and app Auto defaults; no real CLI, network or tools."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, probe_cli
from local_app.server import LocalApp
from local_app.workspace_preferences import WorkspacePreferences

FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_permission_cli.py')]


class WorkspacePreferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ax-workspace-preferences-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.config = self.root / 'claude'; self.config.mkdir()
        self.settings = self.config / 'settings.json'
        self.settings.write_text('{"permissions":{"defaultMode":"manual"},"model":"existing"}', encoding='utf-8')
        self.settings_before = self.settings.read_bytes()
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config),
                                     'WORKSPACE_PERMISSION_FIXTURE': 'runtime-default'})
        env.start(); self.addCleanup(env.stop)
        self.info = probe_cli(FAKE)

    def app(self, info=None):
        app = LocalApp(self.root / 'state', command=FAKE, info=self.info if info is None else info,
                       managed_workspace_root=self.root / 'managed')
        self.addCleanup(app.close)
        return app

    def frames(self):
        path = self.work / 'permission-wire.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def modes(self):
        return [frame['request']['mode'] for frame in self.frames()
                if frame.get('type') == 'control_request' and frame['request']['subtype'] == 'set_permission_mode']

    def wait(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.01)
        self.fail('Fixture did not settle')

    def test_folder_consent_survives_full_restart_without_auto_starting_cli(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        app.close()
        restored = self.app()
        self.assertTrue(restored.get(sid)['trusted'])
        self.assertIsNone(restored.get(sid)['bridge'])
        self.assertEqual([], self.frames())
        restored.send(sid, 'hello', [])
        self.wait(lambda: restored.get(sid)['state'] == 'done')
        self.assertEqual('auto', restored.public(restored.get(sid))['connection']['permissionMode'])
        self.assertEqual(self.settings_before, self.settings.read_bytes())

    def test_legacy_history_without_saved_consent_is_not_silently_trusted(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']; app.close()
        (self.root / 'state/workspace-preferences.json').unlink()
        restored = self.app()
        self.assertFalse(restored.get(sid)['trusted'])
        with self.assertRaises(ValueError): restored.connect(sid)
        restored.trust_workspace(sid); restored.close()
        self.assertTrue(self.app().get(sid)['trusted'])

    def test_approval_does_not_cover_children_or_replacement_at_same_path(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        child = self.work / 'child'; child.mkdir()
        self.assertFalse(app.workspace_preferences.approved(child))
        app.close()
        self.work.rename(self.root / 'original-work'); self.work.mkdir()
        restored = self.app()
        self.assertFalse(restored.get(sid)['trusted'])
        with self.assertRaises(ValueError): restored.send(sid, 'hello', [])
        self.assertEqual([], self.frames())

    def test_folder_replacement_invalidates_trust_in_running_app(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        self.work.rename(self.root / 'original-work'); self.work.mkdir()
        with self.assertRaises(ValueError): app.connect(sid)
        self.assertFalse(app.get(sid)['trusted'])

    def test_corrupt_preferences_grant_nothing(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']; app.close()
        (self.root / 'state/workspace-preferences.json').write_text('{bad', encoding='utf-8')
        self.assertFalse(self.app().get(sid)['trusted'])

    def test_default_auto_is_acknowledged_once_before_first_prompt(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        app.send(sid, 'hello', [])
        self.wait(lambda: app.get(sid)['state'] == 'done')
        app.send(sid, 'again', [])
        self.wait(lambda: app.get(sid)['state'] == 'done')
        frames = self.frames()
        first_user = next(i for i, row in enumerate(frames) if row['type'] == 'user')
        mode_change = next(i for i, row in enumerate(frames) if row.get('request', {}).get('subtype') == 'set_permission_mode')
        self.assertLess(mode_change, first_user)
        self.assertEqual(['auto'], self.modes())
        self.assertNotIn('permissionMode', app.get(sid).get('_sessionControls', {}))
        self.assertEqual({}, app.workspace_preferences.mode(app.get(sid)))
        self.assertEqual(self.settings_before, self.settings.read_bytes())

    def test_explicit_manual_and_reset_choices_survive_full_restart(self):
        for selected in ('manual', None):
            with self.subTest(selected=selected):
                app = self.app(); sid = app.create(str(self.work), True)['id']
                app.connect(sid); app.set_permission_mode(sid, selected)
                app.close()
                restored = self.app()
                self.assertEqual(selected, restored.get(sid)['_sessionControls']['permissionMode'])
                before = len(self.modes()); result = restored.connect(sid)
                self.assertEqual('manual', result['connection']['permissionMode'])
                self.assertEqual(['manual'], self.modes()[before:])
                restored.close()
        self.assertEqual(self.settings_before, self.settings.read_bytes())

    def test_bypass_authorization_never_survives_restart(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        app._remember_control(app.get(sid), 'permissionMode', 'bypassPermissions')
        app.close(); restored = self.app()
        self.assertEqual('default', restored.get(sid)['_sessionControls']['permissionMode'])
        self.assertNotIn('bypassPermissions', (self.root / 'state/workspace-preferences.json').read_text())
        self.assertEqual('manual', restored.connect(sid)['connection']['permissionMode'])

    def test_policy_rejects_auto_without_blocking_connection_or_claiming_auto(self):
        for behavior in ('runtime-reject', 'unsupported', 'runtime-clamp'):
            with self.subTest(behavior=behavior), patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': behavior}):
                app = self.app(); sid = app.create(str(self.work), True)['id']
                result = app.connect(sid)
                self.assertNotEqual('auto', result['connection']['permissionMode'])
                self.assertTrue(result['session']['permissionDefaultNotice'])
                connected = next(event['data'] for event in reversed(app.get(sid)['events'])
                                 if event['type'] == 'connected')
                self.assertEqual(result['session']['permissionDefaultNotice'], connected['permissionDefaultNotice'])
                self.assertEqual({}, app.workspace_preferences.mode(app.get(sid)))
                before = len(self.modes()); app.connect(sid)
                self.assertEqual(before, len(self.modes()))
                app.send(sid, 'hello', [])
                self.wait(lambda: app.get(sid)['state'] == 'done')
                app.close()

    def test_unsupported_help_keeps_cli_baseline(self):
        app = self.app({**self.info, 'help': '--input-format --output-format --permission-prompt-tool'})
        sid = app.create(str(self.work), True)['id']; result = app.connect(sid)
        self.assertEqual('manual', result['connection']['permissionMode'])
        self.assertEqual([], self.modes())

    def test_accepting_current_policy_mode_discards_old_persisted_selection(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']
        app.connect(sid); app.set_permission_mode(sid, 'auto'); app.close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-reject'}):
            restored = self.app(); result = restored.connect(sid)
            self.assertTrue(result['connection']['controlRestore']['issues'][0]['canUseCurrent'])
            result = restored.accept_current_control(sid, 'permissionMode', 'use_current')
            self.assertEqual('manual', result['connection']['permissionMode'])
            self.assertIsNone(result['connection']['controlRestore'])
            restored.close()
            before = len(self.modes()); reopened = self.app(); result = reopened.connect(sid)
            self.assertEqual('manual', result['connection']['permissionMode'])
            self.assertIsNone(result['connection']['controlRestore'])
            self.assertEqual(before, len(self.modes()))
            reopened.set_permission_mode(sid, 'manual')
            self.assertNotIn('_inheritPermissionMode', reopened.get(sid))
            self.assertEqual({'permissionMode': 'manual'}, reopened.workspace_preferences.mode(reopened.get(sid)))

    def test_late_default_ack_does_not_become_explicit_persisted_choice(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}):
            app = self.app(); sid = app.create(str(self.work), True)['id']
            with patch('local_app.bridge.CONTROL_TIMEOUT', .02), self.assertRaises(BridgeError) as caught:
                app.connect(sid)
            self.assertEqual('permission_mode_timeout', caught.exception.code)
            self.wait(lambda: not app.get(sid)['bridge'].model_state()['permissionModeChangePending'])
            self.assertEqual({}, app.workspace_preferences.mode(app.get(sid)))
            self.assertNotIn('permissionMode', app.get(sid).get('_sessionControls', {}))
            self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_save_failure_preserves_runtime_choice_and_reports_persistence_failure(self):
        app = self.app(); sid = app.create(str(self.work), True)['id']; app.connect(sid)
        with patch.object(app.workspace_preferences, '_save', side_effect=OSError('read only')):
            result = app.set_permission_mode(sid, 'manual')
        self.assertEqual('manual', result['connection']['permissionMode'] if 'connection' in result else result['permissionMode'])
        self.assertTrue(app.public(app.get(sid))['permissionPreferenceNotice'])
        self.assertEqual('manual', app.get(sid)['_sessionControls']['permissionMode'])


if __name__ == '__main__': unittest.main()
