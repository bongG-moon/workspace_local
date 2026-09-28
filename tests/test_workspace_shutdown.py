"""Shutdown acknowledgement must mean owned work has finished cleaning up."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.sid = self.app.create(str(self.root), True)['id']
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.releases = []
        self.pool = ThreadPoolExecutor(max_workers=4)

    def tearDown(self):
        for event in self.releases:
            event.set()
        self.pool.shutdown(wait=True)
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, route, data=None, authorized=True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + route,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers=headers)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def delayed_bridge(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def close():
            entered.set()
            if not release.wait(3):
                return False
            return True

        bridge = Mock(closed=False)
        bridge.close.side_effect = close
        self.app.get(self.sid)['bridge'] = bridge
        return bridge, entered, release

    def wait_closing(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status, data = self.request('/api/bootstrap')
            if data.get('closing'):
                return data
            time.sleep(.01)
        self.fail('shutdown did not close admission')

    def test_quit_ack_waits_for_cleanup_and_rejects_new_work(self):
        bridge, entered, release = self.delayed_bridge()
        self.assertFalse(self.request('/api/bootstrap')[1]['closing'])
        quit_result = self.pool.submit(self.request, '/api/quit', {})
        self.assertTrue(entered.wait(2))
        self.assertFalse(quit_result.done())
        state = self.wait_closing()
        self.assertFalse(state['closed'])
        self.assertEqual('closing', state['shutdownState'])
        for route, payload in [('/api/create', {'workspace': str(self.root), 'trusted': True}),
                               ('/api/send', {'id': self.sid, 'text': 'new work'}),
                               ('/api/trust', {'id': self.sid, 'trusted': True}),
                               ('/api/companion', {'id': self.sid, 'action': 'apply'})]:
            with self.subTest(route=route):
                self.assertEqual(409, self.request(route, payload)[0])
        self.assertEqual(1, len(self.app.sessions))
        self.assertEqual([], self.app.get(self.sid)['messages'])
        # Read-only session status stays available throughout cleanup.
        self.assertEqual(200, self.request('/api/session?id=' + self.sid)[0])
        release.set()
        code, payload = quit_result.result(3)
        self.assertEqual(200, code)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['closed'])
        self.assertTrue(payload['closing'])
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.app.close())
        bridge.close.assert_called_once()

    def test_failed_child_cleanup_keeps_server_readable_without_success(self):
        for outcome in (False, None, RuntimeError('cleanup failed')):
            with self.subTest(outcome=outcome):
                # A retry must remain truthful while this child reports failure.
                app = LocalApp(self.root / str(type(outcome).__name__), demo=True)
                sid = app.create(str(self.root), True)['id']
                bridge = Mock(closed=True)
                if isinstance(outcome, Exception):
                    bridge.close.side_effect = outcome
                else:
                    bridge.close.return_value = outcome
                app.get(sid)['bridge'] = bridge
                self.server.app = app
                original = self.app
                self.app = app
                try:
                    for _ in range(2):
                        status, payload = self.request('/api/quit', {})
                        self.assertEqual(503, status)
                        self.assertFalse(payload['ok'])
                        self.assertFalse(payload['closed'])
                        self.assertEqual('failed', payload['shutdownState'])
                    self.assertTrue(self.request('/api/bootstrap')[1]['closing'])
                    self.assertTrue(self.thread.is_alive())
                    self.assertEqual(409, self.request('/api/create', {'trusted': True})[0])
                    self.assertEqual(2, bridge.close.call_count)
                finally:
                    self.app = original
                    self.server.app = original

    def test_concurrent_quit_requests_share_one_cleanup(self):
        bridge, entered, release = self.delayed_bridge()
        first = self.pool.submit(self.request, '/api/quit', {})
        self.assertTrue(entered.wait(2))
        second = self.pool.submit(self.request, '/api/quit', {})
        time.sleep(.05)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        release.set()
        for result in (first, second):
            status, payload = result.result(3)
            self.assertEqual(200, status)
            self.assertTrue(payload['closed'])
        bridge.close.assert_called_once()

    def test_failed_quit_can_retry_cleanup_without_reopening_mutations(self):
        bridge = Mock(closed=True)
        bridge.close.side_effect = [False, True]
        self.app.get(self.sid)['bridge'] = bridge
        self.assertEqual(503, self.request('/api/quit', {})[0])
        self.assertEqual(409, self.request('/api/send', {'id': self.sid, 'text': 'blocked'})[0])
        status, payload = self.request('/api/quit', {})
        self.assertEqual(200, status)
        self.assertTrue(payload['closed'])
        self.assertEqual(2, bridge.close.call_count)

    def test_quit_drains_request_accepted_before_shutdown(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def reconnect(sid):
            started.set()
            release.wait(3)
            return {'ok': True}

        self.app.reconnect = reconnect
        bridge = Mock(closed=False)
        bridge.close.return_value = True
        self.app.get(self.sid)['bridge'] = bridge
        earlier = self.pool.submit(self.request, '/api/reconnect', {'id': self.sid})
        self.assertTrue(started.wait(2))
        quitting = self.pool.submit(self.request, '/api/quit', {})
        self.wait_closing()
        bridge.close.assert_not_called()
        self.assertFalse(quitting.done())
        self.assertEqual(409, self.request('/api/reconnect', {'id': self.sid})[0])
        release.set()
        self.assertEqual(200, earlier.result(3)[0])
        self.assertTrue(quitting.result(3)[1]['closed'])
        bridge.close.assert_called_once()

    def test_unauthorized_quit_does_not_begin_shutdown(self):
        self.assertEqual(403, self.request('/api/quit', {}, authorized=False)[0])
        self.assertFalse(self.request('/api/bootstrap')[1]['closing'])


if __name__ == '__main__':
    unittest.main()
