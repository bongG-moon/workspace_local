"""Copied-image delivery through HTTP, attachment storage and real CLI pipes.

The child is a local deterministic fixture, never Claude or an external model.
All state, generated images and child scripts stay in this checkout's QA folder.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener
import zlib

from local_app.bridge import CHAT_REQUIRED_FLAGS
from local_app.server import LocalApp, Server


ROOT = Path(__file__).resolve().parents[1]
QA_ROOT = ROOT / 'build' / 'qa-clipboard-delivery'
INFO = {'version': '2.1.296 (fixture)', 'help': ' '.join(CHAT_REQUIRED_FLAGS)}


def png_bytes():
    """A complete small RGBA PNG, including CRCs and compressed pixel rows."""
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    width, height = 12, 7
    pixels = (b'\x00' + bytes([120, 91, 213, 255]) * width) * height
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


# Manual native QA may replace this value with the captured PNG bytes before
# loading the suite. No machine-specific fixture path is part of these tests.
PNG_BYTES = png_bytes()

CLI_FIXTURE = r'''
import base64
import hashlib
import json
from pathlib import Path
import sys
import uuid

session = str(uuid.uuid4())

def emit(value):
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
    sys.stdout.buffer.flush()

for line in sys.stdin.buffer:
    value = json.loads(line)
    if value['type'] == 'control_request':
        request = value['request']
        detail = {'tools': [], 'skills': [], 'mcp_servers': [], 'commands': [], 'models': []}
        emit({'type': 'control_response', 'response': {'subtype': 'success',
              'request_id': value['request_id'], 'response': detail}})
        if request['subtype'] == 'interrupt':
            break
    elif value['type'] == 'user':
        content = value['message']['content']
        receipt = {'clientComposed': value.get('client_composed', False), 'images': [], 'files': []}
        if isinstance(content, list):
            receipt['text'] = content[0]['text']
            for block in content:
                if block['type'] == 'image':
                    source = block['source']
                    raw = base64.b64decode(source['data'], validate=True)
                    receipt['images'].append({'sha256': hashlib.sha256(raw).hexdigest(),
                        'size': len(raw), 'mediaType': source['media_type'], 'sourceType': source['type']})
            receipt['textBlocks'] = [block['text'] for block in content if block['type'] == 'text']
        else:
            marker = '\n\n사용자가 선택한 원본 파일 경로(JSON):\n'
            receipt['text'], raw_paths = content.split(marker, 1)
            for name in json.loads(raw_paths):
                # Read only the explicitly supplied test attachment paths.
                path = Path(name)
                raw = path.read_bytes()
                receipt['files'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                         'size': len(raw)})
        emit({'type': 'system', 'subtype': 'init', 'session_id': session,
              'model': 'fixture-only', 'tools': [], 'skills': [], 'mcp_servers': []})
        emit({'type': 'assistant', 'message': {'content': [{'type': 'text',
              'text': json.dumps(receipt, ensure_ascii=False)}]}})
        emit({'type': 'result', 'session_id': session, 'is_error': False, 'result': ''})
'''


class ClipboardDeliveryTests(unittest.TestCase):
    def setUp(self):
        QA_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='case-', dir=QA_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config = self.root / 'claude'
        config.mkdir()
        environment = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config)})
        environment.start()
        self.addCleanup(environment.stop)
        fixture = self.root / 'capture_cli.py'
        fixture.write_text(CLI_FIXTURE, encoding='utf-8')
        self.app = LocalApp(self.root / 'state',
                            command=[sys.executable, '-B', str(fixture)], info=INFO,
                            managed_workspace_root=self.root / 'workspaces')
        self.addCleanup(self.app.close, force=True)
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.http = build_opener(ProxyHandler({}))

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def post(self, route, data, *, binary=False, name=''):
        headers = {'Authorization': 'Bearer ' + self.app.token,
                   'Content-Type': 'application/octet-stream' if binary else 'application/json'}
        if binary:
            headers['X-File-Name'] = quote(name)
        else:
            data = json.dumps(data).encode('utf-8')
        request = Request(self.server.origin + route, data=data, headers=headers)
        try:
            response = self.http.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def create(self, kind):
        data = {'kind': kind}
        if kind == 'work':
            directory = self.root / 'work'
            directory.mkdir()
            data.update(workspace=str(directory), trusted=True)
        code, item = self.post('/api/create', data)
        self.assertEqual(200, code, item)
        return item['id']

    def upload(self, sid, body=None):
        body = PNG_BYTES if body is None else body
        name = '붙여넣은 이미지_20261103000000000_1.png'
        code, result = self.post('/api/attachments/prepare', {'id': sid, 'name': name, 'size': len(body)})
        self.assertEqual(200, code, result)
        code, result = self.post('/api/attachments/upload?id=' + sid, body, binary=True, name=name)
        self.assertEqual(200, code, result)
        path = Path(result['path'])
        self.assertEqual(body, path.read_bytes())
        self.assertTrue(path.is_relative_to(self.app.attachment_store.root / sid))
        # Pasting attaches a managed copy; it must not send a turn by itself.
        self.assertEqual([], self.app.get(sid)['messages'])
        self.assertIsNone(self.app.get(sid)['bridge'])
        return str(path)

    def send_and_receive(self, sid, paths):
        text = '붙여넣은 이미지를 확인해 주세요.'
        code, result = self.post('/api/send', {'id': sid, 'text': text, 'attachments': paths})
        self.assertEqual(200, code, result)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            item = self.app.public(self.app.get(sid))
            if item['state'] in {'done', 'error'}:
                break
            time.sleep(.02)
        self.assertEqual('done', item['state'], item.get('messages'))
        user = [row for row in item['messages'] if row['role'] == 'user']
        answers = [row for row in item['messages'] if row['role'] == 'assistant']
        self.assertEqual(1, len(user))
        self.assertEqual(1, len(answers))
        self.assertEqual(paths, user[0]['files'])
        receipt = json.loads(answers[0]['text'])
        self.assertEqual(text, receipt['text'])
        return receipt, user[0]

    def test_chat_copied_png_bytes_reach_cli_inline_once(self):
        sid = self.create('chat')
        path = self.upload(sid)
        receipt, user = self.send_and_receive(sid, [path])
        self.assertTrue(receipt['clientComposed'])
        self.assertTrue(all(str(self.root) not in block for block in receipt['textBlocks']))
        self.assertEqual([], receipt['files'])
        self.assertEqual([{'sha256': hashlib.sha256(PNG_BYTES).hexdigest(), 'size': len(PNG_BYTES),
                           'mediaType': 'image/png', 'sourceType': 'base64'}], receipt['images'])
        self.assertEqual('read', user['attachmentReads'][0]['status'])
        self.assertEqual(PNG_BYTES, Path(path).read_bytes())

    def test_work_copied_png_path_reaches_cli_and_original_bytes_remain_readable(self):
        sid = self.create('work')
        path = self.upload(sid)
        receipt, _ = self.send_and_receive(sid, [path])
        self.assertFalse(receipt['clientComposed'])
        self.assertEqual([], receipt['images'])
        self.assertEqual([{'path': path, 'sha256': hashlib.sha256(PNG_BYTES).hexdigest(),
                           'size': len(PNG_BYTES)}], receipt['files'])

    def test_unreadable_chat_image_is_not_silently_sent_to_cli(self):
        sid = self.create('chat')
        path = self.upload(sid, b'not-a-PNG')
        code, result = self.post('/api/send', {'id': sid, 'text': '확인', 'attachments': [path]})
        self.assertEqual(400, code, result)
        self.assertIn('첨부파일을 읽지 못했어요', result['error'])
        self.assertIsNone(self.app.get(sid)['bridge'])
        self.assertEqual([], self.app.get(sid)['messages'])
        self.assertTrue(Path(path).is_file())


if __name__ == '__main__':
    unittest.main()
