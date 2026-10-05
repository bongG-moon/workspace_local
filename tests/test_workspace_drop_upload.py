import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from local_app.attachments import AttachmentStore, MAX_UPLOAD
from local_app.completions import ARCHIVE_FILE_TYPES, REFERENCE_FILE_TYPES
from local_app.server import LocalApp, Server


class DropUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def upload(self, name='한글 & 자료.md', body=b'dropped', *, auth=True, sid=None, headers=None):
        fields = {'Content-Type': 'application/octet-stream', 'X-File-Name': quote(name)}
        if auth:
            fields['Authorization'] = 'Bearer ' + self.app.token
        fields.update(headers or {})
        request = Request(self.server.origin + '/api/attachments/upload?id=' + (sid or self.sid),
                          data=body, headers=fields)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_drop_copies_exact_bytes_without_turn_or_workspace_changes(self):
        code, result = self.upload()
        self.assertEqual(200, code)
        self.assertTrue(result['copied'])
        path = Path(result['path'])
        self.assertEqual(b'dropped', path.read_bytes())
        self.assertTrue(path.is_relative_to(self.app.state / 'attachments' / self.sid))
        self.assertEqual('한글 & 자료.md', path.name)
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertIsNone(self.app.get(self.sid)['bridge'])
        code, other = self.upload(body=b'other')
        self.assertEqual(200, code)
        self.assertNotEqual(result['path'], other['path'])
        self.assertEqual(b'dropped', path.read_bytes())

    def test_unauthorized_cross_origin_and_missing_task_cannot_write(self):
        # Repeated immediate rejection used to race unread-body socket close.
        for _ in range(8):
            self.assertEqual(403, self.upload(auth=False)[0])
            self.assertEqual(403, self.upload(headers={'Origin': 'https://example.com'})[0])
        self.assertEqual(400, self.upload(sid='unknown')[0])
        self.assertFalse((self.app.state / 'attachments').exists())

    def prepare(self, *, name='bundle.tar.gz', size=8, sid=None, auth=True, extra=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        data = {'id': sid or self.sid, 'name': name, 'size': size, **(extra or {})}
        request = Request(self.server.origin + '/api/attachments/prepare',
                          data=json.dumps(data).encode(), headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_path_escape_reserved_names_and_unsupported_types_are_rejected(self):
        for name in ['../escape.md', r'folder\escape.txt', 'x.txt:stream', 'CON.txt', 'NUL.md',
                     'bad.txt.', 'a.dll', 'x\n.md']:
            with self.subTest(name=name):
                self.assertEqual(400, self.upload(name)[0])
        self.assertFalse((self.app.state / 'attachments').exists())

    def test_executable_and_archives_copy_exact_bytes_without_execution_or_extraction(self):
        body = b'MZ\0PK\x03\x04' + bytes(range(256)) * 513
        names = ['driver.EXE', '한글 자료.tar.gz', 'bundle.tar.bz2', 'bundle.tar.xz', 'bundle.tar.zst']
        names += ['archive' + suffix for suffix in sorted(ARCHIVE_FILE_TYPES)]
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')), \
                patch('zipfile.ZipFile', side_effect=AssertionError('must not extract')), \
                patch('tarfile.open', side_effect=AssertionError('must not extract')):
            for name in names:
                with self.subTest(name=name):
                    code, result = self.upload(name, body)
                    self.assertEqual(200, code, result)
                    path = Path(result['path'])
                    self.assertEqual(name, path.name)
                    self.assertEqual(body, path.read_bytes())
                    self.assertEqual(len(body), result['size'])
                    self.assertEqual([path], list(path.parent.iterdir()))
                    self.assertEqual([str(path)], self.app.validate_attachments([str(path)]))
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertIsNone(self.app.get(self.sid)['bridge'])

    def test_preflight_is_metadata_only_and_publishes_authoritative_policy(self):
        policy = self.app.bootstrap()['attachmentPolicy']
        self.assertEqual(sorted(REFERENCE_FILE_TYPES), policy['extensions'])
        self.assertEqual(MAX_UPLOAD, policy['maxUploadBytes'])
        self.assertEqual(12, policy['maxAttachments'])
        before = {str(path): path.stat().st_mtime_ns for path in self.app.state.rglob('*')}
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            code, result = self.prepare()
        self.assertEqual((200, {'ok': True, 'name': 'bundle.tar.gz', 'size': 8}), (code, result))
        self.assertFalse(self.app.attachment_store.root.exists())
        self.assertEqual(before, {str(path): path.stat().st_mtime_ns for path in self.app.state.rglob('*')})
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertIsNone(self.app.get(self.sid)['bridge'])

    def test_preflight_rejects_invalid_metadata_auth_session_and_closing_before_copy(self):
        for kwargs in [{'name': 'unsafe.dll'}, {'name': '../file.zip'}, {'size': True},
                       {'size': -1}, {'size': MAX_UPLOAD + 1}, {'size': '1'},
                       {'extra': {'unexpected': 1}}, {'sid': str(uuid.uuid4())}]:
            with self.subTest(kwargs=kwargs):
                code, result = self.prepare(**kwargs)
                self.assertEqual(400, code, result)
                self.assertTrue(result['error'])
        self.assertEqual(403, self.prepare(auth=False)[0])
        self.app.close()
        self.assertEqual(409, self.prepare()[0])
        self.assertFalse(self.app.attachment_store.root.exists())

    def test_preflight_quota_and_upload_recheck_after_other_upload(self):
        with patch('local_app.attachments.MAX_STORAGE', 6):
            self.assertEqual(400, self.prepare(size=7)[0])
            self.assertFalse(self.app.attachment_store.root.exists())
            self.assertEqual(200, self.prepare(size=4)[0])
            self.assertEqual(200, self.upload('first.zip', b'1234')[0])
            code, error = self.upload('second.exe', b'1234')
            self.assertEqual(400, code, error)
            self.assertIn('512 MB', error['error'])
        self.assertEqual(1, len(list(self.app.attachment_store.root.rglob('first.zip'))))
        self.assertEqual([], list(self.app.attachment_store.root.rglob('second.exe')))

    def test_large_supported_upload_preserves_bytes_and_expected_rejections_return_json(self):
        # This size reproduced a Windows connection reset when a rejected request
        # returned before consuming its still-transmitting body.
        body = b'\0\xffMZPK' * (5 * 1024 * 1024)
        code, result = self.upload('large.exe', body)
        self.assertEqual(200, code, result)
        self.assertEqual(body, Path(result['path']).read_bytes())
        code, result = self.upload('unsupported.dll', body)
        self.assertEqual(400, code, result)
        self.assertIn('지원하지 않는 파일 형식', result['error'])
        with patch('local_app.attachments.MAX_STORAGE', len(body)):
            code, result = self.upload('quota.zip', body)
        self.assertEqual(400, code, result)
        self.assertIn('512 MB', result['error'])

    def test_partial_upload_removes_partial_copy_and_storage_is_bounded(self):
        store = self.app.attachment_store
        with self.assertRaises(ValueError):
            store.save(self.sid, 'broken.txt', io.BytesIO(b'short'), 20)
        self.assertEqual([], list(store.root.rglob('*.txt')))
        with self.assertRaises(ValueError):
            store.save(self.sid, 'large.txt', io.BytesIO(), MAX_UPLOAD + 1)
        with patch('local_app.attachments.MAX_STORAGE', 3):
            with self.assertRaises(ValueError):
                store.save(self.sid, 'quota.txt', io.BytesIO(b'four'), 4)

    def test_shutdown_rejects_copy_and_existing_files_remain(self):
        _, first = self.upload()
        self.app.close()
        self.assertEqual(409, self.upload()[0])
        self.assertEqual(b'dropped', Path(first['path']).read_bytes())


if __name__ == '__main__':
    unittest.main()
