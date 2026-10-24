"""Boundary file reads must not stall unrelated controls or reorder run ownership."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import io
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from local_app.artifacts import snapshot
from local_app.bridge import BridgeError, ClaudeSession
from local_app.server import LocalApp


class ObservationResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'a'
        self.other = self.root / 'b'
        self.work.mkdir()
        self.other.mkdir()
        self.source = self.work / 'report.md'
        self.source.write_text('original', encoding='utf-8')
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.other_sid = self.app.create(str(self.other), True)['id']
        self.item = self.app.get(self.sid)
        self.other_item = self.app.get(self.other_sid)
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.addCleanup(lambda: self.executor.shutdown(wait=True))

    def gate(self, target):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('file observation gate was not released')
            return target(*args, **kwargs)
        return entered, release, slow

    def enqueue(self, sid, text):
        return self.app.dispatch.action(sid, {'action': 'enqueue', 'text': text, 'attachments': []})['queue']

    def arm_finish(self):
        self.item.update(state='running', lastRunId='run-a', _artifactSnapshot=snapshot(self.work))
        self.app.file_diffs.start(self.work, 'run-a')
        self.source.write_text('changed by fixture only', encoding='utf-8')

    def test_slow_finish_keeps_other_cancel_reorder_apply_and_metadata_available(self):
        self.arm_finish()
        self.other_item['state'] = 'running'
        cancelled = self.enqueue(self.other_sid, 'cancel this')[0]
        for text in ('second', 'third'):
            rows = self.enqueue(self.other_sid, text)
        interrupted = []
        self.other_item['bridge'] = SimpleNamespace(closed=False, cleanup_complete=True,
            close=lambda: True, interrupt=lambda: interrupted.append(True))
        self.app.demo = False
        entered, release, slow = self.gate(self.app.file_diffs.finish)
        with patch.object(self.app.file_diffs, 'finish', side_effect=slow):
            writer = self.executor.submit(self.app.emit, self.sid, 'result', {})
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual('running', self.executor.submit(self.app.public, self.item).result(.5)['state'])
                self.assertEqual([], self.executor.submit(self.app.results, self.sid).result(.5)['artifacts'])
                result = self.executor.submit(self.app.dispatch.action, self.other_sid,
                    {'action': 'cancel', 'requestId': cancelled['id']}).result(.5)
                order = [row['id'] for row in reversed(result['queue'])]
                result = self.executor.submit(self.app.dispatch.action, self.other_sid,
                    {'action': 'reorder', 'order': order}).result(.5)
                self.assertEqual(order, [row['id'] for row in result['queue']])
                selected = result['queue'][0]
                applied = self.executor.submit(self.app.dispatch.action, self.other_sid,
                    {'action': 'apply_now', 'requestId': selected['id'], 'editRevision': selected['editRevision'],
                     'clientRequestId': 'slow-finish-apply'}).result(.5)
                self.assertEqual(selected['id'], applied['applyingId'])
                self.assertEqual([True], interrupted)
            finally:
                release.set()
            writer.result(2)
        self.assertEqual('done', self.item['state'])
        self.assertEqual(1, len(self.item['artifacts']))
        self.assertEqual('run-a', self.item['artifacts'][0]['runId'])
        self.assertEqual('changed by fixture only', self.source.read_text(encoding='utf-8'))
        self.assertNotIn('_observationPending', self.item)

    def test_slow_start_allows_other_cancel_and_own_stop_before_any_prompt(self):
        self.other_item['state'] = 'running'
        row = self.enqueue(self.other_sid, 'cancel')[0]
        entered, release, slow = self.gate(snapshot)
        with patch('local_app.server.snapshot', side_effect=slow), patch('local_app.demo.run') as run:
            sender = self.executor.submit(self.app.send, self.sid, 'keep original request', [])
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual([], self.executor.submit(self.app.dispatch.action, self.other_sid,
                    {'action': 'cancel', 'requestId': row['id']}).result(.5)['queue'])
                with self.assertRaises(ValueError):
                    self.executor.submit(self.app.send, self.sid, 'duplicate', []).result(.5)
                self.assertEqual('stopped', self.executor.submit(self.app.stop, self.sid).result(.5)['session']['state'])
                with self.assertRaises(ValueError):
                    self.app.send(self.sid, 'must not bypass cancelled capture', [])
            finally:
                release.set()
            with self.assertRaises(BridgeError) as failure:
                sender.result(2)
        self.assertEqual('send_preparation_cancelled', failure.exception.code)
        run.assert_not_called()
        self.assertEqual([], self.item['messages'])
        self.assertNotIn('_observationPending', self.item)
        self.assertEqual(0, self.app._active_observations)

    def test_cancelled_queue_preparation_retains_request_without_delivery(self):
        row = self.enqueue(self.sid, 'preserve queue request')[0]
        entered, release, slow = self.gate(snapshot)
        with patch('local_app.server.snapshot', side_effect=slow), patch('local_app.demo.run') as run:
            sender = self.executor.submit(self.app.dispatch.pump)
            self.assertTrue(entered.wait(2))
            try:
                self.executor.submit(self.app.stop, self.sid).result(.5)
            finally:
                release.set()
            sender.result(2)
        run.assert_not_called()
        saved = self.app.dispatch.snapshot(self.sid)['queue']
        self.assertEqual([row['id']], [entry['id'] for entry in saved])
        self.assertEqual('needs_review', saved[0]['status'])
        self.assertEqual([], self.item['messages'])

    def test_finish_reservation_blocks_same_task_send_restart_and_archive(self):
        self.arm_finish()
        entered, release, slow = self.gate(snapshot)
        with patch('local_app.server.snapshot', side_effect=slow):
            writer = self.executor.submit(self.app.emit, self.sid, 'result', {})
            self.assertTrue(entered.wait(2))
            try:
                for function in (lambda: self.app.send(self.sid, 'too early', []),
                                 lambda: self.app.hide_session(self.sid, True)):
                    with self.assertRaises(ValueError):
                        self.executor.submit(function).result(.5)
                # Restart's native-only guard is intentionally reached first in
                # demo, but the run observation reservation also blocks it.
                self.app.demo = False
                with self.assertRaises(BridgeError) as failure:
                    self.executor.submit(self.app.restart_connection, self.sid, stop_running=True).result(.5)
                self.assertEqual('restart_transition_busy', failure.exception.code)
            finally:
                release.set()
            writer.result(2)
        self.assertEqual('done', self.item['state'])

    def test_retired_bridge_or_run_cannot_publish_late_observation(self):
        for replacement in ('bridge', 'run'):
            with self.subTest(replacement=replacement):
                self.arm_finish()
                bridge = SimpleNamespace(closed=False, cleanup_complete=True, close=lambda: True)
                self.item['bridge'] = bridge
                self.item['artifacts'] = []
                self.item['events'] = []
                entered, release, slow = self.gate(snapshot)
                with patch('local_app.server.snapshot', side_effect=slow):
                    writer = self.executor.submit(self.app._emit_bridge, self.sid, bridge, 'result', {})
                    self.assertTrue(entered.wait(2))
                    with self.app.lock:
                        if replacement == 'bridge':
                            self.item['bridge'] = None
                        else:
                            self.item['lastRunId'] = 'new-run'
                        self.item['state'] = 'running'
                    release.set()
                    writer.result(2)
                self.assertEqual([], self.item['artifacts'])
                self.assertEqual([], self.item['events'])
                self.assertEqual('running', self.item['state'])
                self.assertNotIn('_observationPending', self.item)

    def test_duplicate_terminal_captures_once_without_duplicate_artifacts(self):
        self.arm_finish()
        entered, release, slow = self.gate(self.app.file_diffs.finish)
        with patch.object(self.app.file_diffs, 'finish', side_effect=slow) as capture:
            first = self.executor.submit(self.app.emit, self.sid, 'result', {})
            self.assertTrue(entered.wait(2))
            second = self.executor.submit(self.app.emit, self.sid, 'result', {})
            try:
                with self.assertRaises(TimeoutError):
                    second.result(.1)
            finally:
                release.set()
            first.result(2)
            second.result(2)
        self.assertEqual(1, capture.call_count)
        self.assertEqual(1, len(self.item['artifacts']))

    def test_shutdown_drains_admitted_observation_without_holding_app_lock(self):
        self.arm_finish()
        entered, release, slow = self.gate(snapshot)
        with patch('local_app.server.snapshot', side_effect=slow):
            writer = self.executor.submit(self.app.emit, self.sid, 'result', {})
            self.assertTrue(entered.wait(2))
            closing = self.executor.submit(self.app.close)
            try:
                with self.assertRaises(TimeoutError):
                    closing.result(.1)
                self.assertEqual('running', self.executor.submit(self.app.public, self.item).result(.5)['state'])
            finally:
                release.set()
            writer.result(2)
            self.assertTrue(closing.result(2))
        self.assertEqual('done', self.item['state'])
        self.assertEqual(0, self.app._active_observations)

    def test_capture_failure_is_reported_as_limited_without_blocking_completion(self):
        self.arm_finish()
        with patch('local_app.server.snapshot', side_effect=OSError('offline volume')), \
                patch.object(self.app.file_diffs, 'finish', side_effect=OSError('offline cache')):
            self.app.emit(self.sid, 'result', {})
        self.assertEqual('done', self.item['state'])
        self.assertTrue(self.item['artifactObservation']['limited'])
        self.assertGreater(self.item['artifactObservation']['errors'], 0)
        self.assertEqual([], self.item['artifacts'])
        self.assertNotIn('_observationPending', self.item)

    def test_unsubmitted_native_interrupt_runs_capture_outside_queue_action_lock(self):
        self.arm_finish()
        bridge = ClaudeSession(['not-started'], {}, self.work,
            lambda kind, data: self.app._emit_bridge(self.sid, bridge, kind, data))
        bridge.process = Mock()
        bridge.process.poll.return_value = None
        bridge.process.stdin = io.BytesIO()
        bridge.session_id = str(uuid.uuid4())
        bridge.ready.set()
        bridge.busy = True
        bridge._turn_submitted = False
        bridge._turn_activity_seen = False
        bridge._tool_activity_run_id = 'run-a'
        self.item.update(bridge=bridge, sessionId=bridge.session_id)
        self.app.demo = False
        selected = self.enqueue(self.sid, 'after cancelled sender')[0]
        self.other_item['state'] = 'running'
        other = self.enqueue(self.other_sid, 'cancel elsewhere')[0]
        entered, release, slow = self.gate(snapshot)
        try:
            with patch('local_app.server.snapshot', side_effect=slow):
                applied = self.executor.submit(self.app.dispatch.action, self.sid,
                    {'action': 'apply_now', 'requestId': selected['id'], 'editRevision': selected['editRevision'],
                     'clientRequestId': 'cancel-unsubmitted'}).result(.5)
                self.assertEqual(selected['id'], applied['applyingId'])
                self.assertTrue(entered.wait(2))
                self.assertEqual([], self.executor.submit(self.app.dispatch.action, self.other_sid,
                    {'action': 'cancel', 'requestId': other['id']}).result(.5)['queue'])
                self.assertEqual('stopping', self.item['state'])
                self.assertEqual(b'', bridge.process.stdin.getvalue())
                done = self.item['_observationPending']['done']
                release.set()
                # Wait for the terminal snapshot itself, not only bridge state:
                # the bridge can confirm stop before file capture is published.
                self.assertTrue(done.wait(2))
            self.assertEqual('stopped', self.item['state'])
            self.assertEqual('stopped', bridge.stop_state)
            stopped = [row for row in self.item['events']
                       if row['type'] == 'status' and row['data'].get('state') == 'stopped'][-1]
            self.assertEqual('stopped', stopped['data']['stopState'])
            self.assertFalse(bridge.closed)
            self.assertEqual(1, len(self.item['artifacts']))
        finally:
            release.set()
            self.item['bridge'] = None  # The mock process owns no OS resources.

    def test_no_bridge_stop_reserves_admission_until_its_event_commits(self):
        entered, release, slow = self.gate(self.app.emit)
        with patch.object(self.app, 'emit', side_effect=slow):
            stopper = self.executor.submit(self.app.stop, self.sid)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(BridgeError) as caught:
                    self.executor.submit(self.app.send, self.sid, 'new request', []).result(.5)
                self.assertEqual('stop_in_progress', caught.exception.code)
            finally:
                release.set()
            stopper.result(2)
        self.assertNotIn('_stopAdmission', self.item)
        with patch('local_app.demo.run'):
            self.app.send(self.sid, 'new request', [])
        self.assertEqual('starting', self.item['state'])
        self.assertEqual(['new request'], [row['text'] for row in self.item['messages']])

    def test_late_send_error_does_not_fail_a_new_run(self):
        sent = []
        def send(prompt):
            if not sent:
                sent.append(prompt)
                raise OSError('fixture send failure')
            sent.append(prompt)
        bridge = SimpleNamespace(closed=False, cleanup_complete=True, close=lambda: True,
            _workspaceAutoDefaultChecked=True, send=send)
        bridge.interrupt = lambda: self.app._emit_bridge(self.sid, bridge, 'status', {'state': 'stopped'})
        self.item['bridge'] = bridge
        self.app.demo = False
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.app.emit
        def emit(sid, kind, data, **kwargs):
            if kind == 'error' and data.get('code') == 'send_failed':
                entered.set()
                self.assertTrue(release.wait(5))
            return original(sid, kind, data, **kwargs)
        with patch.object(self.app, 'emit', side_effect=emit):
            first = self.executor.submit(self.app.send, self.sid, 'failed first', [])
            self.assertTrue(entered.wait(2))
            try:
                self.app.stop(self.sid)
                self.app.send(self.sid, 'explicit second', [])
                run_id = self.item['lastRunId']
            finally:
                release.set()
            with self.assertRaises(OSError):
                first.result(2)
        self.assertEqual('starting', self.item['state'])
        self.assertEqual(run_id, self.item['lastRunId'])
        self.assertEqual(['failed first', 'explicit second'], sent)
        self.assertFalse(any(event['type'] == 'error' for event in self.item['events']))


if __name__ == '__main__':
    unittest.main()
