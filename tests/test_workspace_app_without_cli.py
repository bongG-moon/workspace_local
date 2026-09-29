"""The GUI must remain usable when the original terminal CLI is unavailable."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import resolve_cli
from local_app.server import LocalApp, Server


class AppWithoutCliTests(unittest.TestCase):
    def test_unavailable_terminal_opens_gui_and_preserves_local_work(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',
                {'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '1', 'COMPANY_AGENT_CLAUDE': ''}), \
                patch('local_app.bridge.shutil.which', side_effect=AssertionError('No fallback CLI')):
            root = Path(tmp)
            app = LocalApp(root / 'state', managed_workspace_root=root / 'workspaces')
            server = Server(app)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            def request(route, data=None):
                headers = {'Authorization': 'Bearer ' + app.token}
                if data is not None:
                    headers['Content-Type'] = 'application/json'
                req = Request(server.origin + route, headers=headers,
                              data=json.dumps(data).encode() if data is not None else None)
                with urlopen(req, timeout=5) as response:
                    return json.load(response)
            try:
                boot = request('/api/bootstrap')
                self.assertFalse(boot['demo'])
                self.assertIn('앱 전용 로그인은 필요하지 않습니다', boot['error'])
                task = request('/api/create', {'managed': True, 'trusted': True, 'title': '연결 전 업무'})
                self.assertTrue(Path(task['workspace']).is_dir())
                self.assertEqual('연결 전 업무', request('/api/bootstrap')['sessions'][0]['title'])
                self.assertEqual(0, request('/api/attention')['total'])
                with self.assertRaises(HTTPError) as failed:
                    request('/api/send', {'id': task['id'], 'text': '응답 시험', 'trusted': True})
                self.assertEqual(400, failed.exception.code)
                self.assertIn('기존 터미널', json.load(failed.exception)['error'])
                self.assertIsNone(app.command)
            finally:
                app.close()
                server.shutdown()
                server.server_close()
                worker.join(5)

    def test_explicit_existing_command_remains_authoritative(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',
                {'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '1'}):
            executable = Path(tmp) / 'claude.exe'
            executable.write_bytes(b'not executed')
            self.assertEqual([str(executable.resolve())], resolve_cli(str(executable)))
