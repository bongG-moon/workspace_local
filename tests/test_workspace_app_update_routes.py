"""Local update controls retain the application's authentication/lifecycle boundary."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server, WORKSPACE_VERSION


class AppUpdateRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.original = self.app.app_updates
        self.manager = self.app.app_updates = Mock()
        self.snapshot = {'currentVersion': WORKSPACE_VERSION, 'status': 'available',
                         'release': {'version': '9.0.0'}, 'canInstall': True}
        for name in ('snapshot', 'check', 'configure', 'install'):
            getattr(self.manager, name).return_value = self.snapshot
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

    def request(self, body=None, *, auth=True, origin=None, path='/api/app-update'):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if origin:
            headers['Origin'] = origin
        req = Request(self.server.origin + path, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(req, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_release_status_requires_local_capability(self):
        self.assertEqual(self.request(auth=False)[0], 403)
        self.manager.check.assert_not_called()
        self.assertEqual(self.request(), (200, self.snapshot))

    def test_install_rejects_missing_auth_and_foreign_origin(self):
        body = {'action': 'install', 'version': '9.0.0'}
        self.assertEqual(self.request(body, auth=False)[0], 403)
        self.assertEqual(self.request(body, origin='https://example.com')[0], 403)
        self.manager.install.assert_not_called()

    def test_client_cannot_supply_download_or_command(self):
        for field in ('url', 'path', 'command'):
            with self.subTest(field=field):
                body = {'action': 'install', 'version': '9.0.0', field: 'untrusted'}
                self.assertEqual(self.request(body)[0], 400)
        self.manager.install.assert_not_called()

    def test_only_explicit_install_action_reaches_installer(self):
        self.request({'action': 'check'})
        self.request({'action': 'configure', 'autoCheck': False})
        self.manager.check.assert_called_once_with(manual=True)
        self.manager.configure.assert_called_once_with(False)
        self.manager.install.assert_not_called()
        self.assertEqual(self.request({'action': 'install', 'version': '9.0.0'}),
                         (200, self.snapshot))
        self.manager.install.assert_called_once_with('9.0.0')

    def test_window_startup_check_uses_automatic_check_contract_only(self):
        self.assertEqual(self.request({'action': 'startup'}), (200, self.snapshot))
        self.manager.check.assert_called_once_with(startup=True)
        self.manager.configure.assert_not_called()
        self.manager.install.assert_not_called()

    def test_window_startup_check_keeps_auth_origin_and_input_boundaries(self):
        body = {'action': 'startup'}
        self.assertEqual(self.request(body, auth=False)[0], 403)
        self.assertEqual(self.request(body, origin='https://example.com')[0], 403)
        for field in ('manual', 'force', 'url', 'version', 'autoCheck'):
            with self.subTest(field=field):
                self.assertEqual(self.request({**body, field: True})[0], 400)
        self.manager.check.assert_not_called()
        self.manager.configure.assert_not_called()
        self.manager.install.assert_not_called()

    def test_regular_status_poll_does_not_signal_a_window_startup(self):
        self.assertEqual(self.request(), (200, self.snapshot))
        self.manager.check.assert_called_once_with()

    def test_shutdown_cancels_download_and_rejects_install(self):
        self.assertTrue(self.app.close())
        self.manager.close.assert_called_once()
        self.assertEqual(self.request({'action': 'install', 'version': '9.0.0'})[0], 409)
        self.manager.install.assert_not_called()

    def test_bootstrap_exposes_update_status_without_starting_download(self):
        code, boot = self.request(path='/api/bootstrap')
        self.assertEqual(code, 200)
        self.assertEqual(boot['appUpdate'], self.snapshot)
        self.manager.install.assert_not_called()

    def test_update_assets_are_served_with_browser_content_types(self):
        for name, mime, marker in [('app-updates.js', 'text/javascript', b'WorkspaceAppUpdates'),
                                   ('app-updates.css', 'text/css', b'.app-update-dialog')]:
            with self.subTest(name=name):
                with urlopen(self.server.origin + '/' + name, timeout=4) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(mime, response.headers['Content-Type'])
                    self.assertIn(marker, response.read())

    def test_install_callback_keeps_state_and_headless_mode(self):
        cancelled = threading.Event()
        self.app._upgrade_headless = True
        with patch('local_app.update_install.stage_and_launch', return_value={'status': 'launching'}) as stage:
            result = self.app._install_app_update('9.0.0', b'zip', 'a' * 64, cancel=cancelled)
        self.assertEqual(result['status'], 'launching')
        stage.assert_called_once_with(self.app.state, WORKSPACE_VERSION, '9.0.0', b'zip', 'a' * 64,
                                      demo=True, no_browser=True, cancel=cancelled, launcher_bytes=None)

    def test_install_callback_keeps_verified_launcher_bytes(self):
        with patch('local_app.update_install.stage_and_launch', return_value={'status': 'launching'}) as stage:
            self.app._install_app_update('9.0.0', b'zip', 'a' * 64, launcher_bytes=b'MZfixture')
        self.assertEqual(b'MZfixture', stage.call_args.kwargs['launcher_bytes'])


if __name__ == '__main__':
    unittest.main()
