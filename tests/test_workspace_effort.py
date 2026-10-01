"""Session effort contract: actual runtime values, no settings writes or AI turns."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import BridgeError, ClaudeSession, ControlRestoreRequired, probe_cli
from local_app.server import LocalApp, Server

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


class ControlRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-control-restore-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': ''})
        env.start(); self.addCleanup(env.stop)
        command = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_restore_cli.py')]
        self.app = LocalApp(self.root / 'state', command=command, info=probe_cli(command), managed_workspace_root=self.root)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.app.connect(self.sid)

    def item(self):
        return self.app.get(self.sid)

    def frames(self):
        return [json.loads(row) for row in (self.root / 'restore-wire.jsonl').read_text().splitlines()]

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Fixture did not reach the expected state')

    def completed_alternate(self):
        self.app.set_model(self.sid, 'alternate-model')
        self.app.send(self.sid, 'complete alternate turn', [])
        self.wait(lambda: self.item()['state'] == 'done')

    def cancel_turn(self):
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait(lambda: self.item()['bridge'].busy)
        self.app.stop(self.sid)
        self.wait(lambda: self.item()['bridge'].cleanup_complete and self.item()['state'] == 'stopped')

    def test_model_reset_then_cancel_restores_baseline_before_effort_and_one_followup(self):
        self.completed_alternate()
        self.app.set_model(self.sid, None)
        self.app.set_effort(self.sid, 'low')
        self.app.set_permission_mode(self.sid, 'auto')
        self.cancel_turn()
        before = len(self.frames())
        self.app.send(self.sid, 'followup after model reset', [])
        self.wait(lambda: self.item()['state'] == 'done')
        state = self.app.public(self.item())['connection']
        self.assertEqual(('baseline-model', 'low', 'auto'), (state['model'], state['effort'], state['permissionMode']))
        self.assertIsNone(state['modelOverride'])
        requests = self.frames()[before:]
        controls = [row['request'] for row in requests if row['type'] == 'control_request']
        model_at = next(i for i, row in enumerate(controls) if row['subtype'] == 'set_model')
        effort_at = next(i for i, row in enumerate(controls) if row['subtype'] == 'apply_flag_settings')
        self.assertLess(model_at, effort_at)
        self.assertEqual(1, sum(row['type'] == 'user' for row in requests))

    def test_explicit_reset_intents_for_all_controls_survive_cancel_and_repeated_reconnect(self):
        self.completed_alternate()
        self.app.set_model(self.sid, None)
        self.app.set_effort(self.sid, 'high'); self.app.set_effort(self.sid, None)
        self.app.set_permission_mode(self.sid, 'auto'); self.app.set_permission_mode(self.sid, None)
        self.cancel_turn()
        before = len(self.frames())
        for _ in range(2):
            state = self.app.connect(self.sid)['connection']
            self.assertEqual(('baseline-model', 'medium', 'manual'), (state['model'], state['effort'], state['permissionMode']))
            self.assertIsNone(state['controlRestore'])
            self.item()['bridge'].close()
        self.assertEqual({'model': None, 'effort': None, 'permissionMode': None}, self.item()['_sessionControls'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()[before:]))

    def test_invalid_effort_exposes_actual_connected_metadata_and_blocks_without_side_effects(self):
        self.app.set_effort(self.sid, 'high')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'narrow-effort'}):
            result = self.app.connect(self.sid)
        state = result['connection']
        self.assertTrue(state['connected'])
        self.assertEqual('baseline-model', state['model'])
        self.assertEqual(['low'], [row['value'] for row in state['availableEfforts']])
        self.assertEqual('effort_invalid', state['controlRestore']['issues'][0]['code'])
        self.assertFalse(state['controlRestore']['canSend'])
        before_frames, before_messages = len(self.frames()), list(self.item()['messages'])
        for _ in range(2):
            with self.assertRaises(ControlRestoreRequired) as caught:
                self.app.send(self.sid, 'draft stays outside transcript', [])
            self.assertEqual(state, caught.exception.connection)
        self.assertEqual(before_messages, self.item()['messages'])
        self.assertIsNone(self.item().get('lastRunId'))
        self.assertNotIn('_artifactSnapshot', self.item())
        self.assertEqual(before_frames, len(self.frames()))
        result = self.app.set_effort(self.sid, 'low')
        self.assertIsNone(result['controlRestore'])
        self.assertIsNone(result['session']['connection']['controlRestore'])
        event = self.item()['events'][-1]
        self.assertEqual('control_restore_changed', event['type'])
        self.assertIsNone(event['data']['controlRestore'])
        self.app.send(self.sid, 'explicit retry', [])
        self.wait(lambda: self.item()['state'] == 'done')
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))

    def test_model_rejection_defers_effort_then_reset_can_recover_without_actual_override(self):
        self.completed_alternate()
        self.app.set_model(self.sid, None); self.app.set_effort(self.sid, 'low')
        self.cancel_turn()
        before = len(self.frames())
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'reject-model'}):
            state = self.app.connect(self.sid)['connection']
        self.assertEqual('alternate-model', state['model'])
        self.assertEqual(['model'], [row['control'] for row in state['controlRestore']['issues']])
        self.assertFalse(any(row.get('request', {}).get('subtype') == 'apply_flag_settings' for row in self.frames()[before:]))
        state = self.app.set_model(self.sid, 'alternate-model')['session']['connection']
        self.assertEqual(['effort'], [row['control'] for row in state['controlRestore']['issues']])
        self.assertIsNone(state['effortOverride'])
        self.assertTrue(state['effortResetAvailable'])
        state = self.app.set_effort(self.sid, None)['session']['connection']
        self.assertIsNone(state['controlRestore'])
        self.assertEqual('medium', state['effort'])
        self.assertEqual('alternate-model', state['modelOverride'])
        self.assertIsNone(self.item()['_sessionControls']['effort'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()[before:]))

    def test_unavailable_reset_retains_pending_intent_until_valid_explicit_choice(self):
        self.app.set_effort(self.sid, 'high'); self.app.set_effort(self.sid, None)
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'narrow-effort'}):
            state = self.app.connect(self.sid)['connection']
        self.assertEqual('effort_reset_unavailable', state['controlRestore']['issues'][0]['code'])
        with self.assertRaises(BridgeError):
            self.app.set_effort(self.sid, None)
        self.assertIsNone(self.item()['_sessionControls']['effort'])
        self.assertIsNotNone(self.app.public(self.item())['connection']['controlRestore'])
        self.assertIsNone(self.app.set_effort(self.sid, 'low')['session']['connection']['controlRestore'])

    def test_model_selection_revalidates_only_dependent_effort_not_rejected_permission(self):
        self.app.set_effort(self.sid, 'low'); self.app.set_permission_mode(self.sid, 'auto')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'reject-permission'}):
            self.app.connect(self.sid)
        before = len(self.frames())
        state = self.app.set_model(self.sid, 'alternate-model')['session']['connection']
        self.assertEqual(['effort', 'permissionMode'], [row['control'] for row in state['controlRestore']['issues']])
        state = self.app.set_effort(self.sid, 'high')['session']['connection']
        self.assertEqual(['permissionMode'], [row['control'] for row in state['controlRestore']['issues']])
        self.assertFalse(any(row.get('request', {}).get('subtype') == 'set_permission_mode' for row in self.frames()[before:]))
        self.assertEqual('auto', self.item()['_sessionControls']['permissionMode'])
        state = self.app.set_permission_mode(self.sid, None)['session']['connection']
        self.assertIsNone(state['controlRestore'])
        self.assertEqual('manual', state['permissionMode'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_transport_and_auth_failures_do_not_become_actionable_restore_success(self):
        self.app.set_effort(self.sid, 'high')
        for case, code in (('settings-timeout', 'runtime_settings_timeout'), ('settings-auth', 'authentication_failed')):
            with self.subTest(case=case):
                self.item()['bridge'].close()
                with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': case}), patch('local_app.bridge.CONTROL_TIMEOUT', .05):
                    with self.assertRaises(BridgeError) as caught:
                        self.app.connect(self.sid)
                self.assertEqual(code, caught.exception.code)
                self.assertNotIsInstance(caught.exception, ControlRestoreRequired)
                self.assertEqual([], self.item()['messages'])
                self.assertFalse(self.item().get('_connecting'))

    def test_unsupported_effort_restore_remains_actionable_without_replaying_prompt(self):
        self.app.set_effort(self.sid, 'high'); self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'reject-effort'}):
            state = self.app.connect(self.sid)['connection']
        self.assertTrue(state['connected'])
        self.assertFalse(state['capabilities']['setEffort'])
        self.assertEqual('effort_rejected', state['controlRestore']['issues'][0]['code'])
        self.assertEqual('high', self.item()['_sessionControls']['effort'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))
        self.assertTrue(state['controlRestore']['issues'][0]['canUseCurrent'])
        self.assertEqual('medium', state['controlRestore']['issues'][0]['currentValue'])
        with self.assertRaises(BridgeError):
            self.app.set_effort(self.sid, None)
        before = len(self.frames())
        state = self.app.accept_current_control(self.sid, 'effort', 'use_current')['connection']
        self.assertIsNone(state['controlRestore'])
        self.assertNotIn('effort', self.item()['_sessionControls'])
        self.assertIsNone(state['effortOverride'])
        self.assertEqual('medium', state['effort'])
        self.assertEqual(before, len(self.frames()))
        self.app.send(self.sid, 'manual retry after accepting current CLI effort', [])
        self.wait(lambda: self.item()['state'] == 'done')
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))

    def test_each_setter_auth_rejection_is_fatal_not_actionable(self):
        for name, case, value in (('model', 'model-auth', 'baseline-model'),
                                  ('effort', 'effort-auth', 'high'),
                                  ('permissionMode', 'permission-auth', 'auto')):
            with self.subTest(control=name):
                self.item()['_sessionControls'] = {name: value}
                self.item()['bridge'].close()
                with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': case}):
                    with self.assertRaises(BridgeError) as caught:
                        self.app.connect(self.sid)
                self.assertEqual('authentication_failed', caught.exception.code)
                self.assertTrue(self.item()['bridge'].closed)
                self.assertFalse(self.item().get('_controlRestore'))
                self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_settings_failure_is_fatal_with_only_model_or_permission_intent(self):
        for name, value in (('model', None), ('permissionMode', 'auto')):
            for case, code in (('settings-auth', 'authentication_failed'), ('settings-timeout', 'runtime_settings_timeout')):
                with self.subTest(control=name, case=case):
                    self.item()['_sessionControls'] = {name: value}
                    self.item()['bridge'].close()
                    with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': case}), patch('local_app.bridge.CONTROL_TIMEOUT', .05):
                        with self.assertRaises(BridgeError) as caught:
                            self.app.connect(self.sid)
                    self.assertEqual(code, caught.exception.code)
                    self.assertTrue(self.item()['bridge'].closed)
                    self.assertFalse(self.item().get('_controlRestore'))
                    self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_model_readback_failure_during_restore_is_fatal(self):
        self.app.set_model(self.sid, 'alternate-model')
        for case, code in (('model-readback-auth', 'authentication_failed'), ('model-readback-timeout', 'runtime_settings_timeout')):
            with self.subTest(case=case):
                self.item()['bridge'].close()
                with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': case}), patch('local_app.bridge.CONTROL_TIMEOUT', .05):
                    with self.assertRaises(BridgeError) as caught:
                        self.app.connect(self.sid)
                self.assertEqual(code, caught.exception.code)
                self.assertTrue(self.item()['bridge'].closed)
                self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_http_blocker_returns_live_metadata_and_explicit_recovery_requires_auth(self):
        self.app.set_effort(self.sid, 'high'); self.item()['bridge'].close()
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def post(route, data, auth=True):
            headers = {'Content-Type': 'application/json', 'Origin': server.origin}
            if auth:
                headers['Authorization'] = 'Bearer ' + self.app.token
            request = Request(server.origin + route, data=json.dumps(dict(data, id=self.sid)).encode(), headers=headers)
            return urlopen(request, timeout=5)
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'reject-effort'}):
            with post('/api/connect', {}) as response:
                state = json.load(response)['connection']
        self.assertTrue(state['connected'])
        with self.assertRaises(HTTPError) as blocked:
            post('/api/send', {'text': 'preserve this draft', 'attachments': []})
        self.assertEqual(400, blocked.exception.code)
        payload = json.loads(blocked.exception.read())
        self.assertEqual('control_restore_required', payload['code'])
        self.assertEqual(state, payload['connection'])
        with self.assertRaises(HTTPError) as denied:
            post('/api/control-restore', {'control': 'effort', 'action': 'use_current'}, auth=False)
        self.assertEqual(403, denied.exception.code)
        self.assertEqual('high', self.item()['_sessionControls']['effort'])
        with post('/api/control-restore', {'control': 'effort', 'action': 'use_current'}) as response:
            result = json.load(response)
        self.assertIsNone(result['connection']['controlRestore'])
        self.assertEqual([], result['session']['messages'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_accept_current_clears_only_selected_unsupported_control(self):
        self.app.set_model(self.sid, 'alternate-model'); self.app.set_effort(self.sid, 'high')
        self.app.set_permission_mode(self.sid, 'auto')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'model-unsupported'}):
            state = self.app.connect(self.sid)['connection']
        self.assertEqual('model_rejected', state['controlRestore']['issues'][0]['code'])
        state = self.app.accept_current_control(self.sid, 'model', 'use_current')['connection']
        self.assertIsNone(state['controlRestore'])
        self.assertEqual('baseline-model', state['model'])
        self.assertEqual('high', state['effort'])
        self.assertEqual('auto', state['permissionMode'])
        self.assertEqual({'effort': 'high', 'permissionMode': 'auto'}, self.item()['_sessionControls'])
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    def test_reset_keeps_app_baseline_after_accept_current_clears_last_intent_and_direct_send(self):
        self.completed_alternate()
        self.app.set_model(self.sid, None)
        self.cancel_turn()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'model-unsupported'}):
            self.app.connect(self.sid)
        state = self.app.accept_current_control(self.sid, 'model', 'use_current')['connection']
        self.assertEqual('alternate-model', state['model'])
        self.assertEqual({}, self.item()['_sessionControls'])
        self.cancel_turn()
        self.app.send(self.sid, 'ordinary send with CLI owned controls', [])
        self.wait(lambda: self.item()['state'] == 'done')
        self.assertEqual('alternate-model', self.app.public(self.item())['connection']['model'])
        state = self.app.set_model(self.sid, None)['session']['connection']
        self.assertEqual('baseline-model', state['model'])
        self.assertIsNone(state['modelOverride'])
        self.assertIsNone(state['controlRestore'])

    def test_accept_current_never_bypasses_unknown_value_or_risk_confirmation(self):
        self.app.set_permission_mode(self.sid, 'auto'); self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'permission-unsupported'}):
            state = self.app.connect(self.sid)['connection']
        self.assertTrue(state['controlRestore']['issues'][0]['canUseCurrent'])
        bridge = self.item()['bridge']
        bridge.permission_mode = 'bypassPermissions'
        self.app._publish_control_restore(self.item(), bridge)
        self.assertFalse(self.app.public(self.item())['connection']['controlRestore']['issues'][0]['canUseCurrent'])
        with self.assertRaises(BridgeError):
            self.app.accept_current_control(self.sid, 'permissionMode', 'use_current')
        self.assertEqual('auto', self.item()['_sessionControls']['permissionMode'])
        bridge.permission_mode = 'manual'
        state = self.app.accept_current_control(self.sid, 'permissionMode', 'use_current')['connection']
        self.assertIsNone(state['controlRestore'])
        self.assertEqual('manual', state['permissionMode'])
        self.assertNotIn('permissionMode', self.item()['_sessionControls'])
        with self.assertRaises(ValueError):
            self.app.accept_current_control(self.sid, 'model', 'use_current')

    def test_runtime_baselines_reset_intents_and_blocker_are_not_written_to_history(self):
        self.app.set_model(self.sid, None); self.app.set_effort(self.sid, 'high')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'WORKSPACE_RESTORE_CASE': 'narrow-effort'}):
            self.app.connect(self.sid)
        history = ''.join(path.read_text(encoding='utf-8') for path in (self.root / 'state').rglob('*.json'))
        self.assertNotIn('_sessionControls', history)
        self.assertNotIn('_controlBaselines', history)
        self.assertNotIn('_controlRestore', history)
        self.assertFalse((self.root / '.claude' / 'settings.json').exists())


if __name__ == '__main__': unittest.main()
