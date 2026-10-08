"""Authenticated storage routes keep live draft and queue references protected."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server


class AttachmentStorageHttpTests(unittest.TestCase):
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

    def request(self, route, data=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + route, headers=headers,
                          data=None if data is None else json.dumps(data).encode())
        try:
            result = urlopen(request, timeout=5)
        except HTTPError as error:
            result = error
        with result:
            return result.status, json.load(result)

    def upload(self, name):
        return self.app.attachment_store.save(self.sid, name, io.BytesIO(b'1234'), 4)['path']

    def test_cleanup_preserves_durable_draft_queue_schedule_and_original(self):
        draft, queued, scheduled, unused = [self.upload(name + '.zip') for name in ('draft', 'queued', 'schedule', 'unused')]
        code, _ = self.request('/api/drafts', {'id': self.sid, 'revision': 0,
            'draft': {'text': '작성 중', 'attachments': [draft]}, 'stash': None, 'selectedId': self.sid})
        self.assertEqual(200, code)
        self.app.dispatch.queue.enqueue(self.sid, '다음 작업', [queued])
        self.app.dispatch.queue.add_schedule(self.sid, '예약 작업', [scheduled], kind='once', run_at=time.time() + 3600)
        original = self.root / 'original.zip';original.write_bytes(b'original')
        with patch('local_app.attachments.UNUSED_GRACE_SECONDS', 0):
            code, before = self.request('/api/attachments/storage')
            self.assertEqual(200, code, before)
            self.assertEqual((16, 4), (before['usedBytes'], before['removableBytes']))
            code, result = self.request('/api/attachments/cleanup', {})
        self.assertEqual(200, code, result)
        self.assertEqual(1, result['removedCount'])
        self.assertTrue(all(Path(path).is_file() for path in (draft, queued, scheduled)))
        self.assertFalse(Path(unused).exists())
        self.assertEqual(b'original', original.read_bytes())

    def test_status_scan_does_not_hold_application_lock(self):
        called = []
        original = self.app.attachment_store.status
        def checked(*args, **kwargs):
            called.append(self.app.lock._is_owned())
            return original(*args, **kwargs)
        with patch.object(self.app.attachment_store, 'status', side_effect=checked):
            code, _ = self.request('/api/attachments/storage')
        self.assertEqual(200, code)
        self.assertEqual([False], called)

    def test_routes_require_auth_and_cleanup_does_not_accept_a_delete_path(self):
        path = self.upload('kept.zip')
        self.assertEqual(403, self.request('/api/attachments/storage', auth=False)[0])
        self.assertEqual(403, self.request('/api/attachments/cleanup', {}, auth=False)[0])
        self.assertEqual(400, self.request('/api/attachments/cleanup', {'path': path})[0])
        self.assertTrue(Path(path).exists())


if __name__ == '__main__':
    unittest.main()
