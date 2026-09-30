"""Explicit command discovery uses a child handshake, never an AI prompt."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import ClaudeSession
from local_app.completions import complete, REFERENCE_FILE_TYPES
from local_app.server import LocalApp, SAFE_FILES, Server


FIXTURE = r'''
import json, sys, uuid
from pathlib import Path
mode, log = sys.argv[1], Path(sys.argv[2])
session = str(uuid.uuid4())
def emit(value):
    print(json.dumps(value), flush=True)
def init():
    emit({'type':'system', 'subtype':'init', 'session_id':session,
          'model':'fixture-model', 'permissionMode':'default',
          'slash_commands':['company:report'], 'skills':['reported-skill'],
          'tools':['Read'], 'mcp_servers':[], 'plugins':[]})
for line in sys.stdin:
    value = json.loads(line)
    with log.open('a', encoding='utf-8') as out:
        out.write(json.dumps(value) + '\n')
    if value['type'] == 'control_request':
        if mode == 'timeout':
            continue
        if mode == 'rich-first':
            init()
        response = {'subtype':'success', 'request_id':value['request_id'],
                    'response':{'commands':[{'name':'company:report', 'description':'Report'}],
                                'models':[{'value':'fixture-model', 'displayName':'Fixture'}]}}
        if mode == 'rich-first':
            response['response']['commands'].append({'name':'initialization-only'})
        if mode == 'reject':
            response.update(subtype='error', error='Fixture initialization rejected')
        emit({'type':'control_response', 'response':response})
    elif value['type'] == 'user':
        init()
        emit({'type':'result', 'session_id':session, 'is_error':False, 'result':'fixture done'})
'''


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('The fixture did not finish in time')


class PrepareConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-prepare-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.fixture = self.root / 'fixture.py'
        self.fixture.write_text(FIXTURE, encoding='utf-8')
        self.log = self.root / 'wire.jsonl'
        self.command = [sys.executable, '-B', str(self.fixture), 'normal', str(self.log)]
        self.app = LocalApp(self.root / 'state', command=self.command,
                            info={'version':'fixture', 'help':''},
                            managed_workspace_root=self.root / 'workspaces')
        self.addCleanup(self.app.close)
        self.task = self.app.create(str(self.root), True)
        self.sid = self.task['id']

    def frames(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]

    def test_prepare_has_no_user_turn_then_send_reuses_same_child(self):
        reply = self.app.connect(self.sid)
        item = self.app.get(self.sid)
        bridge, connection = item['bridge'], reply['connection']
        pid = bridge.process.pid
        self.assertTrue(connection['reported']['commands'])
        self.assertFalse(connection['reported']['skills'])
        self.assertFalse(connection['reported']['tools'])
        self.assertEqual('', connection['model'])
        self.assertEqual('fixture-model', connection['availableModels'][0]['value'])
        self.assertEqual([], item['messages'])
        self.assertEqual('idle', item['state'])
        self.assertIsNone(item['lastRunId'])
        self.assertNotIn('_artifactSnapshot', item)
        self.assertNotIn('skills', connection)
        rows = complete(item, {'kind':'slash', 'query':'company'})['items']
        self.assertEqual(['/company:report'], [row['invocation'] for row in rows])
        self.assertEqual(['initialize', 'mcp_status'], [row['request']['subtype'] for row in self.frames()])
        self.app.connect(self.sid)
        self.assertEqual(['initialize', 'mcp_status', 'mcp_status'], [row['request']['subtype'] for row in self.frames()])
        self.app.send(self.sid, '/company:report retained argument', [])
        eventually(lambda: item['state'] == 'done')
        self.assertEqual(pid, bridge.process.pid)
        frames = self.frames()
        self.assertEqual(['control_request', 'control_request', 'control_request', 'user'], [row['type'] for row in frames])
        self.assertEqual('/company:report retained argument', frames[-1]['message']['content'])
        self.assertEqual('fixture-model', item['connection']['model'])

    def test_richer_system_init_is_not_lost_during_preparation(self):
        self.command[3] = 'rich-first'
        reply = self.app.connect(self.sid)
        self.assertTrue(reply['connection']['reported']['skills'])
        self.assertEqual(['reported-skill'], reply['connection']['skills'])
        self.assertEqual(['Read'], reply['connection']['tools'])
        self.assertEqual('fixture-model', reply['connection']['model'])
        self.assertEqual([{'name':'company:report', 'description':'Report'}], reply['connection']['slashCommands'])

    def test_timeout_and_rejection_close_owned_child_without_messages(self):
        for mode in ('timeout', 'reject'):
            with self.subTest(mode=mode):
                self.command[3] = mode
                with patch('local_app.bridge.PREPARE_TIMEOUT', .2):
                    with self.assertRaises(ValueError):
                        self.app.connect(self.sid)
                item = self.app.get(self.sid)
                self.assertTrue(item['bridge'].closed)
                self.assertIsNotNone(item['bridge'].process.poll())
                self.assertEqual([], item['messages'])
                self.assertIsNone(item['lastRunId'])
                self.assertFalse(item['_connecting'])

    def test_trust_busy_and_connection_limit_do_not_start_a_child(self):
        item = self.app.get(self.sid)
        with patch('local_app.bridge.subprocess.Popen', side_effect=AssertionError('No launch')):
            item['trusted'] = False
            with self.assertRaisesRegex(ValueError, '동의'):
                self.app.connect(self.sid)
            item['trusted'] = True
            for key in ('_modelUpdating', '_connecting'):
                item[key] = True
                with self.assertRaises(ValueError):
                    self.app.connect(self.sid)
                item[key] = False
            item['state'] = 'approval'
            with self.assertRaises(ValueError):
                self.app.connect(self.sid)
            item['state'] = 'idle'
            for i in range(3):
                extra = self.app.create(str(self.root), True)
                self.app.get(extra['id'])['bridge'] = Mock(closed=False, close=Mock(return_value=True))
            with self.assertRaisesRegex(ValueError, '3개'):
                self.app.connect(self.sid)
        self.assertIsNone(item['bridge'])

    def test_parallel_prepare_and_send_do_not_start_another_connection(self):
        entered, release = threading.Event(), threading.Event()
        original = ClaudeSession.prepare
        errors = []
        def delayed(bridge):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(bridge)
        def run():
            try:
                self.app.connect(self.sid)
            except Exception as exc:
                errors.append(exc)
        with patch.object(ClaudeSession, 'prepare', delayed):
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(ValueError):
                    self.app.connect(self.sid)
                with self.assertRaises(ValueError):
                    self.app.send(self.sid, 'must not send', [])
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, len(self.frames()))
        self.assertEqual([], self.app.get(self.sid)['messages'])

    def test_stopped_task_can_prepare_without_artifact_or_turn_mutation(self):
        item = self.app.get(self.sid)
        item.update(state='stopped', lastRunId='previous-run', artifacts=[{'id':'prior'}])
        before = {key:item[key] for key in ('state', 'lastRunId', 'artifacts', 'messages')}
        self.app.connect(self.sid)
        self.assertEqual(before, {key:item[key] for key in before})
        self.assertNotIn('_artifactSnapshot', item)

    def test_shutdown_drains_admitted_prepare_then_reaps_its_child(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = ClaudeSession.prepare
        failures = []
        def delayed(bridge):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(bridge)
        def connect():
            try:
                with self.app.operation():
                    self.app.connect(self.sid)
            except Exception as exc:
                failures.append(exc)
        def quit_app():
            self.app.close()
            closed.set()
        with patch.object(ClaudeSession, 'prepare', delayed):
            worker = threading.Thread(target=connect)
            worker.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=quit_app)
            closer.start()
            try:
                eventually(lambda: self.app.shutdown_status()['closing'])
                self.assertFalse(closed.is_set())
            finally:
                release.set()
                worker.join(5)
                closer.join(5)
        self.assertEqual([], failures)
        self.assertTrue(closed.is_set())
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertIsNotNone(self.app.get(self.sid)['bridge'].process.poll())
        self.assertEqual([], self.app.get(self.sid)['messages'])

    def test_code_reference_is_forwarded_as_path_and_executable_is_rejected(self):
        source = self.root / 'example.py'
        source.write_text('raise AssertionError("never execute")', encoding='utf-8')
        blocked = self.root / 'program.exe'
        blocked.write_bytes(b'not executable')
        self.assertIn('.py', REFERENCE_FILE_TYPES)
        self.assertIn('.py', SAFE_FILES)
        from local_app.file_preview import build_preview
        from local_app.external_apps import open_document
        preview = build_preview(self.app.allowed_file(self.sid, source))
        self.assertEqual('code', preview['kind'])
        self.assertEqual('python', preview['language'])
        self.assertEqual('raise AssertionError("never execute")', preview['text'])
        with patch('local_app.external_apps.os.startfile', create=True) as launch:
            with self.assertRaises(ValueError):
                open_document(source)
            launch.assert_not_called()
        with self.assertRaises(ValueError):
            self.app.send(self.sid, 'read it', [str(blocked)])
        self.app.send(self.sid, 'Explain @example.py', [str(source)])
        eventually(lambda: self.app.get(self.sid)['state'] == 'done')
        prompt = self.frames()[-1]['message']['content']
        self.assertTrue(prompt.startswith('Explain @example.py'))
        self.assertIn(json.dumps(str(source)), prompt)
        self.assertNotIn('raise AssertionError', prompt)

    def test_http_requires_auth_and_existing_lifecycle_gate(self):
        server = Server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        body = json.dumps({'id':self.sid}).encode()
        def post(auth):
            headers = {'Content-Type':'application/json', 'Origin':server.origin}
            if auth:
                headers['Authorization'] = 'Bearer ' + self.app.token
            return urlopen(Request(server.origin + '/api/connect', data=body, headers=headers), timeout=5)
        with self.assertRaises(HTTPError) as denied:
            post(False)
        self.assertEqual(403, denied.exception.code)
        self.assertIsNone(self.app.get(self.sid)['bridge'])
        with post(True) as response:
            self.assertTrue(json.load(response)['ok'])
        self.assertTrue(self.app.close())
        with self.assertRaises(HTTPError) as closed:
            post(True)
        self.assertEqual(409, closed.exception.code)

    def test_native_endpoint_requires_confirmed_cleanup_not_early_closed_flag(self):
        self.assertTrue(self.app.public(self.app.get(self.sid))['connectionStopped'])
        bridge = ClaudeSession(self.command, {}, self.root, lambda *_: None)
        bridge.closed = True
        self.app.get(self.sid)['bridge'] = bridge
        self.addCleanup(bridge._close_done.set)
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def native():
            return urlopen(Request(server.origin + '/api/native',
                data=json.dumps({'id':self.sid}).encode(), headers={
                    'Content-Type':'application/json', 'Authorization':'Bearer ' + self.app.token}), timeout=5)
        with patch('local_app.server.subprocess.Popen') as launch:
            self.assertFalse(self.app.public(self.app.get(self.sid))['connectionStopped'])
            with self.assertRaises(HTTPError) as pending:
                native()
            self.assertEqual(400, pending.exception.code)
            launch.assert_not_called()
            bridge._close_done.set()
            bridge._close_error = 'cleanup was not confirmed'
            self.assertFalse(self.app.public(self.app.get(self.sid))['connectionStopped'])
            with self.assertRaises(HTTPError):
                native()
            launch.assert_not_called()
            bridge._close_error = None
            self.assertTrue(self.app.public(self.app.get(self.sid))['connectionStopped'])
            with native() as response:
                self.assertTrue(json.load(response)['ok'])
            launch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
