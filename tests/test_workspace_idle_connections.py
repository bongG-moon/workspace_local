"""Idle slot reclamation is bounded, preserves work, and never replays a turn."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from local_app.bridge import probe_cli
from local_app.idle_connections import ConnectionCapacityUnavailable, idle_candidate
from local_app.server import LocalApp


class MemoryBridge:
    def __init__(self, command=None, info=None, root=None, emit=None, resume=None, **options):
        self.emit = emit or (lambda *_: None)
        self.session_id = resume or str(uuid.uuid4())
        self.closed = self.cleanup_complete = self.busy = self.stopping = False
        self._initialized = True
        self.pending = {}; self.tasks = set(); self.capabilities = {}
        self.original_model = 'baseline'; self.original_permission_mode = 'manual'
        self._effort_baselines = {'baseline': 'medium'}
        self.ready = threading.Event(); self.ready.set()
        self.values = {'model': 'baseline', 'effort': 'medium', 'permissionMode': 'manual'}
        self.options = options; self.calls = []; self.sent = []; self.close_count = 0

    def model_state(self):
        return dict(self.values)

    def connection_state(self):
        return {**self.model_state(), 'sessionId': self.session_id, 'capabilities': {}}

    def prepare(self):
        self.calls.append('prepare')

    def set_model(self, value):
        self.calls.append(('model', value)); self.values['model'] = value or self.original_model

    def set_effort(self, value):
        self.calls.append(('effort', value)); self.values['effort'] = value or 'medium'

    def set_permission_mode(self, value):
        self.calls.append(('permissionMode', value)); self.values['permissionMode'] = value or self.original_permission_mode

    def send(self, text):
        self.calls.append(('send', text)); self.sent.append(text)
        self.emit('result', {'sessionId': self.session_id, 'verification': {'state': 'unverified'}})

    def close(self):
        self.close_count += 1
        self.closed = self.cleanup_complete = True
        return True


class IdleConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-idle-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.app = LocalApp(self.root / 'state', command=['fixture-only'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.rows = []
        for index in range(3):
            row = self.app.get(self.app.create(str(self.root), True)['id'])
            bridge = MemoryBridge()
            row.update(bridge=bridge, state='done', updated=index + 1, sessionId=bridge.session_id,
                       connection=bridge.connection_state())
            self.rows.append(row)
        self.target = self.app.get(self.app.create(str(self.root), True)['id'])

    def test_fourth_send_releases_oldest_without_pausing_queue_or_losing_conversation(self):
        old = self.rows[0]; original = old['bridge']; original_id = old['sessionId']
        old['messages'] = [{'role': 'user', 'text': 'past question'}]
        old['_sessionControls'] = {'model': 'chosen', 'effort': 'high', 'permissionMode': 'auto'}
        original.values.update(old['_sessionControls'])
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            self.app.send(self.target['id'], 'fourth request', [])
            self.assertTrue(original.cleanup_complete)
            self.assertEqual(1, original.close_count)
            self.assertEqual('done', old['state'])
            self.assertEqual('past question', old['messages'][0]['text'])
            self.assertFalse(self.app.dispatch.queue.snapshot(old['id'])['paused'])
            self.assertTrue(self.app.public(old)['connection']['idleReleased'])
            self.app.send(old['id'], 'continue original', [])
        bridge = old['bridge']
        self.assertEqual(original_id, bridge.session_id)
        self.assertTrue(bridge.options['require_resume_identity'])
        self.assertEqual(['continue original'], bridge.sent)
        self.assertEqual(['prepare', ('model', 'chosen'), ('effort', 'high'), ('permissionMode', 'auto'),
                          ('send', 'continue original')], bridge.calls)
        self.assertEqual(2, factory.call_count)

    def test_due_schedule_reclaims_and_runs_once_on_same_tick(self):
        queue = self.app.dispatch.queue; now = queue.clock()
        queue.add_schedule(self.target['id'], 'scheduled fourth', [], kind='once', run_at=now + 60,
                           context=self.app.dispatch.context(self.target))
        queue.clock = lambda: now + 61
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            self.app.dispatch.pump(); self.app.dispatch.pump()
        self.assertEqual(1, factory.call_count)
        self.assertEqual(['scheduled fourth'], self.target['bridge'].sent)
        self.assertFalse(queue.snapshot(self.target['id'])['paused'])
        self.assertEqual([], queue.snapshot(self.target['id'])['queue'])
        self.assertEqual('done', queue.snapshot(self.target['id'])['schedules'][0]['lastStatus'])

    def test_busy_approval_controls_children_and_queued_work_are_not_reclaimed(self):
        candidate = self.rows[0]; bridge = candidate['bridge']
        self.rows[1]['state'] = self.rows[2]['state'] = 'running'
        for owner, key, value in ((candidate, 'state', 'approval'), (candidate, 'choice', {'id': 'q'}),
                (candidate, 'requests', {'r': {}}), (candidate, '_restarting', True),
                (candidate, '_permissionUpdating', True), (candidate, '_dispatchClaim', 'claim'),
                (candidate, '_stopAdmission', True), (bridge, 'busy', True), (bridge, 'stopping', True),
                (bridge, 'tasks', {'child'}), (bridge, '_control_waiters', {'ack': {}}),
                (bridge, 'pending', {'approval': {}}), (bridge, '_control_active', True)):
            with self.subTest(key=key):
                before = owner.get(key) if isinstance(owner, dict) else getattr(owner, key, None)
                if isinstance(owner, dict): owner[key] = value
                else: setattr(owner, key, value)
                with self.app.lock: self.assertIsNone(idle_candidate(self.app, self.target))
                if isinstance(owner, dict): owner[key] = before
                else: setattr(owner, key, before)
        self.app.dispatch.queue.enqueue(candidate['id'], 'queued next', context=self.app.dispatch.context(candidate))
        with self.app.lock: self.assertIsNone(idle_candidate(self.app, self.target))
        with patch('local_app.server.ClaudeSession') as factory:
            with self.assertRaises(ConnectionCapacityUnavailable): self.app.connect(self.target['id'])
        factory.assert_not_called(); self.assertFalse(bridge.closed)

    def test_unconfirmed_cleanup_preserves_capacity_and_schedule_request(self):
        old = self.rows[0]['bridge']
        def uncertain_close():
            old.closed = True
            return False
        queue = self.app.dispatch.queue
        queue.enqueue(self.target['id'], 'wait safely', context=self.app.dispatch.context(self.target))
        with patch.object(old, 'close', side_effect=uncertain_close), patch('local_app.server.ClaudeSession') as factory:
            self.app.dispatch.pump()
            self.assertFalse(self.app.connection_capacity_available(self.target))
        factory.assert_not_called()
        self.assertEqual([], self.target['messages'])
        self.assertEqual('queued', queue.snapshot(self.target['id'])['queue'][0]['status'])
        self.assertFalse(queue.snapshot(self.target['id'])['paused'])
        self.assertNotIn('_capacityReservation', self.target)
        self.assertNotIn('_idleReleasing', self.rows[0])
        old.cleanup_complete = True

    def test_close_wait_releases_global_lock_and_reserves_only_one_new_connection(self):
        old = self.rows[0]['bridge']; entered = threading.Event(); release = threading.Event(); errors = []
        self.rows[1]['state'] = self.rows[2]['state'] = 'running'
        original_close = old.close
        def blocked_close():
            entered.set()
            if not release.wait(3): raise AssertionError('test did not release close')
            return original_close()
        def connect():
            try: self.app.connect(self.target['id'])
            except Exception as exc: errors.append(exc)
        with patch.object(old, 'close', side_effect=blocked_close), patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            worker = threading.Thread(target=connect); worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.app.lock.acquire(timeout=.5)); self.app.lock.release()
                with self.assertRaises(ConnectionCapacityUnavailable): self.app.connect(self.target['id'])
                competitor = self.app.create(str(self.root), True)['id']
                with self.assertRaises(ConnectionCapacityUnavailable): self.app.connect(competitor)
                factory.assert_not_called()
            finally:
                release.set(); worker.join(5)
        self.assertFalse(worker.is_alive()); self.assertEqual([], errors)
        self.assertEqual(1, factory.call_count)

    def test_preflight_rejects_invalid_request_without_retiring_another_connection(self):
        with self.assertRaises(ValueError): self.app.send(self.target['id'], '', [])
        self.target['trusted'] = False
        with self.assertRaises(ValueError): self.app.connect(self.target['id'])
        self.assertTrue(all(not row['bridge'].closed for row in self.rows))

    def test_parallel_replacements_keep_reserved_slots_while_other_cleanup_is_pending(self):
        targets = [self.target, self.app.get(self.app.create(str(self.root), True)['id'])]
        entered = [threading.Event(), threading.Event()]; release = [threading.Event(), threading.Event()]
        completed = [threading.Event(), threading.Event()]; errors = []
        original = [row['bridge'].close for row in self.rows[:2]]
        def delayed(index):
            entered[index].set()
            if not release[index].wait(4): raise AssertionError('parallel cleanup test timed out')
            return original[index]()
        def connect(index):
            try: self.app.connect(targets[index]['id'])
            except Exception as error: errors.append(error)
            finally: completed[index].set()
        with patch.object(self.rows[0]['bridge'], 'close', side_effect=lambda: delayed(0)), \
             patch.object(self.rows[1]['bridge'], 'close', side_effect=lambda: delayed(1)), \
             patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            workers = [threading.Thread(target=connect, args=(index,)) for index in range(2)]
            workers[0].start()
            try:
                self.assertTrue(entered[0].wait(2)); workers[1].start()
                self.assertTrue(entered[1].wait(2)); release[0].set()
                self.assertTrue(completed[0].wait(2))
                self.assertEqual([], errors)
                self.assertEqual(1, factory.call_count)
            finally:
                for signal in release: signal.set()
                for worker in workers:
                    if worker.ident: worker.join(5)
        self.assertEqual([], errors); self.assertEqual(2, factory.call_count)
        self.assertEqual(3, sum(row.get('bridge') is not None and not row['bridge'].closed for row in self.app.sessions.values()))

    def test_stop_during_slot_preparation_prevents_the_pending_send(self):
        old = self.rows[0]['bridge']; entered = threading.Event(); release = threading.Event(); errors = []
        original_close = old.close
        def delayed_close():
            entered.set(); release.wait(3)
            return original_close()
        def send():
            try: self.app.send(self.target['id'], 'cancelled before send', [])
            except ValueError as error: errors.append(error)
        with patch.object(old, 'close', side_effect=delayed_close), patch('local_app.server.ClaudeSession') as factory:
            worker = threading.Thread(target=send); worker.start()
            try:
                self.assertTrue(entered.wait(2)); self.app.stop(self.target['id'])
            finally:
                release.set(); worker.join(4)
        factory.assert_not_called()
        self.assertEqual([], self.target['messages']); self.assertEqual(1, len(errors))
        self.assertNotIn('_capacityReservation', self.target)

    def test_accept_current_control_discards_obsolete_idle_restore_value(self):
        item = self.rows[0]; bridge = item['bridge']
        item['_idleRestoreControls'] = {'permissionMode': 'stale-mode'}
        item['_controlRestoreIssues'] = {'permissionMode': {'code': 'permission_mode_rejected'}}
        item['_pendingControlRestore'] = {'permissionMode'}
        with patch.object(bridge, 'accept_current_control', create=True):
            self.app.accept_current_control(item['id'], 'permissionMode', 'use_current')
        self.assertNotIn('permissionMode', item['_idleRestoreControls'])

    def test_unknown_task_uses_normal_validation_error(self):
        with self.assertRaises(ValueError): self.app.connect(str(uuid.uuid4()))

    def test_active_fork_resumes_the_exact_child_but_pending_fork_is_not_reclaimed(self):
        item = self.rows[0]
        item['branch'] = {'status': 'active'}; item['_requireResumeIdentity'] = True
        with patch('local_app.conversation_fork.branch_connection', return_value={'resume': item['sessionId']}), \
             patch('local_app.server.runtime_context', return_value={'configRoot': str(self.root)}):
            self.assertEqual({'resume': item['sessionId'], 'require_resume_identity': True}, self.app._resume_options(item))
        self.rows[1]['state'] = self.rows[2]['state'] = 'running'
        item['branch']['status'] = 'pending'
        with self.app.lock: self.assertIsNone(idle_candidate(self.app, self.target))

    def test_never_submitted_initialize_id_is_not_treated_as_durable_resume(self):
        for item in self.rows: item['state'] = 'idle'
        with self.app.lock: self.assertIsNone(idle_candidate(self.app, self.target))

    def test_viewed_connection_is_kept_when_another_idle_connection_exists(self):
        self.app._viewed_session = self.rows[0]['id']
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge): self.app.connect(self.target['id'])
        self.assertFalse(self.rows[0]['bridge'].closed)
        self.assertTrue(self.rows[1]['bridge'].cleanup_complete)


class IdleResumeFixtureTests(unittest.TestCase):
    def connect_completed(self, app, rows):
        for row in rows[:3]:
            app.connect(row['id']); app.send(row['id'], 'initial fixture request', [])
            deadline = time.monotonic() + 5
            while row['state'] != 'done' and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual('done', row['state'])

    def test_wrong_resume_identity_never_receives_the_new_user_request(self):
        fixture = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'
        with tempfile.TemporaryDirectory(prefix='idle-identity-fixture-') as temporary:
            root = Path(temporary).resolve(); command = [sys.executable, '-B', str(fixture.resolve())]
            app = LocalApp(root / 'state', command=command, info=probe_cli(command), managed_workspace_root=root / 'managed')
            try:
                rows = [app.get(app.create(str(root), True)['id']) for _ in range(4)]
                self.connect_completed(app, rows); app.connect(rows[3]['id'])
                first = rows[0]; original_id = first['sessionId']
                messages = list(first['messages'])
                (root / 'restart-config.json').write_text(json.dumps({'wrongIdentity': True}), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '세션 ID'):
                    app.send(first['id'], 'must never be sent', [])
                self.assertEqual(original_id, first['sessionId'])
                self.assertEqual(messages, first['messages'])
                wire = [json.loads(line) for line in (root / 'restart-wire.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(3, sum(frame['type'] == 'user' for frame in wire))
            finally:
                self.assertTrue(app.close())

    def test_owned_fixture_pid_is_reaped_and_controls_and_identity_are_restored(self):
        fixture = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'
        with tempfile.TemporaryDirectory(prefix='idle-owned-fixture-') as temporary:
            root = Path(temporary).resolve(); command = [sys.executable, '-B', str(fixture.resolve())]
            app = LocalApp(root / 'state', command=command, info=probe_cli(command), managed_workspace_root=root / 'managed')
            try:
                rows = [app.get(app.create(str(root), True)['id']) for _ in range(4)]
                self.connect_completed(app, rows)
                first = rows[0]; original = first['bridge']; session = first['sessionId']
                app.set_model(first['id'], 'alternate-model'); app.set_effort(first['id'], 'high')
                app.set_permission_mode(first['id'], 'auto')
                first['updated'] = 0
                app.connect(rows[3]['id'])
                self.assertTrue(original.cleanup_complete)
                self.assertIsNotNone(original.process.poll())
                self.assertFalse(app.dispatch.queue.snapshot(first['id'])['paused'])
                app.send(first['id'], 'only this new request', [])
                deadline = time.monotonic() + 5
                while first['state'] != 'done' and time.monotonic() < deadline: time.sleep(.01)
                self.assertEqual('done', first['state']); self.assertEqual(session, first['sessionId'])
                connection = app.public(first)['connection']
                self.assertEqual(('alternate-model', 'high', 'auto'), tuple(connection[key] for key in ('model', 'effort', 'permissionMode')))
                wire = [json.loads(line) for line in (root / 'restart-wire.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(4, sum(frame['type'] == 'user' for frame in wire))
                self.assertLessEqual(sum(not row['bridge'].closed for row in rows), 3)
            finally:
                self.assertTrue(app.close())

    def test_idle_restore_of_inherited_permission_keeps_schedule_context(self):
        fixture = Path(__file__).parent / 'fixtures/workspace_restart_cli.py'
        with tempfile.TemporaryDirectory(prefix='idle-context-fixture-') as temporary:
            root = Path(temporary).resolve(); command = [sys.executable, '-B', str(fixture.resolve())]
            app = LocalApp(root / 'state', command=command, info=probe_cli(command), managed_workspace_root=root / 'managed')
            try:
                rows = [app.get(app.create(str(root), True)['id']) for _ in range(4)]
                self.connect_completed(app, rows); app.connect(rows[3]['id'])
                first = rows[0]; expected = app.dispatch.context(first)
                self.assertTrue(first['_idleReleased'])
                queue = app.dispatch.queue; now = queue.clock()
                queue.add_schedule(first['id'], 'scheduled resume', [], kind='once', run_at=now + 60, context=expected)
                queue.clock = lambda: now + 61
                app.dispatch.pump()
                deadline = time.monotonic() + 5
                while first['state'] != 'done' and time.monotonic() < deadline: time.sleep(.01)
                self.assertEqual('done', first['state'])
                self.assertEqual(expected, app.dispatch.context(first))
                self.assertFalse(queue.snapshot(first['id'])['paused'])
            finally:
                self.assertTrue(app.close())


if __name__ == '__main__':
    unittest.main()
