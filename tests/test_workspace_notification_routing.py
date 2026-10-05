"""Notification routing without a desktop process, CLI, or personal settings."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock

from local_app.desktop_notifications import DesktopNotifications, MESSAGES
from local_app.server import LocalApp


class DeliveryStub:
    def __init__(self, result=True, available=True):
        self.result = result
        self.available = self.notification_available = available
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class NotificationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.sid, self.other = str(uuid.uuid4()), str(uuid.uuid4())
        self.app = LocalApp.__new__(LocalApp)
        self.app.lock = threading.RLock()
        self.app.sessions = {}
        self.app._desktop_window = DeliveryStub()
        self.app.tray = DeliveryStub()
        self.app._navigation = None
        self.app.notifier = SimpleNamespace(native_state={}, window_title='Workspace', theme_state={})
        self.app.desktop = DesktopNotifications(self.state, notify=self.app._notify_desktop,
            is_foreground=lambda sid: False, on_open=self.app.open_task, cooldown=0)
        self.addCleanup(self.app.desktop.close)
        self.payload = {'id': 'a' * 64, 'title': '분기 실적 보고서', 'kind': 'attention',
                        'summary': '보고서 형식을 골라 주세요',
                        'message': MESSAGES['attention'], 'sessionId': self.sid,
                        'privatePrompt': 'must never leave the server',
                        'requests': [{'command': 'private command'}]}

    def test_card_receives_only_notification_fields_and_original_callback(self):
        callback = lambda: None
        self.assertTrue(self.app._notify_desktop(self.payload, callback))
        self.assertEqual([{'title': self.payload['title'], 'message': MESSAGES['attention'],
                           'summary': self.payload['summary'],
                           'kind': 'attention', 'notification_id': 'a' * 64,
                           'on_click': callback}], self.app._desktop_window.calls)
        self.assertEqual([], self.app.tray.calls)

    def test_refused_card_does_not_escape_suppression_through_tray(self):
        self.app._desktop_window.result = False
        self.assertFalse(self.app._notify_desktop(self.payload, lambda: None))
        self.assertEqual(1, len(self.app._desktop_window.calls))
        self.assertEqual([], self.app.tray.calls)

    def test_busy_card_is_deferred_without_a_second_channel(self):
        self.app._desktop_window.result = 'busy'
        row = self.app.desktop.publish(self.sid, '첫 업무', 'attention', 'new-question')
        self.assertEqual('queued', row['delivery'])
        self.assertEqual([], self.app.tray.calls)
        self.app._desktop_window.result = True
        self.app.desktop.flush_pending()
        self.assertEqual('requested', self.app.desktop.snapshot()['inbox'][0]['delivery'])
        self.assertEqual(2, len(self.app._desktop_window.calls))

    def test_unsupported_card_falls_back_to_legacy_tray_with_original_callback(self):
        self.app._desktop_window.result = None
        callback = lambda: None
        self.assertTrue(self.app._notify_desktop(self.payload, callback))
        self.assertEqual([{'title': self.payload['title'], 'message': self.payload['summary'],
                           'on_click': callback}], self.app.tray.calls)

    def test_legacy_receipt_without_summary_keeps_fixed_tray_message(self):
        self.payload.pop('summary')
        self.app._desktop_window.result = None
        self.assertTrue(self.app._notify_desktop(self.payload, lambda: None))
        self.assertEqual(MESSAGES['attention'], self.app.tray.calls[0]['message'])

    def test_missing_host_uses_tray_without_needing_to_open_a_window(self):
        self.app._desktop_window = None
        self.assertTrue(self.app._notify_desktop(self.payload, lambda: None))
        self.assertEqual(1, len(self.app.tray.calls))

    def test_live_card_host_can_deliver_without_a_tray(self):
        self.app.tray = None
        self.assertTrue(self.app._notify_desktop(self.payload, lambda: None))
        self.assertTrue(self.app.attention()['desktop']['nativeAvailable'])

    def test_unavailable_channels_do_not_claim_delivery(self):
        for host in (None, DeliveryStub(result=None, available=False)):
            with self.subTest(host=host):
                self.app._desktop_window, self.app.tray = host, None
                self.assertFalse(self.app._notify_desktop(self.payload, lambda: None))
                self.assertFalse(self.app.attention()['desktop']['nativeAvailable'])

    def test_native_availability_reflects_either_channel(self):
        for card_available, tray_available in ((True, True), (False, True), (True, False), (False, False)):
            with self.subTest(card=card_available, tray=tray_available):
                self.app._desktop_window.notification_available = card_available
                self.app.tray.available = tray_available
                self.assertEqual(card_available or tray_available,
                                 self.app.attention()['desktop']['nativeAvailable'])

    def test_click_routes_to_original_task_and_reads_only_its_retained_receipt(self):
        self.app.sessions = {sid: {'id': sid, 'title': title, 'state': 'idle'}
                             for sid, title in ((self.sid, '첫 업무'), (self.other, '다른 업무'))}
        self.app.get = lambda sid: self.app.sessions[sid]
        activations = []
        self.app._open_window_callback = lambda: activations.append(self.app._navigation['sessionId'])
        first = self.app.desktop.publish(self.sid, '첫 업무', 'attention', 'first-approval')
        second = self.app.desktop.publish(self.other, '다른 업무', 'completed', 'second-result')
        self.assertEqual('requested', first['delivery'])
        self.app._viewed_session = self.other
        self.assertTrue(self.app._desktop_window.calls[0]['on_click']())
        self.assertEqual([self.sid], activations)
        self.assertEqual(self.sid, self.app._navigation['sessionId'])
        rows = {row['id']: row for row in DesktopNotifications(self.state).snapshot()['inbox']}
        self.assertTrue(rows[first['id']]['read'])
        self.assertFalse(rows[second['id']]['read'])
        self.assertEqual([], self.app.tray.calls)

    def test_suppressed_banner_stays_unread_in_inbox_without_legacy_banner(self):
        self.app._desktop_window.result = False
        row = self.app.desktop.publish(self.sid, '첫 업무', 'completed', 'first-result')
        self.assertEqual('unavailable', row['delivery'])
        self.assertEqual(1, self.app.desktop.snapshot()['unreadCount'])
        self.assertEqual([], self.app.tray.calls)

    def test_saved_disabled_preference_prevents_both_delivery_paths(self):
        before = self.app.desktop.configure({'enabled': False})['preferences']
        self.app.desktop.close()
        self.app.desktop = DesktopNotifications(self.state, notify=self.app._notify_desktop)
        row = self.app.desktop.publish(self.sid, '첫 업무', 'attention', 'approval')
        self.assertEqual('disabled', row['delivery'])
        self.assertEqual(before, self.app.desktop.snapshot()['preferences'])
        self.assertEqual([], self.app._desktop_window.calls)
        self.assertEqual([], self.app.tray.calls)


class BackgroundNotificationFlowTests(unittest.TestCase):
    """Real server/dispatch lifecycle with fake CLI/window, no renderer polling."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = LocalApp(root / 'state', command=['not-started'], managed_workspace_root=root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(root), True)['id']
        self.item = self.app.get(self.sid)
        self.item['lastRunId'] = 'background-run'
        self.host = self.app._desktop_window = DeliveryStub()
        self.app.tray = Mock(available=True)
        self.app.notifier = Mock(native_state={'supported': True, 'bound': True})
        self.app.notifier.set_visible.return_value = True
        self.app.notifier.is_foreground.return_value = False
        self.app._viewed_session = self.sid
        self.app._viewed_until = float('inf')
        self.app.desktop.cooldown = 0
        self.app.desktop._automatic_retry = False
        class Bridge:
            closed = cleanup_complete = False
            def __init__(bridge): bridge.sent = []
            def send(bridge, prompt):
                bridge.sent.append(prompt)
                self.app.emit(self.sid, 'result', {'sessionId': 'fake-cli-session'})
            def close(bridge):
                bridge.closed = bridge.cleanup_complete = True
                return True
        self.bridge = self.item['bridge'] = Bridge()

    def hide(self):
        self.assertTrue(self.app.hide_window()['hidden'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        self.assertFalse(self.bridge.closed)

    def test_hidden_window_receives_question_approval_and_ready_choice_without_any_ui_poll(self):
        self.hide()
        for tool in ('AskUserQuestion', 'Write'):
            self.app.emit(self.sid, 'request', {'id': 'reused-cli-id', 'tool': tool, 'input': {}})
            self.assertEqual('attention', self.host.calls[-1]['kind'])
            self.app.emit(self.sid, 'request_closed', {'id': 'reused-cli-id'})
        self.assertEqual(2, len(self.host.calls))
        self.assertNotEqual(self.host.calls[0]['notification_id'], self.host.calls[1]['notification_id'])
        self.app.emit(self.sid, 'choice', {
            'schemaVersion': 1, 'id': 'business-style-choice', 'kind': 'html-report-style',
            'responseMode': 'next-user-message', 'question': '디자인을 선택해 주세요.',
            'options': [{'id': 'minimalism', 'label': '미니멀리즘'}], 'allowCustom': True})
        self.assertEqual(2, len(self.host.calls))  # Tool turn still running.
        self.app.emit(self.sid, 'result', {'sessionId': 'fake-cli-session'})
        self.assertEqual(3, len(self.host.calls))
        self.assertEqual('attention', self.host.calls[-1]['kind'])
        self.assertEqual(3, self.app.desktop.snapshot()['unreadCount'])
        self.app.notifier.set_visible.assert_called_once_with(False)
        self.assertFalse(self.bridge.closed)

    def test_plain_final_answer_not_parsed_as_permission_and_still_delivers_completion(self):
        self.hide()
        self.app.emit(self.sid, 'assistant', {'text': '다음 분석 방향을 선택해 주세요: A 또는 B.'})
        self.app.emit(self.sid, 'result', {'sessionId': 'fake-cli-session'})
        self.assertEqual(['completed'], [call['kind'] for call in self.host.calls])
        self.assertEqual({}, self.item['requests'])
        self.assertIsNone(self.item.get('choice'))

    def test_hidden_question_and_approval_deliver_short_summary_without_raw_tool_content(self):
        self.hide()
        self.app.emit(self.sid, 'request', {'id': 'question', 'tool': 'AskUserQuestion', 'input': {
            'questions': [{'question': '보고서 형식을 골라 주세요', 'options': [{'label': 'PRIVATE OPTION'}]}]}})
        self.assertEqual('보고서 형식을 골라 주세요', self.host.calls[-1]['summary'])
        self.assertNotIn('PRIVATE OPTION', str(self.host.calls))
        self.app.emit(self.sid, 'request_closed', {'id': 'question'})
        self.app.emit(self.sid, 'request', {'id': 'approval', 'tool': 'Bash', 'input': {
            'description': '검증 스크립트 실행', 'command': 'PRIVATE COMMAND token=glpat-abcdefghijk'}})
        self.assertEqual('검증 스크립트 실행', self.host.calls[-1]['summary'])
        self.assertNotIn('PRIVATE COMMAND', str(self.host.calls))
        self.assertNotIn('glpat-', str(self.host.calls))

    def test_answered_deferred_summary_is_not_delivered_after_request_closes(self):
        self.hide()
        self.host.result = 'busy'
        self.app.emit(self.sid, 'request', {'id': 'question', 'tool': 'AskUserQuestion', 'input': {
            'questions': [{'question': '보고서 형식을 골라 주세요'}]}})
        self.assertEqual(1, len(self.host.calls))
        self.app.emit(self.sid, 'request_closed', {'id': 'question'})
        self.host.result = True
        self.app.desktop.flush_pending()
        self.assertEqual(1, len(self.host.calls))
        self.assertEqual({}, self.app.desktop._pending)

    def test_hidden_queue_continues_and_complete_quit_closes_cli_and_notification_retry(self):
        self.hide()
        self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': '이어서 분석', 'attachments': []})
        self.app.dispatch.pump()
        self.assertEqual(['이어서 분석'], self.bridge.sent)
        self.assertEqual('completed', self.host.calls[-1]['kind'])
        self.assertTrue(self.app.close())
        self.assertTrue(self.bridge.closed)
        self.assertTrue(self.app.shutdown_status()['closed'])
        self.assertTrue(self.app.desktop._closed)
        self.assertEqual({}, self.app.desktop._pending)

    def test_hidden_once_schedule_runs_at_due_time_without_renderer_or_presence_poll(self):
        self.hide()
        now = time.time()
        self.app.dispatch.action(self.sid, {'action': 'schedule', 'text': '예약 분석', 'attachments': [],
            'schedule': {'kind': 'once', 'runAt': now + 60}})
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.pump()
        self.app.dispatch.pump()
        self.assertEqual(['예약 분석'], self.bridge.sent)
        self.assertEqual('completed', self.host.calls[-1]['kind'])


if __name__ == '__main__':
    unittest.main()
