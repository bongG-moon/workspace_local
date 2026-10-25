"""File-open handoff, lifecycle and same-user HTTP boundary regressions."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.extension_install import ExtensionImporter, REVIEW_TTL
from local_app.extension_open_client import handoff, runtime_connection, OpenError
from local_app.extensions import ExtensionHost, ExtensionError
from local_app.server import LocalApp, Server
from tests.test_extension_import import package


class IncomingPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = ExtensionHost(self.root / 'profile' / 'state', runner=Mock())
        self.importer = ExtensionImporter(self.host)
        self.addCleanup(self.importer.close)
        self.archive = self.root / '확장 package.axextension'
        package(self.archive)

    def receive(self):
        return self.importer.receive(self.archive.read_bytes())

    def test_caller_file_can_disappear_and_review_survives_ui_reload(self):
        receipt = self.receive()
        self.archive.unlink()
        review = self.importer.pending_review(receipt['requestId'])
        with self.assertRaises(ExtensionError):
            self.importer.confirm('incorrect-token', True)
        self.assertEqual(review, self.importer.pending_review(receipt['requestId']))
        self.assertFalse(self.host.root.exists())
        staged = Path(self.importer._pending['path'])
        self.assertTrue(staged.is_file())
        self.importer.confirm(review['token'], True)
        self.assertFalse(staged.exists())
        self.assertIsNone(self.importer.status())
        self.assertEqual('1.0.0', self.host._verified('example.extension')[1]['version'])
        self.host.runner.assert_not_called()

    def test_one_pending_request_and_cancel_expiry_clean_copy(self):
        first = self.receive()
        first_path = Path(self.importer._pending['path'])
        with self.assertRaises(ExtensionError):
            self.receive()
        with self.assertRaises(ExtensionError):
            self.importer.prepare(lambda _: self.fail('picker must not replace incoming request'))
        self.assertEqual(first['requestId'], self.importer.status()['requestId'])
        review = self.importer.pending_review(first['requestId'])
        self.importer.cancel(review['token'])
        self.assertFalse(first_path.exists())
        second = self.receive()
        second_path = Path(self.importer._pending['path'])
        self.host.clock = lambda: self.importer._pending['expires'] + 1
        self.assertIsNone(self.importer.status())
        self.assertFalse(second_path.exists())
        with self.assertRaises(ExtensionError):
            self.importer.pending_review(second['requestId'])

    def test_invalid_or_changed_package_never_partially_installs(self):
        with self.assertRaises(ExtensionError):
            self.importer.receive(b'not a ZIP')
        self.assertIsNone(self.importer._scratch)
        self.assertFalse(self.host.root.exists())
        receipt = self.receive()
        review = self.importer.pending_review(receipt['requestId'])
        Path(self.importer._pending['path']).write_bytes(b'changed')
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], True)
        self.assertFalse(self.host.root.exists())
        self.assertIsNone(self.importer._scratch)

    def test_active_worker_cannot_be_replaced_and_key_is_preserved(self):
        receipt = self.receive()
        review = self.importer.pending_review(receipt['requestId'])
        self.importer.confirm(review['token'], True)
        data = self.host.data_root / 'example.extension'
        (data / 'key.dpapi').write_bytes(b'private-preserve')
        package(self.archive, version='1.0.1', origin='https://other.example')
        receipt = self.receive()
        review = self.importer.pending_review(receipt['requestId'])
        self.host._active.add('example.extension')
        with self.assertRaises(ExtensionError):
            self.importer.confirm(review['token'], True)
        self.host._active.clear()
        self.assertEqual('1.0.0', self.host._verified('example.extension')[1]['version'])
        receipt = self.receive()
        review = self.importer.pending_review(receipt['requestId'])
        self.importer.confirm(review['token'], True)
        self.assertEqual(b'private-preserve', (data / 'key.dpapi').read_bytes())
        self.assertEqual('https://square.example', json.loads((data / 'connection.json').read_text())['baseUrl'])

    def test_status_does_not_wait_for_verification_or_initialize_unused_host(self):
        self.assertIsNone(self.host.pending_import())
        self.assertIsNone(self.host._importer)
        self.assertFalse(self.host.root.parent.exists())
        with self.importer._lock:
            self.assertIsNone(self.importer.status())

    def test_copy_failure_and_expiry_without_screen_cleanup_owned_package(self):
        raw = self.archive.read_bytes()
        with patch.object(Path, 'write_bytes', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.importer.receive(raw)
        self.assertIsNone(self.importer._scratch)
        self.receive()
        staged = Path(self.importer._pending['path'])
        expired = self.importer._pending['expires'] + 1
        self.host.clock = lambda: expired
        self.importer._expire()
        self.assertFalse(staged.exists())
        self.assertIsNone(self.importer._pending)
        self.assertIsNone(self.importer._expiry)


class ExtensionOpenHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.archive = self.root / 'space 한글.axextension'
        package(self.archive)
        self.app = LocalApp(self.state, demo=True, managed_workspace_root=self.root / 'managed')
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.state.mkdir(exist_ok=True)
        (self.state / 'runtime.json').write_text(json.dumps({'url': self.server.origin + '/#token=' + self.app.token}))

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.app.close(force=True)

    def request(self, path, data, *, auth=True, origin=None, content_type='application/octet-stream'):
        headers = {'Content-Type': content_type, 'Origin': origin or self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        with urlopen(Request(self.server.origin + path, data=data, headers=headers), timeout=5) as response:
            return json.loads(response.read())

    def test_live_handoff_is_auth_then_pending_review_only(self):
        before = self.app.drafts.snapshot()
        receipt = handoff(self.state, str(self.archive))
        self.assertTrue(receipt['accepted'])
        self.assertEqual(receipt['requestId'], self.app.attention()['extensionImport']['requestId'])
        self.assertEqual(1, self.app.bootstrap()['extensionImportProtocol'])
        self.assertEqual(before, self.app.drafts.snapshot())
        self.assertFalse(self.app.extensions.root.exists())
        review = self.request('/api/extensions/import/pending', json.dumps({'requestId':receipt['requestId']}).encode(), content_type='application/json')
        self.assertEqual('Example extension', review['extension']['name'])
        self.assertEqual(review, self.app.extensions.import_package('pending', {'requestId':receipt['requestId']}, None))

    def test_browser_cannot_supply_path_or_skip_auth(self):
        for kwargs in ({'auth':False}, {'origin':'https://other.example'}, {'content_type':'application/json'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPError) as caught:
                self.request('/api/extensions/import/file', b'{"path":"C:/private"}', **kwargs)
            self.assertEqual(403, caught.exception.code)
        self.assertIsNone(self.app.extensions._importer)

    def test_same_version_old_host_is_rejected_before_upload(self):
        original = self.app.bootstrap
        with patch.object(self.app, 'bootstrap', side_effect=lambda: {k:v for k,v in original().items() if k!='extensionImportProtocol'}):
            with self.assertRaisesRegex(OpenError, '완전히 종료'):
                handoff(self.state, str(self.archive))
        self.assertIsNone(self.app.extensions._importer)

    def test_runtime_never_follows_nonlocal_address_or_token_leak(self):
        for url in ['https://example.org/#token=' + 's'*32,
                    'http://127.0.0.1:80/private#token=' + 's'*32,
                    'http://user@127.0.0.1:80/#token=' + 's'*32]:
            (self.state/'runtime.json').write_text(json.dumps({'url':url}))
            with self.assertRaises(OpenError) as error:
                runtime_connection(self.state)
            self.assertNotIn('s'*32, str(error.exception))


if __name__ == '__main__':
    unittest.main()
