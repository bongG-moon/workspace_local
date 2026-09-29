"""Realistic SDK setting events never become business turns or stale defaults."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, probe_cli
from local_app.permission_contract import help_permission_modes, mode_cycle, mode_label, mode_options, mode_wire_value
from local_app.server import LocalApp
from tests.test_workspace_choices_hooks import choice

FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_permission_cli.py')]


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Runtime control fixture did not settle')


class RuntimeControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-runtime-controls-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-stale-init'})
        environment.start(); self.addCleanup(environment.stop)
        self.app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE), managed_workspace_root=self.root)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.app.connect(self.sid)

    def item(self):
        return self.app.get(self.sid)

    def frames(self):
        return [json.loads(line) for line in (self.root / 'permission-wire.jsonl').read_text().splitlines()]

    def test_initialize_reports_current_mode_before_any_user_message(self):
        state = self.app.public(self.item())['connection']
        self.assertEqual('manual', state['permissionMode'])
        self.assertEqual('manual mode on', state['permissionModeLabel'])
        self.assertEqual('cli-initialize', state['permissionModeSource'])
        self.assertEqual(['manual', 'acceptEdits', 'plan', 'auto'], state['permissionModeCycle'])
        self.assertEqual([], self.item()['messages'])
        self.assertEqual('idle', self.item()['state'])
        self.assertTrue(all(row['type'] == 'control_request' for row in self.frames()))

    def test_permission_status_null_and_ack_do_not_mark_task_running(self):
        for mode in ('plan', 'acceptEdits', 'auto', 'manual'):
            state = self.app.set_permission_mode(self.sid, mode)['session']
            self.assertEqual('idle', state['state'])
            self.assertEqual(mode, state['connection']['permissionMode'])
            self.assertFalse(self.item()['bridge'].busy)
        self.assertEqual([], self.item()['messages'])
        self.assertFalse(any(event['type'] == 'status' and event['data']['state'] == 'running'
                             for event in self.item()['events']))
        self.assertTrue(all(row['type'] == 'control_request' for row in self.frames()))

    def test_stale_init_after_selection_cannot_reset_mode_during_next_turn(self):
        self.app.set_permission_mode(self.sid, 'auto')
        self.app.send(self.sid, 'one explicit test turn', [])
        eventually(lambda: self.item()['state'] == 'done')
        state = self.app.public(self.item())['connection']
        self.assertEqual('auto', state['permissionMode'])
        self.assertEqual('auto', state['permissionModeOverride'])
        self.item()['bridge'].handle({'type': 'system', 'subtype': 'status', 'status': None})
        self.assertEqual('done', self.item()['state'])

    def test_runtime_mode_updates_are_distinct_from_initial_snapshots(self):
        bridge = self.item()['bridge']
        self.app.set_permission_mode(self.sid, 'auto')
        bridge.handle({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': 'plan'})
        bridge.handle({'type': 'system', 'subtype': 'init', 'permissionMode': 'manual'})
        state = self.app.public(self.item())['connection']
        self.assertEqual('plan', state['permissionMode'])
        self.assertEqual('cli-status', state['permissionModeSource'])
        self.assertEqual('idle', self.item()['state'])

    def test_ack_effective_mode_takes_precedence_over_requested_value(self):
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-clamp'}):
            self.app.connect(self.sid)
        state = self.app.set_permission_mode(self.sid, 'auto')['session']['connection']
        self.assertEqual('manual', state['permissionMode'])
        self.assertEqual('auto', state['permissionModeOverride'])
        self.assertEqual('manual mode on', state['permissionModeLabel'])

    def test_rejected_change_rolls_back_pre_ack_status_without_starting_work(self):
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-reject'}):
            self.app.connect(self.sid)
        with self.assertRaises(BridgeError):
            self.app.set_permission_mode(self.sid, 'auto')
        state = self.app.public(self.item())['connection']
        self.assertEqual('manual', state['permissionMode'])
        self.assertIsNone(state['permissionModeOverride'])
        self.assertEqual('idle', self.item()['state'])
        self.assertEqual([], self.item()['messages'])

    def test_reconnect_restores_confirmed_mode_before_the_next_user_frame(self):
        self.app.set_permission_mode(self.sid, 'auto')
        old = self.item()['bridge']; old.close()
        self.app.send(self.sid, 'continue after reconnect', [])
        eventually(lambda: self.item()['state'] == 'done')
        self.assertIsNot(old, self.item()['bridge'])
        self.assertEqual('auto', self.app.public(self.item())['connection']['permissionMode'])
        frames = self.frames()
        user_index = next(i for i, frame in enumerate(frames) if frame['type'] == 'user')
        controls = [i for i, frame in enumerate(frames) if frame.get('request', {}).get('subtype') == 'set_permission_mode']
        self.assertEqual(2, len(controls))
        self.assertLess(controls[-1], user_index)
        # Even an already-queued emitter from the old child is quarantined.
        old.emit('connected', {'permissionMode': 'manual', 'permissionModeOverride': None})
        old.emit('status', {'state': 'running'})
        self.assertEqual('done', self.item()['state'])
        self.assertEqual('auto', self.app.public(self.item())['connection']['permissionMode'])

    def test_failed_reapply_sends_no_prompt_and_user_can_choose_supported_mode(self):
        self.app.set_permission_mode(self.sid, 'auto')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-reject'}):
            with self.assertRaises(BridgeError):
                self.app.send(self.sid, 'must not reach model', [])
        self.assertEqual([], self.item()['messages'])
        self.assertFalse(any(frame['type'] == 'user' for frame in self.frames()))
        self.assertFalse(self.item().get('_connecting'))
        self.app.set_permission_mode(self.sid, 'plan')
        self.app.send(self.sid, 'explicit retry after choosing plan', [])
        eventually(lambda: self.item()['state'] == 'done')
        self.assertEqual('plan', self.app.public(self.item())['connection']['permissionMode'])
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_choice_reconnect_releases_app_lock_and_claims_answer_once(self):
        self.app.set_permission_mode(self.sid, 'auto')
        self.item()['bridge'].close()
        self.app.emit(self.sid, 'choice', choice())
        choice_id = self.item()['choice']['id']
        entered, release = threading.Event(), threading.Event()
        original = self.app._restore_controls
        errors = []
        def delayed_restore(item, bridge):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Choice restore kept the app lock')
            original(item, bridge)
        def answer():
            try:
                self.app.answer_choice(self.sid, choice_id, option_id='neumorphism')
            except Exception as error:
                errors.append(error)
        with patch.object(self.app, '_restore_controls', side_effect=delayed_restore):
            worker = threading.Thread(target=answer)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(ValueError):
                    self.app.answer_choice(self.sid, choice_id, option_id='neumorphism')
                with self.assertRaises(ValueError):
                    self.app.send(self.sid, 'racing normal send', [])
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        eventually(lambda: self.item()['state'] == 'done')
        self.assertEqual('auto', self.app.public(self.item())['connection']['permissionMode'])
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))
        self.assertNotIn('choice', self.item())
        self.assertNotIn('_choiceAnswerClaim', self.item())

    def test_failed_choice_restore_releases_claim_without_consuming_question(self):
        self.app.set_permission_mode(self.sid, 'auto')
        self.item()['bridge'].close()
        self.app.emit(self.sid, 'choice', choice())
        choice_id = self.item()['choice']['id']
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-reject'}):
            with self.assertRaises(BridgeError):
                self.app.answer_choice(self.sid, choice_id, option_id='neumorphism')
        self.assertEqual(choice_id, self.item()['choice']['id'])
        self.assertNotIn('_choiceAnswerClaim', self.item())
        self.assertEqual([], self.item()['messages'])
        self.app.set_permission_mode(self.sid, 'plan')
        self.app.answer_choice(self.sid, choice_id, option_id='neumorphism')
        eventually(lambda: self.item()['state'] == 'done')
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_incomplete_child_cleanup_does_not_allow_replacement(self):
        self.app.set_permission_mode(self.sid, 'auto')
        old = self.item()['bridge']; old.close()
        old._close_done.clear()
        try:
            with self.assertRaises(ValueError):
                self.app.connect(self.sid)
            with self.assertRaises(ValueError):
                self.app.send(self.sid, 'must wait for prior child', [])
            self.assertIs(old, self.item()['bridge'])
            self.assertEqual([], self.item()['messages'])
        finally:
            old._close_done.set()

    def test_canonical_default_uses_advertised_manual_alias_for_reset_and_reconnect(self):
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-alias'}):
            self.app.connect(self.sid)
            bridge = self.item()['bridge']; pid = bridge.process.pid
            initial = self.app.public(self.item())['connection']
            self.assertEqual('default', initial['permissionMode'])
            self.assertTrue(initial['permissionModeResetAvailable'])
            self.app.set_permission_mode(self.sid, 'plan')
            restored = self.app.set_permission_mode(self.sid, None)['session']['connection']
            self.assertEqual('default', restored['permissionMode'])
            self.assertIsNone(restored['permissionModeOverride'])
            self.assertEqual(pid, bridge.process.pid)
            selected = self.app.set_permission_mode(self.sid, 'manual')['session']['connection']
            self.assertEqual('default', selected['permissionMode'])
            self.assertEqual('manual', selected['permissionModeOverride'])
            self.assertEqual('default', self.item()['_sessionControls']['permissionMode'])
            bridge.close(); self.app.connect(self.sid)
        current = self.app.public(self.item())['connection']
        self.assertEqual('default', current['permissionMode'])
        self.assertEqual('manual mode on', current['permissionModeLabel'])
        self.assertEqual(['manual', 'acceptEdits', 'plan', 'auto'], current['permissionModeCycle'])
        requested = [frame['request']['mode'] for frame in self.frames()
                     if frame.get('request', {}).get('subtype') == 'set_permission_mode']
        self.assertEqual(['plan', 'manual', 'manual', 'manual'], requested)
        self.assertEqual([], self.item()['messages'])

    def test_clamped_auto_remembers_actual_default_and_reconnects_as_manual(self):
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-alias-clamp'}):
            self.app.connect(self.sid)
            state = self.app.set_permission_mode(self.sid, 'auto')['session']['connection']
            self.assertEqual('auto', state['permissionModeOverride'])
            self.assertEqual('default', state['permissionMode'])
            self.item()['bridge'].close(); self.app.connect(self.sid)
        self.assertEqual('default', self.app.public(self.item())['connection']['permissionMode'])
        requested = [frame['request']['mode'] for frame in self.frames()
                     if frame.get('request', {}).get('subtype') == 'set_permission_mode']
        self.assertEqual(['auto', 'manual'], requested)
        self.assertEqual([], self.item()['messages'])

    def test_rejected_manual_alias_cannot_be_reintroduced_by_default_or_reset(self):
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-alias-reject'}):
            self.app.connect(self.sid)
        self.app.set_permission_mode(self.sid, 'plan')
        with self.assertRaises(BridgeError) as caught:
            self.app.set_permission_mode(self.sid, 'manual')
        self.assertEqual('permission_mode_rejected', caught.exception.code)
        for value in ('default', None):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                self.app.set_permission_mode(self.sid, value)
        state = self.app.public(self.item())['connection']
        self.assertEqual(('plan', 'plan'), (state['permissionMode'], state['permissionModeOverride']))
        self.assertFalse(state['permissionModeResetAvailable'])
        self.assertNotIn('manual', state['permissionModeCycle'])
        self.assertNotIn('default', state['permissionModeCycle'])
        requested = [frame['request']['mode'] for frame in self.frames()
                     if frame.get('request', {}).get('subtype') == 'set_permission_mode']
        self.assertEqual(['plan', 'manual'], requested)


class RuntimeModeProjectionTests(unittest.TestCase):
    def test_default_manual_alias_is_shown_once_and_unsafe_modes_are_not_added(self):
        help_text = '--permission-mode <mode> (choices: "manual", "default", "auto", "plan", "acceptEdits", "dontAsk", "bypassPermissions")'
        modes = help_permission_modes(help_text)
        self.assertNotIn('manual', modes)
        self.assertEqual(['default', 'acceptEdits', 'plan', 'auto'], mode_cycle(modes))
        self.assertEqual(['Manual', 'Plan', 'Accept edits', 'Auto'], [row['displayName'] for row in mode_options(modes)])
        self.assertEqual(['default'], [row['value'] for row in mode_options(['default', 'manual', 'default'])])
        self.assertEqual("don't ask on", mode_label('dontAsk'))
        self.assertEqual('bypass permissions on', mode_label('bypassPermissions'))
        self.assertEqual([], mode_options(['dontAsk', 'bypassPermissions']))

    def test_wire_alias_mapping_requires_a_currently_available_manual_choice(self):
        self.assertEqual('manual', mode_wire_value('default', ['manual', 'plan']))
        self.assertEqual('default', mode_wire_value('manual', ['default', 'plan']))
        self.assertIsNone(mode_wire_value('default', ['plan', 'auto']))
        self.assertIsNone(mode_wire_value('bypassPermissions', ['manual', 'plan']))


if __name__ == '__main__':
    unittest.main()
