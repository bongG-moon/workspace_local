"""Private registration is an installer/update-only, bounded one-shot side effect."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app import registration as module
from local_app.registration import RegistrationManager


def config(**changes):
    return {'schema': 1, 'baseUrl': 'http://example.invalid', 'key': 'a' * 64, **changes}


class FakeResponse(io.BytesIO):
    status = 201


def response(**changes):
    return FakeResponse(json.dumps({'service': 'P&T AI SQUARE', 'protocol': 'ax-registration-v1',
        'status': 'ok', 'authenticated': True, 'dryRun': False, 'persisted': True,
        'version': '0.23.46', **changes}).encode())


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.bundle / module.CONFIG_NAME).write_bytes(module.encoded(config()))
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            if manager._worker:
                manager._worker.join(3)
            manager.close()
        self.temp.cleanup()

    def manager(self, version='0.23.46', **options):
        manager = RegistrationManager(self.state, version, application_root=self.bundle, **options)
        self.managers.append(manager)
        return manager

    @staticmethod
    def finished(manager):
        if manager._worker:
            manager._worker.join(3)
            if manager._worker.is_alive():
                raise AssertionError('Registration worker did not complete')

    def test_config_normalizes_http_and_preserves_private_key(self):
        value = module.normalize(config(baseUrl='http://EXAMPLE.invalid:8080/site/'))
        self.assertEqual(value['baseUrl'], 'http://example.invalid:8080/site')
        self.assertEqual(module.from_bytes(module.encoded(value)), value)

    def test_config_rejects_open_schema_and_unsafe_destinations(self):
        for changes in ({'schema': True}, {'schema': 2}, {'key': 'short'}, {'key': 'a' * 257},
                        {'key': 'a' * 32 + '\n'}, {'extra': True},
                        {'baseUrl': 'file:///tmp/a'}, {'baseUrl': 'http://user:secret@example.invalid'},
                        {'baseUrl': 'http://example.invalid?a=secret'}, {'baseUrl': 'http://example.invalid#fragment'},
                        {'baseUrl': 'http://example.invalid/%2e%2e/secret'}, {'baseUrl': 'http://example.invalid:70000'},
                        {'baseUrl': 'http://example.invalid/unsafe\\path'}):
            with self.subTest(changes=list(changes)):
                with self.assertRaisesRegex(ValueError, '^' + module.CONFIG_ERROR):
                    module.normalize(config(**changes))

    def test_config_rejects_oversize_duplicate_and_invalid_utf8(self):
        for raw in (b' ' * (module.MAX_CONFIG + 1), b'\xff',
                    b'{"schema":1,"schema":1,"baseUrl":"http://example.invalid","key":"' + b'a' * 64 + b'"}'):
            with self.assertRaises(ValueError):
                module.from_bytes(raw)

    def test_employee_only_trims_and_limits_unicode_characters(self):
        for value, expected in [('  0012  ', '0012'), (' A B ', 'A B'), ('한글사번', '한글사번'),
                                ('  TEST1234\t', 'TEST1234'), ('1234567😀', '1234567😀')]:
            self.assertEqual(module.checked_employee(value), expected)
        for value in ('', ' \t ', '123456789', None, 1234, True):
            with self.assertRaises(ValueError):
                module.checked_employee(value)

    def test_no_config_means_no_ui_or_network_or_profile(self):
        (self.bundle / module.CONFIG_NAME).unlink()
        with patch.object(module, '_send') as send:
            manager = self.manager()
            manager.startup_ready(installer=True, updated=True)
            manager.prepare_update('0.23.47')
            self.assertFalse(manager.snapshot()['enabled'])
            self.assertFalse(manager.snapshot()['needsEmployee'])
            send.assert_not_called()
            self.assertFalse(manager.root.exists())

    def test_demo_ignores_config_and_creates_no_private_state(self):
        with patch.object(module, '_send') as send:
            manager = self.manager(demo=True)
            manager.startup_ready(installer=True, updated=True)
            self.assertFalse(manager.snapshot()['enabled'])
            self.assertFalse((self.state / module.CONFIG_NAME).exists())
            send.assert_not_called()

    def test_bundle_config_is_preserved_for_public_update_without_config(self):
        first = self.manager()
        self.assertTrue(first.snapshot()['enabled'])
        (self.bundle / module.CONFIG_NAME).unlink()
        self.assertTrue(self.manager().snapshot()['enabled'])
        self.assertEqual(module.from_bytes((self.state / module.CONFIG_NAME).read_bytes()), config())

    def test_new_private_bundle_rotates_saved_key(self):
        self.manager()
        new = config(key='b' * 64)
        (self.bundle / module.CONFIG_NAME).write_bytes(module.encoded(new))
        manager = self.manager()
        self.assertEqual(manager._config, new)
        self.assertEqual(module.from_bytes((self.state / module.CONFIG_NAME).read_bytes()), new)

    def test_invalid_explicit_bundle_does_not_fallback_to_old_key(self):
        self.manager()
        (self.bundle / module.CONFIG_NAME).write_bytes(b'{}')
        self.assertFalse(self.manager().snapshot()['enabled'])

    def test_invalid_profile_is_preserved_and_does_not_start_registration(self):
        path = self.state / 'registration/profile.json'
        path.parent.mkdir()
        path.write_bytes(b'not-json')
        manager = self.manager()
        manager.startup_ready(installer=True)
        self.assertFalse(manager.snapshot()['enabled'])
        self.assertEqual(path.read_bytes(), b'not-json')

    def test_invalid_lifecycle_is_preserved_and_fail_closed(self):
        path = self.state / 'registration/lifecycle.json'
        path.parent.mkdir()
        path.write_bytes(b'{"schema":1}')
        manager = self.manager()
        self.assertFalse(manager.snapshot()['enabled'])
        self.assertEqual(path.read_bytes(), b'{"schema":1}')

    def test_installer_waits_for_employee_after_startup_readiness(self):
        manager = self.manager()
        self.assertFalse(manager.snapshot()['needsEmployee'])
        manager.startup_ready(installer=True)
        self.assertTrue(manager.snapshot()['needsEmployee'])
        self.assertEqual(manager.snapshot()['pendingEvent'], 'initial_install')
        with patch.object(module, '_send', return_value=True) as send:
            manager.save_employee(' 0012 ')
            self.finished(manager)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], {'schema': 1, 'dryRun': False,
            'employeeId': '0012', 'event': 'initial_install', 'version': '0.23.46'})
        self.assertFalse(manager.snapshot()['needsEmployee'])
        self.assertTrue(manager.snapshot()['employeeRegistered'])

    def test_saving_before_ready_does_not_send(self):
        manager = self.manager()
        with patch.object(module, '_send', return_value=True) as send:
            manager.save_employee('TEST')
            send.assert_not_called()
            manager.startup_ready(installer=True)
            self.finished(manager)
            self.assertEqual(send.call_count, 1)

    def test_missing_employee_pending_is_restored_without_reprompting_on_ordinary_versions(self):
        first = self.manager()
        first.startup_ready(installer=True)
        second = self.manager()
        second.startup_ready(installer=True)
        self.assertTrue(second.snapshot()['needsEmployee'])
        replaced = self.manager('0.23.47')
        replaced.startup_ready(installer=True)
        self.assertFalse(replaced.snapshot()['needsEmployee'])

    def test_portable_first_launch_and_manual_copy_never_create_event(self):
        manager = self.manager()
        with patch.object(module, '_send', return_value=True) as send:
            manager.save_employee('TEST')
            manager.startup_ready()
            send.assert_not_called()
            self.assertIsNone(manager.snapshot()['pendingEvent'])
            replaced = self.manager('0.23.47')
            replaced.startup_ready()
            send.assert_not_called()

    def test_update_preparation_requires_employee_but_never_sends(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, module.EMPLOYEE_ERROR):
            manager.prepare_update('0.23.47')
        with patch.object(module, '_send') as send:
            manager.save_employee('TEST')
            manager.prepare_update('0.23.47')
            manager.startup_ready()
            send.assert_not_called()
        self.assertEqual(manager._lifecycle['prepared'], {'version': '0.23.47', 'previousVersion': '0.23.46'})

    def test_confirmed_update_sends_previous_version_once(self):
        manager = self.manager()
        manager.save_employee('TEST')
        manager.prepare_update('0.23.47')
        updated = self.manager('0.23.47')
        with patch.object(module, '_send', return_value=True) as send:
            updated.startup_ready(updated=True)
            self.finished(updated)
            updated.startup_ready(updated=True)
            self.finished(updated)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], {'schema': 1, 'dryRun': False,
            'employeeId': 'TEST', 'event': 'app_update', 'version': '0.23.47', 'previousVersion': '0.23.46'})

    def test_confirmed_update_from_old_app_prompts_if_no_employee(self):
        manager = self.manager()
        manager.startup_ready(updated=True, previous_version='0.23.45')
        self.assertTrue(manager.snapshot()['needsEmployee'])
        self.assertEqual(manager.snapshot()['pendingEvent'], 'app_update')

    def test_mismatched_stale_preparation_does_not_claim_update_on_manual_copy(self):
        manager = self.manager()
        manager.save_employee('TEST')
        manager.prepare_update('0.23.47')
        copied = self.manager('0.23.48')
        with patch.object(module, '_send') as send:
            copied.startup_ready()
            send.assert_not_called()

    def test_failure_is_consumed_across_restart_and_employee_change(self):
        manager = self.manager()
        manager.save_employee('TEST')
        with patch.object(module, '_send', side_effect=OSError('private data must not be logged')) as send:
            manager.startup_ready(installer=True)
            self.finished(manager)
            manager.save_employee('CHANGE')
            manager.startup_ready(installer=True)
            another = self.manager()
            another.startup_ready(installer=True, updated=True)
            self.finished(another)
            self.assertEqual(send.call_count, 1)
        receipt = json.loads((manager.root / 'attempts/0.23.46.json').read_bytes())
        self.assertEqual(receipt, {'schema': 1, 'attempted': True, 'confirmed': False})

    def test_status_and_ordinary_use_are_read_only(self):
        manager = self.manager()
        manager.save_employee('TEST')
        with patch.object(module, '_send') as send, patch.object(module, '_atomic') as write:
            for _ in range(20):
                manager.snapshot()
            send.assert_not_called()
            write.assert_not_called()
        self.assertNotIn('TEST', json.dumps(manager.snapshot()))
        self.assertNotIn('example.invalid', json.dumps(manager.snapshot()))
        self.assertNotIn('a' * 64, json.dumps(manager.snapshot()))

    def test_exclusive_claim_stops_two_independent_managers(self):
        first = self.manager()
        first.save_employee('TEST')
        second = self.manager()
        with patch.object(module, '_send', return_value=True) as send:
            threads = [threading.Thread(target=manager.startup_ready, kwargs={'installer': True})
                       for manager in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
            self.finished(first)
            self.finished(second)
            self.assertEqual(send.call_count, 1)

    def test_claim_survives_process_loss_before_request(self):
        manager = self.manager()
        manager.save_employee('TEST')
        attempt = manager.root / 'attempts/0.23.46.json'
        attempt.parent.mkdir()
        attempt.write_bytes(b'{"schema":1,"attempted":true}')
        with patch.object(module, '_send') as send:
            manager.startup_ready(installer=True)
            send.assert_not_called()

    def test_startup_and_close_never_wait_for_slow_network(self):
        manager = self.manager()
        manager.save_employee('TEST')
        entered, release = threading.Event(), threading.Event()
        def blocked(config, payload):
            entered.set()
            release.wait(2)
            return False
        with patch.object(module, '_send', side_effect=blocked):
            start = time.monotonic()
            manager.startup_ready(installer=True)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            manager.close()
            self.assertLess(time.monotonic() - start, 0.2)
            release.set()
            self.finished(manager)

    def test_local_claim_failure_never_sends(self):
        manager = self.manager()
        manager.save_employee('TEST')
        with patch.object(module.os, 'open', side_effect=PermissionError()), patch.object(module, '_send') as send:
            manager.startup_ready(installer=True)
            send.assert_not_called()

    def test_real_loopback_post_hmac_error_and_redirect_are_single_requests(self):
        for status in (201, 503, 302):
            received = []
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_POST(self):
                    raw = self.rfile.read(min(int(self.headers.get('Content-Length', '0')), 4096))
                    received.append(('POST', self.path, dict(self.headers), raw))
                    body = response().getvalue()
                    self.send_response(status)
                    if status == 302:
                        self.send_header('Location', '/must-not-be-followed')
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                def do_GET(self):
                    received.append(('GET', self.path, dict(self.headers), b''))
                    self.send_response(500)
                    self.end_headers()
            server = HTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = module._send(config(baseUrl=f'http://127.0.0.1:{server.server_port}'), {
                    'schema': 1, 'dryRun': False, 'employeeId': 'TEST',
                    'version': '0.23.46', 'event': 'initial_install'})
                self.assertEqual(result, status == 201)
                self.assertEqual(len(received), 1)
                method, request_path, headers, raw = received[0]
                self.assertEqual((method, request_path), ('POST', module.PATH))
                canonical = '\n'.join(('AX-REGISTRATION-V1', 'POST', module.PATH,
                    headers['X-Ax-Timestamp'], headers['X-Ax-Nonce'], hashlib.sha256(raw).hexdigest()))
                self.assertEqual(headers['X-Ax-Signature'],
                    hmac.new(b'a' * 64, canonical.encode(), hashlib.sha256).hexdigest())
                self.assertFalse(json.loads(raw)['dryRun'])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(3)


class ProtocolTests(unittest.TestCase):
    def payload(self):
        return {'schema': 1, 'dryRun': False, 'employeeId': 'TEST',
                'version': '0.23.46', 'event': 'app_update', 'previousVersion': '0.23.45'}

    def test_post_matches_existing_web_hmac_without_sending_key(self):
        with patch.object(module, 'build_opener') as build:
            build.return_value.open.return_value = response()
            self.assertTrue(module._send(config(), self.payload()))
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://example.invalid/api/ax-workspace/registrations')
        self.assertEqual(request.get_method(), 'POST')
        headers = {key.lower(): value for key, value in request.header_items()}
        canonical = '\n'.join(('AX-REGISTRATION-V1', 'POST', module.PATH,
            headers['x-ax-timestamp'], headers['x-ax-nonce'], hashlib.sha256(request.data).hexdigest()))
        self.assertEqual(headers['x-ax-signature'], hmac.new(b'a' * 64, canonical.encode(), hashlib.sha256).hexdigest())
        self.assertNotIn('a' * 64, request.data.decode())
        self.assertNotIn('a' * 64, json.dumps(headers))
        self.assertEqual(json.loads(request.data), self.payload())
        self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': module.REQUEST_TIMEOUT})
        self.assertIsInstance(build.call_args.args[0], module._NoRedirect)

    def test_redirect_handler_never_follows(self):
        self.assertIsNone(module._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://other.invalid'))

    def test_response_requires_exact_service_protocol_live_persistence(self):
        for changes in ({'service': 'other'}, {'protocol': 'other'}, {'status': 'error'},
                        {'authenticated': False}, {'persisted': False}, {'dryRun': True}, {'version': '0.23.45'}):
            with self.subTest(changes=changes), patch.object(module, 'build_opener') as build:
                build.return_value.open.return_value = response(**changes)
                self.assertFalse(module._send(config(), self.payload()))

    def test_response_size_is_bounded(self):
        large = FakeResponse(b' ' * (module.MAX_RESPONSE + 10))
        with patch.object(module, 'build_opener') as build:
            build.return_value.open.return_value = large
            self.assertFalse(module._send(config(), self.payload()))
        self.assertLessEqual(large.tell() if not large.closed else module.MAX_RESPONSE + 1, module.MAX_RESPONSE + 1)


if __name__ == '__main__':
    unittest.main()
