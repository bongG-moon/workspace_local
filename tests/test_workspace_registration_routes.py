"""Registration follows the local capability and update readiness boundaries."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server, _installed_launcher, main


class RegistrationRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.original = self.app.registration
        self.registration = self.app.registration = Mock()
        self.snapshot = {'enabled': True, 'employeeRegistered': False,
                         'needsEmployee': True, 'pendingEvent': 'initial_install'}
        self.registration.snapshot.return_value = self.snapshot
        self.registration.save_employee.return_value = {
            **self.snapshot, 'employeeRegistered': True, 'needsEmployee': False}
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

    def request(self, data=None, *, path='/api/app-registration', auth=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if origin:
            headers['Origin'] = origin
        request = Request(self.server.origin + path, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_status_requires_local_capability(self):
        self.assertEqual(self.request(auth=False)[0], 403)
        self.registration.snapshot.assert_not_called()
        self.assertEqual(self.request(), (200, self.snapshot))

    def test_save_requires_capability_and_same_origin(self):
        data = {'employeeId': 'TEST1234'}
        self.assertEqual(self.request(data, auth=False)[0], 403)
        self.assertEqual(self.request(data, origin='https://example.com')[0], 403)
        self.registration.save_employee.assert_not_called()

    def test_save_accepts_only_employee_id(self):
        for data in ({}, {'employeeId': 'TEST', 'key': 'not-a-key'},
                     {'employeeId': 'TEST', 'url': 'http://example.com'},
                     {'employeeId': 'TEST', 'version': '9.0.0'}):
            with self.subTest(fields=sorted(data)):
                self.assertEqual(self.request(data)[0], 400)
        self.registration.save_employee.assert_not_called()
        code, result = self.request({'employeeId': '  TEST1234  '})
        self.assertEqual(code, 200)
        self.assertTrue(result['employeeRegistered'])
        self.registration.save_employee.assert_called_once_with('  TEST1234  ')

    def test_bootstrap_and_reads_never_mark_readiness_or_send(self):
        self.assertEqual(self.request(path='/api/bootstrap')[1]['appRegistration'], self.snapshot)
        self.request()
        self.request(path='/api/app-update')
        self.registration.startup_ready.assert_not_called()
        self.registration.prepare_update.assert_not_called()
        self.registration.save_employee.assert_not_called()

    def test_update_cannot_begin_without_registration_when_required(self):
        self.registration.prepare_update.side_effect = ValueError('사번을 입력해 주세요.')
        with patch.object(self.app.app_updates, 'install') as install:
            code, _ = self.request({'action': 'install', 'version': '9.0.0'}, path='/api/app-update')
        self.assertEqual(code, 400)
        install.assert_not_called()

    def test_update_prepares_locally_before_existing_install(self):
        calls = Mock()
        calls.attach_mock(self.registration.prepare_update, 'prepare')
        with patch.object(self.app.app_updates, 'install', return_value={'status': 'downloading'}) as install:
            calls.attach_mock(install, 'install')
            code, result = self.request({'action': 'install', 'version': '9.0.0'}, path='/api/app-update')
        self.assertEqual((code, result), (200, {'status': 'downloading'}))
        self.assertEqual([call[0] for call in calls.mock_calls], ['prepare', 'install'])
        self.registration.startup_ready.assert_not_called()

    def test_shutdown_rejects_registration_and_new_update_intent(self):
        self.assertTrue(self.app.close())
        self.assertEqual(self.request({'employeeId': 'TEST'})[0], 409)
        self.assertEqual(self.request({'action': 'install', 'version': '9.0.0'}, path='/api/app-update')[0], 409)
        self.registration.save_employee.assert_not_called()
        self.registration.prepare_update.assert_not_called()
        self.registration.close.assert_called_once()

    def test_readiness_receives_verified_promotion_and_installer_classification(self):
        with patch('local_app.server._installed_launcher', return_value=True):
            self.app.registration_ready(updated=True)
        self.registration.startup_ready.assert_called_once_with(updated=True, installer=True)

    def test_registration_assets_have_correct_content_types(self):
        for name, mime in [('registration.js', 'text/javascript'), ('registration.css', 'text/css')]:
            with self.subTest(asset=name):
                with urlopen(self.server.origin + '/' + name, timeout=4) as response:
                    self.assertIn(mime, response.headers['Content-Type'])
                    self.assertGreater(len(response.read()), 0)


class InstallerClassificationTests(unittest.TestCase):
    def test_portable_and_missing_marker_do_not_count_as_install(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_installed_launcher())
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / 'AX-Workspace.exe'
            exe.write_bytes(b'MZfixture')
            with patch.dict(os.environ, {'COMPANY_WORKSPACE_RELAUNCH_ICON': str(exe)}, clear=True):
                self.assertFalse(_installed_launcher())
                marker = exe.with_name('AX-Workspace.install-id')
                marker.write_text('AX.Workspace.Desktop.Test', encoding='ascii')
                self.assertFalse(_installed_launcher())
                marker.write_text('AX.Workspace.Desktop', encoding='ascii')
                self.assertTrue(_installed_launcher())
                marker.write_bytes(b'AX.Workspace.Desktop' + b' ' * 128)
                self.assertFalse(_installed_launcher())

    def test_relative_or_non_executable_entry_is_not_an_install(self):
        for entry in ('AX-Workspace.exe', 'icon.ico'):
            with self.subTest(entry=entry), patch.dict(os.environ, {'COMPANY_WORKSPACE_RELAUNCH_ICON': entry}):
                self.assertFalse(_installed_launcher())


class RegistrationStartupOrderingTests(unittest.TestCase):
    def run_startup(self, *, confirmed=False, failure=None):
        with tempfile.TemporaryDirectory() as root:
            app, server, window = Mock(), Mock(), Mock()
            app.state = Path(root)
            app.token = 'synthetic-local-capability'
            app.appearance.snapshot.return_value = {'theme': 'light'}
            app.tray = None
            app.close.return_value = True
            server.origin, server.server_port = 'http://127.0.0.1:12345', 12345
            order = []

            def promote(*args, **kwargs):
                self.assertTrue((app.state / 'runtime.json').is_file())
                order.append('promoted')
                if failure:
                    raise failure
                return confirmed

            app.registration_ready.side_effect = lambda **kw: order.append('registration')
            app.dispatch.start.side_effect = lambda: order.append('dispatch')
            app.app_updates.start.side_effect = lambda: order.append('updates')
            server.serve_forever.side_effect = lambda **kw: order.append('serving')
            with patch('sys.argv', ['workspace', '--state', root, '--no-browser']), \
                    patch('local_app.startup.verify_process', return_value='normal'), \
                    patch('local_app.server.LocalApp', return_value=app), \
                    patch('local_app.server.Server', return_value=server), \
                    patch('local_app.native_window.DesktopHost', return_value=window), \
                    patch('local_app.update_install.confirm_running_update', side_effect=promote):
                main()
            return app, order

    def test_successful_update_is_confirmed_before_registration_and_serving(self):
        app, order = self.run_startup(confirmed=True)
        self.assertEqual(order, ['promoted', 'registration', 'dispatch', 'updates', 'serving'])
        app.registration_ready.assert_called_once_with(updated=True)

    def test_ordinary_launch_is_not_described_as_successful_update(self):
        app, _ = self.run_startup(confirmed=False)
        app.registration_ready.assert_called_once_with(updated=False)

    def test_failed_update_confirmation_never_dispatches_registration(self):
        app, order = self.run_startup(failure=ValueError('synthetic invalid pending install'))
        self.assertEqual(order, ['promoted', 'dispatch', 'updates', 'serving'])
        app.registration_ready.assert_not_called()


if __name__ == '__main__':
    unittest.main()
