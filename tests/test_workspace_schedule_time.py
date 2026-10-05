"""Calendar boundaries, durable schedules and unchanged delivery semantics."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from local_app.schedule_time import first_run, timing, next_after
from local_app.work_queue import WorkQueue
from tests import test_workspace_dispatch_routes as routes


def at(year=2026, month=10, day=5, hour=8, minute=0):
    return datetime(year, month, day, hour, minute).timestamp()


class CalendarTimingTests(unittest.TestCase):
    def next(self, schedule, now):
        return datetime.fromtimestamp(first_run(schedule, now))

    def test_weekdays_skip_weekends_and_exact_slot(self):
        rule = {'kind': 'weekdays', 'time': '09:00'}
        for now in (at(day=9, hour=9), at(day=10), at(day=11, hour=23)):
            self.assertEqual(datetime(2026, 10, 12, 9), self.next(rule, now))
        self.assertEqual(datetime(2026, 10, 9, 9), self.next(rule, at(day=9)))

    def test_daily_and_selected_weekdays_keep_existing_calendar_behavior(self):
        self.assertEqual(datetime(2026, 10, 6, 9), self.next({'kind': 'daily', 'time': '09:00'}, at(hour=9)))
        self.assertEqual(datetime(2026, 10, 7, 9), self.next({'kind': 'weekly', 'time': '09:00', 'weekdays': [0, 2, 4]}, at(hour=9)))

    def test_monthly_day_and_year_boundaries(self):
        self.assertEqual(datetime(2026, 11, 1, 9), self.next({'kind': 'monthly', 'time': '09:00'}, at(day=5)))
        self.assertEqual(datetime(2027, 1, 1, 9), self.next({'kind': 'monthly', 'time': '09:00'}, at(month=12, day=31)))
        self.assertEqual(datetime(2026, 10, 10, 9, 30), self.next({'kind': 'monthly', 'time': '09:30', 'dayOfMonth': 10}, at(day=5)))

    def test_nonexistent_month_days_are_skipped_without_date_rollover(self):
        rule = {'kind': 'monthly', 'time': '09:00', 'dayOfMonth': 31}
        self.assertEqual(datetime(2026, 3, 31, 9), self.next(rule, at(month=1, day=31, hour=9)))
        rule['dayOfMonth'] = 29
        self.assertEqual(datetime(2028, 2, 29, 9), self.next(rule, at(year=2028, month=1, day=31)))
        self.assertEqual(datetime(2026, 3, 29, 9), self.next(rule, at(month=1, day=31)))

    def test_portal_eight_hour_example_has_three_daily_slots(self):
        rule = {'kind': 'interval', 'intervalMinutes': 480, 'startTime': '07:00', 'endTime': '23:59'}
        self.assertEqual(datetime(2026, 10, 5, 7), self.next(rule, at(hour=6)))
        self.assertEqual(datetime(2026, 10, 5, 15), self.next(rule, at(hour=7)))
        self.assertEqual(datetime(2026, 10, 5, 23), self.next(rule, at(hour=15)))
        self.assertEqual(datetime(2026, 10, 6, 7), self.next(rule, at(hour=23)))

    def test_interval_end_is_exclusive_and_daily_anchor_is_preserved(self):
        rule = {'kind': 'interval', 'intervalMinutes': 480, 'startTime': '07:00', 'endTime': '15:00'}
        self.assertEqual(datetime(2026, 10, 6, 7), self.next(rule, at(hour=7)))
        rule.update(intervalMinutes=150, startTime='09:00', endTime='18:00')
        self.assertEqual(datetime(2026, 10, 5, 14), self.next(rule, at(hour=11, minute=30)))
        self.assertEqual(datetime(2026, 10, 6, 9), self.next(rule, at(hour=18)))
        rule['intervalMinutes'] = 1440
        self.assertEqual(datetime(2026, 10, 6, 9), self.next(rule, at(hour=9)))

    def test_large_missed_gap_finds_one_future_slot(self):
        rule = timing('interval', None, interval_minutes=1, start_time='00:00', end_time='23:59')
        self.assertEqual(at(year=2036, minute=1), next_after(rule, at(year=2036)))

    def test_invalid_dates_intervals_and_clock_fields_are_rejected(self):
        invalid = [dict(kind='monthly', time='09:00', dayOfMonth=x) for x in (0, 32, True, 2.5, '2')]
        invalid += [dict(kind='interval', intervalMinutes=x, startTime='09:00', endTime='18:00') for x in (0, 1441, True, 1.5, '60')]
        invalid += [dict(kind='interval', intervalMinutes=30, startTime=start, endTime=end)
                    for start, end in [('9:00', '18:00'), ('09:00', '09:00'), ('23:00', '01:00'), ('09:00', '25:00')]]
        invalid += [dict(kind='weekly', time='09:00', weekdays=[]), dict(kind='bogus', time='09:00')]
        for rule in invalid:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                first_run(rule, at())


class ExpandedScheduleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = at(hour=6)
        self.queue = WorkQueue(self.root, clock=lambda: self.now)

    def add(self, kind='interval', **extras):
        wire = {'kind': kind, **extras}
        if kind == 'interval':
            wire = {'intervalMinutes': 480, 'startTime': '07:00', 'endTime': '23:59', **wire}
        else:
            wire.setdefault('time', '09:00')
        first = first_run(wire, self.now)
        return self.queue.add_schedule('task', 'same existing request', kind=kind, run_at=first,
            time=wire.get('time'), weekdays=wire.get('weekdays'), day_of_month=wire.get('dayOfMonth'),
            interval_minutes=wire.get('intervalMinutes'), start_time=wire.get('startTime'), end_time=wire.get('endTime'),
            client_id=wire.get('clientRequestId'))

    def test_new_rules_roundtrip_and_restart_still_requires_confirmation(self):
        for kind in ('weekdays', 'monthly', 'interval'):
            self.add(kind)
        loaded = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNone(loaded.warning)
        self.assertEqual(['weekdays', 'monthly', 'interval'], [r['kind'] for r in loaded.snapshot()['schedules']])
        self.now = at(day=10, hour=20)
        loaded.tick()
        self.assertEqual([], loaded.snapshot()['queue'])
        loaded.resume('task'); loaded.tick()
        self.assertEqual([], loaded.snapshot()['queue'])
        self.assertTrue(all(r['nextAt'] > self.now for r in loaded.snapshot()['schedules']))

    def test_interval_never_accumulates_while_an_occurrence_is_pending(self):
        schedule = self.add()
        self.now = at(hour=7); self.queue.tick()
        self.now = at(hour=15); self.queue.tick()
        self.assertEqual(1, len(self.queue.snapshot()['queue']))
        self.assertEqual('previous_pending', self.queue.snapshot()['schedules'][0]['lastStatus'])
        self.assertEqual(at(hour=23), self.queue.snapshot()['schedules'][0]['nextAt'])
        self.queue.cancel_schedule('task', schedule['id'])
        self.assertEqual([], self.queue.snapshot()['queue'])

    def test_pause_and_edit_preserve_intent_and_clear_old_rule_fields(self):
        row = self.add()
        self.queue.set_schedule_enabled('task', row['id'], False)
        new = self.queue.update_schedule('task', row['id'], 'edited', kind='monthly', run_at=at(month=11, day=1, hour=9), time='09:00', day_of_month=1)
        self.assertFalse(new['enabled']); self.assertTrue(new['pausedByUser'])
        self.assertIsNone(new['intervalMinutes']); self.assertIsNone(new['startTime'])
        self.queue.set_schedule_enabled('task', row['id'], True)
        self.assertTrue(self.queue.snapshot()['schedules'][0]['enabled'])

    def test_same_request_id_does_not_duplicate_or_change_time_rule(self):
        row = self.add(clientRequestId='request')
        self.now = at(day=6)
        self.assertEqual(row['id'], self.add(clientRequestId='request')['id'])
        with self.assertRaises(ValueError):
            self.add(clientRequestId='request', intervalMinutes=60)
        self.assertEqual(1, len(self.queue.snapshot()['schedules']))

    def test_old_schedule_store_without_new_fields_is_still_accepted(self):
        self.add('daily')
        data = json.loads(self.queue.path.read_text())
        for field in ('dayOfMonth', 'intervalMinutes', 'startTime', 'endTime'):
            data['schedules'][0].pop(field)
        self.queue.path.write_text(json.dumps(data))
        loaded = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNone(loaded.warning)
        self.assertEqual('daily', loaded.snapshot()['schedules'][0]['kind'])

    def test_corrupt_interval_stays_preserved_and_never_auto_executes(self):
        self.add()
        data = json.loads(self.queue.path.read_text()); data['schedules'][0]['intervalMinutes'] = 0
        raw = json.dumps(data); self.queue.path.write_text(raw)
        loaded = WorkQueue(self.root, clock=lambda: self.now)
        self.assertIsNotNone(loaded.warning)
        self.assertEqual(raw, loaded.path.read_text())
        with self.assertRaises(ValueError):
            loaded.tick()


class ExpandedScheduleRoutesTests(unittest.TestCase):
    setUp = routes.DispatchRoutesTests.setUp
    shutdown = routes.DispatchRoutesTests.shutdown
    request = routes.DispatchRoutesTests.request

    def test_each_new_time_rule_delivers_once_through_existing_send(self):
        rules = [{'kind': 'weekdays', 'time': '09:00'}, {'kind': 'monthly', 'time': '09:00', 'dayOfMonth': 10},
                 {'kind': 'interval', 'intervalMinutes': 150, 'startTime': '09:00', 'endTime': '18:00'}]
        for index, rule in enumerate(rules):
            with self.subTest(rule=rule):
                self.now = at(); self.app.dispatch.queue.clock = lambda: self.now
                code, value = self.request({'action': 'schedule', 'text': f'request {index}', 'attachments': [], 'schedule': rule})
                self.assertEqual(200, code, value)
                row = value['schedules'][0]
                self.assertEqual(rule['kind'], row['kind']); self.assertEqual(index, len(self.bridge.sent))
                self.now = row['nextRunAt']; self.app.dispatch.pump(); self.app.dispatch.pump()
                self.assertEqual([f'request {i}' for i in range(index + 1)], self.bridge.sent)
                overview = self.app.dispatch.overview()['schedules'][0]
                if rule['kind'] == 'interval':
                    self.assertEqual(150, overview['intervalMinutes']); self.assertEqual('09:00', overview['startTime'])
                if rule['kind'] == 'monthly':
                    self.assertEqual(10, overview['dayOfMonth'])
                self.app.dispatch.queue.cancel_schedule(self.sid, row['id'])

    def test_invalid_timing_cannot_register_or_start_work(self):
        for rule in ({'kind': 'monthly', 'time': '09:00', 'dayOfMonth': 32},
                     {'kind': 'interval', 'intervalMinutes': 0, 'startTime': '09:00', 'endTime': '18:00'}):
            code, _ = self.request({'action': 'schedule', 'text': 'invalid', 'attachments': [], 'schedule': rule})
            self.assertEqual(400, code)
        self.assertEqual([], self.app.dispatch.snapshot(self.sid)['schedules'])
        self.assertEqual([], self.bridge.sent)
