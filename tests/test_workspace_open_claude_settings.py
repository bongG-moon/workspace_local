"""The settings editor can only open the current CLI's known user settings."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from local_app.server import LocalApp, Server


class OpenClaudeSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        work = root / 'work'
        work.mkdir()
        self.settings = root / 'fixture profile & settings' / 'settings.json'
        self.settings.parent.mkdir()
        self.settings.write_bytes(b'{"model":"synthetic-test-model"}')
        self.app = LocalApp(root / 'state', command=['unused'], managed_workspace_root=root / 'managed')
        self.addCleanup(self.app.close)
        self.work = self.app.create(str(work), True)['id']
        self.chat = self.app.create(kind='chat')['id']
        self.bridge = Mock(closed=False, stopping=False)
        self.bridge.process.poll.return_value = None
        self.bridge.user_settings_path.return_value = self.settings
        for sid in (self.work, self.chat):
            item = self.app.get(sid)
            item['bridge'] = self.bridge
            self.addCleanup(item.update, bridge=None)
        trusted_editor = str(root / 'system' / 'notepad.exe')
        self.editor = self.enterContext(patch('local_app.external_apps.windows_program', return_value=trusted_editor))
        self.launch = self.enterContext(patch('local_app.server.subprocess.Popen'))

    @unittest.skipUnless(os.name == 'nt', 'Notepad is a Windows handoff')
    def test_each_session_uses_its_own_captured_file_and_only_notepad(self):
        other = self.settings.parents[1] / 'other settings' / 'settings.json'
        other.parent.mkdir()
        other.touch()
        chat_bridge = Mock(closed=False, stopping=False)
        chat_bridge.process.poll.return_value = None
        chat_bridge.user_settings_path.return_value = other
        self.app.get(self.chat)['bridge'] = chat_bridge
        for sid, expected in ((self.work, self.settings), (self.chat, other)):
            with self.subTest(sid=sid), patch.object(Path, 'read_bytes', side_effect=AssertionError('file contents read')), \
                    patch.object(Path, 'read_text', side_effect=AssertionError('file contents read')):
                self.assertEqual({'ok': True}, self.app.open_claude_settings(sid))
                self.editor.assert_called_with('notepad.exe')
                self.assertEqual([self.editor.return_value, str(expected)], self.launch.call_args.args[0])
                self.assertEqual(str(Path(self.editor.return_value).parent), self.launch.call_args.kwargs['cwd'])
                self.assertFalse(self.launch.call_args.kwargs.get('shell', False))

    def test_missing_unknown_and_non_settings_paths_do_not_launch_or_create(self):
        self.settings.unlink()
        for path in (None, self.settings, Path('relative/settings.json'),
                     self.settings.with_name('credentials.json'), self.settings.parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.user_settings_path.return_value = path
                self.app.open_claude_settings(self.work)
        self.assertFalse(self.settings.exists())
        self.launch.assert_not_called()

    def test_ineligible_connections_do_not_launch(self):
        with patch.object(self.app, 'demo', True), self.assertRaises(ValueError):
            self.app.open_claude_settings(self.work)
        self.app.session_visibility.set_hidden(self.work, True)
        with self.assertRaises(ValueError):
            self.app.open_claude_settings(self.work)
        item = self.app.get(self.chat)
        for field in ('_deleting', '_removingFromList', '_idleReleasing', '_restarting'):
            with self.subTest(field=field), patch.dict(item, {field: True}), self.assertRaises(ValueError):
                self.app.open_claude_settings(self.chat)
        with patch.dict(item, {'trusted': False}), self.assertRaises(ValueError):
            self.app.open_claude_settings(self.chat)
        with patch.object(self.bridge, 'closed', True), self.assertRaises(ValueError):
            self.app.open_claude_settings(self.chat)
        self.bridge.process.poll.return_value = 0
        with self.assertRaises(ValueError):
            self.app.open_claude_settings(self.chat)
        self.bridge.user_settings_path.assert_not_called()
        self.launch.assert_not_called()

    def test_path_and_editor_failures_do_not_expose_private_details(self):
        private_detail = 'fixture-private-path-and-token'
        with patch.object(self.bridge, 'user_settings_path', side_effect=OSError(private_detail)), \
                self.assertRaisesRegex(ValueError, '설정 파일 위치') as error:
            self.app.open_claude_settings(self.work)
        self.assertNotIn(private_detail, str(error.exception))
        with patch('local_app.server.workspace_path', side_effect=ValueError(private_detail)), \
                self.assertRaisesRegex(ValueError, '실제 위치') as error:
            self.app.open_claude_settings(self.work)
        self.assertNotIn(private_detail, str(error.exception))
        self.launch.assert_not_called()
        if os.name == 'nt':
            self.launch.side_effect = OSError(private_detail)
            with self.assertRaisesRegex(ValueError, '메모장') as error:
                self.app.open_claude_settings(self.work)
            self.assertNotIn(private_detail, str(error.exception))

    @unittest.skipUnless(os.name == 'nt', 'Notepad is a Windows handoff')
    def test_connection_replaced_during_path_checks_cannot_launch(self):
        item = self.app.get(self.work)
        other = Mock(closed=False, stopping=False)
        other.process.poll.return_value = None
        def replace(_):
            item['bridge'] = other
            return str(self.settings.parent / 'notepad.exe')
        self.editor.side_effect = replace
        with self.assertRaisesRegex(ValueError, '연결이 바뀌'):
            self.app.open_claude_settings(self.work)
        self.launch.assert_not_called()

    def test_route_requires_local_authenticated_post_and_only_session_id(self):
        server = Server(self.app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(method, body=None, origin=server.origin, token=self.app.token):
            client = http.client.HTTPConnection(*server.server_address[:2], timeout=3)
            try:
                headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Origin': origin}
                client.request(method, '/api/open-claude-settings', json.dumps(body) if body else None, headers)
                reply = client.getresponse()
                reply.read()
                return reply.status
            finally:
                client.close()
        try:
            valid = {'id': self.work}
            self.assertEqual(403, request('POST', valid, token='wrong'))
            self.assertEqual(403, request('POST', valid, origin='https://elsewhere.invalid'))
            self.assertEqual(404, request('GET'))
            for body in ({**valid, 'path': str(self.settings)}, {'id': []}, {'id': 'unknown'}):
                self.assertEqual(400, request('POST', body))
            self.launch.assert_not_called()
            if os.name == 'nt':
                self.assertEqual(200, request('POST', valid))
                self.launch.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
