"""Explicit-stop classification without a real CLI, authentication, or network."""
from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from local_app.bridge import ClaudeSession, cli_arguments
from local_app.server import LocalApp


STOP_DIAGNOSTIC = '[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'


def result_frame(session_id, **fields):
    return {'type': 'result', 'session_id': session_id, 'is_error': True,
            'result': STOP_DIAGNOSTIC, **fields}


class StopClassificationTests(unittest.TestCase):
    def test_followup_waits_for_inflight_completed_result_before_sending_next_turn(self):
        # busy=False precedes delivery of the result through the app lock. An
        # interrupt in that window must not synthesize stopped and allow a new
        # run to be completed by the prior run's delayed result callback.
        for already_waiting in (False, True):
            with self.subTest(already_waiting=already_waiting), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                work = root / 'work'
                work.mkdir()
                app = LocalApp(root / 'state', command=['not-started'], managed_workspace_root=root / 'managed')
                sid = app.create(str(work), True)['id']
                item = app.get(sid)
                entered, release = threading.Event(), threading.Event()
                failures = []

                def emit(kind, data):
                    if kind == 'result':
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError('Result delivery was not released')
                    app._emit_bridge(sid, bridge, kind, data)

                bridge = ClaudeSession(['not-started'], {}, work, emit)
                bridge.process = Mock()
                bridge.process.poll.return_value = None
                bridge.process.stdin = io.BytesIO()
                bridge.session_id = str(uuid.uuid4())
                bridge.ready.set()
                bridge.busy = True
                bridge._turn_submitted = True
                bridge._turn_activity_seen = False
                item.update(bridge=bridge, state='running', sessionId=bridge.session_id, lastRunId='old-run')
                selected = app.dispatch.queue.enqueue(sid, 'selected next turn', context=app.dispatch.context(item))
                remaining = app.dispatch.queue.enqueue(sid, 'keep queued', context=app.dispatch.context(item))
                action = {'action': 'apply_now', 'requestId': selected['id'], 'editRevision': 0,
                          'clientRequestId': 'select-next'}
                reader = None
                try:
                    if already_waiting:
                        self.assertEqual('waiting_activity', app.dispatch.action(sid, action)['applyingPhase'])

                    def finish_old_turn():
                        try:
                            bridge.handle({'type': 'result', 'session_id': bridge.session_id,
                                           'is_error': False, 'result': ''})
                        except BaseException as exc:
                            failures.append(exc)

                    reader = threading.Thread(target=finish_old_turn, daemon=True)
                    reader.start()
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(bridge.busy)
                    self.assertEqual('running', item['state'])
                    if not already_waiting:
                        self.assertEqual('waiting_activity', app.dispatch.action(sid, action)['applyingPhase'])

                    def send_next(_sid, text, attachments, **_kwargs):
                        item.update(state='running', lastRunId='next-run')
                        bridge.busy = True

                    with patch.object(app, 'send', side_effect=send_next) as send:
                        app.dispatch.pump()
                        send.assert_not_called()
                        self.assertEqual('waiting_activity', app.dispatch.snapshot(sid)['applyingPhase'])
                        self.assertEqual(b'', bridge.process.stdin.getvalue())
                        release.set()
                        reader.join(2)
                        self.assertFalse(reader.is_alive())
                        self.assertEqual([], failures)
                        self.assertEqual('done', item['state'])
                        app.dispatch.pump()
                        send.assert_called_once()
                        self.assertEqual('selected next turn', send.call_args.args[1])
                        self.assertEqual('running', item['state'])
                        queue = app.dispatch.snapshot(sid)['queue']
                        self.assertEqual([(selected['id'], 'submitted'), (remaining['id'], 'queued')],
                                         [(row['id'], row['status']) for row in queue])
                finally:
                    release.set()
                    if reader is not None:
                        reader.join(2)
                    item['bridge'] = None  # The injected process is not a real owned child.
                    app.close()

    def test_interrupt_readiness_requires_root_activity_after_each_submission(self):
        bridge, native_id, _ = self.session()
        bridge._turn_submitted = True
        self.assertFalse(bridge.interrupt_ready)
        for frame in (
                {'type': 'system', 'subtype': 'init', 'session_id': native_id},
                {'type': 'system', 'subtype': 'status', 'status': 'compacting'},
                {'type': 'assistant', 'parent_tool_use_id': 'worker',
                 'message': {'content': []}}):
            bridge.handle(frame)
            self.assertFalse(bridge.interrupt_ready, frame)
        bridge.handle({'type': 'stream_event', 'session_id': native_id,
                       'event': {'type': 'message_start', 'message': {'id': 'root'}}})
        self.assertTrue(bridge.interrupt_ready)
        bridge.busy = False
        with patch('local_app.bridge.threading.Thread'):
            bridge.send('next explicit turn')
        bridge._turn_submitted = True
        self.assertFalse(bridge.interrupt_ready)
        bridge.handle({'type': 'assistant', 'session_id': native_id,
                       'message': {'id': 'next-root', 'content': []}})
        self.assertTrue(bridge.interrupt_ready)

    def test_unsent_turn_can_cancel_but_finished_turn_defers_to_terminal_callback(self):
        bridge, _, _ = self.session()
        bridge._turn_submitted = False
        self.assertTrue(bridge.interrupt_ready)
        bridge._turn_submitted = True
        bridge.busy = False
        self.assertFalse(bridge.interrupt_ready)
        self.assertFalse(bridge.interrupt_when_ready())

    def test_guarded_interrupt_rechecks_submission_atomically(self):
        bridge, _, events = self.session()
        bridge._turn_submitted = False
        self.assertTrue(bridge.interrupt_ready)
        # Sender wins the bridge lock after dispatch read the prior readiness.
        bridge._turn_submitted = True
        with patch.object(bridge, '_write') as write:
            self.assertFalse(bridge.interrupt_when_ready())
        write.assert_not_called()
        self.assertTrue(bridge.busy)
        self.assertFalse(bridge.stopping)
        self.assertIsNone(bridge._stop_attempt)
        self.assertEqual([], events)

    def session(self, *, stopping=False, **options):
        events = []
        native_id = str(uuid.uuid4())
        bridge = ClaudeSession(['not-started'], {}, Path.cwd(),
                               lambda kind, data: events.append((kind, data)), **options)
        bridge.session_id = native_id
        bridge.busy = True
        bridge.stopping = stopping
        bridge.pending = {'question': {'tool_name': 'AskUserQuestion'},
                          'permission': {'tool_name': 'Bash'}}
        bridge._permission_choices = {'permission': {'choice': 'allow'}}
        bridge.tasks = {'worker'}
        self.addCleanup(bridge.close)
        return bridge, native_id, events

    def assert_turn_released(self, bridge, events):
        self.assertFalse(bridge.busy)
        self.assertEqual({}, bridge.pending)
        self.assertEqual({}, bridge._permission_choices)
        self.assertEqual(set(), bridge.tasks)
        self.assertEqual({'question', 'permission'},
                         {data['id'] for kind, data in events if kind == 'request_closed'})

    def test_unrequested_diagnostic_remains_incomplete_turn_failure(self):
        bridge, native_id, events = self.session()
        bridge.handle(result_frame(native_id))

        self.assert_turn_released(bridge, events)
        errors = [data for kind, data in events if kind == 'error']
        self.assertEqual(1, len(errors))
        self.assertEqual('cli_turn_incomplete', errors[0]['code'])
        self.assertEqual(native_id, errors[0]['resumeSessionId'])
        self.assertEqual(native_id, bridge.resume_id)
        self.assertFalse(any(kind in {'assistant', 'result'} for kind, _ in events))
        self.assertIsNone(bridge.process)

    def test_requested_stop_releases_turn_without_error_answer_or_completion(self):
        for fields in ({}, {'result': '', 'errors': [STOP_DIAGNOSTIC]}):
            with self.subTest(fields=fields):
                bridge, native_id, events = self.session(stopping=True)
                bridge.handle(result_frame(native_id, **fields))

                self.assert_turn_released(bridge, events)
                self.assertEqual(native_id, bridge.session_id)
                self.assertEqual(native_id, bridge.resume_id)
                self.assertFalse(any(kind in {'assistant', 'result', 'error'}
                                     for kind, _ in events), events)
                self.assertFalse(any(kind == 'status' and data.get('state') == 'stopped'
                                     for kind, data in events))
                self.assertFalse(bridge.closed)  # Cleanup still owns the final stop status.
                self.assertIsNone(bridge.process)  # No automatic replay or reconnect.

    def test_prompt_check_is_benign_only_during_explicit_stop(self):
        message = 'prompt_check: the turn was stopped before its prompt was checked'
        for stopping in (False, True):
            bridge, native_id, events = self.session(stopping=stopping)
            bridge.handle(result_frame(native_id, result=message))
            self.assertEqual(not stopping, any(kind == 'error' for kind, _ in events))
        bridge, native_id, events = self.session(stopping=True)
        bridge.handle(result_frame(native_id, result=message, errors=['actual tool failure']))
        self.assertTrue(any(kind == 'error' for kind, _ in events))

    def test_delegated_diagnostic_cannot_finish_parent_stop_or_replace_identity(self):
        bridge, native_id, events = self.session(stopping=True)
        bridge.handle(result_frame(str(uuid.uuid4()), parent_tool_use_id='worker'))

        self.assertTrue(bridge.busy)
        self.assertEqual({'question', 'permission'}, set(bridge.pending))
        self.assertEqual({'worker'}, bridge.tasks)
        self.assertEqual(native_id, bridge.session_id)
        self.assertEqual([], events)

    def test_unrecognized_diagnostic_or_other_session_is_not_an_expected_stop(self):
        for fields in (
                {'result': '[ede_diagnostic]'},
                {'result': '[ede_diagnostic] result_type=assistant last_content_type=text stop_reason=end_turn'},
                {'session_id': str(uuid.uuid4())}):
            with self.subTest(fields=fields):
                bridge, native_id, events = self.session(stopping=True)
                frame = result_frame(native_id)
                frame.update(fields)
                bridge.handle(frame)
                self.assertTrue(any(kind == 'error' for kind, _ in events), events)

    def test_authentication_failure_is_preserved_in_either_result_field(self):
        auth = 'Failed to authenticate: OAuth session expired and could not be refreshed'
        for fields in ({'result': auth}, {'errors': [auth]},
                       {'result': auth, 'errors': [STOP_DIAGNOSTIC]},
                       {'errors': [STOP_DIAGNOSTIC, auth]}):
            with self.subTest(fields=fields):
                bridge, native_id, events = self.session(stopping=True)
                bridge.resume_id = native_id
                bridge.handle(result_frame(native_id, **fields))
                errors = [data for kind, data in events if kind == 'error']
                self.assertEqual(['cli_authentication'], [row['code'] for row in errors])
                self.assertEqual(native_id, errors[0]['resumeSessionId'])
                self.assertTrue(bridge.closed)
                self.assertFalse(any(kind in {'assistant', 'result'} for kind, _ in events))

    def test_real_error_mixed_with_stop_diagnostic_is_not_suppressed(self):
        failure = 'Tool execution failed: permission denied for the requested file'
        for fields in ({'result': failure}, {'errors': [failure]},
                       {'errors': [STOP_DIAGNOSTIC, failure]},
                       {'result': failure, 'errors': [STOP_DIAGNOSTIC]},
                       {'result': STOP_DIAGNOSTIC + '\n' + failure}):
            with self.subTest(fields=fields):
                bridge, native_id, events = self.session(stopping=True)
                bridge.handle(result_frame(native_id, **fields))
                self.assert_turn_released(bridge, events)
                self.assertEqual(1, sum(kind == 'error' for kind, _ in events), events)
                self.assertFalse(any(kind in {'assistant', 'result'} for kind, _ in events))

    def test_uninitialized_fork_result_is_not_mistaken_for_confirmed_stop(self):
        source, child = str(uuid.uuid4()), str(uuid.uuid4())
        events = []
        bridge = ClaudeSession(['not-started'],
            {'help': '--resume [value]\n --fork-session\n --session-id <uuid>\n'}, Path.cwd(),
            lambda kind, data: events.append((kind, data)), source,
            fork_session=True, new_session_id=child)
        self.addCleanup(bridge.close)
        bridge.busy = bridge.stopping = True
        bridge.handle(result_frame(child))

        self.assertTrue(any(kind == 'error' for kind, _ in events), events)
        self.assertFalse(any(kind in {'assistant', 'result'} for kind, _ in events))

    def test_first_native_result_preserves_identity_recovery_without_init(self):
        bridge, native_id, events = self.session(stopping=True)
        bridge.session_id = None
        bridge.handle(result_frame(native_id))

        errors = [data for kind, data in events if kind == 'error']
        self.assertEqual(1, len(errors))
        self.assertEqual(native_id, errors[0]['resumeSessionId'])
        self.assertEqual(native_id, bridge.resume_id)

    def test_initialized_fork_can_stop_without_false_failure(self):
        source, child = str(uuid.uuid4()), str(uuid.uuid4())
        events = []
        bridge = ClaudeSession(['not-started'],
            {'help': '--resume [value]\n --fork-session\n --session-id <uuid>\n'}, Path.cwd(),
            lambda kind, data: events.append((kind, data)), source,
            fork_session=True, new_session_id=child)
        self.addCleanup(bridge.close)
        bridge.handle({'type': 'system', 'subtype': 'init', 'session_id': child})
        events.clear()
        bridge.busy = bridge.stopping = True
        bridge.handle(result_frame(child))

        self.assertEqual([], events)
        self.assertFalse(bridge.busy)
        self.assertEqual(child, bridge.resume_id)

    def test_failed_owned_process_cleanup_still_reports_error(self):
        bridge, native_id, events = self.session(stopping=True)
        bridge.handle(result_frame(native_id))
        events.clear()
        process = bridge.process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('not-started', 5)
        with patch('local_app.bridge.os.name', 'nt'), \
                patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=0)), \
                patch('local_app.bridge.time.sleep'):
            bridge._finish_stop()

        self.assertFalse(bridge.cleanup_complete)
        self.assertTrue(any(kind == 'error' for kind, _ in events), events)
        self.assertFalse(any(kind == 'status' and data.get('state') == 'stopped'
                             for kind, data in events))

    def test_new_connection_can_resume_same_native_conversation_normally(self):
        stopped, native_id, _ = self.session(stopping=True)
        stopped.handle(result_frame(native_id))
        self.assertTrue(stopped.close())
        events = []
        resumed = ClaudeSession(['not-started'], {}, Path.cwd(),
            lambda kind, data: events.append((kind, data)), stopped.resume_id)
        self.addCleanup(resumed.close)

        self.assertFalse(resumed.stopping)
        self.assertIn('--resume=' + native_id,
                      cli_arguments(resumed.command, resumed.info, resumed.session_id))
        resumed.busy = True
        resumed.handle(result_frame(native_id, is_error=False, result='후속 요청의 정상 답변'))
        self.assertFalse(resumed.busy)
        self.assertEqual(native_id, resumed.resume_id)
        self.assertEqual(['후속 요청의 정상 답변'],
                         [data['text'] for kind, data in events if kind == 'assistant'])
        self.assertEqual(1, sum(kind == 'result' for kind, _ in events))
        self.assertFalse(any(kind == 'error' for kind, _ in events))


class LocalAppStopClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-stop-classification-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        work = root / 'work'
        work.mkdir()
        self.app = LocalApp(root / 'state', command=['not-started'],
                            managed_workspace_root=root / 'managed')
        self.addCleanup(self.app.close)
        self.app.desktop._automatic_retry = False
        self.app.desktop.cooldown = 0
        self.sid = self.app.create(str(work), True)['id']
        self.item = self.app.get(self.sid)
        self.native_id = str(uuid.uuid4())
        self.bridge = ClaudeSession(['not-started'], {}, work,
            lambda kind, data: self.app._emit_bridge(self.sid, self.bridge, kind, data),
            self.native_id)
        self.item.update(bridge=self.bridge, sessionId=self.native_id, state='running',
                         lastRunId='stopped-run', messages=[{'role': 'user', 'text': '원래 요청'}])
        self.bridge.busy = True
        self.bridge.pending = {'question': {'tool_name': 'AskUserQuestion'}}
        self.bridge.tasks = {'worker'}
        process = self.bridge.process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.return_value = 0
        self.app.emit(self.sid, 'request', {'id': 'question', 'tool': 'AskUserQuestion',
                                           'input': {'questions': []}})

    def stop_with_diagnostic(self):
        def interrupted(frame):
            self.assertEqual('interrupt', frame['request']['subtype'])
            self.assertTrue(self.bridge.stopping)
            self.bridge.handle(result_frame(self.native_id))
            self.bridge.handle({'type': 'control_response', 'response': {
                'subtype': 'success', 'request_id': frame['request_id'], 'response': {}}})

        with patch.object(self.bridge, '_write', side_effect=interrupted), \
                patch('local_app.bridge.threading.Thread') as worker, \
                patch('local_app.bridge.time.sleep'):
            self.app.stop(self.sid)
            worker.assert_called_once()
            worker.call_args.kwargs['target']()

    def test_stop_does_not_create_failure_or_completion_event_or_notification(self):
        with patch.object(self.app, '_publish_notification',
                          wraps=self.app._publish_notification) as publish:
            self.stop_with_diagnostic()

        self.assertEqual('stopped', self.item['state'])
        self.assertEqual({}, self.item['requests'])
        self.assertFalse(self.bridge.busy)
        self.assertEqual(set(), self.bridge.tasks)
        self.assertFalse(self.bridge.cleanup_complete)
        self.assertFalse(self.bridge.closed)
        self.assertEqual('stopped', self.bridge.stop_state)
        self.assertEqual(self.native_id, self.item['sessionId'])
        self.assertEqual([{'role': 'user', 'text': '원래 요청'}], self.item['messages'])
        self.assertFalse(any(row['type'] in {'assistant', 'result', 'error'}
                             for row in self.item['events']))
        self.assertFalse(any(call.args[2] in {'error', 'completed'}
                             for call in publish.call_args_list))
        self.assertFalse(any(row['kind'] in {'error', 'completed'}
                             for row in self.app.desktop.snapshot()['inbox']))
        self.assertEqual('stopped', self.app.dispatch.snapshot(self.sid)['reason'])

    def test_explicit_followup_keeps_stopped_child_and_same_session(self):
        self.stop_with_diagnostic()
        previous = self.bridge
        with patch.object(ClaudeSession, 'send', autospec=True) as send:
            self.app.send(self.sid, '후속 요청', [])
        resumed = self.item['bridge']

        self.assertIs(previous, resumed)
        self.assertFalse(resumed.closed)
        self.assertFalse(resumed.stopping)
        self.assertEqual(self.native_id, resumed.session_id)
        self.assertIn('--resume=' + self.native_id,
                      cli_arguments(resumed.command, resumed.info, resumed.session_id))
        send.assert_called_once_with(resumed, '후속 요청')
        resumed.handle(result_frame(self.native_id, is_error=False, result='후속 요청 완료'))

        self.assertEqual('done', self.item['state'])
        self.assertEqual(self.native_id, self.item['sessionId'])
        self.assertEqual(['원래 요청', '후속 요청', '후속 요청 완료'],
                         [row['text'] for row in self.item['messages']])
        self.assertFalse(any(row['type'] == 'error' for row in self.item['events']))
        self.assertFalse(any(row['kind'] == 'error'
                             for row in self.app.desktop.snapshot()['inbox']))


class StopProtocolProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-stop-protocol-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture = Path(__file__).parent / 'fixtures/workspace_stop_cli.py'
        self.events = []
        self.bridge = ClaudeSession([sys.executable, '-B', str(fixture.resolve())], {}, self.root,
                                    lambda kind, data: self.events.append((kind, data)))
        self.addCleanup(self.bridge.close)
        self.bridge.prepare()

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('isolated stop fixture did not settle')

    def configure(self, **value):
        (self.root / 'stop-config.json').write_text(json.dumps(value), encoding='utf-8')

    def wire(self):
        return [json.loads(line) for line in (self.root / 'stop-wire.jsonl').read_text().splitlines()]

    def start_turn(self, text='wait for cancellation'):
        self.bridge.send(text)
        self.wait(lambda: any(row.get('type') == 'user' and row['message']['content'] == text
                              for row in self.wire()))

    def test_ack_and_result_in_either_order_keep_pid_and_explicit_followup(self):
        for order in ('ack-first', 'result-first'):
            with self.subTest(order=order):
                self.configure(order=order)
                pid, native_id = self.bridge.process.pid, self.bridge.session_id
                self.bridge.model_override = 'preserved-selection'
                self.start_turn('wait ' + order)
                self.bridge.interrupt()
                self.wait(lambda: self.bridge.stop_state == 'stopped')
                self.assertFalse(self.bridge.stopping)
                self.assertFalse(self.bridge.closed)
                self.assertFalse(self.bridge.cleanup_complete)
                self.assertIsNone(self.bridge.process.poll())
                self.assertEqual('preserved-selection', self.bridge.model_override)
                self.bridge.send('followup ' + order)
                self.wait(lambda: not self.bridge.busy)
                self.assertEqual(pid, self.bridge.process.pid)
                self.assertEqual(native_id, self.bridge.session_id)
                self.assertFalse(any(kind == 'error' for kind, _ in self.events))
        self.assertEqual(4, sum(row['type'] == 'user' for row in self.wire()))

    def test_approval_is_drained_and_repeated_stop_does_not_duplicate_interrupt(self):
        self.configure(delay=.15)
        self.start_turn('wait approval')
        self.wait(lambda: bool(self.bridge.pending))
        self.bridge.interrupt()
        self.bridge.interrupt()
        with self.assertRaises(ValueError):
            self.bridge.send('must not submit before acknowledgement')
        self.wait(lambda: self.bridge.stop_state == 'stopped')
        self.assertEqual({}, self.bridge.pending)
        self.assertEqual(1, sum(row.get('request', {}).get('subtype') == 'interrupt' for row in self.wire()))
        self.assertFalse(any(row.get('message', {}).get('content', '').startswith('must not') for row in self.wire()))

    def test_idle_stop_preserves_connection_without_protocol_or_forced_cleanup(self):
        process = self.bridge.process
        self.bridge.interrupt()
        self.assertEqual('stopped', self.bridge.stop_state)
        self.assertFalse(self.bridge.closed)
        self.assertIsNone(process.poll())
        self.assertFalse(any(row.get('request', {}).get('subtype') == 'interrupt' for row in self.wire()))

    def test_old_watchdog_cannot_close_followup_turn(self):
        with patch('local_app.bridge.STOP_RESPONSE_TIMEOUT', .25):
            self.start_turn()
            self.bridge.interrupt()
            self.wait(lambda: self.bridge.stop_state == 'stopped')
            self.start_turn('wait next turn')
            time.sleep(.35)
        self.assertTrue(self.bridge.busy)
        self.assertFalse(self.bridge.closed)
        self.assertIsNone(self.bridge.process.poll())

    def test_missing_ack_or_terminal_uses_bounded_owned_cleanup(self):
        for missing in ('ack', 'result'):
            with self.subTest(missing=missing):
                # Each subcase needs its own connection after fallback closes it.
                if self.bridge.closed:
                    self.setUp()
                self.configure(missing=missing)
                with patch('local_app.bridge.STOP_RESPONSE_TIMEOUT', .15):
                    self.start_turn()
                    self.bridge.interrupt()
                    self.wait(lambda: self.bridge.stop_state == 'stopped')
                self.assertTrue(self.bridge.cleanup_complete)
                self.assertTrue(self.bridge.closed)

    def test_real_error_and_auth_failure_are_not_suppressed(self):
        self.configure(error='Tool execution failed: permission denied', order='result-first')
        self.start_turn()
        self.bridge.interrupt()
        self.wait(lambda: self.bridge.stop_state == 'stopped')
        self.assertTrue(any(kind == 'error' and data.get('code') == 'task_failed' for kind, data in self.events))
        self.assertTrue(any(kind == 'status' and data.get('state') == 'error' for kind, data in self.events))
        self.assertFalse(self.bridge.closed)
        self.configure(error='Failed to authenticate: OAuth session expired', order='ack-first')
        self.start_turn('wait authentication')
        self.bridge.interrupt()
        self.wait(lambda: any(kind == 'error' and data.get('code') == 'cli_authentication'
                              for kind, data in self.events))
        self.assertTrue(self.bridge.closed)

    def test_cancel_before_user_write_never_replays_prompt_or_closes_followup(self):
        with patch('local_app.bridge.threading.Thread') as worker:
            self.bridge.send('cancelled before submission')
            pending = worker.call_args
        self.bridge.interrupt()
        self.assertEqual('stopped', self.bridge.stop_state)
        self.assertFalse(self.bridge.closed)
        self.bridge.send('explicit next turn')
        pending.kwargs['target'](*pending.kwargs['args'])
        self.wait(lambda: not self.bridge.busy)
        messages = [row['message']['content'] for row in self.wire() if row['type'] == 'user']
        self.assertEqual(['explicit next turn'], messages)

    def test_first_system_identity_arriving_after_stop_is_retained(self):
        self.bridge.close()
        self.configure(deferInit=True)
        self.bridge = ClaudeSession(self.bridge.command, {}, self.root,
                                    lambda kind, data: self.events.append((kind, data)))
        self.addCleanup(self.bridge.close)
        self.bridge.prepare()
        self.assertIsNone(self.bridge.session_id)
        self.start_turn()
        self.bridge.interrupt()
        self.wait(lambda: self.bridge.stop_state == 'stopped')
        self.assertIsNotNone(self.bridge.session_id)
        self.assertFalse(self.bridge.closed)
        self.assertFalse(any(kind == 'error' for kind, _ in self.events))

    def test_disconnect_option_closes_owned_connection_for_native_handoff(self):
        self.bridge.interrupt(disconnect=True)
        self.wait(lambda: self.bridge.cleanup_complete)
        self.assertTrue(self.bridge.closed)


if __name__ == '__main__':
    unittest.main()
