"""Integration boundaries for native conversation branches and existing app state."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.server import LocalApp, Server


INFO = {'help': '--resume [value]\n --fork-session\n --session-id <uuid>\n', 'version': 'fixture 1'}


class PreparedBridge:
    def __init__(self, command, info, cwd, emit, resume=None, **options):
        self.resume, self.options, self.cwd, self.emit = resume, options, cwd, emit
        self.session_id = options.get('new_session_id') or resume
        self.closed = self.cleanup_complete = self.stopping = self.busy = self._control_active = False
        self.pending, self.tasks, self.capabilities = {}, set(), {}
        self.sent = []
        self.ready = threading.Event()
        self.lock = threading.RLock()

    def model_state(self):
        return {'model': 'fixture-model', 'permissionMode': 'default', 'effort': None}

    def connection_state(self):
        return {'sessionId': self.session_id, **self.model_state(), 'forkConfirmed': False}

    def prepare(self):
        self.ready.set()
        return self.connection_state()

    def close(self):
        self.closed = self.cleanup_complete = True
        return True

    def send(self, prompt):
        self.sent.append(prompt)


class ProductivityRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-productivity-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'plain Claude work'
        self.work.mkdir()
        self.config = self.root / 'config'
        self.environment = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = self.make_app()
        self.addCleanup(self.app.close)
        self.parent_id = self.app.create(str(self.work), True, title='원본 업무')['id']
        self.native_id = str(uuid.uuid4())
        self.source = self.write_transcript(self.native_id)
        parent = self.app.get(self.parent_id)
        parent.update(sessionId=self.native_id, state='done', messages=[{'role': 'user', 'text': 'original prompt'}],
                      _allowBypass=True, _sessionControls={'model': 'parent-override', 'permissionMode': 'bypassPermissions'})
        self.app.save(self.parent_id)
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def make_app(self):
        return LocalApp(self.root / 'state', command=['fixture-cli'], info=INFO,
                        managed_workspace_root=self.root / 'managed')

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def write_transcript(self, sid):
        path = self.config / 'projects' / 'fixture' / (sid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        prompt_id = str(uuid.uuid4())
        rows = [{'type': 'user', 'sessionId': sid, 'uuid': prompt_id, 'parentUuid': None, 'cwd': str(self.work),
                 'message': {'role': 'user', 'content': 'original prompt'}},
                {'type': 'assistant', 'sessionId': sid, 'uuid': str(uuid.uuid4()), 'parentUuid': prompt_id,
                 'cwd': str(self.work), 'message': {'role': 'assistant', 'content': 'original answer'}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
        return path

    def request(self, route='/api/session/branch', data=None, *, sid=None, authorized=True):
        sid = sid or self.parent_id
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + route + '?id=' + sid,
                          data=json.dumps({'id': sid, **data}).encode('utf-8') if data is not None else None,
                          headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def create_branch(self):
        status, value = self.request(data={})
        self.assertEqual(200, status, value)
        return value['session']

    def test_preview_is_read_only_and_api_requires_authorization(self):
        before = self.source.read_bytes()
        history = (self.root / 'state' / 'history-index.json').read_bytes()
        with patch('local_app.server.ClaudeSession') as bridge:
            status, preview = self.request()
            self.assertEqual(200, status, preview)
            self.assertTrue(preview['available'])
            self.assertEqual('latest', preview['scope'])
            self.assertEqual(403, self.request(authorized=False)[0])
            self.assertEqual(403, self.request(data={}, authorized=False)[0])
        bridge.assert_not_called()
        self.assertEqual(1, len(self.app.sessions))
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual(history, (self.root / 'state' / 'history-index.json').read_bytes())

    def test_create_keeps_source_and_separates_trust_controls_queue_and_history(self):
        before = self.source.read_bytes()
        with patch('local_app.server.ClaudeSession') as bridge:
            child = self.create_branch()
        bridge.assert_not_called()
        self.assertFalse(child['trusted'])
        self.assertEqual('idle', child['state'])
        self.assertIsNone(child['sessionId'])
        self.assertNotEqual(self.parent_id, child['id'])
        self.assertEqual([], child['requests'])
        self.assertEqual([], child['artifacts'])
        self.assertIsNone(child['modelOverride'])
        self.assertIsNone(child['permissionModeOverride'])
        item = self.app.get(child['id'])
        self.assertNotIn('_allowBypass', item)
        self.assertNotIn('_sessionControls', item)
        self.assertEqual([], self.app.dispatch.snapshot(child['id'])['queue'])
        self.assertEqual(self.native_id, self.app.get(self.parent_id)['sessionId'])
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual([self.source], list(self.config.rglob('*.jsonl')))

    def test_restart_keeps_pending_fork_and_no_permission_inheritance(self):
        child = self.create_branch()
        restored = self.make_app()
        self.addCleanup(restored.close)
        row = restored.get(child['id'])
        self.assertFalse(row['trusted'])
        self.assertEqual(child['branch'], row['branch'])
        self.assertNotIn('_sessionControls', row)
        self.assertNotIn('_allowBypass', row)
        self.assertEqual({'resume': self.native_id, 'fork_session': True,
                          'new_session_id': child['branch']['childSessionId']}, restored._resume_options(row))

    def test_prepare_rechecks_trust_and_uses_native_fork_without_prompt(self):
        child = self.create_branch()
        with patch('local_app.server.ClaudeSession', side_effect=PreparedBridge) as factory:
            code, _ = self.request('/api/connect', {}, sid=child['id'])
            self.assertEqual(400, code)
            factory.assert_not_called()
            self.app.get(child['id'])['trusted'] = True
            code, value = self.request('/api/connect', {}, sid=child['id'])
            self.assertEqual(200, code, value)
        connected = self.app.get(child['id'])['bridge']
        self.assertEqual(self.native_id, connected.resume)
        self.assertEqual({'fork_session': True, 'new_session_id': child['branch']['childSessionId']}, connected.options)
        self.assertEqual([], connected.sent)
        self.assertEqual('pending', self.app.get(child['id'])['branch']['status'])
        self.assertEqual('idle', value['session']['state'])

    def test_first_explicit_send_uses_fork_and_result_activates_only_child(self):
        child = self.create_branch()
        with patch('local_app.server.ClaudeSession', side_effect=PreparedBridge):
            self.app.send(child['id'], 'try another approach', [], trusted=True)
        item = self.app.get(child['id'])
        self.assertEqual(['try another approach'], item['bridge'].sent)
        self.assertEqual('pending', item['branch']['status'])
        self.assertEqual(self.native_id, self.app.get(self.parent_id)['sessionId'])
        self.write_transcript(child['branch']['childSessionId'])
        self.app._emit_bridge(child['id'], item['bridge'], 'result', {'sessionId': child['branch']['childSessionId']})
        self.assertEqual('active', item['branch']['status'])
        item['bridge'].close()
        self.assertEqual({'resume': child['branch']['childSessionId']}, self.app._resume_options(item))

    def test_changed_parent_refuses_before_saving_business_request(self):
        child = self.create_branch()
        item = self.app.get(child['id'])
        previous = list(item['messages'])
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write('\n')
        with patch('local_app.server.ClaudeSession') as factory, self.assertRaisesRegex(ValueError, '원본 대화가 변경'):
            self.app.send(child['id'], 'do not send', [], trusted=True)
        factory.assert_not_called()
        self.assertEqual(previous, item['messages'])
        self.assertEqual('idle', item['state'])

    def test_prepared_pending_branch_rechecks_source_before_first_prompt(self):
        child = self.create_branch()
        item = self.app.get(child['id'])
        item['trusted'] = True
        with patch('local_app.server.ClaudeSession', side_effect=PreparedBridge):
            self.app.connect(child['id'])
        connected = item['bridge']
        previous = list(item['messages'])
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write('\n')
        with self.assertRaisesRegex(ValueError, '원본 대화가 변경'):
            self.app.send(child['id'], 'do not send stale branch', [], trusted=True)
        self.assertEqual([], connected.sent)
        self.assertEqual(previous, item['messages'])

    def test_retired_child_events_do_not_change_a_replacement_or_parent(self):
        child = self.create_branch()
        item = self.app.get(child['id'])
        current, retired = object(), object()
        item['bridge'] = current
        before = item['sessionId']
        self.app._emit_bridge(child['id'], retired, 'result', {'sessionId': self.native_id})
        self.assertEqual(before, item['sessionId'])
        self.assertEqual('pending', item['branch']['status'])
        self.assertEqual(self.native_id, self.app.get(self.parent_id)['sessionId'])
        item['bridge'] = None

    @unittest.skipUnless(os.name == 'nt', 'Windows native CLI launch contract')
    def test_native_handoff_uses_validated_fork_and_then_child_resume(self):
        child = self.create_branch()
        item = self.app.get(child['id'])
        item['trusted'] = True
        with patch('local_app.server.subprocess.Popen') as launch:
            code, value = self.request('/api/native', {}, sid=child['id'])
            self.assertEqual(200, code, value)
        arguments = launch.call_args.args[0]
        self.assertIn('--resume=' + self.native_id, arguments)
        self.assertIn('--fork-session', arguments)
        self.assertEqual(child['branch']['childSessionId'], arguments[arguments.index('--session-id') + 1])
        self.assertNotIn('--dangerously-skip-permissions', arguments)
        self.assertNotIn('--allow-dangerously-skip-permissions', arguments)
        self.assertNotIn('--print', arguments)
        self.write_transcript(child['branch']['childSessionId'])
        with patch('local_app.server.subprocess.Popen') as launch:
            code, value = self.request('/api/native', {}, sid=child['id'])
            self.assertEqual(200, code, value)
        arguments = launch.call_args.args[0]
        self.assertIn('--resume=' + child['branch']['childSessionId'], arguments)
        self.assertNotIn('--fork-session', arguments)
        self.assertNotIn('--resume=' + self.native_id, arguments)

    @unittest.skipUnless(os.name == 'nt', 'Windows native CLI launch contract')
    def test_native_handoff_rejects_pending_source_change_before_launch(self):
        child = self.create_branch()
        self.app.get(child['id'])['trusted'] = True
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write('\n')
        with patch('local_app.server.subprocess.Popen') as launch:
            code, _ = self.request('/api/native', {}, sid=child['id'])
        self.assertEqual(400, code)
        launch.assert_not_called()

    def test_unsupported_cli_busy_source_and_missing_record_fail_without_new_task(self):
        for change in ('unsupported', 'busy', 'missing'):
            with self.subTest(change=change):
                self.app.info = INFO if change != 'unsupported' else {}
                parent = self.app.get(self.parent_id)
                parent['state'] = 'running' if change == 'busy' else 'done'
                parent['sessionId'] = str(uuid.uuid4()) if change == 'missing' else self.native_id
                code, _ = self.request(data={})
                self.assertEqual(400, code)
                self.assertEqual(1, len(self.app.sessions))

    def test_save_failure_removes_unsaved_branch_only(self):
        with patch.object(self.app, 'save', side_effect=OSError('fixture')), self.assertRaises(OSError):
            self.app.fork_session(self.parent_id)
        self.assertEqual([self.parent_id], list(self.app.sessions))
        self.assertEqual(self.native_id, self.app.get(self.parent_id)['sessionId'])

    def test_idle_dispatch_pump_does_not_hydrate_dormant_history(self):
        for index in range(12):
            sid = self.app.create(str(self.work), True, title='대기 기록 ' + str(index))['id']
            self.app.get(sid)['messages'] = [{'role': 'assistant', 'text': 'saved history ' * 1000}]
            self.app.save(sid)
        restored = self.make_app()
        self.addCleanup(restored.close)
        self.assertTrue(all(row.get('_historyUnloaded') for row in restored.sessions.values()))
        with patch.object(restored.history, 'hydrate', wraps=restored.history.hydrate) as hydrate:
            restored.dispatch.pump()
            restored.dispatch.pump()
            self.assertEqual(0, hydrate.call_count)
        self.assertTrue(all(row.get('_historyUnloaded') for row in restored.sessions.values()))


if __name__ == '__main__':
    unittest.main()
