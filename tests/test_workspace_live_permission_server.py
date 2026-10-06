"""Server admission/state races using a local protocol fixture, never Claude."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, probe_cli
from local_app.server import LocalApp
from tests.test_workspace_permissions import eventually

FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_permission_cli.py')]


class LivePermissionServerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-live-permission-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'runtime-live'})
        env.start(); self.addCleanup(env.stop)
        self.app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE), managed_workspace_root=self.root)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.app.connect(self.sid)
        self.item = self.app.get(self.sid)
        self.bridge = self.item['bridge']

    def frames(self):
        return [json.loads(line) for line in (self.root / 'permission-wire.jsonl').read_text(encoding='utf-8').splitlines()]

    def start(self, prompt='hold'):
        self.app.send(self.sid, prompt, [])
        eventually(lambda: self.item.get('sessionId') is not None)
        if prompt != 'hold': eventually(lambda: bool(self.item['requests']))

    def test_running_change_keeps_current_work_and_records_only_control(self):
        self.start()
        identity = (self.bridge.process.pid, self.item['sessionId'], self.item['lastRunId'])
        messages = copy.deepcopy(self.item['messages'])
        result = self.app.set_permission_mode(self.sid, 'plan')
        self.assertEqual('running', result['session']['state'])
        self.assertEqual('plan', result['permissionMode'])
        self.assertEqual('plan', self.item['_sessionControls']['permissionMode'])
        self.assertEqual(identity, (self.bridge.process.pid, self.item['sessionId'], self.item['lastRunId']))
        self.assertEqual(messages, self.item['messages'])
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_pending_approval_or_question_is_not_answered_by_mode_change(self):
        for prompt in ('permission', 'question'):
            with self.subTest(prompt=prompt):
                self.start(prompt)
                before = copy.deepcopy(self.item['requests'])
                state = self.item['state']
                result = self.app.set_permission_mode(self.sid, 'auto')
                self.assertEqual(state, result['session']['state'])
                self.assertEqual(before, self.item['requests'])
                self.bridge.respond(next(iter(self.bridge.pending)), False)
                eventually(lambda: self.item['state'] == 'done')
        self.assertEqual(2, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_result_during_mode_request_stays_done_and_current_activity_is_kept(self):
        self.start()
        original = self.bridge.set_permission_mode
        activity = {'runId': self.item['lastRunId'], 'phase': 'tool_running',
                    'tool': 'Read', 'toolUseId': 'fixture-tool', 'updatedAt': 1}
        def racing(mode):
            self.app.emit(self.sid, 'run_activity', activity)
            self.assertTrue(self.app.public(self.item).get('runActivity'))
            self.bridge.handle({'type': 'result', 'session_id': self.item['sessionId'], 'result': 'Fixture done'})
            return original(mode)
        with patch.object(self.bridge, 'set_permission_mode', side_effect=racing):
            self.assertEqual('done', self.app.set_permission_mode(self.sid, 'auto')['session']['state'])
        self.assertEqual('done', self.item['state'])

    def test_live_bypass_never_reconnects_or_stops_current_work(self):
        self.start()
        with patch.object(self.bridge, 'close') as close:
            with self.assertRaisesRegex(ValueError, '현재 작업을 마친 뒤'):
                self.app.set_permission_mode(self.sid, 'bypassPermissions', bypass_confirmed=True)
            close.assert_not_called()
        self.assertIs(self.bridge, self.item['bridge'])
        self.assertTrue(self.bridge.busy)

    def test_timeout_preserves_work_and_late_ack_remembers_confirmed_selection(self):
        self.bridge.close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}): self.app.connect(self.sid)
        self.bridge = self.item['bridge']
        self.start()
        with patch('local_app.bridge.CONTROL_TIMEOUT', .02):
            with self.assertRaises(BridgeError): self.app.set_permission_mode(self.sid, 'auto')
        self.assertTrue(self.bridge.busy)
        self.assertFalse(self.bridge.closed)
        self.assertNotEqual('auto', self.item.get('_sessionControls', {}).get('permissionMode'))
        eventually(lambda: self.item.get('_sessionControls', {}).get('permissionMode') == 'auto'
                   and not self.bridge._control_active)
        self.assertEqual('auto', self.item['_sessionControls']['permissionMode'])
        self.assertEqual('running', self.item['state'])

    def test_parallel_setting_change_rejected_without_hiding_run_or_stopping(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        original = self.bridge.set_permission_mode
        errors = []
        def slow(mode):
            entered.set(); release.wait(3); return original(mode)
        def change():
            try: self.app.set_permission_mode(self.sid, 'plan')
            except Exception as exc: errors.append(str(exc))
        with patch.object(self.bridge, 'set_permission_mode', side_effect=slow):
            worker = threading.Thread(target=change); worker.start()
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(ValueError): self.app.set_permission_mode(self.sid, 'auto')
                self.assertEqual('running', self.app.public(self.item)['state'])
            finally: release.set(); worker.join(3)
        self.assertEqual([], errors)
        self.assertEqual('plan', self.item['_sessionControls']['permissionMode'])

    def test_queue_waits_without_failed_delivery_for_unresolved_mode_control(self):
        row = self.app.dispatch.queue.enqueue(self.sid, 'queued follow-up', [], self.app.dispatch.context(self.item))
        self.bridge._control_active = True
        try:
            self.app.dispatch.pump()
            current = self.app.dispatch.queue.snapshot(self.sid)['queue']
            self.assertEqual('queued', next(entry for entry in current if entry['id'] == row['id'])['status'])
            self.assertFalse(any(frame['type'] == 'user' for frame in self.frames()))
        finally: self.bridge._control_active = False
        self.app.dispatch.pump()
        eventually(lambda: self.item['state'] == 'done')
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_queue_claim_waits_for_server_mode_reservation(self):
        row = self.app.dispatch.queue.enqueue(self.sid, 'reserved follow-up', [], self.app.dispatch.context(self.item))
        self.item['_permissionUpdating'] = True
        try:
            self.assertIsNone(self.app.dispatch.queue.claim(self.sid, self.item, self.app.dispatch.context(self.item)))
            self.app.dispatch.pump()
            self.assertEqual('queued', self.app.dispatch.queue.snapshot(self.sid)['queue'][0]['status'])
        finally: self.item['_permissionUpdating'] = False

    def test_unconfirmed_mode_blocks_direct_send_before_history_or_run_changes(self):
        self.bridge.close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}): self.app.connect(self.sid)
        self.bridge = self.item['bridge']
        with patch('local_app.bridge.CONTROL_TIMEOUT', .02):
            with self.assertRaises(BridgeError): self.app.set_permission_mode(self.sid, 'auto')
        snapshot = copy.deepcopy({key: self.item.get(key) for key in ('messages', 'state', 'lastRunId', 'verification', 'artifacts')})
        with self.assertRaises(ValueError): self.app.send(self.sid, 'must remain unsent', [])
        self.assertEqual(snapshot, {key: self.item.get(key) for key in snapshot})
        self.assertFalse(any(frame['type'] == 'user' for frame in self.frames()))

    def test_late_success_clears_only_permission_restore_issue(self):
        self.bridge.close()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}): self.app.connect(self.sid)
        self.bridge = self.item['bridge']
        self.item['_pendingControlRestore'] = {'permissionMode', 'model'}
        self.item['_controlRestoreIssues'] = {
            'permissionMode': {'control': 'permissionMode', 'code': 'permission_mode_rejected', 'message': 'Fixture rejected'},
            'model': {'control': 'model', 'code': 'model_rejected', 'message': 'Other setting still requires input'}}
        self.app._publish_control_restore(self.item, self.bridge)
        with patch('local_app.bridge.CONTROL_TIMEOUT', .02):
            with self.assertRaises(BridgeError): self.app.set_permission_mode(self.sid, 'plan')
        eventually(lambda: not self.bridge._control_active)
        self.assertEqual({'model'}, self.item['_pendingControlRestore'])
        self.assertEqual({'model'}, set(self.item['_controlRestoreIssues']))
        self.assertEqual(['model'], [row['control'] for row in self.item['_controlRestore']['issues']])
        self.assertEqual('plan', self.item['_sessionControls']['permissionMode'])
        mode_event = next(event for event in reversed(self.item['events']) if event['type'] == 'permission_mode_changed')
        self.assertEqual(['model'], [row['control'] for row in mode_event['data']['controlRestore']['issues']])

    def test_inherited_bypass_can_be_reselected_without_restarting_active_work(self):
        self.bridge.original_permission_mode = 'bypassPermissions'
        self.item['_controlBaselines']['permissionMode'] = 'bypassPermissions'
        self.bridge._bypass_supported = True
        self.bridge._permission_modes.append('bypassPermissions')
        self.app.set_permission_mode(self.sid, 'plan')
        self.start()
        pid = self.bridge.process.pid
        self.assertFalse(self.bridge.allow_bypass_permissions)
        with patch.object(self.bridge, 'close') as close:
            result = self.app.set_permission_mode(self.sid, 'bypassPermissions', bypass_confirmed=True)
            close.assert_not_called()
        self.assertEqual('bypassPermissions', result['permissionMode'])
        self.assertEqual('running', result['session']['state'])
        self.assertEqual(pid, self.bridge.process.pid)
