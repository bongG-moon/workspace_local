"""Authenticated choice/mode dispatch and single-consumption HTTP contracts."""
import json
import os
from pathlib import Path
import tempfile
import threading
import sys
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server
from local_app.bridge import probe_cli


ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ['COMPANY_AGENT_SOURCE']).expanduser().resolve() if 'COMPANY_AGENT_SOURCE' in os.environ else ROOT / 'company-agent-plugin'
if not all((CORE / name).is_file() for name in ('scripts/harness_cli.py', 'scripts/company_agent/report_styles.py')):
    if 'COMPANY_AGENT_SOURCE' in os.environ:
        raise RuntimeError('COMPANY_AGENT_SOURCE must point to a complete Company Agent plugin source root (containing scripts/harness_cli.py).')
    CORE = None
CORE_SKIP = 'Optional Company Agent integration: set COMPANY_AGENT_SOURCE to its plugin source root.'


class ControlRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        work = root / 'work'; work.mkdir()
        self.app = LocalApp(root / 'state', command=['never-start'], managed_workspace_root=root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(work), True)['id']
        self.item = self.app.get(self.sid)
        self.bridge = Mock(closed=False)
        self.bridge.ready.is_set.return_value = True
        self.item['bridge'] = self.bridge
        self.addCleanup(lambda: self.item.update(bridge=None))
        self.server = Server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(2)

    def post(self, path, data, auth=True):
        headers = {'Content-Type': 'application/json', 'Origin': self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        with urlopen(Request(self.server.origin + path, data=json.dumps(data).encode(), headers=headers), timeout=3) as response:
            return json.loads(response.read())

    def test_control_routes_require_authentication_before_dispatch(self):
        for route, data in [('/api/permission-mode', {'mode': 'auto'}), ('/api/choice', {'choiceId': 'none'}),
                            ('/api/respond', {'allow': True, 'permissionChoiceId': 'none'})]:
            with self.subTest(route=route), self.assertRaises(HTTPError) as caught:
                self.post(route, {'id': self.sid, **data}, auth=False)
            self.assertEqual(403, caught.exception.code)
        self.bridge.set_permission_mode.assert_not_called()
        self.bridge.send.assert_not_called()

    def test_missing_mode_is_not_an_implicit_reset_and_ack_projects_state(self):
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/permission-mode', {'id': self.sid})
        self.assertEqual(400, caught.exception.code)
        self.bridge.set_permission_mode.assert_not_called()
        self.bridge.set_permission_mode.return_value = {'permissionMode': 'auto', 'permissionModeOverride': 'auto'}
        result = self.post('/api/permission-mode', {'id': self.sid, 'mode': 'auto'})
        self.assertTrue(result['ok'])
        self.assertEqual('auto', result['session']['permissionModeOverride'])
        self.bridge.set_permission_mode.assert_called_once_with('auto')

    def test_choice_is_bound_to_session_and_consumed_once(self):
        question = {'schemaVersion': 1, 'kind': 'html-report-style', 'id': 'html-report:additional-style',
                    'question': '디자인 선택', 'options': [{'id': 'neumorphism', 'label': '뉴모피즘'}],
                    'allowCustom': True, 'responseMode': 'next-user-message'}
        self.app.emit(self.sid, 'choice', question)
        self.item['state'] = 'done'
        current = self.item['choice']['id']
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/choice', {'id': self.sid, 'choiceId': 'expired', 'optionId': 'neumorphism'})
        self.assertEqual(400, caught.exception.code)
        self.bridge.send.assert_not_called()
        payload = {'id': self.sid, 'choiceId': current, 'optionId': 'neumorphism'}
        result = self.post('/api/choice', payload)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['session']['choice'])
        with self.assertRaises(HTTPError):
            self.post('/api/choice', payload)
        self.bridge.send.assert_called_once()

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_registered_core_choice_round_trip_through_real_child_and_http(self):
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-X', 'utf8', str(root / 'tests/fixtures/workspace_fake_cli.py')]
        self.app.command = command
        self.app.info = probe_cli(command)
        self.item['bridge'] = None
        self.app.companion.client = Mock()
        self.app.companion.client.choice_helper.return_value = {
            'python': sys.executable, 'script': str(CORE / 'scripts/harness_cli.py')}
        def wait_done():
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.item['state'] in {'done', 'error'}:
                    break
                time.sleep(.01)
            self.assertEqual('done', self.item['state'])
        self.post('/api/send', {'id': self.sid, 'text': 'HTML_CHOICES_PROTOCOL_TEST'})
        self.addCleanup(lambda: self.item.get('bridge') and self.item['bridge'].close())
        wait_done()
        self.assertEqual(10, len(self.item['choice']['options']))
        choice_id = self.item['choice']['id']
        process = self.item['bridge'].process
        self.post('/api/choice', {'id': self.sid, 'choiceId': choice_id, 'optionId': 'neumorphism'})
        wait_done()
        self.assertIs(process, self.item['bridge'].process)
        self.assertNotIn('choice', self.item)
        self.assertEqual(2, sum(row['role'] == 'user' for row in self.item['messages']))
        self.assertIn('선택한 디자인을 확인했습니다', self.item['messages'][-1]['text'])


if __name__ == '__main__':
    unittest.main()
