"""HTTP boundaries for ordinary Claude imports, notifications and Bypass opt-in.

Fixtures are synthetic and the control bridge records calls instead of starting
Claude. No test reads a real profile, invokes AI, or changes OS notification or
permission settings.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.server import LocalApp, Server


class ControlBridge:
    def __init__(self, *, resume=None, allow=False, available=True):
        self.closed = self.stopping = self.cleanup_complete = False
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.ready.set()
        self.session_id = resume
        self.allow_bypass_permissions = allow
        self.available = available
        self.calls = []
        self.original_model = 'original-model'
        self.original_permission_mode = 'default'
        self._effort_baselines = {'original-model': 'medium', 'chosen-model': 'low'}
        self.model = 'original-model'
        self.model_override = None
        self.effort = 'medium'
        self.effort_override = None
        self.permission_mode = 'default'
        self.permission_mode_override = None
        self.deny_bypass = False

    def model_state(self):
        return {'model': self.model, 'modelOverride': self.model_override,
                'effort': self.effort, 'effortOverride': self.effort_override,
                'permissionMode': self.permission_mode, 'permissionModeOverride': self.permission_mode_override,
                'bypassPermissions': {'available': self.available}}

    def connection_state(self):
        return {'sessionId': self.session_id, **self.model_state()}

    def close(self):
        self.calls.append(('close',))
        self.closed = self.cleanup_complete = True
        return True

    def prepare(self):
        self.calls.append(('prepare',))
        return self.connection_state()

    def set_model(self, value):
        self.calls.append(('model', value))
        self.model, self.model_override = value or self.original_model, value
        return self.model_state()

    def set_effort(self, value):
        self.calls.append(('effort', value))
        self.effort, self.effort_override = value or self._effort_baselines[self.model], value
        return self.model_state()

    def set_permission_mode(self, value):
        self.calls.append(('permission', value))
        if value == 'bypassPermissions' and self.deny_bypass:
            self.available = False
            raise ValueError('Fixture policy declined Bypass')
        self.permission_mode, self.permission_mode_override = value or self.original_permission_mode, value
        return self.model_state()

    def send(self, prompt):
        raise AssertionError('A control-only operation sent a business message')


class ContinuityRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'ordinary claude config'
        self.cwd = self.root / '한글 원래 업무 & files'
        self.cwd.mkdir()
        self.cli_sid = str(uuid.uuid4())
        self.path = self.transcript(self.config)
        self.context = patch('local_app.server.runtime_context', side_effect=lambda _: {
            'configRoot': str(self.config), 'authentication': 'shared-with-cli', 'entry': 'synthetic'})
        self.context.start()
        self.addCleanup(self.context.stop)
        self.app = self.new_app()
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def new_app(self):
        app = LocalApp(self.root / 'state', command=['synthetic-never-start'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(app.close)
        return app

    def transcript(self, config):
        path = config / 'projects' / 'arbitrary-project-name' / (self.cli_sid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        user = {'type': 'user', 'uuid': str(uuid.uuid4()), 'parentUuid': None,
                'sessionId': self.cli_sid, 'cwd': str(self.cwd), 'timestamp': '2026-09-30T01:00:00Z',
                'message': {'role': 'user', 'content': 'Original ordinary Claude question'}}
        answer = {'type': 'assistant', 'uuid': str(uuid.uuid4()), 'parentUuid': user['uuid'],
                  'sessionId': self.cli_sid, 'cwd': str(self.cwd), 'timestamp': '2026-09-30T01:01:00Z',
                  'message': {'role': 'assistant', 'content': [
                      {'type': 'text', 'text': 'Original answer'},
                      {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'PRIVATE_COMMAND'}}]}}
        path.write_text('\n'.join(json.dumps(value, ensure_ascii=False) for value in [user, answer]) + '\n', encoding='utf-8')
        return path

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, data=None, *, auth=True):
        headers = {'Content-Type': 'application/json', 'Origin': self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + path, headers=headers,
                          data=json.dumps(data).encode('utf-8') if data is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def imported(self):
        code, response = self.request('/api/claude-sessions/import', {'sessionId': self.cli_sid})
        self.assertEqual(200, code, response)
        return response['session']['id']

    def test_authentication_precedes_any_import_read_or_mutation(self):
        with patch.object(self.app, 'import_sessions') as read, patch.object(self.app, 'import_session') as write:
            for path, data in [('/api/claude-sessions', None),
                               ('/api/claude-sessions?sessionId=' + self.cli_sid, None),
                               ('/api/claude-sessions/import', {'sessionId': self.cli_sid})]:
                self.assertEqual(403, self.request(path, data, auth=False)[0])
            read.assert_not_called()
            write.assert_not_called()

    def test_list_is_metadata_only_and_import_deduplicates_without_ai_or_original_changes(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        with patch('local_app.server.ClaudeSession') as child, patch('local_app.server.subprocess.Popen') as popen:
            code, listing = self.request('/api/claude-sessions')
            self.assertEqual(200, code)
            self.assertEqual(1, len(listing['sessions']))
            self.assertNotIn('messages', listing['sessions'][0])
            self.assertNotIn('PRIVATE_COMMAND', json.dumps(listing))
            sid = self.imported()
            code, repeated = self.request('/api/claude-sessions/import', {'sessionId': self.cli_sid})
            self.assertEqual(200, code)
            self.assertTrue(repeated['existing'])
            self.assertEqual(sid, repeated['session']['id'])
            child.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(1, len(self.app.sessions))
        item = self.app.get(sid)
        self.assertEqual(str(self.cwd), item['workspace'])
        self.assertEqual(self.cli_sid, item['sessionId'])
        self.assertFalse(item['trusted'])
        self.assertEqual('idle', item['state'])
        self.assertEqual({}, item['requests'])
        self.assertIsNone(item['bridge'])
        self.assertEqual(['Original ordinary Claude question', 'Original answer'], [m['text'] for m in item['messages']])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_import_preserves_trust_boundary_and_config_scope_after_restart(self):
        sid = self.imported()
        reloaded = self.new_app()
        original = reloaded.get(sid)
        self.assertFalse(original['trusted'])
        self.assertEqual(str(self.config.resolve()), original['importedConfigRoot'])
        self.app.get(sid)['trusted'] = False
        with patch('local_app.server.ClaudeSession') as child:
            self.assertEqual(400, self.request('/api/connect', {'id': sid})[0])
            self.assertEqual(400, self.request('/api/send', {'id': sid, 'text': 'Do not send'})[0])
            child.assert_not_called()

    def test_deep_invalid_json_does_not_break_list_preview_or_import_transport(self):
        self.path.write_bytes(b'[' * 20000 + b'0' + b']' * 20000 + b'\n' + self.path.read_bytes())
        before = self.path.read_bytes()
        with patch('local_app.server.ClaudeSession') as child:
            code, listing = self.request('/api/claude-sessions')
            self.assertEqual(200, code, listing)
            self.assertEqual(1, len(listing['sessions']))
            code, preview = self.request('/api/claude-sessions?sessionId=' + self.cli_sid)
            self.assertEqual(200, code, preview)
            self.assertTrue(preview['truncated'])
            self.assertEqual(2, len(preview['messages']))
            self.assertEqual(2, len(self.app.get(self.imported())['messages']))
            child.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_configuration_blocks_connect_send_and_native_handoff(self):
        sid = self.imported()
        self.app.get(sid)['trusted'] = True
        self.config = self.root / 'another-config'
        with patch('local_app.server.ClaudeSession') as child, patch('local_app.server.subprocess.Popen') as popen:
            for path, body in [('/api/connect', {'id': sid}),
                               ('/api/send', {'id': sid, 'text': 'Do not send', 'trusted': True}),
                               ('/api/native', {'id': sid})]:
                with self.subTest(path=path):
                    code, response = self.request(path, body)
                    self.assertEqual(400, code, response)
                    self.assertIn('설정 위치', response['error'])
            child.assert_not_called()
            popen.assert_not_called()
        self.assertEqual(2, len(self.app.get(sid)['messages']))

    def test_same_uuid_in_different_config_is_not_deduplicated_to_old_scope(self):
        sid = self.imported()
        self.config = self.root / 'second-config'
        self.transcript(self.config)
        code, response = self.request('/api/claude-sessions/import', {'sessionId': self.cli_sid})
        self.assertEqual(200, code, response)
        self.assertFalse(response['existing'])
        self.assertNotEqual(sid, response['session']['id'])
        self.assertEqual(str(self.config.resolve()), self.app.get(response['session']['id'])['importedConfigRoot'])

    def test_malformed_uuid_and_deleted_original_workspace_never_launch(self):
        with patch('local_app.server.ClaudeSession') as child:
            for value in ['../file', self.cli_sid + ' --model other', None, '']:
                self.assertEqual(400, self.request('/api/claude-sessions/import', {'sessionId': value})[0])
            self.cwd.rmdir()
            self.assertEqual(400, self.request('/api/claude-sessions/import', {'sessionId': self.cli_sid})[0])
            child.assert_not_called()
        self.assertEqual({}, self.app.sessions)

    def test_notifications_require_auth_and_open_the_correct_existing_task(self):
        sid = self.imported()
        other = self.app.create(str(self.cwd), True, title='Other task')['id']
        with patch.object(self.app.desktop, 'configure', wraps=self.app.desktop.configure) as configure:
            self.assertEqual(403, self.request('/api/notifications', {'action': 'configure', 'preferences': {'enabled': False}}, auth=False)[0])
            configure.assert_not_called()
        self.assertEqual(200, self.request('/api/notifications', {'action': 'configure', 'preferences': {'enabled': False}})[0])
        self.app.get(sid)['lastRunId'] = uuid.uuid4().hex
        self.app.emit(sid, 'result', {'sessionId': self.cli_sid})
        self.app.get(other)['lastRunId'] = uuid.uuid4().hex
        self.app.emit(other, 'error', {'message': 'PRIVATE_ERROR_DETAIL'})
        code, attention = self.request('/api/attention')
        self.assertEqual(200, code)
        self.assertEqual(2, attention['desktop']['unreadCount'])
        self.assertNotIn('PRIVATE_ERROR_DETAIL', json.dumps(attention['desktop']))
        selected = next(row for row in attention['desktop']['inbox'] if row['sessionId'] == sid)
        self.assertEqual('disabled', selected['delivery'])
        callback = Mock()
        self.app._open_window_callback = callback
        self.assertEqual(200, self.request('/api/notifications', {'action': 'open', 'notificationId': selected['id']})[0])
        callback.assert_called_once_with()
        self.assertEqual(sid, self.app._navigation['sessionId'])
        self.assertEqual(1, self.app.desktop.snapshot()['unreadCount'])
        self.assertEqual(200, self.request('/api/notifications', {'action': 'read'})[0])
        self.assertEqual(0, self.app.desktop.snapshot()['unreadCount'])

    def controls(self, *, available=True):
        sid = self.imported()
        item = self.app.get(sid)
        item['trusted'] = True
        item['state'] = 'done'
        old = ControlBridge(resume=self.cli_sid, available=available)
        old.set_model('chosen-model')
        old.set_effort('high')
        old.set_permission_mode('acceptEdits')
        old.calls.clear()
        item.update(bridge=old, connection=old.connection_state(), _sessionControls={
            'model': 'chosen-model', 'effort': 'high', 'permissionMode': 'acceptEdits'})
        return sid, item, old

    def test_bypass_needs_literal_confirmation_and_supported_connection(self):
        sid, item, old = self.controls()
        with patch('local_app.server.ClaudeSession') as factory:
            for confirmed in [False, 'true', 1, None]:
                code, _ = self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': confirmed})
                self.assertEqual(400, code)
            self.assertEqual([], old.calls)
            factory.assert_not_called()
            old.available = False
            self.assertEqual(400, self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': True})[0])
            factory.assert_not_called()
        self.assertFalse(old.closed)
        self.assertEqual('acceptEdits', item['_sessionControls']['permissionMode'])
        self.assertFalse(item.get('_allowBypass', False))

    def test_confirmed_bypass_reconnects_same_uuid_and_restores_controls_without_turn(self):
        sid, item, old = self.controls()
        before = deepcopy(item['messages'])
        replacement = ControlBridge(resume=self.cli_sid, allow=True)
        # A resumed CLI can report selected values as its startup defaults. The
        # app must retain the pre-change values for subsequent reset controls.
        replacement.original_model = 'resumed-selected-model'
        replacement.original_permission_mode = 'acceptEdits'
        replacement._effort_baselines = {'chosen-model': 'high'}
        with patch('local_app.server.ClaudeSession', return_value=replacement) as factory:
            code, result = self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': True})
        self.assertEqual(200, code, result)
        self.assertEqual([('close',)], old.calls)
        self.assertEqual(self.cli_sid, factory.call_args.args[4])
        self.assertEqual(self.cwd, factory.call_args.args[2])
        self.assertTrue(factory.call_args.kwargs['allow_bypass_permissions'])
        self.assertEqual([('prepare',), ('model', 'chosen-model'), ('effort', 'high'),
                          ('permission', 'acceptEdits'), ('permission', 'bypassPermissions')], replacement.calls)
        self.assertEqual('bypassPermissions', result['session']['connection']['permissionMode'])
        self.assertEqual('done', item['state'])
        self.assertEqual(before, item['messages'])
        self.assertIsNone(item['lastRunId'])
        self.assertEqual('original-model', replacement.original_model)
        self.assertEqual('default', replacement.original_permission_mode)
        self.assertEqual('low', replacement._effort_baselines['chosen-model'])
        code, reset = self.request('/api/effort', {'id': sid, 'effort': None})
        self.assertEqual(200, code, reset)
        self.assertEqual('low', reset['session']['connection']['effort'])
        code, reset = self.request('/api/permission-mode', {'id': sid, 'mode': None})
        self.assertEqual(200, code, reset)
        self.assertEqual('default', reset['session']['connection']['permissionMode'])
        code, reset = self.request('/api/model', {'id': sid, 'model': None})
        self.assertEqual(200, code, reset)
        self.assertEqual('original-model', reset['session']['connection']['model'])
        self.assertEqual(before, item['messages'])

    def test_policy_rejection_does_not_record_a_bypass_override_or_send_prompt(self):
        sid, item, old = self.controls()
        before = deepcopy(item['messages'])
        replacement = ControlBridge(resume=self.cli_sid, allow=True)
        replacement.deny_bypass = True
        with patch('local_app.server.ClaudeSession', return_value=replacement):
            code, result = self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': True})
        self.assertEqual(400, code, result)
        self.assertEqual('acceptEdits', item['_sessionControls']['permissionMode'])
        self.assertEqual('acceptEdits', item['connection']['permissionMode'])
        self.assertEqual('acceptEdits', item['connection']['permissionModeOverride'])
        self.assertEqual(before, item['messages'])
        self.assertFalse(item['_permissionUpdating'])

    def test_failed_close_preserves_old_connection_without_starting_opted_in_child(self):
        sid, item, old = self.controls()
        old.close = Mock(return_value=False)
        with patch('local_app.server.ClaudeSession') as factory:
            code, response = self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': True})
            self.assertEqual(400, code, response)
            factory.assert_not_called()
        self.assertIs(old, item['bridge'])
        self.assertEqual('acceptEdits', item['_sessionControls']['permissionMode'])
        self.assertFalse(item.get('_allowBypass', False))
        self.assertFalse(item['_permissionUpdating'])

    def test_failed_control_restore_does_not_apply_bypass_or_erase_previous_choices(self):
        sid, item, old = self.controls()
        before = deepcopy(item['messages'])
        replacement = ControlBridge(resume=self.cli_sid, allow=True)
        replacement.set_effort = Mock(side_effect=ValueError('Fixture restore failure'))
        with patch('local_app.server.ClaudeSession', return_value=replacement):
            code, response = self.request('/api/permission-mode', {'id': sid, 'mode': 'bypassPermissions', 'bypassConfirmed': True})
        self.assertEqual(400, code, response)
        self.assertNotIn(('permission', 'bypassPermissions'), replacement.calls)
        self.assertEqual({'model': 'chosen-model', 'effort': 'high', 'permissionMode': 'acceptEdits'}, item['_sessionControls'])
        self.assertTrue(item['_needsControlRestore'])
        self.assertEqual(before, item['messages'])


if __name__ == '__main__':
    unittest.main()
