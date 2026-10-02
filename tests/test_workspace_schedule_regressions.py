"""Schedule boundary regressions using temporary stores and a recording CLI bridge."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import threading
import unittest
from unittest.mock import patch

from local_app.work_queue import WorkQueue
from tests import test_workspace_dispatch_routes as dispatch_fixtures
from tests import test_workspace_productivity_frontend as frontend_fixtures
from tests import test_workspace_work_queue as queue_fixtures


class PreparedRecordingBridge(dispatch_fixtures.Bridge):
    stopping = False

    def __init__(self, app, sid, on_prepare=None):
        super().__init__(app, sid)
        self.on_prepare, self.frames = on_prepare, []
        self.controls = {}

    def prepare(self):
        self.frames.append(('prepare', None))
        if self.on_prepare:
            self.on_prepare()

    def connection_state(self):
        return {'sessionId': 'fixture-session', 'capabilities': {}, **self.model_state()}

    def model_state(self):
        return dict(self.controls)

    def set_model(self, value):
        self.frames.append(('model', value))
        self.controls['model'] = value
        return self.model_state()

    def set_effort(self, value):
        self.frames.append(('effort', value))
        self.controls['effort'] = value
        return self.model_state()

    def set_permission_mode(self, value):
        self.frames.append(('permissionMode', value))
        self.controls['permissionMode'] = value
        return self.model_state()

    def send(self, prompt):
        self.frames.append(('send', prompt))
        super().send(prompt)


class ScheduleDurabilityTests(unittest.TestCase):
    setUp = queue_fixtures.WorkQueueTests.setUp
    add = queue_fixtures.WorkQueueTests.add
    claim = queue_fixtures.WorkQueueTests.claim
    schedule = queue_fixtures.WorkQueueTests.schedule

    def finish(self, row, run_id):
        self.queue.dispatched(row['id'], run_id)
        self.queue.observe('task', 'result', {'lastRunId': run_id})

    def test_daily_runs_again_after_completion_with_new_occurrence_identity(self):
        schedule = self.schedule('daily')
        self.now += 60
        self.queue.tick()
        first = self.claim()
        self.finish(first, 'day-one')
        self.now = self.queue.snapshot('task')['schedules'][0]['nextRunAt']
        self.queue.tick()
        second = self.claim()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(schedule['id'], second['scheduleId'])
        self.assertEqual((8, 1), (datetime.fromtimestamp(second['dueAt']).hour,
                                 datetime.fromtimestamp(second['dueAt']).minute))
        self.finish(second, 'day-two')
        state = self.queue.snapshot('task')
        self.assertEqual([], state['queue'])
        self.assertTrue(state['schedules'][0]['enabled'])
        self.assertEqual('done', state['schedules'][0]['lastRun']['status'])

    def test_weekly_completion_crosses_weekend_and_keeps_selected_days(self):
        self.now = datetime(2026, 10, 2, 8, 0).timestamp()  # Friday.
        self.schedule('weekly', weekdays=[0, 4])
        self.now += 60
        self.queue.tick()
        self.finish(self.claim(), 'friday')
        monday = self.queue.snapshot('task')['schedules'][0]['nextRunAt']
        self.assertEqual(datetime(2026, 10, 5, 8, 1), datetime.fromtimestamp(monday))
        self.now = monday
        self.queue.tick()
        self.finish(self.claim(), 'monday')
        next_friday = self.queue.snapshot('task')['schedules'][0]['nextRunAt']
        self.assertEqual(datetime(2026, 10, 9, 8, 1), datetime.fromtimestamp(next_friday))

    def test_concurrent_schedule_registration_and_ticks_create_one_occurrence(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            created = list(pool.map(lambda _: self.schedule(client_id='same-registration'), range(12)))
        self.assertEqual(1, len({row['id'] for row in created}))
        self.now += 60
        with ThreadPoolExecutor(max_workers=6) as pool:
            ticks = list(pool.map(lambda _: self.queue.tick(), range(12)))
        state = self.queue.snapshot('task')
        self.assertEqual(1, sum(ticks))
        self.assertEqual(1, len(state['schedules']))
        self.assertEqual(1, len(state['queue']))

    def test_recurring_registration_retry_across_midnight_and_restart_is_idempotent(self):
        self.now = datetime(2026, 9, 28, 23, 58).timestamp()
        first_at = datetime(2026, 9, 28, 23, 59).timestamp()
        first = self.queue.add_schedule('task', 'nightly', [], kind='daily', run_at=first_at,
                                        time='23:59', context=self.context, client_id='nightly')
        self.now = datetime(2026, 9, 29, 0, 1).timestamp()
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        retry = restarted.add_schedule('task', 'nightly', [], kind='daily',
                                       run_at=datetime(2026, 9, 29, 23, 59).timestamp(),
                                       time='23:59', context=self.context, client_id='nightly')
        self.assertEqual(first['id'], retry['id'])
        self.assertEqual(1, len(restarted.snapshot('task')['schedules']))
        self.assertEqual([], restarted.snapshot('task')['queue'])
        self.assertEqual('restart', restarted.snapshot('task')['reason'])

    def test_failed_due_occurrence_write_blocks_delivery_and_restart_skips_it(self):
        self.schedule()
        before = (self.root / 'work-queue.json').read_bytes()
        self.now += 60
        with patch('local_app.work_queue.HistoryStore._write', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.queue.tick()
        self.assertEqual(before, (self.root / 'work-queue.json').read_bytes())
        with self.assertRaises(ValueError):
            self.claim()
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        restarted.resume('task')
        restarted.tick()
        state = restarted.snapshot('task')
        self.assertEqual([], state['queue'])
        self.assertFalse(state['schedules'][0]['enabled'])
        self.assertEqual('missed', state['schedules'][0]['lastRun']['status'])

    def test_failed_completion_write_keeps_delivered_schedule_for_manual_review(self):
        self.schedule('daily')
        self.now += 60
        self.queue.tick()
        row = self.claim()
        self.queue.dispatched(row['id'], 'sent-run')
        with patch('local_app.work_queue.HistoryStore._write', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.queue.observe('task', 'result', {'lastRunId': 'sent-run'})
        stored = json.loads((self.root / 'work-queue.json').read_text('utf-8'))
        self.assertEqual('submitted', stored['queue'][0]['status'])
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        restarted.resume('task')
        self.assertIsNone(restarted.claim('task', self.ready, self.context))
        self.assertEqual('needs_review', restarted.snapshot('task')['queue'][0]['state'])

    def test_two_schedules_at_same_time_wait_for_each_others_completion(self):
        first, second = self.schedule(), self.schedule()
        self.now += 60
        self.queue.tick()
        row = self.claim()
        self.assertEqual(first['id'], row['scheduleId'])
        self.assertIsNone(self.claim())
        self.finish(row, 'first-run')
        next_row = self.claim()
        self.assertEqual(second['id'], next_row['scheduleId'])
        self.finish(next_row, 'second-run')
        self.assertEqual([], self.queue.snapshot('task')['queue'])

    def test_completed_once_edit_rearms_one_new_occurrence(self):
        schedule = self.schedule()
        self.now += 60
        self.queue.tick()
        first = self.claim()
        self.finish(first, 'original-run')
        completed = self.queue.snapshot('task')['schedules'][0]
        self.assertFalse(completed['enabled'])
        self.assertFalse(completed['pausedByUser'])
        edited = self.queue.update_schedule('task', schedule['id'], 'rearmed', kind='once',
                                            run_at=self.now + 60, context=self.context)
        self.assertTrue(edited['enabled'])
        self.now += 60
        self.queue.tick()
        self.queue.tick()
        second = self.claim()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual('rearmed', second['text'])
        self.finish(second, 'edited-run')
        self.assertIsNone(self.claim())

    def test_explicit_pause_survives_late_result_restart_and_edit(self):
        schedule = self.schedule()
        self.now += 60
        self.queue.tick()
        row = self.claim()
        self.queue.dispatched(row['id'], 'late-result')
        self.queue.set_schedule_enabled('task', schedule['id'], False)
        self.queue.observe('task', 'result', {'lastRunId': 'late-result'})
        self.queue = WorkQueue(self.root, clock=lambda: self.now)
        restored = self.queue.snapshot('task')['schedules'][0]
        self.assertEqual('done', restored['lastRun']['status'])
        self.assertTrue(restored['pausedByUser'])
        edited = self.queue.update_schedule('task', schedule['id'], 'keep paused', kind='once',
                                            run_at=self.now + 60, context=self.context)
        self.assertFalse(edited['enabled'])
        self.assertTrue(edited['pausedByUser'])
        self.queue.resume('task')
        self.queue.set_schedule_enabled('task', schedule['id'], True)
        self.now += 60
        self.queue.tick()
        self.assertEqual('keep paused', self.claim()['text'])

    def test_legacy_consumed_once_and_explicit_pause_migrate_separately(self):
        completed = self.schedule()
        self.now += 60
        self.queue.tick()
        self.finish(self.claim(), 'legacy-run')
        paused_once = self.schedule()
        paused_daily = self.schedule('daily')
        for schedule in (paused_once, paused_daily):
            self.queue.set_schedule_enabled('task', schedule['id'], False)
        store = self.root / 'work-queue.json'
        old = json.loads(store.read_text('utf-8'))
        for schedule in old['schedules']:
            schedule.pop('pausedByUser')
        store.write_text(json.dumps(old), encoding='utf-8')
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        flags = {row['id']: row['pausedByUser'] for row in restarted.snapshot('task')['schedules']}
        self.assertEqual({completed['id']: False, paused_once['id']: True, paused_daily['id']: True}, flags)

    def test_invalid_persisted_pause_intent_fails_closed_without_overwrite(self):
        self.schedule()
        store = self.root / 'work-queue.json'
        data = json.loads(store.read_text('utf-8'))
        data['schedules'][0]['pausedByUser'] = 'false'
        store.write_text(json.dumps(data), encoding='utf-8')
        original = store.read_bytes()
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNotNone(restarted.warning)
        self.assertEqual(original, store.read_bytes())

    def test_rearming_uncertain_delivery_does_not_replay_or_clear_review(self):
        schedule = self.schedule()
        self.now += 60
        self.queue.tick()
        row = self.claim()
        self.queue.failed(row['id'])
        self.queue.update_schedule('task', schedule['id'], 'future request', kind='once',
                                   run_at=self.now + 60, context=self.context)
        self.queue.resume('task')
        self.now += 60
        self.queue.tick()
        self.assertIsNone(self.claim())
        state = self.queue.snapshot('task')
        self.assertEqual([(row['id'], 'needs_review')], [(r['id'], r['state']) for r in state['queue']])
        self.assertEqual('previous_pending', state['schedules'][0]['lastRun']['status'])


class ScheduleDispatchTests(unittest.TestCase):
    setUp = dispatch_fixtures.DispatchRoutesTests.setUp
    shutdown = dispatch_fixtures.DispatchRoutesTests.shutdown
    request = dispatch_fixtures.DispatchRoutesTests.request

    def register(self, text='scheduled', **schedule):
        code, value = self.request({'action': 'schedule', 'text': text, 'attachments': [],
                                   'clientRequestId': text, 'schedule': schedule})
        self.assertEqual(200, code, value)
        return next(row for row in value['schedules'] if row['text'] == text)

    def test_busy_schedule_waits_past_grace_then_dispatches_once_after_result(self):
        now = self.app.dispatch.queue.clock()
        row = self.register(kind='once', runAt=now + 60)
        self.app.get(self.sid)['state'] = 'running'
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual('queued', self.app.dispatch.snapshot(self.sid)['queue'][0]['state'])
        self.app.dispatch.queue.clock = lambda: now + 3600
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        self.app.dispatch.pump()
        self.app.dispatch.pump()
        self.assertEqual(['scheduled'], self.bridge.sent)
        schedule = self.app.dispatch.snapshot(self.sid)['schedules'][0]
        self.assertEqual(row['id'], schedule['id'])
        self.assertEqual('done', schedule['lastRun']['status'])

    def test_schedule_waits_for_choice_even_when_session_state_is_done(self):
        now = self.app.dispatch.queue.clock()
        self.register(kind='once', runAt=now + 60)
        item = self.app.get(self.sid)
        item.update(state='done', choice={'id': 'fixture-choice'})
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.app.emit(self.sid, 'choice_closed', {'id': 'fixture-choice'})
        self.app.dispatch.pump()
        self.assertEqual(['scheduled'], self.bridge.sent)

    def test_daily_and_weekly_http_rules_execute_at_computed_local_time(self):
        weekday = datetime.now().weekday()
        daily = self.register('daily prompt', kind='daily', time='08:01')
        weekly = self.register('weekly prompt', kind='weekly', time='08:01', weekdays=[weekday])
        for row in (daily, weekly):
            local = datetime.fromtimestamp(row['nextRunAt'])
            self.assertEqual((8, 1), (local.hour, local.minute))
        self.assertEqual(weekday, datetime.fromtimestamp(weekly['nextRunAt']).weekday())
        first_due = min(daily['nextRunAt'], weekly['nextRunAt'])
        self.app.dispatch.queue.clock = lambda: first_due
        self.app.dispatch.pump()
        self.app.dispatch.pump()
        expected = [row['text'] for row in (daily, weekly) if row['nextRunAt'] == first_due]
        self.assertEqual(expected, self.bridge.sent)

    def test_schedule_http_pause_edit_resume_delete_withdraws_waiting_prompt(self):
        now = self.app.dispatch.queue.clock()
        schedule = self.register(kind='once', runAt=now + 60)
        self.app.get(self.sid)['state'] = 'running'
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.pump()
        code, paused = self.request({'action': 'schedule_pause', 'requestId': schedule['id']})
        self.assertEqual(200, code, paused)
        self.assertEqual([], paused['queue'])
        code, edited = self.request({'action': 'schedule_update', 'requestId': schedule['id'],
                                     'text': 'revised schedule', 'attachments': [],
                                     'schedule': {'kind': 'once', 'runAt': now + 120, 'enabled': False}})
        self.assertEqual(200, code, edited)
        self.assertFalse(edited['schedules'][0]['enabled'])
        code, resumed = self.request({'action': 'schedule_resume', 'requestId': schedule['id']})
        self.assertEqual(200, code, resumed)
        self.assertTrue(resumed['schedules'][0]['enabled'])
        code, deleted = self.request({'action': 'schedule_cancel', 'requestId': schedule['id']})
        self.assertEqual(200, code, deleted)
        self.app.dispatch.queue.clock = lambda: now + 121
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual([], self.app.dispatch.snapshot(self.sid)['schedules'])

    def test_schedule_claim_disk_failure_never_reaches_cli_bridge(self):
        now = self.app.dispatch.queue.clock()
        self.register(kind='once', runAt=now + 60)
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.queue.tick()
        with patch('local_app.work_queue.HistoryStore._write', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertIsNotNone(self.app.dispatch.snapshot(self.sid)['warning'])


class ScheduleCapacityRegressionTests(unittest.TestCase):
    """Connection admission waits safely, then uses one reserved slot."""
    setUp = dispatch_fixtures.DispatchRoutesTests.setUp
    shutdown = dispatch_fixtures.DispatchRoutesTests.shutdown

    def test_capacity_block_waits_without_uncertain_delivery(self):
        # Capacity rejection happens before CLI construction and before a user
        # message is recorded, so this request should remain safely queued.
        occupied = [self.sid]
        for _ in range(2):
            sid = self.app.create(str(self.work), True)['id']
            self.app.get(sid)['bridge'] = dispatch_fixtures.Bridge(self.app, sid)
            occupied.append(sid)
        waiting = self.app.create(str(self.work), True)['id']
        now = self.app.dispatch.queue.clock()
        self.app.dispatch.action(waiting, {
            'action': 'schedule', 'text': 'wait for available connection', 'attachments': [],
            'schedule': {'kind': 'once', 'runAt': now + 60},
        })
        self.app.dispatch.queue.clock = lambda: now + 61
        with patch('local_app.server.ClaudeSession') as factory:
            self.app.dispatch.pump()
        factory.assert_not_called()
        self.assertEqual([], self.app.get(waiting)['messages'])
        blocked = self.app.dispatch.snapshot(waiting)
        self.app.get(occupied[0])['bridge'].close()
        recording = dispatch_fixtures.Bridge(self.app, waiting)
        with patch('local_app.server.ClaudeSession', return_value=recording):
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        self.assertEqual(
            ('queued', ['wait for available connection']),
            (blocked['queue'][0]['state'], recording.sent),
            'A known zero-send capacity rejection must wait and run once when a slot becomes free.',
        )

    def test_capacity_counts_each_other_bridge_or_reservation_once(self):
        second = self.app.get(self.app.create(str(self.work), True)['id'])
        second['_dispatchClaim'] = 'second-reservation'
        candidate = self.app.get(self.app.create(str(self.work), True)['id'])
        candidate['_dispatchClaim'] = 'own-reservation'
        with self.app.lock:
            self.assertTrue(self.app.connection_capacity_available(candidate))
            second['bridge'] = dispatch_fixtures.Bridge(self.app, second['id'])
            self.assertTrue(self.app.connection_capacity_available(candidate))
            third = self.app.get(self.app.create(str(self.work), True)['id'])
            third['_dispatchClaim'] = 'third-reservation'
            self.assertFalse(self.app.connection_capacity_available(candidate))
            # A task already connected needs no additional connection slot.
            self.assertTrue(self.app.connection_capacity_available(self.app.get(self.sid)))
            self.app.demo = True
            self.assertTrue(self.app.connection_capacity_available(candidate))

    def test_competing_connect_and_send_cannot_take_dispatch_reservation(self):
        occupied = self.app.create(str(self.work), True)['id']
        self.app.get(occupied)['bridge'] = dispatch_fixtures.Bridge(self.app, occupied)
        waiting = self.app.create(str(self.work), True)['id']
        competitor = self.app.create(str(self.work), True)['id']
        self.app.dispatch.action(waiting, {'action': 'enqueue', 'text': 'reserved request', 'attachments': []})
        original_send = self.app.send
        recording = dispatch_fixtures.Bridge(self.app, waiting)
        claims = []

        def send_with_competition(sid, text, attachments, **kwargs):
            claim = self.app.get(sid).get('_dispatchClaim')
            claims.append(claim)
            self.assertEqual(kwargs['_dispatch_claim'], claim)
            with self.assertRaisesRegex(ValueError, '3개'):
                self.app.connect(competitor)
            with self.assertRaisesRegex(ValueError, '3개'):
                original_send(competitor, 'competing request', [])
            return original_send(sid, text, attachments, **kwargs)

        with patch.object(self.app, 'send', side_effect=send_with_competition), \
                patch('local_app.server.ClaudeSession', return_value=recording) as factory:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        factory.assert_called_once()
        self.assertEqual(1, len(claims))
        self.assertEqual(['reserved request'], recording.sent)
        self.assertEqual([], self.app.get(competitor)['messages'])
        self.assertIsNone(self.app.get(waiting).get('_dispatchClaim'))
        self.assertEqual([], self.app.dispatch.snapshot(waiting)['queue'])

    def test_ambiguous_send_failure_releases_slot_but_never_retries(self):
        waiting = self.app.create(str(self.work), True)['id']
        self.app.dispatch.action(waiting, {'action': 'enqueue', 'text': 'ambiguous request', 'attachments': []})
        with patch.object(self.app, 'send', side_effect=OSError('delivery may have started')) as send:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        send.assert_called_once()
        self.assertIsNone(self.app.get(waiting).get('_dispatchClaim'))
        state = self.app.dispatch.snapshot(waiting)
        self.assertEqual('needs_review', state['queue'][0]['state'])
        self.assertEqual('delivery_unknown', state['reason'])

    def test_same_task_external_connect_cannot_interrupt_reserved_dispatch(self):
        waiting = self.app.create(str(self.work), True)['id']
        self.app.dispatch.action(waiting, {'action': 'enqueue', 'text': 'reserved request', 'attachments': []})
        original_send = self.app.send
        attempt_ready, release_prepare = threading.Event(), threading.Event()
        connect_errors = []

        def hold_prepare():
            attempt_ready.set()
            if not release_prepare.wait(3):
                raise AssertionError('Competing connection did not release its prepare wait')

        recording = PreparedRecordingBridge(self.app, waiting, hold_prepare)

        def external_connect():
            try:
                self.app.connect(waiting)
            except ValueError as error:
                connect_errors.append(str(error))
            finally:
                attempt_ready.set()

        def send_with_same_task_connect(sid, text, attachments, **kwargs):
            self.assertEqual(kwargs['_dispatch_claim'], self.app.get(sid).get('_dispatchClaim'))
            with ThreadPoolExecutor(max_workers=1) as pool:
                connection = pool.submit(external_connect)
                try:
                    self.assertTrue(attempt_ready.wait(3))
                    return original_send(sid, text, attachments, **kwargs)
                finally:
                    release_prepare.set()
                    connection.result(timeout=3)

        with patch.object(self.app, 'send', side_effect=send_with_same_task_connect), \
                patch('local_app.server.ClaudeSession', return_value=recording) as factory:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        self.assertEqual([], self.app.dispatch.snapshot(waiting)['queue'])
        self.assertEqual(['reserved request'], recording.sent)
        self.assertEqual([('send', 'reserved request')], recording.frames)
        self.assertEqual(1, len(connect_errors))
        self.assertIn('대기 요청', connect_errors[0])
        factory.assert_called_once()
        self.assertIsNone(self.app.get(waiting).get('_dispatchClaim'))

    def test_reserved_dispatch_restores_own_connection_controls_before_send(self):
        item = self.app.get(self.sid)
        self.bridge.close()
        item['_sessionControls'] = {'model': 'chosen-model', 'effort': 'high', 'permissionMode': 'plan'}
        self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': 'restored request', 'attachments': []})
        recording = PreparedRecordingBridge(self.app, self.sid)
        with patch.object(self.app, 'connect', wraps=self.app.connect) as connect, \
                patch('local_app.server.ClaudeSession', return_value=recording):
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        claim = item['messages'][0]['requestId']
        connect.assert_called_once_with(self.sid, _dispatch_claim=claim)
        self.assertEqual([('prepare', None), ('model', 'chosen-model'), ('effort', 'high'),
                          ('permissionMode', 'plan'), ('send', 'restored request')], recording.frames)
        self.assertEqual(item['_sessionControls'], recording.model_state())
        self.assertEqual([], self.app.dispatch.snapshot(self.sid)['queue'])
        self.assertFalse(item.get('_needsControlRestore'))
        self.assertFalse(item.get('_connecting'))
        self.assertIsNone(item.get('_dispatchClaim'))


@unittest.skipUnless(frontend_fixtures.NODE, 'Node.js is required for schedule UI defect checks')
class ScheduleFrontendRegressionTests(unittest.TestCase):
    """Completed scheduling and explicit trust return preserve user intent."""
    run_case = frontend_fixtures.WorkspaceProductivityFrontendTests.run_case

    def test_edit_completed_once_rearms_future_execution(self):
        self.run_case(r"""(async()=>{
          active.state='done';
          const completed={id:'completed-once',kind:'once',text:'original prompt',attachments:[],
            enabled:false,runAt:Date.now()/1000-3600,nextRunAt:null,lastRun:{status:'done'}};
          WorkspaceWorkflow.openEditor('schedule',completed);
          $('schedule-at').value='2099-01-01T09:00';
          let call;api=async(path,body)=>{call=body;return {queue:[],schedules:[],revision:1};};
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(call.action,'schedule_update');assert.equal(call.requestId,'completed-once');
          assert.ok(call.schedule.runAt>Date.now()/1000);
          assert.equal(call.schedule.enabled,true,
            'Editing a completed one-shot to a future time must rearm its execution.');
        })()""", ('workflow',))

    def test_restart_resume_opens_folder_trust_confirmation(self):
        self.run_case(r"""(async()=>{
          active.state='idle';active.trusted=false;
          const state={revision:1,paused:true,reason:'restart',queue:[],schedules:[
            {id:'restart-daily',kind:'daily',time:'09:00',text:'daily prompt',enabled:true,
             nextRunAt:Date.now()/1000+3600}],steer:{supported:false}};
          api=async()=>state;await WorkspaceWorkflow.refresh();
          let confirmations=0,resumeCalls=0;
          chooseFolder=async()=>{confirmations++;};
          api=async(path,body)=>{if(body?.action==='resume')resumeCalls++;
            throw Error('업무 폴더를 다시 확인한 뒤 대기·예약을 실행해 주세요.');};
          await $('workflow-resume').onclick();await settle();
          assert.deepEqual([confirmations,resumeCalls],[1,0],
            'Restart resume must obtain folder trust before submitting the guarded resume action.');
        })()""", ('workflow',))

    def test_explicitly_paused_completed_once_edit_preserves_pause(self):
        self.run_case(r"""(async()=>{
          active.state='done';
          const paused={id:'paused-once',kind:'once',text:'keep paused',attachments:[],
            enabled:false,pausedByUser:true,runAt:Date.now()/1000-3600,nextRunAt:null,lastRun:{status:'done'}};
          api=async()=>({queue:[],schedules:[paused],revision:1});await WorkspaceWorkflow.refresh();
          assert.match(flatText($('schedule-list')),/일시 정지/);
          WorkspaceWorkflow.openEditor('schedule',paused);$('schedule-at').value='2099-01-01T09:00';
          let call;api=async(path,body)=>{call=body;return {queue:[],schedules:[],revision:2};};
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(call.schedule.enabled,false);
        })()""", ('workflow',))

    def test_trust_confirmation_resumes_once_without_submitting_composer(self):
        self.run_case(r"""(async()=>{
          active.state='idle';active.trusted=false;$('prompt').value='unsent draft';attachments=['C:/kept.csv'];
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {queue:[],schedules:[],revision:1};};
          await WorkspaceWorkflow.requestResume();
          const confirmed={id:active.id,generation:selectionGeneration,folderGeneration:folderChoiceGeneration};
          assert.equal($('folder-form').dataset.afterTrust,'workflow');assert.equal(calls.length,0);
          $('trust').checked=true;
          await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          await WorkspaceWorkflow.resumeAfterTrust(confirmed);
          assert.deepEqual(calls.map(call=>[call.path,call.body.action]),[['/api/trust',undefined],['/api/dispatch','resume']]);
          assert.equal(calls[1].body.id,'A');assert.equal(active.trusted,true);
          assert.equal($('prompt').value,'unsent draft');assert.equal(attachments[0],'C:/kept.csv');
        })()""", ('workflow',))

    def test_individual_resume_retains_schedule_id_through_trust_confirmation(self):
        self.run_case(r"""(async()=>{
          active.state='idle';active.trusted=false;
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {queue:[],schedules:[],revision:1};};
          await WorkspaceWorkflow.requestResume({action:'schedule_resume',requestId:'paused-schedule'});
          $('trust').checked=true;
          await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          assert.equal(calls.length,2);assert.equal(calls[1].body.action,'schedule_resume');
          assert.equal(calls[1].body.requestId,'paused-schedule');
        })()""", ('workflow',))

    def test_cancelled_confirmation_cannot_resume_later(self):
        self.run_case(r"""(async()=>{
          active.state='idle';const calls=[];api=async(path,body)=>{calls.push({path,body});return {};};
          for(const cancel of [()=>emit($('folder-dialog'),'cancel'),()=>emit($('folder-form'),'submit',{submitter:{value:'cancel'}})]){
            active.trusted=false;await WorkspaceWorkflow.requestResume();
            const confirmed={id:active.id,generation:selectionGeneration,folderGeneration:folderChoiceGeneration};
            cancel();active.trusted=true;await WorkspaceWorkflow.resumeAfterTrust(confirmed);
          }
          assert.equal(calls.length,0);
        })()""", ('workflow',))

    def test_selection_change_during_trust_does_not_resume_another_task(self):
        self.run_case(r"""(async()=>{
          active.state='idle';active.trusted=false;let resolveTrust;const calls=[];
          api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{resolveTrust=resolve;});};
          await WorkspaceWorkflow.requestResume();$('trust').checked=true;
          const submission=$('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          selectionGeneration++;WorkspaceWorkflow.contextChanged();
          active={id:'B',workspace:'C:/fixture/B',state:'idle',trusted:false};
          $('prompt').value='task B draft';attachments=['C:/B.csv'];resolveTrust({});await submission;
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/trust');
          assert.equal(active.trusted,false);assert.equal($('prompt').value,'task B draft');
          assert.equal(attachments[0],'C:/B.csv');
        })()""", ('workflow',))

    def test_old_trust_response_cannot_consume_new_confirmation(self):
        self.run_case(r"""(async()=>{
          active.state='idle';active.trusted=false;let resolveTrust;const calls=[];
          api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>{resolveTrust=resolve;});};
          await WorkspaceWorkflow.requestResume();$('trust').checked=true;
          const submission=$('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          emit($('folder-dialog'),'cancel');$('folder-dialog').close();
          await WorkspaceWorkflow.requestResume({action:'schedule_resume',requestId:'new-intent'});
          resolveTrust({});await submission;
          assert.equal(calls.length,1);assert.equal(active.trusted,false);assert.equal($('folder-dialog').open,true);
          api=async(path,body)=>{calls.push({path,body});return {queue:[],schedules:[],revision:1};};
          $('trust').checked=true;await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          assert.equal(calls.length,3);assert.equal(calls[2].body.action,'schedule_resume');
          assert.equal(calls[2].body.requestId,'new-intent');
        })()""", ('workflow',))


if __name__ == '__main__':
    unittest.main()
