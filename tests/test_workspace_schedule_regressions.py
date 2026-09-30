"""Schedule boundary regressions using temporary stores and a recording CLI bridge."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from local_app.work_queue import WorkQueue
from tests import test_workspace_dispatch_routes as dispatch_fixtures
from tests import test_workspace_productivity_frontend as frontend_fixtures
from tests import test_workspace_work_queue as queue_fixtures


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


class KnownScheduleCapacityDefect(unittest.TestCase):
    """Confirmed in 0.20.1; expected failure is not a passing regression."""
    setUp = dispatch_fixtures.DispatchRoutesTests.setUp
    shutdown = dispatch_fixtures.DispatchRoutesTests.shutdown

    @unittest.expectedFailure
    def test_known_defect_capacity_block_waits_without_uncertain_delivery(self):
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


@unittest.skipUnless(frontend_fixtures.NODE, 'Node.js is required for schedule UI defect checks')
class KnownScheduleFrontendDefects(unittest.TestCase):
    """Assert intended recovery behavior, retaining two confirmed 0.20.1 defects."""
    run_case = frontend_fixtures.WorkspaceProductivityFrontendTests.run_case

    @unittest.expectedFailure
    def test_known_defect_edit_completed_once_rearms_future_execution(self):
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

    @unittest.expectedFailure
    def test_known_defect_restart_resume_opens_folder_trust_confirmation(self):
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


if __name__ == '__main__':
    unittest.main()
