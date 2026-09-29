"""Session effort contract: actual runtime values, no settings writes or AI turns."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, ClaudeSession, probe_cli
from local_app.server import LocalApp

FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_effort_cli.py')]


class EffortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-effort-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = []

    def connect(self, case=''):
        bridge = ClaudeSession(FAKE, probe_cli(FAKE), self.root,
                               lambda kind, data: self.events.append((kind, data)))
        self.addCleanup(bridge.close)
        with patch.dict(os.environ, {'WORKSPACE_EFFORT_CASE': case}):
            bridge.prepare()
        return bridge

    def frames(self):
        return [json.loads(row) for row in (self.root / 'effort-wire.jsonl').read_text().splitlines()]

    def test_prepare_reads_reported_model_effort_without_prompt_or_secret(self):
        bridge = self.connect()
        state = bridge.model_state()
        self.assertEqual('reported-model', state['model'])
        self.assertEqual('medium', state['effort'])
        self.assertIsNone(state['effortOverride'])
        self.assertEqual(['low', 'medium', 'high', 'max'], [r['value'] for r in state['availableEfforts']])
        self.assertTrue(state['capabilities']['setModel'])
        self.assertEqual('confirmed', state['effortSupport'])
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(state))
        self.assertTrue(all(row['type'] == 'control_request' for row in self.frames()))

    def test_effort_changes_only_flag_layer_preserving_ultracode_and_process(self):
        bridge = self.connect(); pid = bridge.process.pid
        state = bridge.set_effort('high')
        self.assertEqual(('high', 'high'), (state['effort'], state['effortOverride']))
        self.assertEqual(pid, bridge.process.pid)
        self.assertFalse(bridge.closed)
        changes = [r['request'] for r in self.frames() if r['request']['subtype'] == 'apply_flag_settings']
        self.assertEqual([{'subtype': 'apply_flag_settings', 'settings': {'effortLevel': 'high', 'ultracode': True}}], changes)
        self.assertTrue(all(row['type'] == 'control_request' for row in self.frames()))

    def test_clamped_applied_value_is_distinct_from_requested_override(self):
        bridge = self.connect('clamp'); state = bridge.set_effort('max')
        self.assertEqual('max', state['effortOverride'])
        self.assertEqual('low', state['effort'])

    def test_model_switch_refreshes_actual_effort_and_choices(self):
        bridge = self.connect(); bridge.set_effort('high')
        state = bridge.set_model('restricted-model')
        self.assertEqual('low', state['effort'])
        self.assertEqual(['low'], [r['value'] for r in state['availableEfforts']])
        state = bridge.set_model('unsupported-model')
        self.assertIsNone(state['effort'])
        self.assertFalse(state['capabilities']['setEffort'])
        with self.assertRaises(BridgeError): bridge.set_effort('high')

    def test_invalid_or_unreported_levels_cannot_reach_flag_request(self):
        bridge = self.connect()
        for value in ('xhigh', 'ultracode', '', [], {'effortLevel': 'high'}, True):
            with self.subTest(value=value), self.assertRaises(BridgeError): bridge.set_effort(value)
        self.assertFalse(any(r['request']['subtype'] == 'apply_flag_settings' for r in self.frames()))

    def test_busy_pending_and_control_operation_reject_changes(self):
        bridge = self.connect()
        for name, value in [('busy', True), ('pending', {'request': {}}), ('_control_active', True)]:
            old = getattr(bridge, name); setattr(bridge, name, value)
            with self.assertRaises(BridgeError) as caught: bridge.set_effort('high')
            self.assertEqual('session_busy', caught.exception.code)
            setattr(bridge, name, old)

    def test_rejection_preserves_applied_value_and_disables_unknown_control(self):
        for case in ('rejected', 'unsupported'):
            bridge = self.connect(case)
            with self.assertRaises(BridgeError): bridge.set_effort('high')
            self.assertEqual('medium', bridge.effort)
            self.assertIsNone(bridge.effort_override)
            self.assertFalse(bridge.closed)
            if case == 'unsupported': self.assertFalse(bridge.capabilities['setEffort'])
            bridge.close()

    def test_missing_current_state_is_not_invented_or_mutated(self):
        bridge = self.connect('legacy')
        self.assertIsNone(bridge.model_state()['effort'])
        with self.assertRaises(BridgeError): bridge.set_effort('high')
        self.assertFalse(any(r['request']['subtype'] == 'apply_flag_settings' for r in self.frames()))

    def test_timeout_or_unconfirmed_readback_retires_uncertain_child(self):
        for case in ('timeout', 'unconfirmed'):
            bridge = self.connect(case)
            with patch('local_app.bridge.CONTROL_TIMEOUT', .1), self.assertRaises(BridgeError):
                bridge.set_effort('high')
            self.assertTrue(bridge.cleanup_complete)

    def test_reset_restores_observed_level_in_same_connection(self):
        bridge = self.connect(); pid = bridge.process.pid; bridge.set_effort('high')
        state = bridge.set_effort(None)
        self.assertFalse(state['effortResetRequiresReconnect'])
        self.assertFalse(bridge.closed)
        self.assertEqual(pid, bridge.process.pid)
        self.assertEqual('medium', state['effort'])
        self.assertIsNone(state['effortOverride'])
        changes = [r['request']['settings']['effortLevel'] for r in self.frames()
                   if r['request']['subtype'] == 'apply_flag_settings']
        self.assertEqual(['high', 'medium'], changes)

    def test_reset_without_original_for_current_model_preserves_other_controls(self):
        bridge = self.connect(); bridge.set_effort('high')
        bridge.set_permission_mode('auto'); bridge.set_model('restricted-model')
        with self.assertRaises(BridgeError) as caught:
            bridge.set_effort(None)
        self.assertEqual('effort_reset_unavailable', caught.exception.code)
        self.assertFalse(bridge.closed)
        self.assertEqual('auto', bridge.permission_mode)
        self.assertEqual('restricted-model', bridge.model)

    def test_app_effort_control_preserves_messages_and_does_not_hold_app_lock(self):
        with patch.dict(os.environ, {'WORKSPACE_EFFORT_CASE': ''}):
            app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE), managed_workspace_root=self.root)
            self.addCleanup(app.close)
            item = app.create(str(self.root), True); sid = item['id']; app.connect(sid)
            result = app.set_effort(sid, 'high')
            self.assertEqual([], result['session']['messages'])
            self.assertEqual('idle', result['session']['state'])
            self.assertEqual('high', result['session']['connection']['effort'])
            self.assertEqual('effort_changed', app.get(sid)['events'][-1]['type'])
            self.assertFalse(app.get(sid).get('_modelUpdating'))
            result = app.set_effort(sid, None)
            self.assertFalse(result['session']['connectionStopped'])
            self.assertTrue(result['session']['connection']['capabilities']['setEffort'])
            self.assertEqual('medium', result['session']['connection']['effort'])

    def test_reset_preserves_sibling_overrides_in_reply_event_and_session(self):
        with patch.dict(os.environ, {'WORKSPACE_EFFORT_CASE': ''}):
            app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE), managed_workspace_root=self.root)
            self.addCleanup(app.close)
            sid = app.create(str(self.root), True)['id']; app.connect(sid)
            app.set_model(sid, 'reported-model')
            app.set_permission_mode(sid, 'auto')
            app.set_effort(sid, 'high')
            self.assertEqual('reported-model', app.get(sid)['modelOverride'])
            self.assertEqual('auto', app.get(sid)['permissionModeOverride'])
            result = app.set_effort(sid, None)
            event = app.get(sid)['events'][-1]
            self.assertEqual('effort_changed', event['type'])
            for state in (result, result['session']['connection'], event['data'],
                          app.get(sid)['bridge'].model_state()):
                self.assertEqual('reported-model', state['modelOverride'])
                self.assertEqual('auto', state['permissionModeOverride'])
                self.assertIsNone(state['effortOverride'])
                self.assertEqual('reported-model', state['model'])
                self.assertEqual('auto', state['permissionMode'])
                self.assertEqual('medium', state['effort'])
            self.assertTrue(result['session']['connection']['connected'])
            for item in (result['session'], app.get(sid)):
                self.assertEqual('reported-model', item['modelOverride'])
                self.assertEqual('auto', item['permissionModeOverride'])

    def test_late_init_does_not_overwrite_confirmed_model_or_effort(self):
        bridge = self.connect(); bridge.set_model('restricted-model'); bridge.set_effort('low')
        bridge.handle({'type': 'system', 'subtype': 'init', 'model': 'reported-model', 'effort': 'medium'})
        self.assertEqual('restricted-model', bridge.model)
        self.assertEqual('low', bridge.effort)
        self.assertEqual('cli-settings', bridge.model_state()['effortSource'])

    def test_confirmed_controls_restore_on_task_reconnect_without_settings_writes(self):
        with patch.dict(os.environ, {'WORKSPACE_EFFORT_CASE': ''}):
            app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE), managed_workspace_root=self.root)
            self.addCleanup(app.close)
            sid = app.create(str(self.root), True)['id']; app.connect(sid)
            app.set_model(sid, 'reported-model'); app.set_effort(sid, 'high'); app.set_permission_mode(sid, 'auto')
            old = app.get(sid)['bridge']; old.close()
            app.connect(sid)
            state = app.public(app.get(sid))['connection']
            self.assertIsNot(old, app.get(sid)['bridge'])
            self.assertEqual(('reported-model', 'high', 'auto'), (state['model'], state['effort'], state['permissionMode']))
            self.assertEqual([], app.get(sid)['messages'])
            self.assertTrue(all(row['type'] == 'control_request' for row in self.frames()))
            history = ''.join(path.read_text(encoding='utf-8') for path in (self.root / 'state').rglob('*.json'))
            self.assertNotIn('_sessionControls', history)


if __name__ == '__main__': unittest.main()
