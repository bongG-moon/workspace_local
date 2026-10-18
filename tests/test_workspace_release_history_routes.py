"""Authenticated history routes, using an isolated demo app and loopback only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server, WORKSPACE_VERSION
from local_app.ui_health import frontend_row


class ReleaseHistoryRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.original = self.app.release_history
        self.manager = self.app.release_history = Mock()
        self.snapshot = {'status': 'ready', 'currentVersion': WORKSPACE_VERSION,
                         'source': {'provider': 'github', 'label': 'GitHub'}, 'entries': [],
                         'checkedAt': None, 'error': None}
        self.manager.snapshot.return_value = self.snapshot
        self.manager.check.return_value = self.snapshot
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.close()
        self.original.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, body=None, *, auth=True, origin=None, path='/api/app-update-history'):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if origin:
            headers['Origin'] = origin
        request = Request(self.server.origin + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_snapshot_requires_auth_and_never_starts_a_check(self):
        self.assertEqual(403, self.request(auth=False)[0])
        self.assertEqual((200, self.snapshot), self.request())
        self.manager.snapshot.assert_called_once_with()
        self.manager.check.assert_not_called()

    def test_explicit_check_and_force_are_the_only_accepted_actions(self):
        for force in (False, True):
            self.assertEqual((200, self.snapshot), self.request({'action': 'check', 'force': force}))
            self.manager.check.assert_called_with(force=force)
        self.assertEqual(2, self.manager.check.call_count)
        self.manager.reset_mock()
        for body in ({}, {'action': 'check'}, {'action': 'install', 'force': False},
                     {'action': 'check', 'force': 1}, {'action': 'check', 'force': 'true'},
                     {'action': 'check', 'force': False, 'url': 'https://evil.example'},
                     {'action': 'check', 'force': False, 'token': 'secret'}):
            with self.subTest(body=body):
                self.assertEqual(400, self.request(body)[0])
        self.manager.check.assert_not_called()

    def test_checks_retain_auth_origin_and_shutdown_boundaries(self):
        body = {'action': 'check', 'force': True}
        self.assertEqual(403, self.request(body, auth=False)[0])
        self.assertEqual(403, self.request(body, origin='https://evil.example')[0])
        self.manager.check.assert_not_called()
        self.assertTrue(self.app.close())
        self.manager.close.assert_called_once_with()
        self.assertEqual(409, self.request(body)[0])
        self.manager.check.assert_not_called()

    def test_bootstrap_does_not_trigger_history_fetch_and_demo_reads_bundled_notes(self):
        self.assertEqual(200, self.request(path='/api/bootstrap')[0])
        self.manager.check.assert_not_called()
        with patch('local_app.release_history.source_download') as download:
            snapshot = self.original.check(force=True)
        download.assert_not_called()
        current = next(e for e in snapshot['entries'] if e['version'] == WORKSPACE_VERSION)
        self.assertEqual('bundled', current['origin'])
        self.assertIsNone(current['publishedAt'])

    def test_history_assets_are_served_and_startup_health_accepts_module(self):
        for name, mime, marker in [('release-history.js', 'text/javascript', b'WorkspaceReleaseHistory'),
                                   ('release-history.css', 'text/css', b'release-history')]:
            with self.subTest(name=name), urlopen(self.server.origin + '/' + name, timeout=4) as response:
                self.assertEqual(200, response.status)
                self.assertIn(mime, response.headers['Content-Type'])
                self.assertIn(marker, response.read())
        row = frontend_row({'documentId': 'history-fixture', 'event': 'module',
                            'module': 'release-history', 'status': 'ready'})
        self.assertEqual('release-history', row['module'])


if __name__ == '__main__':
    unittest.main()
