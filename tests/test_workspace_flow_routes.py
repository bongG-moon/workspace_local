"""Whole-app attention and explicit file/command helper HTTP boundaries."""
import json
from pathlib import Path
import tempfile
import threading
import sys
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server
from local_app.bridge import probe_cli
from local_app.completions import CompletionDiscovery
from local_app.claude_inventory import ClaudeInventory


class FlowRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['never-start'], managed_workspace_root=self.root / 'managed')
        self.app.completion_discovery = CompletionDiscovery(ClaudeInventory(config=self.root / 'config'))
        self.addCleanup(self.app.close)
        self.a = self.app.create(str(self.work), True, title='A')['id']
        other = self.root / 'other'; other.mkdir()
        self.b = self.app.create(str(other), True, title='B')['id']
        self.server = Server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(2)

    def request(self, route, data=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        req = Request(self.server.origin + route, headers=headers,
                      data=json.dumps(data).encode() if data is not None else None)
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_attention_aggregates_all_tasks_and_request_lifetimes_without_arguments(self):
        self.app.emit(self.a, 'request', {'id': 'reused', 'tool': 'Bash', 'input': {'command': 'SECRET-SENTINEL'}})
        self.app.emit(self.b, 'request', {'id': 'q', 'tool': 'AskUserQuestion', 'input': {}})
        first = self.request('/api/attention')
        self.assertEqual((2, 1, 1), (first['total'], first['approvalCount'], first['questionCount']))
        self.assertEqual({self.a, self.b}, {row['sessionId'] for row in first['items']})
        self.assertNotIn('SECRET-SENTINEL', json.dumps(first))
        self.assertNotIn(self.app.token, first['windowTitle'])
        self.app.emit(self.a, 'request', {'id': 'reused', 'tool': 'Bash', 'input': {}})
        self.assertEqual(first['revision'], self.request('/api/attention')['revision'])
        self.app.emit(self.a, 'request_closed', {'id': 'reused'})
        self.app.emit(self.a, 'request', {'id': 'reused', 'tool': 'Bash', 'input': {}})
        self.assertNotEqual(first['revision'], self.request('/api/attention')['revision'])
        self.app.emit(self.a, 'status', {'state': 'stopped'})
        self.app.emit(self.b, 'error', {'message': 'fixture'})
        self.assertEqual(0, self.request('/api/attention')['total'])

    def test_authentication_and_native_binding_do_not_trust_client_window_identity(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/attention', auth=False)
        self.assertEqual(403, caught.exception.code)
        with patch.object(self.app.notifier, 'bind', return_value={'supported': False, 'bound': False}) as bind:
            result = self.request('/api/attention/bind', {'hwnd': 123, 'pid': 456, 'title': 'forged'})
            bind.assert_called_once_with()
            self.assertFalse(result['native']['bound'])

    def test_html_open_reveal_and_source_actions_share_task_scope(self):
        doc = self.work / '한글.html'; doc.write_text('<b>test</b>', encoding='utf-8')
        with patch('local_app.external_apps.open_document', return_value={'ok': True, 'requested': True}) as opened:
            for action in ['open', 'reveal', 'text']:
                self.assertTrue(self.request('/api/open', {'id': self.a, 'path': str(doc), 'action': action})['requested'])
                opened.assert_called_with(doc, action)
            before = opened.call_count
            with self.assertRaises(HTTPError):
                self.request('/api/open', {'id': self.b, 'path': str(doc)})
            self.assertEqual(before, opened.call_count)

    def test_file_suggestions_do_not_grant_trust_or_read_file_contents(self):
        doc = self.work / '한글.md'; doc.write_text('SECRET-SENTINEL', encoding='utf-8')
        self.app.get(self.a)['trusted'] = False
        data = self.request('/api/completions', {'id': self.a, 'kind': 'file', 'query': '한글', 'attachments': []})
        self.assertEqual([str(doc)], [row['path'] for row in data['items']])
        self.assertNotIn('SECRET-SENTINEL', json.dumps(data))
        self.assertFalse(self.app.get(self.a)['trusted'])
        self.assertEqual([], self.request('/api/completions', {'id': self.b, 'kind': 'file', 'query': '한글'})['items'])

    def test_slash_helpers_use_current_live_connection_only(self):
        item = self.app.get(self.a)
        item['bridge'] = Mock(closed=False)
        item['bridge'].process.poll.return_value = None
        self.addCleanup(lambda: item.update(bridge=None))
        item['connection'] = {'reported': {'commands': True}, 'slashCommands': [{'name': 'company-agent:html-report', 'description': '보고서'}]}
        first = self.request('/api/completions', {'id': self.a, 'kind': 'slash', 'query': 'company'})
        self.assertEqual('/company-agent:html-report', first['items'][0]['invocation'])
        self.assertEqual([], self.request('/api/completions', {'id': self.b, 'kind': 'slash', 'query': ''})['items'])
        item['bridge'].closed = True
        self.assertEqual([], self.request('/api/completions', {'id': self.a, 'kind': 'slash', 'query': ''})['items'])

    def test_home_slash_discovery_never_creates_task_trust_or_cli(self):
        skill = self.root / 'config/skills/skill-creator/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: skill-creator\ndescription: Create skills\n---\nPRIVATE INSTRUCTIONS', encoding='utf-8')
        project = self.work / '.claude/skills/skill-project/SKILL.md'
        project.parent.mkdir(parents=True)
        project.write_text('---\nname: skill-project\n---\nPRIVATE PROJECT', encoding='utf-8')
        self.app.get(self.a)['trusted'] = False
        with patch('local_app.bridge.subprocess.Popen', side_effect=AssertionError('no CLI')):
            common = self.request('/api/completions', {'id': None, 'kind': 'slash', 'query': 'sk'})
            folder = self.request('/api/completions', {'id': self.a, 'kind': 'slash', 'query': 'sk'})
        self.assertEqual(['/skill-creator'], [row['invocation'] for row in common['items']])
        self.assertEqual({'/skill-creator', '/skill-project'}, {row['invocation'] for row in folder['items']})
        self.assertTrue(common['discovery'])
        self.assertEqual('discovered', common['items'][0]['availability'])
        self.assertEqual('installed', common['items'][0]['source'])
        self.assertNotIn('PRIVATE', json.dumps(folder))
        self.assertFalse(self.app.get(self.a)['trusted'])
        self.assertEqual(2, len(self.app.sessions))
        self.assertTrue(all(item.get('bridge') is None for item in self.app.sessions.values()))
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/completions', {'kind': 'file', 'query': ''})
        self.assertEqual(400, caught.exception.code)

    def test_runtime_slash_and_file_selection_reach_the_child_without_rewriting(self):
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-X', 'utf8', str(root / 'tests/fixtures/workspace_fake_cli.py')]
        self.app.command, self.app.info = command, probe_cli(command)
        item = self.app.get(self.a)
        self.request('/api/send', {'id': self.a, 'text': 'STREAM_PROTOCOL_TEST'})
        def wait(predicate):
            deadline = time.monotonic() + 8
            while not predicate() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(predicate())
        wait(lambda: item['state'] == 'done')
        rows = self.request('/api/completions', {'id': self.a, 'kind': 'slash', 'query': 'test'})['items']
        self.assertEqual('/test-skill', rows[0]['invocation'])
        document = self.work / '한글 문서.md'; document.write_text('fixture', encoding='utf-8')
        selected = self.request('/api/completions', {'id': self.a, 'kind': 'file', 'query': '한글'})['items'][0]['path']
        text = rows[0]['invocation'] + ' 한글 자료 정리'
        self.request('/api/send', {'id': self.a, 'text': text, 'attachments': [selected]})
        wait(lambda: item['state'] == 'question')
        received = item['messages'][-1]['text']
        self.assertTrue(received.startswith('요청 확인: /test-skill 한글 자료 정리\n\n'))
        self.assertEqual([str(document)], json.loads(received.split('경로(JSON):\n', 1)[1]))


if __name__ == '__main__':
    unittest.main()
