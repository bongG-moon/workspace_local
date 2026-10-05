"""Cancelling quit must preserve work; confirming quit closes only owned work."""
import re
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app.server import AppClosing
from tests import test_workspace_shutdown as shutdown_fixture


class QuitConfirmationTests(unittest.TestCase):
    setUp = shutdown_fixture.ShutdownTests.setUp
    tearDown = shutdown_fixture.ShutdownTests.tearDown
    request = shutdown_fixture.ShutdownTests.request
    wait_closing = shutdown_fixture.ShutdownTests.wait_closing

    def test_active_work_requires_confirmation_without_touching_services_or_queue(self):
        item = self.app.get(self.sid)
        bridge = Mock(closed=False, busy=False, stopping=False, pending={})
        bridge.close.return_value = True
        item['bridge'] = bridge
        queue_before = self.app.dispatch.snapshot(self.sid)
        with patch.object(self.app.app_updates, 'close') as update_close, \
                patch.object(self.app.dispatch, 'stop') as dispatch_stop, \
                patch.object(self.app.notifier, 'close') as notifier_close:
            for state in ('starting', 'running', 'approval', 'question', 'stopping'):
                with self.subTest(state=state):
                    item['state'] = state
                    code, result = self.request('/api/quit', {})
                    self.assertEqual(409, code)
                    self.assertEqual('quit_confirmation_required', result['code'])
                    self.assertEqual('작업 중인 내용이 있습니다. 그래도 종료하시겠습니까?', result['message'])
                    self.assertFalse(result['closing'])
                    self.assertFalse(result['closed'])
                    self.assertEqual(state, item['state'])
            update_close.assert_not_called()
            dispatch_stop.assert_not_called()
            notifier_close.assert_not_called()
        bridge.close.assert_not_called()
        self.assertEqual(queue_before, self.app.dispatch.snapshot(self.sid))

    def test_cancel_consumes_one_use_challenge_and_keeps_work_running(self):
        self.app.get(self.sid)['state'] = 'running'
        identifier = self.app.begin_quit_confirmation()
        self.assertTrue(re.fullmatch(r'[0-9a-f]{32}', identifier))
        self.assertEqual(identifier, self.app.begin_quit_confirmation())
        code, result = self.request('/api/quit', {'confirmed': False, 'confirmationId': identifier})
        self.assertEqual(200, code)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['closing'])
        self.assertEqual('running', self.app.get(self.sid)['state'])
        self.assertFalse(self.app.dispatch.stopped.is_set())
        code, result = self.request('/api/quit', {'confirmed': True, 'confirmationId': identifier})
        self.assertEqual(409, code)
        self.assertEqual('quit_confirmation_expired', result['code'])
        self.assertFalse(result['closing'])
        self.assertNotEqual(identifier, self.app.begin_quit_confirmation())

    def test_confirmed_quit_closes_all_owned_bridges_and_preserves_history(self):
        second = self.app.create(str(self.root), True)['id']
        bridges = []
        for sid in (self.sid, second):
            item = self.app.get(sid)
            item['state'] = 'running'
            item['messages'].append({'role': 'user', 'text': 'preserved conversation'})
            self.app.save(sid)
            bridge = Mock(closed=False, busy=True, stopping=False, pending={})
            bridge.close.return_value = True
            item['bridge'] = bridge
            bridges.append(bridge)
        identifier = self.app.begin_quit_confirmation()
        code, result = self.request('/api/quit', {'confirmed': True, 'confirmationId': identifier})
        self.assertEqual(200, code)
        self.assertTrue(result['closed'])
        for bridge in bridges:
            bridge.close.assert_called_once()
        self.assertEqual(2, len(self.app.sessions))
        self.assertTrue(all(item['messages'][0]['text'] == 'preserved conversation' for item in self.app.sessions.values()))

    def test_confirmation_flag_and_expired_challenges_cannot_bypass_confirmation(self):
        self.app.get(self.sid)['state'] = 'running'
        for value in (1, 'true', None, []):
            with self.subTest(value=value):
                self.assertEqual(400, self.request('/api/quit', {'confirmed': value})[0])
                self.assertFalse(self.app.shutdown_status()['closing'])
        identifier = self.app.begin_quit_confirmation()
        self.app._quit_confirmation['expires'] = time.monotonic() - 1
        for candidate in (identifier, 'not-a-challenge', 'a' * 32):
            code, result = self.request('/api/quit', {'confirmed': True, 'confirmationId': candidate})
            self.assertEqual(409, code)
            self.assertEqual('quit_confirmation_expired', result['code'])
            self.assertFalse(result['closing'])

    def test_confirmed_quit_releases_an_inflight_connection_waiter_before_draining(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def connect(sid):
            entered.set()
            release.wait(3)
            return {'ok': True}
        self.app.connect = connect
        bridge = Mock(closed=False, busy=False, stopping=False, pending={})
        bridge.close.side_effect = lambda: (release.set() or True)
        self.app.get(self.sid)['bridge'] = bridge
        preparing = self.pool.submit(self.request, '/api/connect', {'id': self.sid})
        self.assertTrue(entered.wait(2))
        self.assertEqual(409, self.request('/api/quit', {})[0])
        bridge.close.assert_not_called()
        start = time.monotonic()
        code, result = self.request('/api/quit', {'confirmed': True})
        self.assertEqual(200, code)
        self.assertTrue(result['closed'])
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(200, preparing.result(3)[0])

    def test_unresponsive_operation_is_bounded_and_retry_stays_truthful(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def reconnect(sid):
            entered.set(); release.wait(3)
            return {'ok': True}
        self.app.reconnect = reconnect
        earlier = self.pool.submit(self.request, '/api/reconnect', {'id': self.sid})
        self.assertTrue(entered.wait(2))
        with patch('local_app.server.SHUTDOWN_DRAIN_TIMEOUT', .05):
            start = time.monotonic()
            code, result = self.request('/api/quit', {'confirmed': True})
        self.assertEqual(503, code)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(result['closed'])
        self.assertIn({'code': 'operations_pending'}, result['shutdownIssues'])
        with self.assertRaises(AppClosing):
            self.app.send(self.sid, 'must not start after shutdown admission', [])
        release.set()
        self.assertEqual(200, earlier.result(3)[0])
        self.assertTrue(self.request('/api/quit', {})[1]['closed'])

    def test_work_starting_after_dialog_was_opened_is_included_when_confirmed(self):
        identifier = self.app.begin_quit_confirmation()
        item = self.app.get(self.sid)
        item['state'] = 'running'
        bridge = Mock(closed=False, busy=True, stopping=False, pending={})
        bridge.close.return_value = True
        item['bridge'] = bridge
        code, result = self.request('/api/quit', {'confirmed': True, 'confirmationId': identifier})
        self.assertEqual(200, code)
        self.assertTrue(result['closed'])
        bridge.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
