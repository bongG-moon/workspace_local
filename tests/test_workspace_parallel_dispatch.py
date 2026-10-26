"""Preparation in one task must not serialize unrelated scheduled/follow-up work."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app.idle_connections import MAX_CONNECTIONS
from local_app.server import LocalApp
from test_workspace_idle_connections import MemoryBridge


class ParallelDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.dispatch = self.app.dispatch

    def task(self, name):
        workspace = self.root / name
        workspace.mkdir()
        sid = self.app.create(str(workspace), True)['id']
        row = self.dispatch.action(sid, {'action': 'enqueue', 'text': name,
                                        'attachments': []})['queue'][0]
        return sid, workspace, row

    def wait(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('concurrent dispatch did not reach expected state')

    def drain(self):
        self.wait(lambda: not self.dispatch._dispatching_sessions)

    def test_production_scheduler_starts_second_task_during_first_scan(self):
        first, workspace, _ = self.task('first')
        second, _, _ = self.task('second')
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        original = self.app._capture_observation
        def capture(path, *args, **kwargs):
            if Path(path) == workspace:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('scan gate not released')
            return original(path, *args, **kwargs)
        def run(app, sid, text):
            if sid == second:
                second_started.set()
        with patch.object(self.app, '_capture_observation', side_effect=capture), \
                patch('local_app.demo.run', side_effect=run):
            try:
                self.dispatch.start()
                self.assertTrue(entered.wait(2))
                self.assertTrue(second_started.wait(1), 'second task waited for first task scan')
                self.assertFalse(release.is_set())
                self.assertEqual([], self.app.get(first)['messages'])
                self.assertEqual('second', self.app.get(second)['messages'][0]['text'])
            finally:
                self.dispatch.stop()
                release.set()
                self.drain()
        self.assertEqual(1, len(self.app.get(first)['messages']))
        self.assertEqual(1, len(self.app.get(second)['messages']))

    def test_pending_preparations_are_bounded_and_same_task_is_never_submitted_twice(self):
        sessions = [self.task(f'task-{index}')[0] for index in range(MAX_CONNECTIONS + 1)]
        release = threading.Event()
        calls = []
        calls_lock = threading.Lock()
        def send(sid, *args, **kwargs):
            with calls_lock:
                calls.append(sid)
            if not release.wait(5):
                raise AssertionError('send gate not released')
        with patch.object(self.app, 'send', side_effect=send):
            try:
                self.dispatch.pump(background=True)
                self.wait(lambda: len(calls) == MAX_CONNECTIONS)
                for _ in range(20):
                    self.dispatch.pump(background=True)
                self.assertEqual(MAX_CONNECTIONS, len(self.dispatch._dispatching_sessions))
                self.assertEqual(MAX_CONNECTIONS, len(calls))
                waiting = set(sessions) - set(calls)
                self.assertEqual(1, len(waiting))
                waiting_sid = waiting.pop()
                self.assertEqual('queued', self.dispatch.snapshot(waiting_sid)['queue'][0]['status'])
            finally:
                release.set()
                self.drain()
            self.dispatch.pump(background=True)
            self.drain()
        self.assertCountEqual(sessions, calls)
        self.assertFalse(self.dispatch._dispatching_sessions)
        self.assertTrue(all(not self.app.get(sid).get('_dispatchClaim') and
                            not self.app.get(sid).get('_capacityReservation') for sid in sessions))

    def test_same_task_stop_cancels_preparation_without_sending_or_losing_queue(self):
        sid, _, row = self.task('stop-before-send')
        entered, release = threading.Event(), threading.Event()
        original = self.app._capture_observation
        def capture(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('scan gate not released')
            return original(*args, **kwargs)
        with patch.object(self.app, '_capture_observation', side_effect=capture), \
                patch('local_app.demo.run') as run:
            try:
                self.dispatch.pump(background=True)
                self.assertTrue(entered.wait(2))
                self.app.stop(sid)
            finally:
                release.set()
                self.drain()
        run.assert_not_called()
        self.assertEqual([], self.app.get(sid)['messages'])
        saved = self.dispatch.snapshot(sid)['queue']
        self.assertEqual([row['id']], [entry['id'] for entry in saved])
        self.assertEqual('needs_review', saved[0]['status'])
        self.assertNotIn('_dispatchClaim', self.app.get(sid))
        self.assertNotIn('_capacityReservation', self.app.get(sid))

    def test_fast_capacity_rejections_do_not_churn_workers_and_next_tick_rotates(self):
        sessions = [self.task(f'waiting-{index}')[0] for index in range(MAX_CONNECTIONS + 3)]
        # Model a worker finishing before start() returns: all connections may
        # be occupied, so repeated admissions can reject almost immediately.
        class ImmediateThread:
            def __init__(self, *, target, args, **kwargs):
                self.target, self.args = target, args
            def start(self):
                self.target(*self.args)
        with patch('local_app.app_dispatch.threading.Thread', ImmediateThread), \
                patch.object(self.dispatch, '_pump_session') as prepare:
            self.dispatch.pump(background=True)
            self.assertEqual(MAX_CONNECTIONS, prepare.call_count)
            self.assertEqual(sessions[:MAX_CONNECTIONS], [call.args[0] for call in prepare.call_args_list])
            prepare.reset_mock()
            self.dispatch.pump(background=True)
            self.assertEqual(MAX_CONNECTIONS, prepare.call_count)
            self.assertEqual(sessions[MAX_CONNECTIONS:], [call.args[0] for call in prepare.call_args_list[:3]])
        self.assertTrue(all(self.dispatch.snapshot(sid)['queue'][0]['status'] == 'queued' for sid in sessions))

    def test_obviously_blocked_tasks_do_not_create_preparation_workers(self):
        blockers = ({'trusted': False}, {'requests': {'approval': {}}}, {'choice': {'id': 'question'}},
                    {'verification': {'state': 'needs-review'}}, {'_controlRestore': {'reason': 'required'}})
        for index, state in enumerate(blockers):
            sid, _, _ = self.task(f'blocked-{index}')
            self.app.get(sid).update(state)
        with patch('local_app.app_dispatch.threading.Thread') as thread:
            self.dispatch.pump(background=True)
        thread.assert_not_called()
        self.assertFalse(self.dispatch._dispatching_sessions)

    def test_shutdown_drains_claimed_preparation_and_prevents_delivery(self):
        sid, _, row = self.task('shutdown-before-send')
        entered, release = threading.Event(), threading.Event()
        original = self.app._capture_observation
        def capture(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('scan gate not released')
            return original(*args, **kwargs)
        with patch.object(self.app, '_capture_observation', side_effect=capture), \
                patch('local_app.demo.run') as run, ThreadPoolExecutor(max_workers=1) as pool:
            try:
                self.dispatch.pump(background=True)
                self.assertTrue(entered.wait(2))
                closing = pool.submit(self.app.close)
                self.wait(self.dispatch.stopped.is_set)
                with self.assertRaises(TimeoutError):
                    closing.result(.05)
            finally:
                release.set()
                self.drain()
            self.assertTrue(closing.result(2))
        run.assert_not_called()
        self.assertEqual([], self.app.get(sid)['messages'])
        saved = self.dispatch.queue.snapshot(sid)['queue']
        self.assertEqual([row['id']], [entry['id'] for entry in saved])
        self.assertEqual('needs_review', saved[0]['status'])
        self.assertIsNone(self.dispatch.error)
        self.assertEqual(0, self.app._active_operations)
        self.assertNotIn('_dispatchClaim', self.app.get(sid))
        self.assertNotIn('_capacityReservation', self.app.get(sid))

    def test_unexpected_worker_failure_is_visible_and_uncertain_request_is_not_retried(self):
        sid, _, row = self.task('unexpected-failure')
        with patch.object(self.app, 'send', side_effect=AssertionError('controlled worker failure')) as send:
            self.dispatch.pump(background=True)
            self.drain()
            self.assertTrue(self.dispatch.error)
            self.dispatch.pump(background=True)
            send.assert_called_once()
        saved = self.dispatch.snapshot(sid)['queue']
        self.assertEqual([row['id']], [entry['id'] for entry in saved])
        self.assertEqual('needs_review', saved[0]['status'])
        self.assertNotIn('_dispatchClaim', self.app.get(sid))
        self.assertNotIn('_capacityReservation', self.app.get(sid))

    def test_stopped_dispatcher_admits_no_preparation(self):
        sid, _, _ = self.task('remain-queued')
        self.dispatch.stop()
        with patch.object(self.app, 'send') as send:
            self.dispatch.pump(background=True)
            self.dispatch._dispatch_worker(sid)
        send.assert_not_called()
        self.assertEqual('queued', self.dispatch.snapshot(sid)['queue'][0]['status'])
        self.assertFalse(self.dispatch._dispatching_sessions)

    def idle_connections(self):
        self.app.demo = False
        rows = []
        for index in range(4):
            workspace = self.root / f'idle-{index}'
            workspace.mkdir()
            row = self.app.get(self.app.create(str(workspace), True)['id'])
            bridge = MemoryBridge(emit=lambda kind, data, sid=row['id']: self.app.emit(sid, kind, data))
            row.update(bridge=bridge, state='done', updated=index + 1, sessionId=bridge.session_id,
                       connection=bridge.connection_state())
            rows.append(row)
        return rows

    def test_idle_cleanup_does_not_block_new_followup_on_another_connection(self):
        rows = self.idle_connections()
        entered, release = threading.Event(), threading.Event()
        original = rows[0]['bridge'].close
        def close():
            entered.set()
            if not release.wait(5):
                raise AssertionError('idle cleanup gate not released')
            return original()
        with patch.object(rows[0]['bridge'], 'close', side_effect=close) as cleanup:
            try:
                self.dispatch.request_retention()
                self.dispatch.start()
                self.assertTrue(entered.wait(2))
                self.dispatch.action(rows[-1]['id'], {'action': 'enqueue', 'text': 'during cleanup',
                                                      'attachments': []})
                self.wait(lambda: rows[-1]['bridge'].sent == ['during cleanup'], timeout=1.5)
                self.assertFalse(release.is_set())
                for _ in range(20):
                    self.dispatch.maintain_connections(background=True)
                cleanup.assert_called_once()
                self.assertTrue(self.dispatch._retention_running)
            finally:
                self.dispatch.stop()
                release.set()
                self.wait(lambda: not self.dispatch._retention_running)
                self.drain()
        self.assertEqual(0, self.app._active_operations)

    def test_shutdown_waits_for_admitted_retention_worker(self):
        rows = self.idle_connections()
        entered, release = threading.Event(), threading.Event()
        original = rows[0]['bridge'].close
        def close():
            entered.set()
            if not release.wait(5):
                raise AssertionError('idle cleanup gate not released')
            return original()
        with patch.object(rows[0]['bridge'], 'close', side_effect=close), ThreadPoolExecutor(max_workers=1) as pool:
            try:
                self.dispatch.request_retention()
                self.dispatch.maintain_connections(background=True)
                self.assertTrue(entered.wait(2))
                closing = pool.submit(self.app.close)
                self.wait(self.dispatch.stopped.is_set)
                with self.assertRaises(TimeoutError):
                    closing.result(.05)
            finally:
                release.set()
                self.wait(lambda: not self.dispatch._retention_running)
            self.assertTrue(closing.result(2))
        self.assertEqual(0, self.app._active_operations)
        self.assertTrue(all(row['bridge'].closed for row in rows))

    def test_rejected_maintenance_thread_keeps_pending_and_clears_its_reservation(self):
        self.dispatch.request_retention()
        with patch('local_app.app_dispatch.threading.Thread', side_effect=RuntimeError('thread limit')):
            with self.assertRaises(RuntimeError):
                self.dispatch.maintain_connections(background=True)
        self.assertTrue(self.dispatch._retention_pending)
        self.assertFalse(self.dispatch._retention_running)
        self.dispatch.maintain_connections()
        self.assertFalse(self.dispatch._retention_pending)
        self.assertFalse(self.dispatch._retention_running)


if __name__ == '__main__':
    unittest.main()
