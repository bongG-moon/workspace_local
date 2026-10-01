from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.work_queue import WorkQueue


class WorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 28, 8, 0).timestamp()  # Monday, local time.
        self.queue = WorkQueue(self.root, clock=lambda: self.now)
        self.context = {'workspace': str(self.root), 'model': 'current-model',
                        'effort': 'high', 'permissionMode': 'default'}
        self.ready = {'state': 'done', 'trusted': True, 'requests': {}, 'choice': None}

    def add(self, text='follow up', sid='task', **kwargs):
        return self.queue.enqueue(sid, text, [], self.context, **kwargs)

    def claim(self, sid='task', state=None):
        return self.queue.claim(sid, self.ready if state is None else state, self.context)

    def schedule(self, kind='once', **kwargs):
        return self.queue.add_schedule('task', 'scheduled prompt', [], kind=kind,
                                       run_at=self.now + 60, context=self.context, **kwargs)

    def test_claim_is_durable_before_send_and_completion_releases_next(self):
        first, second = self.add('first'), self.add('second')
        claim = self.claim()
        self.assertEqual(first['id'], claim['id'])
        stored = json.loads((self.root / 'work-queue.json').read_text('utf-8'))
        self.assertEqual('dispatching', stored['queue'][0]['status'])
        self.assertIsNone(self.claim())
        self.queue.dispatched(claim['id'], 'run1')
        self.queue.observe('task', 'result', {'lastRunId': 'run1'})
        self.assertEqual(second['id'], self.claim()['id'])

    def test_approval_question_and_choice_wait_without_forcing_manual_resume(self):
        item = self.add()
        for state in ('running', 'starting', 'approval', 'question', 'error', 'stopped'):
            self.assertIsNone(self.claim(state={**self.ready, 'state': state}))
        self.assertIsNone(self.claim(state={**self.ready, 'requests': {'approval': {}}}))
        self.assertIsNone(self.claim(state={**self.ready, 'choice': {'id': 'business-question'}}))
        self.queue.observe('task', 'request', {'id': 'request'})
        self.queue.observe('task', 'request_closed', {'id': 'request'})
        self.queue.observe('task', 'result', {'lastRunId': 'previous'})
        self.assertEqual(item['id'], self.claim()['id'])

    def test_stop_and_error_hold_even_if_session_later_reports_idle(self):
        self.add()
        self.queue.observe('task', 'error', {'message': 'failure'})
        self.assertIsNone(self.claim())
        self.queue.resume('task')
        self.queue.observe('task', 'status', {'state': 'stopped'})
        self.assertIsNone(self.claim())
        self.queue.resume('task')
        self.assertIsNotNone(self.claim())

    def test_hook_verification_issue_does_not_start_follow_up(self):
        self.add()
        self.queue.observe('task', 'result', {'verification': {'state': 'needs-review'}})
        self.assertIsNone(self.claim())

    def test_result_arriving_during_send_is_matched_after_send_returns(self):
        first, second = self.add('first'), self.add('second')
        self.claim()
        self.queue.observe('task', 'result', {'lastRunId': 'quick-run'})
        self.queue.dispatched(first['id'], 'quick-run')
        self.assertEqual(second['id'], self.claim()['id'])

    def test_unrelated_result_cannot_complete_queued_delivery(self):
        first = self.add()
        self.claim()
        self.queue.observe('task', 'result', {'lastRunId': 'old-run'})
        self.queue.dispatched(first['id'], 'new-run')
        self.assertEqual('submitted', self.queue.snapshot('task')['queue'][0]['state'])
        self.queue.observe('task', 'result', {'lastRunId': 'old-run'})
        self.assertIsNone(self.claim())

    def test_restart_holds_queue_and_never_retries_uncertain_delivery(self):
        first, second = self.add('first'), self.add('second')
        self.claim()
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        snapshot = restarted.snapshot('task')
        self.assertEqual('restart', snapshot['reason'])
        self.assertEqual('needs_review', snapshot['queue'][0]['state'])
        restarted.resume('task')
        self.assertIsNone(restarted.claim('task', self.ready, self.context))
        restarted.cancel('task', first['id'])
        self.assertEqual(second['id'], restarted.claim('task', self.ready, self.context)['id'])

    def test_failed_send_is_not_automatically_retried(self):
        item = self.add()
        self.claim()
        self.queue.failed(item['id'])
        self.queue.resume('task')
        self.assertIsNone(self.claim())
        self.assertEqual('needs_review', self.queue.snapshot('task')['queue'][0]['state'])

    def test_restore_preflight_preserves_unsent_request_until_explicit_resume(self):
        first = self.add()
        self.claim()
        self.queue.defer_unsubmitted(first['id'])
        snapshot = self.queue.snapshot('task')
        self.assertEqual('control_restore_required', snapshot['reason'])
        self.assertEqual('queued', snapshot['queue'][0]['state'])
        self.assertEqual(first['text'], snapshot['queue'][0]['text'])
        self.assertIsNone(snapshot['queue'][0]['runId'])
        self.assertIsNone(self.claim())
        self.queue.resume('task')
        claimed = self.claim()
        self.assertEqual(first['id'], claimed['id'])
        self.assertIsNone(claimed['reason'])

    def test_restore_preflight_cannot_requeue_submitted_or_uncertain_work(self):
        first = self.add()
        self.claim()
        self.queue.dispatched(first['id'], 'real-run')
        self.queue.defer_unsubmitted(first['id'])
        self.assertEqual('submitted', self.queue.snapshot('task')['queue'][0]['state'])
        self.queue.failed(first['id'])
        self.queue.defer_unsubmitted(first['id'])
        self.assertEqual('needs_review', self.queue.snapshot('task')['queue'][0]['state'])
        self.assertEqual('delivery_unknown', self.queue.snapshot('task')['reason'])

    def test_restore_preflight_preserves_stop_and_round_trips_without_corrupting_store(self):
        first = self.add()
        self.claim()
        self.queue.pause('task', 'stopped')
        self.queue.defer_unsubmitted(first['id'])
        self.assertEqual('stopped', self.queue.snapshot('task')['reason'])
        self.queue.pause('task', 'control_restore_required')
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNone(restarted.warning)
        snapshot = restarted.snapshot('task')
        self.assertEqual('restart', snapshot['reason'])
        self.assertEqual('queued', snapshot['queue'][0]['state'])
        self.assertEqual(first['id'], snapshot['queue'][0]['id'])

    def test_scheduled_restore_preflight_preserves_one_occurrence(self):
        schedule = self.schedule('daily')
        self.now += 60
        self.queue.tick()
        claim = self.claim()
        self.queue.defer_unsubmitted(claim['id'])
        self.now += 86400
        self.queue.tick()
        snapshot = self.queue.snapshot('task')
        self.assertEqual(1, len(snapshot['queue']))
        self.assertEqual(schedule['id'], snapshot['queue'][0]['scheduleId'])
        self.assertIsNone(self.claim())

    def test_trust_and_same_settings_are_rechecked_at_execution(self):
        self.add()
        self.assertIsNone(self.claim(state={**self.ready, 'trusted': False}))
        self.assertIsNone(self.queue.claim('task', self.ready, {**self.context, 'permissionMode': 'auto'}))
        self.assertEqual('settings_changed', self.queue.snapshot('task')['reason'])
        self.queue.resume('task')
        self.assertIsNotNone(self.claim())

    def test_explicit_settings_confirmation_updates_future_work_only(self):
        sent = self.add('already sent')
        self.claim()
        self.queue.dispatched(sent['id'], 'active-run')
        waiting = self.add('waiting')
        enabled, disabled = self.schedule('daily'), self.schedule('weekly')
        self.queue.set_schedule_enabled('task', disabled['id'], False)
        other = self.add('other task', sid='other')
        changed = {**self.context, 'model': 'new model', 'effort': 'medium'}
        self.queue.confirm_context('task', changed)
        state = self.queue.snapshot('task')
        rows = {row['id']: row for row in state['queue']}
        schedules = {row['id']: row for row in state['schedules']}
        self.assertEqual(self.context, rows[sent['id']]['context'])
        self.assertEqual(changed, rows[waiting['id']]['context'])
        self.assertEqual(changed, schedules[enabled['id']]['context'])
        self.assertEqual(self.context, schedules[disabled['id']]['context'])
        self.assertEqual(self.context, self.queue.snapshot('other')['queue'][0]['context'])
        self.queue.failed(sent['id'])
        self.queue.confirm_context('task', changed)
        uncertain = next(row for row in self.queue.snapshot('task')['queue'] if row['id'] == sent['id'])
        self.assertEqual('needs_review', uncertain['status'])
        self.assertEqual(self.context, uncertain['context'])
        saved = json.loads((self.root / 'work-queue.json').read_text('utf-8'))
        self.assertEqual(changed, next(row for row in saved['schedules'] if row['id'] == enabled['id'])['context'])

    def test_cancel_edit_reorder_are_scoped_and_do_not_touch_running_work(self):
        first, second, third = self.add('first'), self.add('second'), self.add('third')
        self.add('other task', sid='other')
        with self.assertRaises(ValueError):
            self.queue.cancel('other', first['id'])
        self.queue.edit('task', first['id'], 'revised', ['C:/report.html'])
        self.queue.cancel('task', second['id'])
        self.queue.reorder('task', [third['id'], first['id']])
        self.assertEqual(third['id'], self.claim()['id'])
        with self.assertRaises(ValueError):
            self.queue.edit('task', third['id'], 'too late')
        self.assertEqual(1, len(self.queue.snapshot('other')['queue']))

    def test_parallel_claims_deliver_only_one_row(self):
        self.add()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.claim(), range(12)))
        self.assertEqual(1, sum(result is not None for result in results))

    def test_client_idempotency_survives_restart_and_rejects_reused_payload_key(self):
        item = self.add(client_id='browser-request')
        self.assertEqual(item['id'], self.add(client_id='browser-request')['id'])
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        duplicate = restarted.enqueue('task', 'follow up', [], self.context, client_id='browser-request')
        self.assertEqual(item['id'], duplicate['id'])
        with self.assertRaises(ValueError):
            self.add('different message', client_id='browser-request')
        self.assertEqual(1, len(restarted.snapshot('task')['queue']))

    def test_once_schedule_creates_exactly_one_occurrence(self):
        schedule = self.schedule()
        self.now += 60
        self.assertTrue(self.queue.tick())
        self.assertFalse(self.queue.tick())
        snapshot = self.queue.snapshot('task')
        self.assertEqual(1, len(snapshot['queue']))
        self.assertEqual(schedule['id'], snapshot['queue'][0]['scheduleId'])
        self.assertFalse(snapshot['schedules'][0]['enabled'])
        self.assertEqual(self.context, snapshot['queue'][0]['context'])

    def test_schedule_registration_retry_after_due_time_does_not_duplicate(self):
        first = self.schedule(client_id='once-registration')
        first_at = self.now + 60
        self.now += 61
        self.queue.tick()
        again = self.queue.add_schedule('task', 'scheduled prompt', [], kind='once', run_at=first_at,
                                        context=self.context, client_id='once-registration')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(1, len(self.queue.snapshot('task')['queue']))
        self.assertEqual(1, len(self.queue.snapshot('task')['schedules']))

    def test_schedule_retry_after_delete_does_not_recreate_registration(self):
        first = self.schedule(client_id='cancelled-registration')
        self.queue.cancel_schedule('task', first['id'])
        again = self.schedule(client_id='cancelled-registration')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual([], self.queue.snapshot('task')['schedules'])

    def test_schedule_pause_withdraws_queued_occurrence_and_resume_skips_missed(self):
        schedule = self.schedule('daily')
        self.now += 60
        self.queue.tick()
        self.queue.set_schedule_enabled('task', schedule['id'], False)
        self.assertEqual([], self.queue.snapshot('task')['queue'])
        self.now += 2 * 86400
        self.queue.set_schedule_enabled('task', schedule['id'], True)
        self.queue.tick()
        state = self.queue.snapshot('task')
        self.assertEqual([], state['queue'])
        self.assertGreater(state['schedules'][0]['nextRunAt'], self.now)

    def test_schedule_pause_does_not_interrupt_already_submitted_request(self):
        schedule = self.schedule('daily')
        self.now += 60
        self.queue.tick()
        claim = self.claim()
        self.queue.dispatched(claim['id'], 'scheduled-run')
        self.queue.set_schedule_enabled('task', schedule['id'], False)
        self.assertEqual('submitted', self.queue.snapshot('task')['queue'][0]['state'])
        self.queue.observe('task', 'result', {'lastRunId': 'scheduled-run'})
        self.assertEqual('done', self.queue.snapshot('task')['schedules'][0]['lastRun']['status'])

    def test_schedule_reschedule_withdraws_old_unsent_occurrence(self):
        schedule = self.schedule()
        self.now += 60
        self.queue.tick()
        self.queue.update_schedule('task', schedule['id'], 'revised scheduled prompt', [],
                                   kind='once', run_at=self.now + 120, context=self.context)
        self.assertEqual([], self.queue.snapshot('task')['queue'])
        self.now += 120
        self.queue.tick()
        self.assertEqual('revised scheduled prompt', self.queue.snapshot('task')['queue'][0]['text'])

    def test_schedule_delivery_error_is_visible_and_not_retried(self):
        self.schedule()
        self.now += 60
        self.queue.tick()
        item = self.claim()
        self.queue.dispatched(item['id'], 'scheduled-error')
        self.queue.observe('task', 'error', {})
        snapshot = self.queue.snapshot('task')
        self.assertEqual('needs_review', snapshot['schedules'][0]['lastRun']['status'])
        self.assertEqual('needs_review', snapshot['queue'][0]['state'])

    def test_sleep_misses_are_skipped_without_catchup_burst(self):
        self.schedule('daily')
        self.now += 4 * 86400
        self.queue.tick()
        snapshot = self.queue.snapshot('task')
        self.assertEqual([], snapshot['queue'])
        self.assertEqual('missed', snapshot['schedules'][0]['lastRun']['status'])
        self.assertGreater(snapshot['schedules'][0]['nextRunAt'], self.now)

    def test_weekly_multiple_days_keep_local_wall_time(self):
        self.schedule('weekly', weekdays=[0, 2, 4])
        self.now += 60
        self.queue.tick()
        upcoming = datetime.fromtimestamp(self.queue.snapshot('task')['schedules'][0]['nextRunAt'])
        self.assertEqual((2, 8, 1), (upcoming.weekday(), upcoming.hour, upcoming.minute))

    def test_pending_recurrence_cannot_accumulate(self):
        self.schedule('daily')
        self.now += 60
        self.queue.tick()
        self.now += 86400
        self.queue.tick()
        snapshot = self.queue.snapshot('task')
        self.assertEqual(1, len(snapshot['queue']))
        self.assertEqual('previous_pending', snapshot['schedules'][0]['lastStatus'])

    def test_restart_requires_confirm_and_skips_overdue_schedules(self):
        self.schedule('daily')
        restarted = WorkQueue(self.root, clock=lambda: self.now)
        self.now += 61
        restarted.tick()
        self.assertEqual([], restarted.snapshot('task')['queue'])
        restarted.resume('task')
        restarted.tick()
        self.assertEqual([], restarted.snapshot('task')['queue'])
        self.assertGreater(restarted.snapshot('task')['schedules'][0]['nextRunAt'], self.now)

    def test_cancel_schedule_cancels_its_unsent_occurrence_only(self):
        schedule = self.schedule()
        manual = self.add()
        self.now += 60
        self.queue.tick()
        self.queue.cancel_schedule('task', schedule['id'])
        snapshot = self.queue.snapshot('task')
        self.assertEqual([], snapshot['schedules'])
        self.assertEqual([manual['id']], [row['id'] for row in snapshot['queue']])

    def test_schedule_inputs_reject_past_or_inconsistent_wall_time(self):
        for kwargs in ({'kind': 'hourly'}, {'kind': 'weekly', 'weekdays': [True]},
                       {'kind': 'daily', 'time': '09:00'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.queue.add_schedule('task', 'prompt', [], run_at=self.now + 60, **kwargs)

    def test_corrupt_store_is_preserved_and_disables_execution(self):
        path = self.root / 'work-queue.json'
        path.write_bytes(b'{broken')
        engine = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNotNone(engine.warning)
        with self.assertRaises(ValueError):
            engine.enqueue('task', 'no automatic reset')
        self.assertEqual(b'{broken', path.read_bytes())

    def test_failed_durable_claim_never_returns_dispatchable_work(self):
        self.add()
        with patch('local_app.work_queue.HistoryStore._write', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.claim()
        self.assertIsNotNone(self.queue.warning)
        with self.assertRaises(ValueError):
            self.claim()


if __name__ == '__main__':
    unittest.main()
