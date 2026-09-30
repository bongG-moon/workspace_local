"""Notification routing without a desktop process, CLI, or personal settings."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid

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
                        'message': MESSAGES['attention'], 'sessionId': self.sid,
                        'privatePrompt': 'must never leave the server',
                        'requests': [{'command': 'private command'}]}

    def test_card_receives_only_notification_fields_and_original_callback(self):
        callback = lambda: None
        self.assertTrue(self.app._notify_desktop(self.payload, callback))
        self.assertEqual([{'title': self.payload['title'], 'message': MESSAGES['attention'],
                           'kind': 'attention', 'notification_id': 'a' * 64,
                           'on_click': callback}], self.app._desktop_window.calls)
        self.assertEqual([], self.app.tray.calls)

    def test_refused_card_does_not_escape_suppression_through_tray(self):
        self.app._desktop_window.result = False
        self.assertFalse(self.app._notify_desktop(self.payload, lambda: None))
        self.assertEqual(1, len(self.app._desktop_window.calls))
        self.assertEqual([], self.app.tray.calls)

    def test_unsupported_card_falls_back_to_legacy_tray_with_original_callback(self):
        self.app._desktop_window.result = None
        callback = lambda: None
        self.assertTrue(self.app._notify_desktop(self.payload, callback))
        self.assertEqual([{'title': self.payload['title'], 'message': MESSAGES['attention'],
                           'on_click': callback}], self.app.tray.calls)

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


if __name__ == '__main__':
    unittest.main()
