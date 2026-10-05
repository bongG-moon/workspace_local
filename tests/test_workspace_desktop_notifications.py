from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from local_app.desktop_notifications import DesktopNotifications, MAX_INBOX, MAX_SEEN, notification_id
from local_app.attention import DEFAULT_REQUEST_SUMMARY
from local_app.tray import WorkspaceTray, _WindowsTray, _notification_text


class DesktopNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sid, self.other = str(uuid.uuid4()), str(uuid.uuid4())
        self.clock = 1000.0
        self.foreground = False
        self.deliveries, self.opened = [], []
        self.center = self.make_center()

    def deliver(self, row, callback):
        self.deliveries.append((row, callback))
        return True

    def make_center(self, **kwargs):
        center = DesktopNotifications(self.root, notify=self.deliver,
                                    is_foreground=lambda sid: self.foreground,
                                    on_open=lambda sid: self.opened.append(sid),
                                    clock=lambda: self.clock, monotonic=lambda: self.clock,
                                    automatic_retry=False, **kwargs)
        self.addCleanup(center.close)
        return center

    def publish(self, event='run:one', kind='completed', sid=None, title='검증 업무'):
        return self.center.publish(sid or self.sid, title, kind, event)

    def test_completed_attention_and_error_use_fixed_messages_and_only_task_metadata(self):
        for kind in ('completed', 'attention', 'error'):
            row = self.publish(kind=kind)
            self.assertEqual('requested', row['delivery'])
            self.assertEqual(kind, row['kind'])
            self.assertEqual('검증 업무', row['title'])
            self.assertNotIn('run:one', json.dumps(row))
            self.clock += 4
        self.assertEqual(3, len(self.deliveries))
        allowed = {'id', 'sessionId', 'title', 'kind', 'message', 'createdAt', 'read', 'delivery'}
        self.assertTrue(all(set(row) == allowed | ({'summary'} if row['kind'] == 'attention' else set())
                            for row, _ in self.deliveries))

    def test_attention_summary_is_sanitized_persisted_and_old_schema_still_loads(self):
        row = self.center.publish(self.sid, '질문 업무', 'attention', 'question',
                                  summary='보고서 형식 token=glpat-abcdefghijk 를 확인해 주세요')
        self.assertNotIn('glpat-', row['summary'])
        self.assertIn('보고서 형식', row['summary'])
        self.assertEqual(row['summary'], self.deliveries[0][0]['summary'])
        reopened = self.make_center()
        self.assertIsNone(reopened.warning)
        self.assertEqual(row['summary'], reopened.snapshot()['inbox'][0]['summary'])
        old = json.loads(self.center.path.read_text('utf-8'))
        old['inbox'][0].pop('summary')
        self.center.path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
        legacy = self.make_center()
        self.assertIsNone(legacy.warning)
        self.assertEqual(row['id'], legacy.snapshot()['inbox'][0]['id'])
        self.assertEqual(DEFAULT_REQUEST_SUMMARY, legacy.snapshot()['inbox'][0]['summary'])
        self.assertTrue(legacy.publish(self.sid, '질문 업무', 'attention', 'question', summary='다른 요약')['duplicate'])
        self.assertEqual(1, len(self.deliveries))

    def test_legacy_or_empty_attention_calls_always_deliver_a_short_action_label(self):
        for index, value in enumerate(('', '   ', None, 'python PRIVATE.py')):
            with self.subTest(summary=value):
                self.clock += 4
                row = self.center.publish(self.sid, '질문 업무', 'attention', 'missing-' + str(index), summary=value)
                self.assertEqual(DEFAULT_REQUEST_SUMMARY, row['summary'])
                self.assertEqual(DEFAULT_REQUEST_SUMMARY, self.deliveries[-1][0]['summary'])
        self.assertEqual(4, len(self.deliveries))

    def test_summary_never_replaces_receipt_identity_or_completion_message(self):
        row = self.center.publish(self.sid, '질문 업무', 'attention', 'question', summary='형식을 골라 주세요')
        duplicate = self.center.publish(self.sid, '질문 업무', 'attention', 'question', summary='다른 질문')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(row['summary'], duplicate['summary'])
        self.clock += 4
        complete = self.center.publish(self.sid, '완료 업무', 'completed', 'done', summary='불필요한 원문')
        self.assertNotIn('summary', complete)

    def test_duplicate_is_one_delivery_and_remains_deduplicated_after_restart(self):
        original = self.publish()
        self.assertTrue(self.publish()['duplicate'])
        self.center = self.make_center()
        self.assertTrue(self.publish()['duplicate'])
        self.assertEqual(1, len(self.deliveries))
        self.assertEqual(original['id'], self.center.snapshot()['inbox'][0]['id'])

    def test_concurrent_duplicate_callbacks_request_only_one_banner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: self.publish(), range(16)))
        self.assertEqual(1, len(self.deliveries))
        self.assertEqual(15, sum(value.get('duplicate', False) for value in values))
        self.assertEqual(1, self.center.snapshot()['unreadCount'])

    def test_identity_separates_tasks_and_kinds_but_never_saves_raw_event_key(self):
        first = self.publish('request:PRIVATE-ID')
        self.clock += 4
        second = self.publish('request:PRIVATE-ID', sid=self.other)
        self.clock += 4
        third = self.publish('request:PRIVATE-ID', kind='attention')
        self.assertEqual(3, len({first['id'], second['id'], third['id']}))
        self.assertNotIn('PRIVATE-ID', (self.root / 'desktop-notifications.json').read_text('utf-8'))

    def test_foreground_stays_inbox_and_cooldown_defers_new_request_without_losing_it(self):
        self.foreground = True
        self.assertEqual('foreground', self.publish()['delivery'])
        self.foreground = False
        self.assertEqual('requested', self.publish('two')['delivery'])
        self.assertEqual('queued', self.publish('three')['delivery'])
        self.assertEqual(1, len(self.deliveries))
        self.assertEqual(3, self.center.snapshot()['unreadCount'])
        self.clock += 4
        self.center.flush_pending()
        self.assertEqual(2, len(self.deliveries))
        self.assertEqual('requested', self.center.snapshot()['inbox'][0]['delivery'])
        self.center.flush_pending()
        self.assertEqual(2, len(self.deliveries))

    def test_busy_card_retries_exact_new_request_and_never_duplicates_previous_request(self):
        original = self.publish('original')
        self.clock += 4
        self.center.notify = lambda *args: 'busy'
        waiting = self.publish('new-question', 'attention')
        self.assertEqual('queued', waiting['delivery'])
        self.assertEqual('requested', original['delivery'])
        self.assertTrue(self.publish('new-question', 'attention')['duplicate'])
        self.clock += 4
        self.center.notify = self.deliver
        self.center.flush_pending()
        self.assertEqual([original['id'], waiting['id']], [row['id'] for row, _ in self.deliveries])
        self.center.flush_pending()
        self.assertEqual(2, len(self.deliveries))

    def test_retry_expires_is_runtime_only_and_close_cancels_future_delivery(self):
        self.center.notify = lambda *args: 'busy'
        self.publish('busy')
        first_deadline = next(iter(self.center._pending.values()))
        self.clock += 10
        self.center.flush_pending()
        self.assertEqual(first_deadline, next(iter(self.center._pending.values())))
        self.clock += 60
        self.center.notify = self.deliver
        self.center.flush_pending()
        self.assertEqual([], self.deliveries)
        self.assertEqual({}, self.center._pending)
        self.assertEqual('unavailable', self.center.snapshot()['inbox'][0]['delivery'])
        self.center.notify = lambda *args: 'busy'
        queued = self.publish('another')
        fresh = self.make_center()
        self.assertTrue(fresh.publish(self.sid, '검증 업무', 'completed', 'another')['duplicate'])
        self.assertEqual({}, fresh._pending)
        self.center.close()
        self.center.notify = self.deliver
        self.clock += 4
        self.center.flush_pending()
        self.assertEqual([], self.deliveries)

    def test_answered_question_and_read_or_disabled_receipts_do_not_surface_late(self):
        self.center.notify = lambda *args: 'busy'
        self.publish('question', 'attention')
        self.center.retain_attention([])
        self.assertEqual({}, self.center._pending)
        self.clock += 4
        read = self.publish('read')
        self.center.mark_read(read['id'])
        self.clock += 4
        self.center.flush_pending()
        self.assertEqual({}, self.center._pending)
        self.publish('disabled')
        self.center.configure({'completed': False})
        self.assertEqual({}, self.center._pending)
        self.assertEqual('disabled', self.center.snapshot()['inbox'][0]['delivery'])
        self.center.notify = self.deliver
        self.clock += 4
        self.center.flush_pending()
        self.assertEqual([], self.deliveries)

    def test_queued_request_becoming_foreground_is_not_pushed_and_suppression_never_retries(self):
        self.center.notify = lambda *args: 'busy'
        self.publish('question', 'attention')
        self.foreground = True
        self.clock += 4
        self.center.notify = self.deliver
        self.center.flush_pending()
        self.assertEqual([], self.deliveries)
        self.assertEqual('foreground', self.center.snapshot()['inbox'][0]['delivery'])
        self.foreground = False
        self.clock += 4
        self.center.notify = lambda *args: False
        self.assertEqual('unavailable', self.publish('suppressed')['delivery'])
        self.assertEqual({}, self.center._pending)

    def test_retry_timer_delivers_without_ui_poll_and_shutdown_releases_timer(self):
        delivered, calls = threading.Event(), []
        def notify(row, callback):
            calls.append(row['id'])
            if len(calls) == 1:
                return 'busy'
            delivered.set()
            return True
        center = DesktopNotifications(self.root / 'timer', notify=notify, cooldown=0)
        self.addCleanup(center.close)
        row = center.publish(self.sid, '백그라운드 질문', 'attention', 'wait')
        self.assertEqual('queued', row['delivery'])
        self.assertTrue(delivered.wait(3))
        self.assertEqual([row['id'], row['id']], calls)
        center.close()
        self.assertIsNone(center._retry_timer)

    def test_removed_task_cancels_only_its_deferred_banner_and_keeps_history_unread(self):
        self.center.notify = lambda *args: 'busy'
        removed = self.publish('removed')
        self.clock += 4
        retained = self.publish('retained', sid=self.other)
        self.center.cancel_pending_session(self.sid)
        self.assertEqual({retained['id']}, set(self.center._pending))
        rows = {row['id']: row for row in self.center.snapshot()['inbox']}
        self.assertFalse(rows[removed['id']]['read'])
        self.assertEqual('unavailable', rows[removed['id']]['delivery'])
        self.center.notify = self.deliver
        self.clock += 4
        self.center.flush_pending()
        self.assertEqual([retained['id']], [row['id'] for row, _ in self.deliveries])

    def test_cancel_during_foreground_check_prevents_late_native_send(self):
        for cancel in ('answered', 'removed'):
            with self.subTest(cancel=cancel):
                center = self.make_center()
                center.notify = lambda *args: 'busy'
                row = center.publish(self.sid, '대기 질문', 'attention', 'race-' + cancel)
                self.clock += 4
                entered, release = threading.Event(), threading.Event()
                calls = []
                def foreground(sid):
                    entered.set()
                    self.assertTrue(release.wait(3))
                    return False
                center.is_foreground = foreground
                center.notify = lambda *args: calls.append(args) or True
                worker = threading.Thread(target=center.flush_pending)
                try:
                    worker.start()
                    self.assertTrue(entered.wait(2))
                    self.assertIn(row['id'], center._pending)
                    self.assertIn(row['id'], center._inflight)
                    if cancel == 'answered':
                        center.retain_attention([])
                    else:
                        center.cancel_pending_session(self.sid)
                    self.assertNotIn(row['id'], center._pending)
                finally:
                    release.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], calls)
                self.assertEqual({}, center._inflight)
                self.assertEqual({}, center._pending)
                self.clock += 4
                center.flush_pending()
                self.assertEqual([], calls)
                self.assertEqual('unavailable', center.snapshot()['inbox'][0]['delivery'])
                center.close()

    def test_cancel_during_native_busy_response_never_resurrects_deferred_receipt(self):
        for phase in ('initial', 'retry'):
            for cancel in ('answered', 'removed'):
                with self.subTest(phase=phase, cancel=cancel):
                    center = self.make_center()
                    event = phase + '-' + cancel
                    if phase == 'retry':
                        center.notify = lambda *args: 'busy'
                        center.publish(self.sid, '대기 질문', 'attention', event)
                        self.clock += 4
                    entered, release = threading.Event(), threading.Event()
                    calls = []
                    def notify(row, callback):
                        calls.append(row['id'])
                        entered.set()
                        self.assertTrue(release.wait(3))
                        return 'busy'
                    center.notify = notify
                    target = center.flush_pending if phase == 'retry' else lambda: center.publish(
                        self.sid, '대기 질문', 'attention', event)
                    worker = threading.Thread(target=target)
                    try:
                        worker.start()
                        self.assertTrue(entered.wait(2))
                        if cancel == 'answered':
                            center.retain_attention([])
                        else:
                            center.cancel_pending_session(self.sid)
                    finally:
                        release.set()
                        worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(1, len(calls))
                    self.assertEqual({}, center._pending)
                    self.assertEqual({}, center._inflight)
                    self.clock += 4
                    center.flush_pending()
                    self.assertEqual(1, len(calls))
                    self.assertEqual('unavailable', center.snapshot()['inbox'][0]['delivery'])
                    center.close()

    def test_parallel_flush_cannot_deliver_same_cancellable_receipt_twice(self):
        self.center.notify = lambda *args: 'busy'
        row = self.publish('race-parallel', 'attention')
        self.clock += 4
        entered, release = threading.Event(), threading.Event()
        calls = []
        def notify(value, callback):
            calls.append(value['id'])
            entered.set()
            self.assertTrue(release.wait(3))
            return True
        self.center.notify = notify
        worker = threading.Thread(target=self.center.flush_pending)
        try:
            worker.start()
            self.assertTrue(entered.wait(2))
            self.clock += 4
            self.center.flush_pending()
            self.assertEqual([row['id']], calls)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual({}, self.center._pending)
        self.assertEqual({}, self.center._inflight)
        self.assertEqual('requested', self.center.snapshot()['inbox'][0]['delivery'])

    def test_preferences_persist_and_disable_only_selected_kinds(self):
        self.center.configure({'completed': False})
        self.assertEqual('disabled', self.publish()['delivery'])
        self.assertEqual('requested', self.publish('question', 'attention')['delivery'])
        self.center.configure({'enabled': False})
        self.center = self.make_center()
        self.clock += 4
        self.assertEqual('disabled', self.publish('error', 'error')['delivery'])
        self.assertFalse(self.center.snapshot()['preferences']['completed'])
        self.assertEqual(1, len(self.deliveries))

    def test_invalid_preferences_are_rejected_without_creating_state(self):
        for value in (None, {}, {'enabled': 1}, {'unexpected': True}, {'enabled': 'yes'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.center.configure(value)
        self.assertFalse(self.center.path.exists())

    def test_native_unavailable_or_failure_does_not_claim_displayed(self):
        for notify in (None, lambda *args: False, lambda *args: 1):
            self.center.notify = notify
            self.clock += 4
            row = self.publish(str(self.clock))
            self.assertEqual('unavailable', row['delivery'])
            self.assertNotIn('displayed', row)
        self.center.notify = lambda *args: (_ for _ in ()).throw(OSError('PRIVATE DETAIL'))
        self.clock += 4
        self.assertEqual('unavailable', self.publish('failure')['delivery'])
        self.assertNotIn('PRIVATE DETAIL', str(self.center.snapshot()))

    def test_click_uses_retained_task_identity_and_marks_only_that_notification_read(self):
        first = self.publish()
        self.clock += 4
        second = self.publish('two', sid=self.other)
        self.assertTrue(self.deliveries[0][1]())
        self.assertEqual([self.sid], self.opened)
        rows = {row['id']: row for row in self.center.snapshot()['inbox']}
        self.assertTrue(rows[first['id']]['read'])
        self.assertFalse(rows[second['id']]['read'])
        self.center.mark_read()
        self.assertEqual(0, self.make_center().snapshot()['unreadCount'])

    def test_callbacks_run_without_notification_lock(self):
        outcomes = []
        def open_task(sid):
            thread = threading.Thread(target=lambda: outcomes.append(self.center.snapshot()))
            thread.start(); thread.join(1)
            self.assertFalse(thread.is_alive(), 'callback held notification lock')
        self.center.on_open = open_task
        row = self.publish()
        self.assertTrue(self.center.open(row['id']))
        self.assertEqual(1, len(outcomes))

    def test_public_receipt_identity_matches_publish_without_mutating_state(self):
        identity = notification_id(self.sid, 'attention', 'native-request')
        self.assertFalse(self.center.path.exists())
        self.assertEqual(identity, self.publish('native-request', 'attention')['id'])
        self.assertNotEqual(identity, notification_id(self.other, 'attention', 'native-request'))

    def test_batch_read_marks_only_exact_current_receipts_and_saves_once(self):
        old = self.publish('old', 'attention')
        first = self.publish('first', 'attention')
        second = self.publish('second', 'attention')
        with patch.object(self.center, '_save', wraps=self.center._save) as save:
            self.center.mark_read_many([first['id'], second['id']])
        self.assertEqual(1, save.call_count)
        rows = {row['id']: row for row in self.make_center().snapshot()['inbox']}
        self.assertFalse(rows[old['id']]['read'])
        self.assertTrue(rows[first['id']]['read'])
        self.assertTrue(rows[second['id']]['read'])
        self.assertEqual([], self.opened)

    def test_invalid_or_stale_batch_does_not_partially_read_or_write(self):
        first = self.publish('first', 'attention')
        before = self.center.path.read_bytes()
        invalid = [None, [], 'not-list', [None], ['not-id'], [first['id'], first['id']],
                   [first['id'], '0' * 64], [first['id']] * (MAX_INBOX + 1)]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.center.mark_read_many(values)
            self.assertEqual(before, self.center.path.read_bytes())
            self.assertFalse(self.center.snapshot()['inbox'][0]['read'])

    def test_missing_or_closed_task_callback_does_not_mark_read(self):
        row = self.publish()
        self.center.on_open = lambda sid: False
        self.assertFalse(self.center.open(row['id']))
        self.assertFalse(self.center.snapshot()['inbox'][0]['read'])
        self.assertFalse(self.center.open('0' * 64))
        self.center.close()
        self.assertFalse(self.deliveries[0][1]())
        self.assertTrue(self.publish('later')['closed'])
        self.assertEqual(1, len(self.center.snapshot()['inbox']))

    def test_corrupt_or_wrong_schema_file_is_preserved_and_never_reenables_banners(self):
        for raw in ('{"broken":true}', '{not-json', '[' * 2000):
            self.center.path.write_text(raw, encoding='utf-8')
            loaded = self.make_center()
            self.assertIsNotNone(loaded.snapshot()['warning'])
            self.assertFalse(loaded.snapshot()['preferences']['enabled'])
            loaded.publish(self.sid, 'title', 'completed', raw[:20])
            self.assertEqual(raw, loaded.path.read_text('utf-8'))
        self.assertEqual([], self.deliveries)

    def test_failed_preference_write_keeps_old_file_and_values(self):
        self.center.configure({'enabled': False})
        before = self.center.path.read_bytes()
        with patch('local_app.desktop_notifications.HistoryStore._write', side_effect=OSError('PRIVATE')):
            with self.assertRaises(ValueError):
                self.center.configure({'enabled': True})
        self.assertFalse(self.center.snapshot()['preferences']['enabled'])
        self.assertEqual(before, self.center.path.read_bytes())
        self.assertNotIn('PRIVATE', str(self.center.snapshot()))

    def test_inbox_and_dedup_are_bounded_and_aged_click_is_not_retargeted(self):
        self.center.configure({'enabled': False})
        first = self.publish('old')
        for index in range(MAX_SEEN + 2):
            self.publish(str(index))
        self.assertEqual(MAX_INBOX, len(self.center.snapshot()['inbox']))
        stored = json.loads(self.center.path.read_text('utf-8'))
        self.assertEqual(MAX_SEEN, len(stored['seen']))
        self.assertFalse(self.center.open(first['id']))
        self.assertEqual([], self.opened)

    def test_title_controls_and_surrogates_cannot_break_native_or_json_payload(self):
        row = self.publish(title='업무\x00\r\n\u202e\ud800 제목' + '가' * 200)
        self.assertLessEqual(len(row['title']), 100)
        self.assertNotIn('\x00', row['title'])
        self.assertNotIn('\u202e', row['title'])
        self.assertIsNone(self.center.snapshot()['warning'])
        self.assertEqual(row['id'], self.make_center().snapshot()['inbox'][0]['id'])


class FakeNative:
    def __init__(self, owner):
        self.owner = owner
        self.stopped = threading.Event()
        self.posted = []
    def run(self):
        self.owner._mark_ready(True)
        self.stopped.wait(5)
    def request_stop(self): self.stopped.set()
    def post_update(self): pass
    def post_notification(self):
        self.posted.append(self.owner._notification)
        self.owner._notification['sent'] = True
        return True


class TrayNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tray = WorkspaceTray('notification-test', lambda: None, lambda: None, native_factory=FakeNative)
        self.addCleanup(self.tray.stop)
        self.assertTrue(self.tray.start())

    def test_active_notification_target_cannot_be_replaced_by_newer_task(self):
        first, second = threading.Event(), threading.Event()
        self.assertTrue(self.tray.notify(title='first', message='fixed status', on_click=first.set))
        self.assertFalse(self.tray.notify(title='second', message='fixed status', on_click=second.set))
        self.tray._notification_event(0x402)
        self.tray._notification_event(0x405)
        self.assertTrue(first.wait(1))
        self.assertFalse(second.is_set())
        self.assertTrue(self.tray.notify(title='second', message='fixed status', on_click=second.set))
        self.tray._notification_event(0x402)
        self.tray._notification_event(0x405)
        self.assertTrue(second.wait(1))

    def test_suppressed_timeout_and_stop_never_open_another_task(self):
        clicked = threading.Event()
        for event in (0x403, 0x404, 0x405):
            self.assertTrue(self.tray.notify(title='task', message='status', on_click=clicked.set))
            self.tray._notification_event(event)  # no SHOW: no clickable banner was confirmed
            self.assertFalse(clicked.is_set())
        self.assertTrue(self.tray.notify(title='task', message='status', on_click=clicked.set))
        self.tray._notification_event(0x402)
        self.tray.stop()
        self.tray._notification_event(0x405)
        self.assertFalse(clicked.is_set())

    def test_failed_native_post_releases_slot_and_absent_tray_is_noop(self):
        with patch.object(self.tray._backend, 'post_notification', return_value=False):
            self.assertFalse(self.tray.notify(title='task', message='status'))
        self.assertIsNone(self.tray._notification)
        self.assertTrue(self.tray.notify(title='task', message='status'))
        self.tray.stop()
        self.assertFalse(self.tray.notify(title='task', message='status'))

    def test_utf16_buffer_limits_preserve_complete_emoji_and_remove_controls(self):
        value = _notification_text('😀' * 80, 63)
        self.assertEqual('😀' * 31, value)
        self.assertEqual('task status', _notification_text('task\x00status', 30))
        self.assertFalse(self.tray.notify(title='', message='status'))
        self.assertFalse(self.tray.notify(title='task', message='status', on_click='arbitrary command'))

    def test_native_balloon_flags_respect_quiet_time_and_do_not_queue_stale_notifications(self):
        native = object.__new__(_WindowsTray)
        native.owner = self.tray
        native._added = True
        class Notice(ctypes.Structure):
            _fields_ = [('flags', ctypes.c_uint), ('title', ctypes.c_wchar * 64),
                        ('info', ctypes.c_wchar * 256), ('infoFlags', ctypes.c_uint)]
        native._data = Notice
        calls = []
        def notify(action, pointer):
            value = pointer._obj
            calls.append((action, value.flags, value.infoFlags, value.title, value.info))
            return True
        native.shell = SimpleNamespace(Shell_NotifyIconW=notify)
        self.tray._notification = {'title': 'task', 'message': 'generic state', 'on_click': None,
                                   'sent': False, 'shown': False}
        native._show_notification()
        native._show_notification()
        self.assertEqual([(1, 0x50, 0x81, 'task', 'generic state')], calls)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('COMPANY_WORKSPACE_TEST_NOTIFICATIONS') == '1',
                     'Opt in: temporary Windows tray/banner with fixed diagnostic text only')
class NativeNotificationTests(unittest.TestCase):
    def test_windows_accepts_notification_request_and_owned_callback_without_display_guarantee(self):
        clicked = threading.Event()
        tray = WorkspaceTray('notification-native-' + uuid.uuid4().hex, lambda: None, lambda: None)
        self.addCleanup(tray.stop)
        self.assertTrue(tray.start(), tray.status())
        native = tray._backend
        requested = threading.Event()
        original = native.shell.Shell_NotifyIconW
        def observe(action, pointer):
            result = original(action, pointer)
            if action == 1 and pointer._obj.flags & 0x10 and result:
                requested.set()
            return result
        with patch.object(native.shell, 'Shell_NotifyIconW', side_effect=observe):
            self.assertTrue(tray.notify(title='Company Workspace 알림 검증',
                                       message='임시 알림 전달 경로를 확인하고 있습니다.', on_click=clicked.set))
            self.assertTrue(requested.wait(3))
            # Only our callback HWND receives synthetic events; no other window
            # is touched. This verifies routing, not OS visual presentation.
            native.user.PostMessageW(native.hwnd, native.CALLBACK, 0, 0x402 | (native.ICON_ID << 16))
            native.user.PostMessageW(native.hwnd, native.CALLBACK, 0, 0x405 | (native.ICON_ID << 16))
            self.assertTrue(clicked.wait(2))
        tray.stop()
        self.assertIsNone(native.hwnd)
        self.assertIsNone(native.mutex)


if __name__ == '__main__':
    unittest.main()
