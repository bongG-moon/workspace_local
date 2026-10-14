"""A soft three-connection target must never interrupt work or replay requests."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app.bridge import probe_cli
from local_app.idle_connections import MAX_CONNECTIONS, RETAINED_CONNECTIONS, ConnectionCapacityUnavailable
from local_app.server import LocalApp
from test_workspace_idle_connections import MemoryBridge


class ConnectionRetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-retention-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.app = LocalApp(self.root / 'state', command=['fixture-only'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.rows = []
        for index in range(MAX_CONNECTIONS):
            row = self.app.get(self.app.create(str(self.root), True)['id'])
            bridge = MemoryBridge(emit=lambda kind, data, sid=row['id']: self.app.emit(sid, kind, data))
            row.update(bridge=bridge, state='done', updated=index + 1, sessionId=bridge.session_id,
                       connection=bridge.connection_state())
            self.rows.append(row)
        self.dispatch = self.app.dispatch

    def maintain(self):
        self.dispatch.request_retention()
        for _ in range(MAX_CONNECTIONS + 1):
            self.dispatch.maintain_connections()

    def live(self):
        return [row for row in self.rows if not row['bridge'].closed]

    def test_five_working_connections_remain_running(self):
        for row in self.rows:
            row['state'] = 'running'
            row['bridge'].busy = True
        self.maintain()
        self.assertEqual(5, MAX_CONNECTIONS)
        self.assertEqual(3, RETAINED_CONNECTIONS)
        self.assertEqual(self.rows, self.live())
        self.assertTrue(all(row['bridge'].close_count == 0 for row in self.rows))

    def test_three_working_and_two_done_keep_only_the_three_working(self):
        for row in self.rows[:3]:
            row['state'] = 'running'
            row['bridge'].busy = True
        self.maintain()
        self.assertEqual(self.rows[:3], self.live())
        self.assertTrue(all(row['state'] == 'running' for row in self.rows[:3]))
        self.assertTrue(all(row['state'] == 'done' for row in self.rows[3:]))

    def test_five_done_keep_three_recent_connections_and_persisted_work(self):
        old = self.rows[0]
        old['messages'] = [{'role': 'user', 'text': 'keep this conversation'}]
        original_id = old['sessionId']
        self.maintain()
        self.assertEqual(self.rows[2:], self.live())
        self.assertEqual(original_id, old['sessionId'])
        self.assertEqual('keep this conversation', old['messages'][0]['text'])
        self.assertEqual('done', old['state'])
        self.assertFalse(self.dispatch.queue.snapshot(old['id'])['paused'])
        self.assertTrue(self.app.public(old)['connection']['idleReleased'])
        self.assertEqual([1, 1, 0, 0, 0], [row['bridge'].close_count for row in self.rows])

    def test_viewed_done_connection_is_preferred_over_other_idle_connections(self):
        self.app._viewed_session = self.rows[0]['id']
        self.maintain()
        self.assertEqual([self.rows[0], *self.rows[3:]], self.live())

    def test_one_worker_pass_closes_at_most_one_child(self):
        self.dispatch.request_retention()
        self.dispatch.maintain_connections()
        self.assertEqual(4, len(self.live()))
        self.dispatch.maintain_connections()
        self.assertEqual(3, len(self.live()))
        self.dispatch.maintain_connections()
        self.assertFalse(self.dispatch._retention_pending)

    def test_approval_choice_child_and_queue_work_are_preserved(self):
        self.rows[0]['state'] = 'approval'
        self.rows[0]['requests'] = {'approval': {'tool': 'Write'}}
        self.rows[1]['choice'] = {'id': 'question'}
        self.rows[2]['bridge'].tasks = {'background-child'}
        self.dispatch.queue.enqueue(self.rows[3]['id'], 'next request',
                                    context=self.dispatch.context(self.rows[3]))
        self.maintain()
        self.assertEqual(self.rows[:4], self.live())
        self.assertEqual('queued', self.dispatch.queue.snapshot(self.rows[3]['id'])['queue'][0]['status'])
        self.assertEqual({'background-child'}, self.rows[2]['bridge'].tasks)

    def test_never_submitted_connections_are_not_closed_without_durable_session(self):
        for row in self.rows:
            row['state'] = 'idle'
        self.maintain()
        self.assertEqual(5, len(self.live()))

    def test_idle_ticks_do_not_scan_or_hydrate_history(self):
        for index in range(3000):
            sid = f'remembered-{index}'
            self.app.sessions[sid] = {'id': sid, 'state': 'idle', 'bridge': None,
                                      'workspace': str(self.root), 'trusted': False}
        with patch('local_app.app_dispatch.trim_idle_connection',
                   side_effect=AssertionError('no maintenance without a state change')), \
             patch.object(self.app, 'get', side_effect=AssertionError('idle tick must not hydrate history')):
            for _ in range(20):
                self.dispatch.maintain_connections()

    def test_followup_is_dispatched_before_retention_and_only_once(self):
        row = self.rows[0]
        bridge = row['bridge']
        self.dispatch.queue.enqueue(row['id'], 'continue immediately', context=self.dispatch.context(row))
        self.dispatch.request_retention()
        self.dispatch.pump()
        self.dispatch.maintain_connections()
        self.dispatch.pump()
        self.dispatch.maintain_connections()
        self.assertEqual(['continue immediately'], bridge.sent)
        self.assertFalse(bridge.closed)
        self.assertEqual([], self.dispatch.queue.snapshot(row['id'])['queue'])

    def test_future_schedule_reconnects_same_session_and_restores_controls_once(self):
        row = self.rows[0]
        original = row['bridge']
        original_id = original.session_id
        controls = {'model': 'chosen', 'effort': 'high', 'permissionMode': 'auto'}
        row['_sessionControls'] = dict(controls)
        original.values.update(controls)
        queue = self.dispatch.queue
        now = queue.clock()
        queue.add_schedule(row['id'], 'scheduled continuation', [], kind='once', run_at=now + 60,
                           context=self.dispatch.context(row))
        self.maintain()
        self.assertTrue(original.closed)
        self.assertFalse(queue.snapshot(row['id'])['paused'])
        queue.clock = lambda: now + 61
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            self.dispatch.pump()
            self.dispatch.pump()
        resumed = row['bridge']
        self.assertEqual(1, factory.call_count)
        self.assertEqual(original_id, resumed.session_id)
        self.assertTrue(resumed.options['require_resume_identity'])
        self.assertEqual(['prepare', ('model', 'chosen'), ('effort', 'high'), ('permissionMode', 'auto'),
                          ('send', 'scheduled continuation')], resumed.calls)
        self.assertEqual([], queue.snapshot(row['id'])['queue'])
        self.assertEqual('done', queue.snapshot(row['id'])['schedules'][0]['lastStatus'])

    def test_cleanup_failure_is_not_retried_or_allowed_to_pause_dispatch(self):
        row = self.rows[0]
        old = row['bridge']
        for other in self.rows[1:]:
            other['state'] = 'running'
        with patch.object(old, 'close', side_effect=OSError('fixture cleanup failed')) as close:
            self.maintain()
            self.maintain()
        close.assert_called_once()
        self.assertIsNone(self.dispatch.error)
        self.assertFalse(self.dispatch.queue.snapshot(row['id'])['paused'])
        self.assertTrue(row['_idleReleaseFailed'])
        self.assertNotIn('_idleReleasing', row)
        self.assertFalse(row['_connecting'])

    def test_closing_does_not_hold_app_lock_and_rejects_same_child_send(self):
        row = self.rows[0]
        old = row['bridge']
        entered, release = threading.Event(), threading.Event()
        errors = []
        original_close = old.close

        def blocked_close():
            entered.set()
            if not release.wait(4):
                raise AssertionError('test did not release close')
            return original_close()

        def maintain():
            try:
                self.dispatch.maintain_connections()
            except Exception as error:
                errors.append(error)

        self.dispatch.request_retention()
        with patch.object(old, 'close', side_effect=blocked_close):
            worker = threading.Thread(target=maintain)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.app.lock.acquire(timeout=.5))
                self.app.lock.release()
                with self.assertRaises(ConnectionCapacityUnavailable):
                    self.app.send(row['id'], 'race with cleanup', [])
                self.assertEqual([], old.sent)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, old.close_count)

    def test_result_arriving_during_an_unsuccessful_pass_is_not_lost(self):
        entered, release = threading.Event(), threading.Event()

        def no_candidate(app):
            entered.set()
            if not release.wait(4):
                raise AssertionError('test did not release maintenance')
            return False

        self.dispatch.request_retention()
        with patch('local_app.app_dispatch.trim_idle_connection', side_effect=no_candidate):
            worker = threading.Thread(target=self.dispatch.maintain_connections)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.dispatch.observe(self.rows[0]['id'], 'result', {'sessionId': self.rows[0]['sessionId']})
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.dispatch._retention_pending)

    def test_shutdown_drains_an_admitted_cleanup_before_finishing(self):
        old = self.rows[0]['bridge']
        entered, release = threading.Event(), threading.Event()
        stopping, shutdown_done = threading.Event(), threading.Event()
        errors, result = [], []
        original_close = old.close
        original_stop = self.dispatch.stop

        def blocked_close():
            entered.set()
            if not release.wait(4):
                raise AssertionError('test did not release cleanup')
            return original_close()

        def stop_dispatch():
            original_stop()
            stopping.set()

        def maintain():
            try:
                self.dispatch.maintain_connections()
            except Exception as error:
                errors.append(error)

        def shutdown():
            try:
                result.append(self.app.close())
            except Exception as error:
                errors.append(error)
            finally:
                shutdown_done.set()

        self.dispatch.request_retention()
        with patch.object(old, 'close', side_effect=blocked_close), \
             patch.object(self.dispatch, 'stop', side_effect=stop_dispatch):
            worker = threading.Thread(target=maintain)
            closer = threading.Thread(target=shutdown)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(1, self.app._active_operations)
                closer.start()
                self.assertTrue(stopping.wait(2))
                self.assertFalse(shutdown_done.wait(.05))
                self.assertEqual(0, old.close_count)
            finally:
                release.set()
                worker.join(5)
                if closer.ident:
                    closer.join(5)
        self.assertEqual([], errors)
        self.assertEqual([True], result)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(0, self.app._active_operations)
        self.assertEqual('closed', self.app._shutdown_state)
        self.assertTrue(all(row['bridge'].cleanup_complete for row in self.rows))


class ConnectionRetentionProcessTests(unittest.TestCase):
    def test_worker_reaps_old_fixture_pids_and_resume_sends_once_with_original_controls(self):
        fixture = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'
        with tempfile.TemporaryDirectory(prefix='retention-process-fixture-') as temporary:
            root = Path(temporary).resolve()
            command = [sys.executable, '-B', str(fixture.resolve())]
            app = LocalApp(root / 'state', command=command, info=probe_cli(command),
                           managed_workspace_root=root / 'managed')
            processes = []

            def wait_for(predicate, message):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    with app.lock:
                        if predicate():
                            return
                    time.sleep(.02)
                self.fail(message)

            try:
                rows = [app.get(app.create(str(root), True)['id']) for _ in range(MAX_CONNECTIONS)]
                for index, row in enumerate(rows):
                    app.send(row['id'], f'initial fixture request {index}', [])
                    wait_for(lambda: row['state'] == 'done', 'fixture request did not complete')
                    processes.append(row['bridge'].process)
                first = rows[0]
                original_id = first['sessionId']
                originals = [row['bridge'] for row in rows]
                app.set_model(first['id'], 'alternate-model')
                app.set_effort(first['id'], 'high')
                app.set_permission_mode(first['id'], 'auto')
                with app.lock:
                    for index, row in enumerate(rows):
                        row['updated'] = index + 1
                self.assertEqual(5, sum(process.poll() is None for process in processes))
                app.dispatch.start()
                wait_for(lambda: sum(not row['bridge'].closed for row in rows) == RETAINED_CONNECTIONS
                         and all(bridge.cleanup_complete for bridge in originals[:2]),
                         'existing dispatch worker did not automatically retain three connections')
                self.assertTrue(all(process.poll() is not None for process in processes[:2]))
                self.assertTrue(all(process.poll() is None for process in processes[2:]))
                self.assertTrue(all(row['state'] == 'done' for row in rows))
                self.assertFalse(app.dispatch.queue.snapshot(first['id'])['paused'])

                app.send(first['id'], 'only this resumed request', [])
                wait_for(lambda: first['state'] == 'done', 'resumed fixture request did not complete')
                processes.append(first['bridge'].process)
                self.assertEqual(original_id, first['sessionId'])
                self.assertIsNot(originals[0], first['bridge'])
                connection = app.public(first)['connection']
                self.assertEqual(('alternate-model', 'high', 'auto'),
                                 tuple(connection[key] for key in ('model', 'effort', 'permissionMode')))
                wire = [json.loads(line) for line in (root / 'restart-wire.jsonl').read_text(encoding='utf-8').splitlines()]
                user_frames = [frame for frame in wire if frame['type'] == 'user']
                self.assertEqual(6, len(user_frames))
                self.assertEqual(1, sum(frame['message']['content'] == 'only this resumed request' for frame in user_frames))
                self.assertEqual(1, sum(frame['type'] == 'startup' and frame['pid'] == processes[-1].pid
                                       and frame['sessionId'] == original_id for frame in wire))
            finally:
                self.assertTrue(app.close())
                if app.dispatch.thread:
                    app.dispatch.thread.join(2)
            self.assertTrue(all(process.poll() is not None for process in processes))
            self.assertFalse(app.dispatch.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
