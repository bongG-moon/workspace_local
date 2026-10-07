"""Stop admission and same-session follow-up without real Claude or personal state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import BridgeError, probe_cli
from local_app.server import LocalApp, Server


BASE_FIXTURE = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'
SOFT_INTERRUPT = """    elif subtype == 'interrupt':
        import time
        deadline = time.monotonic() + 6
        while not (root / 'release-stop').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        terminal = {'type': 'result', 'session_id': session, 'is_error': True,
                    'result': '[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'}
        acknowledgement = {'type': 'control_response', 'response': {
            'subtype': 'success', 'request_id': data['request_id'], 'response': {}}}
        if config.get('stopResultFirst'):
            emit(terminal); emit(acknowledgement)
        else:
            emit(acknowledgement); emit(terminal)
        continue
"""


def eventually(predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Stop fixture did not settle')


class StopResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-stop-resume-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = self.root / 'fixture.py'
        source = BASE_FIXTURE.read_text(encoding='utf-8')
        needle = "    elif subtype == 'interrupt':\n        break\n"
        self.assertEqual(1, source.count(needle))
        self.fixture.write_text(source.replace(needle, SOFT_INTERRUPT), encoding='utf-8')
        command = [sys.executable, '-B', str(self.fixture)]
        self.app = LocalApp(self.root / 'state', command=command, info=probe_cli(command),
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.addCleanup(self.release_stop)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)

    def frames(self):
        return [json.loads(line) for line in (self.root / 'restart-wire.jsonl').read_text().splitlines()]

    def connect(self, **config):
        (self.root / 'release-stop').unlink(missing_ok=True)
        (self.root / 'restart-config.json').write_text(json.dumps(config), encoding='utf-8')
        self.app.connect(self.sid)
        return self.item['bridge']

    def release_stop(self):
        (self.root / 'release-stop').touch()

    def wait_user_count(self, count):
        eventually(lambda: sum(row['type'] == 'user' for row in self.frames()) == count)

    def test_stop_and_manual_followup_keep_pid_identity_and_selected_controls(self):
        for result_first in (False, True):
            with self.subTest(result_first=result_first):
                if self.item.get('bridge'):
                    self.item['bridge'].close()
                bridge = self.connect(stopResultFirst=result_first)
                identity, pid = self.item['sessionId'], bridge.process.pid
                self.app.set_model(self.sid, 'alternate-model')
                self.app.set_effort(self.sid, 'high')
                self.app.set_permission_mode(self.sid, 'auto')
                before = len([row for row in self.frames() if row['type'] == 'user'])
                self.app.send(self.sid, 'wait for cancellation', [])
                self.wait_user_count(before + 1)
                response = self.app.stop(self.sid)
                self.assertEqual('stopping', response['session']['stopState'])
                self.assertEqual('stopping', response['session']['state'])
                with self.assertRaises(BridgeError) as caught:
                    self.app.send(self.sid, 'not submitted while stopping', [])
                self.assertEqual('stop_in_progress', caught.exception.code)
                self.release_stop()
                eventually(lambda: self.app.stop_state(self.item) == 'stopped')
                state = self.app.public(self.item)
                self.assertEqual('stopped', self.item['state'])
                self.assertFalse(state['connectionStopped'])
                self.assertTrue(state['connection']['connected'])
                self.assertEqual(('alternate-model', 'high', 'auto'),
                    tuple(state['connection'][key] for key in ('model', 'effort', 'permissionMode')))
                self.assertIsNone(bridge.process.poll())
                self.assertEqual(before + 1, len([row for row in self.frames() if row['type'] == 'user']))
                self.app.send(self.sid, 'explicit next request', [])
                eventually(lambda: self.item['state'] == 'done')
                self.assertIs(bridge, self.item['bridge'])
                self.assertEqual(pid, self.item['bridge'].process.pid)
                self.assertEqual(identity, self.item['sessionId'])
                self.assertNotIn('not submitted while stopping', [row['text'] for row in self.item['messages']])
                self.assertEqual(before + 2, len([row for row in self.frames() if row['type'] == 'user']))

    def test_queue_stays_paused_until_explicit_resume_then_uses_same_connection(self):
        bridge = self.connect()
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        self.app.dispatch.queue.enqueue(self.sid, 'only queued follow-up', context=self.app.dispatch.context(self.item))
        self.app.stop(self.sid)
        with self.assertRaises(BridgeError) as caught:
            self.app.dispatch.action(self.sid, {'action': 'resume'})
        self.assertEqual('stop_in_progress', caught.exception.code)
        self.app.dispatch.pump()
        self.assertEqual('queued', self.app.dispatch.snapshot(self.sid)['queue'][0]['status'])
        self.release_stop()
        eventually(lambda: self.app.stop_state(self.item) == 'stopped')
        self.app.dispatch.pump()
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))
        self.app.dispatch.action(self.sid, {'action': 'resume'})
        self.app.dispatch.pump()
        eventually(lambda: self.item['state'] == 'done')
        self.assertIs(bridge, self.item['bridge'])
        self.assertEqual(2, sum(row['type'] == 'user' for row in self.frames()))

    def test_saved_queue_apply_interrupts_once_then_keeps_cli_pid_and_queue_identity(self):
        for result_first in (False, True):
            with self.subTest(result_first=result_first):
                if self.item.get('bridge'):
                    self.item['bridge'].close()
                bridge = self.connect(stopResultFirst=result_first)
                identity, pid = self.item['sessionId'], bridge.process.pid
                self.app.set_permission_mode(self.sid, 'auto')
                context = self.app.dispatch.context(self.item)
                before = sum(row['type'] == 'user' for row in self.frames())
                interrupted = self.app.dispatch.queue.enqueue(self.sid, 'wait for cancellation', context=context)
                self.app.dispatch.pump(); self.wait_user_count(before + 1)
                first = self.app.dispatch.queue.enqueue(self.sid, 'first remaining', context=context)
                selected = self.app.dispatch.queue.enqueue(self.sid, 'selected now', context=context)
                last = self.app.dispatch.queue.enqueue(self.sid, 'last remaining', context=context)
                action = {'action': 'apply_now', 'requestId': selected['id'], 'editRevision': 0,
                          'clientRequestId': selected['id']}
                self.app.dispatch.action(self.sid, action)
                self.assertTrue(self.app.dispatch.action(self.sid, action)['alreadyApplied'])
                self.app.dispatch.pump()
                self.assertEqual(before + 1, sum(row['type'] == 'user' for row in self.frames()))
                self.release_stop()
                eventually(lambda: self.app.stop_state(self.item) == 'stopped')
                for expected in range(before + 2, before + 5):
                    self.app.dispatch.pump()
                    self.wait_user_count(expected)
                    eventually(lambda: self.item['state'] == 'done')
                self.assertEqual(['selected now', 'first remaining', 'last remaining'],
                                 [row['message']['content'] for row in self.frames() if row['type'] == 'user'][-3:])
                self.assertEqual([selected['id'], first['id'], last['id']],
                                 [row.get('requestId') for row in self.item['messages'] if row['role'] == 'user'][-3:])
                previous = next(row for row in self.app.dispatch.queue.data['queue'] if row['id'] == interrupted['id'])
                self.assertEqual('cancelled', previous['status'])
                self.assertEqual((identity, pid), (self.item['sessionId'], bridge.process.pid))
                self.assertEqual('auto', self.app.public(self.item)['connection']['permissionMode'])
                interrupts = [row for row in self.frames() if row.get('request', {}).get('subtype') == 'interrupt']
                self.assertEqual(1 if not result_first else 2, len(interrupts))
                self.assertTrue(self.app.dispatch.action(self.sid, action)['alreadyApplied'])
                self.assertEqual([], self.app.dispatch.snapshot(self.sid)['queue'])

    def test_fresh_queue_after_recovered_stop_autoruns_on_real_cli_success(self):
        # Gate one synthetic CLI result so requests can be enqueued through the
        # production controller while the transport is actually busy.
        source = self.fixture.read_text(encoding='utf-8')
        needle = "    if data['type'] == 'user':\n"
        gate = """        if data['message']['content'] == 'wait for successful completion':
            import time
            deadline = time.monotonic() + 6
            while not (root / 'release-completion').exists() and time.monotonic() < deadline:
                time.sleep(.01)
"""
        self.assertEqual(1, source.count(needle))
        self.fixture.write_text(source.replace(needle, needle + gate), encoding='utf-8')
        bridge = self.connect()
        identity, pid = self.item['sessionId'], bridge.process.pid
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        self.app.stop(self.sid)
        self.release_stop()
        eventually(lambda: self.app.stop_state(self.item) == 'stopped')
        self.assertTrue(self.app.dispatch.snapshot(self.sid)['paused'])
        self.app.send(self.sid, 'wait for successful completion', [])
        self.wait_user_count(2)
        for text in ('automatic first', 'automatic second'):
            self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': text, 'attachments': [],
                                               'clientRequestId': text})
        self.assertFalse(self.app.dispatch.snapshot(self.sid)['paused'])
        self.app.dispatch.start()
        self.app.dispatch.pump()
        self.assertEqual(2, sum(row['type'] == 'user' for row in self.frames()))
        (self.root / 'release-completion').touch()
        eventually(lambda: not self.app.dispatch.snapshot(self.sid)['queue'])
        self.assertEqual(['wait for cancellation', 'wait for successful completion',
                          'automatic first', 'automatic second'],
                         [row['message']['content'] for row in self.frames() if row['type'] == 'user'])
        self.assertEqual('done', self.item['state'])
        self.assertEqual('unverified', self.item['verification']['state'])
        self.assertEqual((identity, pid), (self.item['sessionId'], bridge.process.pid))
        self.assertIsNone(self.app.dispatch.error)

    def test_failed_cleanup_blocks_send_connect_and_queue_without_losing_pending_work(self):
        self.item['bridge'] = Mock(closed=True, stopping=True, stop_state='failed',
                                   cleanup_complete=False, cleanup_retryable=False)
        self.item['bridge'].close.return_value = True
        self.item['state'] = 'idle'  # Reproduces stale UI / queue-resume projection.
        row = self.app.dispatch.queue.enqueue(self.sid, 'preserved next request',
                                              context=self.app.dispatch.context(self.item))
        for action in (lambda: self.app.send(self.sid, 'do not submit', []),
                       lambda: self.app.connect(self.sid),
                       lambda: self.app.dispatch.action(self.sid, {'action': 'resume'})):
            with self.assertRaises(BridgeError) as caught:
                action()
            self.assertEqual('stop_cleanup_unverified', caught.exception.code)
        self.app.dispatch.pump()
        self.assertEqual([], self.item['messages'])
        snapshot = self.app.dispatch.snapshot(self.sid)
        self.assertEqual(row['id'], snapshot['queue'][0]['id'])
        self.assertEqual('queued', snapshot['queue'][0]['status'])
        public = self.app.public(self.item)
        self.assertEqual('failed', public['stopState'])
        self.assertFalse(public['cleanupRetryable'])

    def test_pending_result_does_not_announce_completion_and_old_stop_cannot_overwrite_new_turn(self):
        bridge = self.connect()
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        self.app.stop(self.sid)
        sequence = self.item['seq']
        self.app.emit(self.sid, 'result', {'sessionId': self.item['sessionId'], 'verification': {'state': 'unverified'}})
        self.assertNotEqual('done', self.item['state'])
        self.assertEqual(sequence, self.item['seq'])
        self.release_stop()
        eventually(lambda: self.app.stop_state(self.item) == 'stopped')
        old_run = self.item['lastRunId']
        self.app.send(self.sid, 'wait for cancellation', [])
        self.app._emit_bridge(self.sid, bridge, 'status', {'state': 'stopped', 'runId': old_run})
        self.assertNotEqual('stopped', self.item['state'])

    def test_stop_error_api_carries_actionable_state_without_generic_reconnect(self):
        bridge = self.connect()
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def cleanup():
            server.shutdown(); server.server_close(); worker.join(2)
        self.addCleanup(cleanup)
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        self.app.stop(self.sid)
        request = Request(server.origin + '/api/send',
            data=json.dumps({'id': self.sid, 'text': 'not submitted', 'attachments': []}).encode(),
            headers={'Authorization': 'Bearer ' + self.app.token, 'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        with caught.exception as response:
            payload = json.load(response)
        self.assertEqual('stop_in_progress', payload['code'])
        self.assertEqual('stopping', payload['stopState'])
        self.assertFalse(payload['connectionStopped'])
        self.assertFalse(payload['cleanupRetryable'])

    def test_explicit_disconnect_releases_owned_cli_without_replaying_request(self):
        bridge = self.connect()
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        result = self.app.stop(self.sid, disconnect=True)
        self.assertIn(result['session']['stopState'], {'stopping', 'stopped'})
        eventually(lambda: self.app.public(self.item)['connectionStopped'])
        self.assertIsNotNone(bridge.process.poll())
        self.assertTrue(bridge.cleanup_complete)
        self.assertEqual('stopped', self.app.public(self.item)['stopState'])
        self.assertEqual(1, sum(row['type'] == 'user' for row in self.frames()))
        self.app.send(self.sid, 'explicit follow-up after disconnect', [])
        eventually(lambda: self.item['state'] == 'done')
        self.assertIsNot(bridge, self.item['bridge'])
        self.assertEqual(2, sum(row['type'] == 'user' for row in self.frames()))

    def test_explicit_disconnect_flag_rejects_truthy_non_booleans(self):
        bridge = self.connect()
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.app.stop(self.sid, disconnect=value)
        self.assertIsNone(bridge.process.poll())
        self.assertIsNone(self.app.stop_state(self.item))

    def test_stop_admission_blocks_new_turn_before_bridge_interrupt_acquires_its_lock(self):
        bridge = self.connect()
        entered, release = threading.Event(), threading.Event()
        original = bridge.interrupt
        errors = []
        def delayed():
            entered.set(); release.wait(3)
            return original()
        def stop():
            try:
                self.app.stop(self.sid)
            except Exception as exc:
                errors.append(exc)
        with patch.object(bridge, 'interrupt', delayed):
            worker = threading.Thread(target=stop)
            worker.start()
            self.assertTrue(entered.wait(2))
            try:
                for action in (lambda: self.app.send(self.sid, 'must not race stop', []),
                               lambda: self.app.connect(self.sid),
                               lambda: self.app.set_model(self.sid, 'alternate-model')):
                    with self.assertRaises(BridgeError) as caught:
                        action()
                    self.assertEqual('stop_in_progress', caught.exception.code)
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertNotIn('_stopAdmission', self.item)
        self.assertFalse(any(row['type'] == 'user' for row in self.frames()))

    @unittest.skipUnless(os.name == 'nt', 'Native terminal handoff is Windows-only')
    def test_native_handoff_requires_explicit_disconnect_not_cooperative_stop(self):
        bridge = self.connect()
        identity = self.item['sessionId']
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def cleanup():
            server.shutdown(); server.server_close(); worker.join(2)
        self.addCleanup(cleanup)
        def post(route, **body):
            request = Request(server.origin + route, data=json.dumps({'id': self.sid, **body}).encode(),
                headers={'Authorization': 'Bearer ' + self.app.token, 'Content-Type': 'application/json'})
            try:
                response = urlopen(request, timeout=3)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)
        self.app.send(self.sid, 'wait for cancellation', [])
        self.wait_user_count(1)
        self.app.stop(self.sid)
        self.release_stop()
        eventually(lambda: self.app.stop_state(self.item) == 'stopped')
        self.assertFalse(bridge.cleanup_complete)
        code, _ = post('/api/native')
        self.assertEqual(400, code)
        code, result = post('/api/stop', disconnect=True)
        self.assertEqual(200, code)
        self.assertIn('session', result)
        eventually(lambda: bridge.cleanup_complete)
        with patch('local_app.server.subprocess.Popen') as launched:
            code, _ = post('/api/native')
        self.assertEqual(200, code)
        launched.assert_called_once()
        self.assertIn('--resume=' + identity, launched.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
