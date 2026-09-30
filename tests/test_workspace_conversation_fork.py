import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from local_app.bridge import ClaudeSession, cli_arguments
from local_app.conversation_fork import (ConversationForkError, branch_connection, fork_capability,
                                         normalize_branch, prepare_fork, validate_fork_source)


INFO = {'help': '--resume [value]\n --fork-session\n --session-id <uuid>\n'}


class ConversationForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-branch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'claude config'
        self.workspace = self.root / '한글 업무 & files'
        self.workspace.mkdir()
        self.source_id = str(uuid.uuid4())
        self.parent = {'id': str(uuid.uuid4()), 'sessionId': self.source_id, 'title': '원래 대화',
                       'workspace': str(self.workspace), 'state': 'done', 'trusted': True,
                       'pinned': True, 'requests': {}, 'messages': [{'role': 'user', 'text': 'UI is not model context'}],
                       '_allowBypass': True, '_sessionControls': {'permissionMode': 'bypassPermissions'},
                       'queue': [{'text': 'DO NOT COPY'}], 'attachments': ['private.csv'], 'bridge': None}
        self.records = self.rows(self.source_id)
        self.source = self.write(self.source_id, self.records)

    def rows(self, sid):
        prompt_id = str(uuid.uuid4())
        return [{'type': 'user', 'sessionId': sid, 'uuid': prompt_id, 'parentUuid': None,
                 'cwd': str(self.workspace), 'message': {'role': 'user', 'content': '원본 질문'}},
                {'type': 'assistant', 'sessionId': sid, 'uuid': str(uuid.uuid4()), 'parentUuid': prompt_id,
                 'cwd': str(self.workspace), 'message': {'role': 'assistant', 'content': '원본 답변'}}]

    def write(self, sid, rows):
        path = self.config / 'projects' / 'work' / (sid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
        return path

    def seed(self):
        return prepare_fork(self.parent, INFO, self.config)

    def test_real_transcript_preview_new_ids_and_no_permission_or_queue_copy(self):
        before = copy.deepcopy(self.parent)
        source_bytes = self.source.read_bytes()
        with patch('subprocess.Popen') as popen:
            child = self.seed()
        popen.assert_not_called()
        self.assertEqual(self.parent, before)
        self.assertEqual(source_bytes, self.source.read_bytes())
        self.assertEqual([self.source], list(self.config.rglob('*.jsonl')))
        self.assertNotEqual(child['id'], self.parent['id'])
        self.assertNotEqual(child['branch']['childSessionId'], self.source_id)
        self.assertIsNone(child['sessionId'])
        self.assertFalse(child['trusted'])
        self.assertFalse(child['pinned'])
        self.assertEqual([{'role': 'user', 'text': '원본 질문'}, {'role': 'assistant', 'text': '원본 답변'}], child['messages'])
        self.assertEqual(str(self.workspace), child['workspace'])
        self.assertEqual(str(self.config), child['importedConfigRoot'])
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), child['branch']['sourceFingerprint'])
        for key in ('queue', 'attachments', '_allowBypass', '_sessionControls', 'bridge', 'requests', 'choice', 'artifacts'):
            self.assertNotIn(key, child)
        child['messages'][0]['text'] = 'child display changed'
        self.assertEqual(source_bytes, self.source.read_bytes())

    def test_capability_requires_exact_advertised_native_flags(self):
        self.assertTrue(fork_capability(INFO)['available'])
        self.assertFalse(fork_capability(INFO)['atMessage'])
        for help_text in ('', '--resume-session-at --fork-session --session-id',
                          '--resume --fork-session-extra --session-id', '--resume --fork-session', None):
            with self.subTest(help=help_text):
                self.assertFalse(fork_capability({'help': help_text})['available'])
                with self.assertRaises(ConversationForkError):
                    prepare_fork(self.parent, {'help': help_text}, self.config)

    def test_busy_paused_partial_and_pending_decisions_refused(self):
        for state in ('starting', 'running', 'question', 'approval', 'stopped', 'error'):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, '마친 뒤'):
                prepare_fork({**self.parent, 'state': state}, INFO, self.config)
        for field in ('requests', 'choice', '_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                prepare_fork({**self.parent, field: True}, INFO, self.config)
        bridge = SimpleNamespace(busy=False, pending={}, tasks={'worker'}, _control_active=False)
        with self.assertRaises(ValueError):
            prepare_fork({**self.parent, 'bridge': bridge}, INFO, self.config)

    def test_missing_transcript_and_workspace_mismatch_never_use_ui_text_as_context(self):
        with self.assertRaises(ValueError):
            prepare_fork({**self.parent, 'sessionId': str(uuid.uuid4())}, INFO, self.config)
        with self.assertRaisesRegex(ValueError, '폴더가 다릅니다'):
            prepare_fork({**self.parent, 'workspace': str(self.root)}, INFO, self.config)
        with self.assertRaises(ValueError):
            prepare_fork({**self.parent, 'sessionId': '--dangerously-skip-permissions'}, INFO, self.config)

    def test_original_profile_and_pending_branch_profile_are_bound(self):
        with self.assertRaisesRegex(ValueError, '설정 위치'):
            prepare_fork({**self.parent, 'importedConfigRoot': str(self.root / 'other')}, INFO, self.config)
        child = self.seed()
        with self.assertRaisesRegex(ValueError, '설정 위치'):
            branch_connection(child, INFO, self.root / 'other')
        self.assertFalse((self.root / 'other').exists())

    def test_pending_connection_uses_fork_only_while_source_unchanged(self):
        child = self.seed()
        options = branch_connection(child, INFO, self.config)
        self.assertEqual({'resume': self.source_id, 'fork_session': True,
                          'new_session_id': child['branch']['childSessionId']}, options)
        # Even if the parent ends up with identical visible text, hidden changes
        # cannot silently move the branch's native starting point.
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'sessionId': self.source_id, 'type': 'metadata', 'value': 'new'}) + '\n')
        with self.assertRaisesRegex(ValueError, '원본 대화가 변경'):
            branch_connection(child, INFO, self.config)

    def test_existing_native_child_resumes_independently_when_parent_changes(self):
        child = self.seed()
        child_id = child['branch']['childSessionId']
        self.write(child_id, self.rows(child_id))
        self.source.write_text('parent no longer valid', encoding='utf-8')
        self.assertEqual({'resume': child_id}, branch_connection(child, {}, self.config))
        self.assertEqual('pending', child['branch']['status'], 'connection helper must not mutate caller metadata')

    def test_active_missing_or_corrupt_child_never_reforks_parent(self):
        child = self.seed()
        child['branch']['status'] = 'active'
        with self.assertRaisesRegex(ValueError, '원본 대화로 대신 연결하지 않습니다'):
            branch_connection(child, INFO, self.config)
        child['branch']['status'] = 'pending'
        self.write(child['branch']['childSessionId'], [])
        with self.assertRaises(ValueError):
            branch_connection(child, INFO, self.config)

    def test_wrong_child_identity_or_workspace_refused(self):
        child = self.seed()
        child['sessionId'] = self.source_id
        with self.assertRaisesRegex(ValueError, '세션 ID가 다릅니다'):
            branch_connection(child, INFO, self.config)
        child['sessionId'] = None
        child_id = child['branch']['childSessionId']
        rows = self.rows(child_id)
        for record in rows:
            record['cwd'] = str(self.root)
        self.write(child_id, rows)
        with self.assertRaisesRegex(ValueError, '업무 폴더'):
            branch_connection(child, INFO, self.config)

    def test_metadata_sanitizer_discards_extras_and_rejects_corruption(self):
        branch = self.seed()['branch']
        self.assertEqual(branch, normalize_branch({**branch, 'trusted': True, '_allowBypass': True}))
        for patch_value in ({'status': 'other'}, {'scope': 'message'}, {'sourceFingerprint': 'bad'},
                            {'childSessionId': self.source_id}, {'configRoot': '../other'}, {'createdAt': float('nan')},
                            {'sourceTaskId': '1'}, {'sourceTitle': 'x' * 201}):
            with self.subTest(patch=patch_value), self.assertRaises(ValueError):
                normalize_branch({**branch, **patch_value})

    def test_source_modification_during_preview_rejected(self):
        from local_app.conversation_fork import SessionImporter
        original = SessionImporter.load
        def changed(reader, sid):
            result = original(reader, sid)
            with self.source.open('ab') as stream:
                stream.write(b'\n')
            return result
        with patch.object(SessionImporter, 'load', changed), self.assertRaisesRegex(ValueError, '변경'):
            self.seed()

    def test_source_size_limit_and_display_memory_cap(self):
        with patch('local_app.conversation_fork.MAX_FILE_BYTES', 10), self.assertRaisesRegex(ValueError, '32 MiB'):
            self.seed()
        records = []
        last = None
        for index in range(180):
            record = {'type': 'user' if index % 2 == 0 else 'assistant', 'sessionId': self.source_id,
                      'uuid': str(uuid.uuid4()), 'parentUuid': last, 'cwd': str(self.workspace),
                      'message': {'content': str(index) + 'x' * 5000}}
            last = record['uuid']
            records.append(record)
        self.write(self.source_id, records)
        child = self.seed()
        self.assertLessEqual(len(child['messages']), 150)
        self.assertLessEqual(sum(len(message['text']) for message in child['messages']), 500000)


class ConversationForkBridgeTests(unittest.TestCase):
    def setUp(self):
        self.source_id, self.child_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.events = []
        self.bridge = ClaudeSession(['fixture'], INFO, Path.cwd(), lambda kind, data: self.events.append((kind, data)),
                                    self.source_id, fork_session=True, new_session_id=self.child_id)
        self.addCleanup(self.bridge.close)

    def test_native_arguments_and_no_parent_permission_inheritance(self):
        arguments = cli_arguments(['claude'], INFO, self.source_id, fork_session=True, new_session_id=self.child_id)
        self.assertIn('--resume=' + self.source_id, arguments)
        self.assertIn('--session-id=' + self.child_id, arguments)
        self.assertIn('--fork-session', arguments)
        for name in ('--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', '--model', '--permission-mode', '--settings'):
            self.assertNotIn(name, arguments)
        for source, target in (('--dangerously-skip-permissions', self.child_id), (self.source_id, '--model=other'),
                               (self.source_id, self.source_id)):
            with self.assertRaises(ValueError):
                cli_arguments(['claude'], INFO, source, fork_session=True, new_session_id=target)
        with self.assertRaises(ValueError):
            cli_arguments(['claude'], {}, self.source_id, fork_session=True, new_session_id=self.child_id)
        with self.assertRaises(ValueError):
            cli_arguments(['claude'], INFO, self.source_id, new_session_id=self.child_id)
        with self.assertRaises(ValueError):
            cli_arguments(['claude'], INFO, self.source_id, fork_session='yes', new_session_id=self.child_id)

    def test_new_branch_may_explicitly_opt_in_to_bypass_after_confirmation(self):
        info = {'help': INFO['help'] + '--allow-dangerously-skip-permissions Enable optional mode\n'
                '  --permission-mode <mode> (choices: "default", "bypassPermissions")\n'}
        arguments = cli_arguments(['claude'], info, self.source_id, fork_session=True,
                                  new_session_id=self.child_id, allow_bypass_permissions=True)
        self.assertIn('--allow-dangerously-skip-permissions', arguments)
        self.assertIn('--fork-session', arguments)
        self.assertNotIn('--dangerously-skip-permissions', arguments)
        self.assertNotIn('--permission-mode', arguments)

    def test_initialize_ack_is_not_native_fork_confirmation(self):
        self.bridge.handle({'type': 'control_response', 'response': {'subtype': 'success',
                            'request_id': self.bridge.initialize_id, 'response': {}}})
        self.assertTrue(self.bridge.ready.is_set())
        self.assertEqual(self.child_id, self.bridge.session_id)
        self.assertFalse(self.bridge.connection_state()['forkConfirmed'])
        self.assertIsNone(self.bridge.resume_id)

    def test_native_child_identity_is_confirmed_and_used_for_followup(self):
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'session_id': self.child_id})
        self.assertTrue(self.bridge.connection_state()['forkConfirmed'])
        self.assertEqual(self.child_id, self.bridge.resume_id)
        self.assertEqual(self.child_id, self.events[-1][1]['sessionId'])
        self.bridge.handle({'type': 'result', 'session_id': self.child_id, 'is_error': False})
        self.assertEqual(self.child_id, self.events[-1][1]['sessionId'])
        self.assertFalse(any(data.get('sessionId') == self.source_id for _, data in self.events))

    def test_parent_wrong_missing_and_late_ids_fail_closed(self):
        for native in (self.source_id, str(uuid.uuid4()), '--flag', None):
            with self.subTest(native=native):
                events = []
                bridge = ClaudeSession(['fixture'], INFO, Path.cwd(), lambda k, d: events.append((k, d)),
                                       self.source_id, fork_session=True, new_session_id=self.child_id)
                bridge.handle({'type': 'system', 'subtype': 'init', 'session_id': native})
                self.assertTrue(bridge.closed)
                self.assertEqual('fork_identity', events[-1][1]['code'])
                self.assertFalse(any(kind == 'connected' for kind, _ in events))
                count = len(events)
                bridge.handle({'type': 'result', 'session_id': self.source_id, 'result': 'late parent result'})
                self.assertEqual(count, len(events))
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'session_id': self.child_id})
        self.bridge.handle({'type': 'assistant', 'session_id': self.source_id,
                            'message': {'content': [{'type': 'text', 'text': 'wrong source'}]}})
        self.assertTrue(self.bridge.closed)
        self.assertFalse(any(kind == 'assistant' for kind, _ in self.events))

    def test_child_subagent_ids_do_not_replace_branch_identity(self):
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'session_id': self.child_id})
        self.bridge.handle({'type': 'assistant', 'session_id': str(uuid.uuid4()), 'parent_tool_use_id': 'child-tool',
                            'message': {'content': [{'type': 'text', 'text': 'worker text'}]}})
        self.assertFalse(self.bridge.closed)
        self.assertEqual(self.child_id, self.bridge.connection_state()['sessionId'])

    def test_control_only_process_forks_without_model_prompt(self):
        fixture = '''import json, sys
from pathlib import Path
def emit(value):
    print(json.dumps(value), flush=True)
Path('arguments.json').write_text(json.dumps(sys.argv), encoding='utf-8')
sid = next(value.split('=',1)[1] for value in sys.argv if value.startswith('--session-id='))
for line in sys.stdin:
    frame=json.loads(line)
    with Path('frames.jsonl').open('a', encoding='utf-8') as log: log.write(json.dumps(frame)+'\\n')
    assert frame['type']=='control_request', 'model request forbidden'
    subtype=frame['request']['subtype']
    if subtype=='initialize': emit({'type':'system','subtype':'init','session_id':sid})
    detail={'applied':{}} if subtype=='get_settings' else {}
    emit({'type':'control_response','response':{'subtype':'success','request_id':frame['request_id'],'response':detail}})
'''
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            path = folder / 'control_only.py'
            path.write_text(fixture, encoding='utf-8')
            bridge = ClaudeSession([sys.executable, '-X', 'utf8', str(path)], INFO, folder, lambda *_: None,
                                   self.source_id, fork_session=True, new_session_id=self.child_id)
            try:
                result = bridge.prepare()
                self.assertEqual(self.child_id, result['sessionId'])
                self.assertTrue(result['forkConfirmed'])
            finally:
                bridge.close()
            arguments = json.loads((folder / 'arguments.json').read_text(encoding='utf-8'))
            self.assertIn('--fork-session', arguments)
            frames = [json.loads(line) for line in (folder / 'frames.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(frames)
            self.assertTrue(all(frame['type'] == 'control_request' for frame in frames))
            self.assertFalse(bridge.busy)


if __name__ == '__main__':
    unittest.main()
