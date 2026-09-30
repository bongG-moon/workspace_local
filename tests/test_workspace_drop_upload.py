import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from local_app.attachments import AttachmentStore, MAX_UPLOAD
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
            response = urlopen(request, timeout=3)
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
        self.assertEqual(403, self.upload(auth=False)[0])
        self.assertEqual(403, self.upload(headers={'Origin': 'https://example.com'})[0])
        self.assertEqual(400, self.upload(sid='unknown')[0])
        self.assertFalse((self.app.state / 'attachments').exists())

    def test_path_escape_reserved_names_and_executables_are_rejected(self):
        for name in ['../escape.md', r'folder\escape.txt', 'x.txt:stream', 'CON.txt', 'NUL.md',
                     'bad.txt.', 'a.exe', 'x\n.md']:
            with self.subTest(name=name):
                self.assertEqual(400, self.upload(name)[0])
        self.assertFalse((self.app.state / 'attachments').exists())

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
