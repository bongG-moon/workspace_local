"""Plain chat ownership, deletion and API boundaries; no network or real Claude."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from local_app.bridge import BridgeError, CHAT_REQUIRED_FLAGS
from local_app.history import HistoryStore
from local_app.idle_connections import idle_candidate, replacement_candidate, _prepare_release, _finish_release
from local_app.server import LocalApp, Server
from tests.test_workspace_idle_connections import MemoryBridge

INFO = {'version': '2.1.296 (fixture)', 'help': ' '.join(CHAT_REQUIRED_FLAGS)}


class PlainChatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ax-plain-chat-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'state'
        self.app = self.make_app()

    def make_app(self, demo=False):
        app = LocalApp(self.state, command=['fixture-never-start'], info=INFO, demo=demo,
                       managed_workspace_root=self.root / 'managed')
        self.addCleanup(app.close)
        return app

    def chat(self, title=None):
        return self.app.get(self.app.create(kind='chat', title=title)['id'])

    def work(self):
        return self.app.get(self.app.create(str(self.root), True)['id'])

    def upload(self, sid, name='reference.txt', content=b'GENERATED_REFERENCE_7821'):
        return self.app.attachment_store.save(sid, name, io.BytesIO(content), len(content))['path']

    def test_creation_uses_owned_directory_without_work_trust_and_preserves_work(self):
        chat = self.chat()
        self.assertEqual('chat-v1', chat['runtimeProfile'])
        self.assertTrue(chat['trusted'])
        workspace = Path(chat['workspace'])
        self.assertEqual(self.state / 'chat-spaces' / chat['id'] / 'workspace', workspace)
        self.assertTrue(workspace.is_dir())
        self.assertEqual([], self.app.files(chat['id']))
        self.assertEqual({'runtime_profile': 'chat-v1'}, self.app.chat_runtime_options(chat))
        with self.assertRaises(ValueError):
            self.app.create(str(self.root), kind='chat')
        with self.assertRaises(ValueError):
            self.app.create(kind='chat', managed=True)
        with self.assertRaises(ValueError):
            self.app.create(str(workspace), True)
        with self.assertRaises(ValueError):
            self.app.create(str(self.root))
        work = self.work()
        self.assertEqual(('work', 'work'), (work['kind'], work['runtimeProfile']))
        self.assertEqual({}, self.app.chat_runtime_options(work))

    def test_kind_persists_and_legacy_rows_remain_work(self):
        chat, work = self.chat(), self.work()
        self.app.close()
        for path in (self.state / 'history-index.json', self.state / 'history-sessions' / (work['id'] + '.json')):
            value = json.loads(path.read_text(encoding='utf-8'))
            row = next(row for row in value['sessions'] if row['id'] == work['id']) if 'sessions' in value else value
            row.pop('kind', None)
            row.pop('runtimeProfile', None)
            path.write_text(json.dumps(value), encoding='utf-8')
        restored = self.make_app()
        self.assertEqual('chat', restored.get(chat['id'])['kind'])
        self.assertTrue(restored.get(chat['id'])['trusted'])
        self.assertEqual('chat-v1', restored.get(chat['id'])['runtimeProfile'])
        self.assertEqual('work', restored.get(work['id'])['kind'])
        # Explicit folder approval is restored from app-owned preferences;
        # it is still absent from the conversation history itself.
        self.assertTrue(restored.get(work['id'])['trusted'])
        self.assertIsNone(restored.get(chat['id'])['bridge'])

    def test_kind_profile_mismatch_is_not_loaded_as_unrestricted_chat(self):
        chat = self.chat()
        self.app.close()
        path = self.state / 'history-index.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['sessions'][0]['runtimeProfile'] = 'work'
        path.write_text(json.dumps(value), encoding='utf-8')
        store = HistoryStore(self.state)
        self.assertEqual([], store.load(lazy=True))
        self.assertTrue(store.warning)
        self.assertTrue(Path(chat['workspace']).is_dir())

    def test_send_supplies_attachment_content_and_chat_profile_without_workspace_scan(self):
        chat = self.chat()
        upload = self.upload(chat['id'])
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory, \
                patch('local_app.server.snapshot', side_effect=AssertionError('chat must not scan workspace')), \
                patch.object(self.app.file_diffs, 'start', side_effect=AssertionError('chat must not diff files')):
            self.app.send(chat['id'], '이 자료를 요약해줘', [upload])
        self.assertEqual('chat-v1', factory.call_args.kwargs['runtime_profile'])
        sent = chat['bridge'].sent[0]
        self.assertIsInstance(sent, list)
        text = '\n'.join(row.get('text', '') for row in sent)
        self.assertIn('GENERATED_REFERENCE_7821', text)
        self.assertNotIn(upload, text)
        self.assertNotIn('사용자가 선택한 원본 파일 경로', text)
        self.assertEqual('read', chat['messages'][0]['attachmentReads'][0]['status'])
        self.assertTrue(list((Path(chat['workspace']).parent / 'cache').glob('*.json')))

    def test_work_keeps_existing_path_transport_and_original_file(self):
        work = self.work()
        original = self.root / 'original.txt'
        original.write_text('original data', encoding='utf-8')
        with patch('local_app.server.ClaudeSession', side_effect=MemoryBridge) as factory:
            self.app.send(work['id'], 'read selected file', [str(original)])
        self.assertNotIn('runtime_profile', factory.call_args.kwargs)
        sent = work['bridge'].sent[0]
        self.assertIsInstance(sent, str)
        self.assertEqual([str(original)], json.loads(sent.splitlines()[-1]))
        self.assertIn('사용자가 선택한 원본 파일 경로', sent)
        self.assertEqual('original data', original.read_text(encoding='utf-8'))

    def test_retained_chat_reconnect_never_restores_tool_permission_mode(self):
        class StrictChatBridge(MemoryBridge):
            def set_permission_mode(self, value):
                raise BridgeError('chat_tools_disabled', 'chat has no permission controls', 'work only')

        chat = self.chat()
        with patch('local_app.server.ClaudeSession', side_effect=StrictChatBridge):
            self.app.send(chat['id'], 'first conversation turn', [])
            original_id = chat['bridge'].session_id
            with self.app.lock:
                old, resumable = _prepare_release(self.app, chat, 'test-retention')
            self.assertTrue(_finish_release(self.app, chat, old, resumable))
            self.app.send(chat['id'], 'continue the same conversation', [])
        self.assertEqual(original_id, chat['bridge'].session_id)
        self.assertEqual(1, len(chat['bridge'].sent))
        self.assertNotIn('permissionMode', chat.get('_idleRestoreControls', {}))

    def test_chat_io_transitions_are_never_retention_or_replacement_candidates(self):
        chat = self.chat()
        bridge = MemoryBridge()
        chat.update(bridge=bridge, state='done', sessionId=bridge.session_id)
        for key in ('_deleting', '_chatPreparing'):
            chat[key] = True
            try:
                with self.subTest(key=key), self.app.lock:
                    self.assertIsNone(idle_candidate(self.app, None))
                    self.assertIsNone(replacement_candidate(self.app, None))
            finally:
                chat.pop(key)

    def test_chat_rejects_external_other_chat_and_modified_copies_before_bridge(self):
        first, second = self.chat(), self.chat()
        external = self.root / 'external.txt'
        external.write_text('not uploaded', encoding='utf-8')
        foreign = self.upload(second['id'])
        changed = Path(self.upload(first['id']))
        changed.write_text('replaced after upload with longer content', encoding='utf-8')
        with patch('local_app.server.ClaudeSession') as factory:
            for path in (str(external), foreign, str(changed)):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.app.send(first['id'], 'question', [path])
        factory.assert_not_called()
        self.assertEqual([], first['messages'])
        self.assertIsNone(first['bridge'])
        with self.assertRaises(ValueError):
            self.app.allowed_file(first['id'], str(external))

    def test_work_cannot_borrow_another_chat_owned_copy(self):
        chat, work = self.chat(), self.work()
        attachment = self.upload(chat['id'])
        with patch('local_app.server.ClaudeSession') as factory, self.assertRaises(ValueError):
            self.app.send(work['id'], 'read borrowed copy', [attachment])
        factory.assert_not_called()

    def test_work_external_path_owner_check_does_not_scan_attachment_store(self):
        original = self.root / 'original-reference.txt'
        original.write_text('external reference', encoding='utf-8')
        with patch.object(self.app.attachment_store, '_scan', side_effect=AssertionError('external path must not scan copies')):
            self.assertIsNone(self.app.attachment_store.owner(original))

    def test_chat_terminal_events_and_manual_stop_leave_no_work_queue_metadata(self):
        chat = self.chat()
        sid = chat['id']
        queue = self.app.dispatch.queue
        for kind, data in (('status', {'state': 'stopped'}), ('error', {'code': 'fixture-error'}),
                           ('result', {'verification': {'state': 'needs-review'}})):
            with self.subTest(kind=kind):
                self.app.dispatch.observe(sid, kind, data)
                self.assertEqual({}, queue.data['holds'])
                self.assertEqual(0, queue.data['revision'])
                self.assertFalse(queue.path.exists())
        self.assertTrue(self.app.dispatch._retention_pending)
        chat['_dispatchClaim'] = 'old-fixture-claim'
        self.app.dispatch.steering[sid] = {'fixture': True}
        self.app.dispatch.manual_stop(sid)
        self.assertNotIn('_dispatchClaim', chat)
        self.assertNotIn(sid, self.app.dispatch.steering)
        self.assertEqual({}, queue.data['holds'])
        self.assertFalse(queue.path.exists())
        self.assertFalse(self.app.delete_chat(sid, True)['cleanupPending'])
        self.assertFalse(queue.path.exists())

    def test_work_terminal_events_and_manual_stop_still_pause_followups(self):
        work = self.work()
        sid = work['id']
        queue = self.app.dispatch.queue
        for kind, data, reason in (('status', {'state': 'stopped'}, 'stopped'),
                                   ('error', {'code': 'fixture-error'}, 'error'),
                                   ('result', {'verification': {'state': 'needs-review'}}, 'error')):
            with self.subTest(kind=kind):
                self.app.dispatch.observe(sid, kind, data)
                self.assertEqual(reason, queue.data['holds'][sid])
                self.assertTrue(queue.path.is_file())
                self.assertEqual(reason, json.loads(queue.path.read_text(encoding='utf-8'))['holds'][sid])
        self.assertTrue(self.app.dispatch._retention_pending)
        work['_dispatchClaim'] = 'old-fixture-claim'
        self.app.dispatch.manual_stop(sid)
        self.assertNotIn('_dispatchClaim', work)
        self.assertEqual('stopped', queue.data['holds'][sid])

    def test_delete_requires_exact_confirmation_and_does_not_hide_chat(self):
        chat = self.chat()
        for confirmed in (False, None, 'true', 1):
            with self.subTest(confirmed=confirmed), self.assertRaises(ValueError):
                self.app.delete_chat(chat['id'], confirmed)
        with self.assertRaises(ValueError):
            self.app.hide_session(chat['id'], True)
        self.assertTrue(Path(chat['workspace']).is_dir())
        self.assertIn(chat['id'], self.app.sessions)
        work = self.work()
        with self.assertRaises(ValueError):
            self.app.delete_chat(work['id'], True)
        hidden = self.app.hide_session(work['id'], True)
        self.assertTrue(hidden['historyPreserved'])
        self.assertTrue(Path(work['workspace']).is_dir())

    def test_delete_stops_only_owned_connection_and_removes_owned_data(self):
        chat, other = self.chat(), self.work()
        original = self.root / 'user-original.txt'
        original.write_bytes(b'original stays')
        upload = self.upload(chat['id'], content=original.read_bytes())
        target = Path(chat['workspace']).parent
        (target / 'cache' / 'generated.json').write_text('{}', encoding='utf-8')
        (target / 'workspace' / 'temporary.txt').write_text('temporary', encoding='utf-8')
        self.app.drafts.update({'id': chat['id'], 'revision': 0, 'draft': {'text': 'unfinished', 'attachments': [upload]}, 'stash': None, 'selectedId': chat['id']})
        progress = self.state / 'progress' / (chat['id'] + '.sqlite3')
        progress.parent.mkdir(exist_ok=True)
        progress.write_bytes(b'generated progress fixture')
        child, sibling = MemoryBridge(), MemoryBridge()
        child.busy = True
        chat.update(bridge=child, state='running')
        other.update(bridge=sibling, state='running')
        result = self.app.delete_chat(chat['id'], True)
        self.assertFalse(result['cleanupPending'])
        self.assertTrue(child.cleanup_complete)
        self.assertEqual(1, child.close_count)
        self.assertFalse(sibling.closed)
        self.assertIn(other['id'], self.app.sessions)
        self.assertNotIn(chat['id'], self.app.sessions)
        self.assertFalse(target.exists())
        self.assertFalse(Path(upload).exists())
        self.assertFalse(progress.exists())
        self.assertFalse((self.state / 'history-sessions' / (chat['id'] + '.json')).exists())
        self.assertFalse(any(row['id'] == chat['id'] for row in self.app.drafts.snapshot()['entries']))
        self.assertEqual(b'original stays', original.read_bytes())
        self.assertEqual(0, self.app.chat_spaces.snapshot()['pendingCount'])

    def test_unconfirmed_child_cleanup_keeps_chat_and_all_files(self):
        chat = self.chat()
        upload = self.upload(chat['id'])
        child = MemoryBridge()
        chat['bridge'] = child
        with patch.object(child, 'close', return_value=False), self.assertRaises(ValueError):
            self.app.delete_chat(chat['id'], True)
        self.assertIn(chat['id'], self.app.sessions)
        self.assertTrue(Path(upload).exists())
        self.assertTrue(Path(chat['workspace']).is_dir())
        self.assertNotIn(chat['id'], self.app.chat_spaces.pending)
        self.assertNotIn('_deleting', chat)

    def test_locked_file_creates_durable_pending_cleanup_retried_on_restart(self):
        chat = self.chat()
        target = Path(chat['workspace']).parent
        locked = target / 'cache' / 'locked.txt'
        locked.write_bytes(b'locked fixture')
        real_unlink = Path.unlink

        def blocked(path, *args, **kwargs):
            if path == locked:
                raise PermissionError('fixture lock')
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', blocked):
            result = self.app.delete_chat(chat['id'], True)
        self.assertTrue(result['cleanupPending'])
        self.assertTrue(locked.exists())
        self.assertTrue((target / '.owner.json').exists())
        self.assertIn(chat['id'], self.app.chat_spaces.pending)
        self.app.close()
        restored = self.make_app()
        self.assertNotIn(chat['id'], restored.sessions)
        self.assertFalse(target.exists())
        self.assertEqual(0, restored.chat_spaces.snapshot()['pendingCount'])

    def test_tampered_owner_or_workspace_blocks_connect_and_delete(self):
        chat = self.chat()
        original_workspace = chat['workspace']
        chat['workspace'] = str(self.root)
        with self.assertRaises(ValueError):
            self.app.chat_runtime_options(chat)
        with self.assertRaises(ValueError):
            self.app.delete_chat(chat['id'], True)
        chat['workspace'] = original_workspace
        owner = Path(original_workspace).parent / '.owner.json'
        owner.write_text('{"id":"someone-else"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.app.chat_runtime_options(chat)
        with self.assertRaises(ValueError):
            self.app.delete_chat(chat['id'], True)
        self.assertTrue(self.root.exists())
        self.assertTrue(Path(original_workspace).is_dir())

    def test_reparse_child_is_not_followed_on_cleanup(self):
        chat = self.chat()
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep.txt'
        sentinel.write_bytes(b'keep')
        link = Path(chat['workspace']) / 'linked'
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            if os.name != 'nt':
                self.skipTest('Symlink privilege unavailable: ' + str(exc))
            # NTFS junctions exercise the same reparse guard without requiring
            # Developer Mode or administrator symlink privileges. Paths stay
            # data in the child environment, never interpolated into code.
            outcome = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                                      'New-Item -ItemType Junction -Path $env:AX_TEST_LINK -Target $env:AX_TEST_TARGET -ErrorAction Stop | Out-Null'],
                                     env={**os.environ, 'AX_TEST_LINK': str(link), 'AX_TEST_TARGET': str(outside)},
                                     capture_output=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(0, outcome.returncode, 'Could not create isolated junction fixture')
        result = self.app.delete_chat(chat['id'], True)
        self.assertTrue(result['cleanupPending'])
        self.assertEqual(b'keep', sentinel.read_bytes())
        self.assertTrue(link.is_symlink() or getattr(link.lstat(), 'st_file_attributes', 0) & 0x400)
        self.assertIn(chat['id'], self.app.chat_spaces.pending)


class PlainChatHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ax-chat-http-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, route, data=None, auth=True, raw=None, name='file.txt'):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        body = None if data is None else json.dumps(data).encode('utf-8')
        if raw is not None:
            body = raw
            headers.update({'Content-Type': 'application/octet-stream', 'X-File-Name': quote(name)})
        request = Request(self.server.origin + route, data=body, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def create(self):
        code, item = self.request('/api/create', {'kind': 'chat'})
        self.assertEqual(200, code, item)
        return item['id']

    def test_authenticated_create_upload_send_delete_and_original_boundary(self):
        self.assertEqual(403, self.request('/api/create', {'kind': 'chat'}, auth=False)[0])
        sid = self.create()
        code, uploaded = self.request('/api/attachments/upload?id=' + sid, raw=b'generated text document')
        self.assertEqual(200, code, uploaded)
        code, reply = self.request('/api/send', {'id': sid, 'text': '요약해줘', 'attachments': [uploaded['path']]})
        self.assertEqual(200, code, reply)
        self.assertEqual('read', self.app.get(sid)['messages'][0]['attachmentReads'][0]['status'])
        self.assertEqual(403, self.request('/api/session/delete', {'id': sid, 'confirmed': True}, auth=False)[0])
        self.assertEqual(400, self.request('/api/session/delete', {'id': sid, 'confirmed': False})[0])
        self.assertEqual(400, self.request('/api/session/delete', {'id': sid, 'confirmed': True, 'path': str(self.root)})[0])
        code, deleted = self.request('/api/session/delete', {'id': sid, 'confirmed': True})
        self.assertEqual(200, code, deleted)
        self.assertFalse(deleted['cleanupPending'])
        self.assertFalse(Path(uploaded['path']).exists())
        self.assertTrue(self.root.exists())

    def test_chat_denies_workspace_operations_and_unsupported_uploads(self):
        sid = self.create()
        for route, body in (
                ('/api/trust', {'trusted': True}),
                ('/api/dispatch', {'action': 'enqueue', 'text': 'not allowed'}),
                ('/api/session/branch', {}),
                ('/api/customizations', {'scope': 'user', 'kind': 'skill'}),
                ('/api/completions', {'kind': 'slash', 'query': '/skill'}),
                ('/api/native', {}),
                ('/api/permission-mode', {'mode': 'auto'})):
            with self.subTest(route=route):
                code, value = self.request(route, {'id': sid, **body})
                self.assertEqual(400, code, value)
        for extension in ('exe', 'zip', '7z'):
            self.assertEqual(400, self.request('/api/attachments/prepare', {'id': sid, 'name': 'test.' + extension, 'size': 4})[0])
        code, value = self.request('/api/attachments/upload?id=' + sid, raw=b'EXE!', name='not-executed.exe')
        self.assertEqual(400, code, value)

    def test_chat_copy_cannot_be_reused_by_other_chat_or_draft(self):
        first, second = self.create(), self.create()
        code, uploaded = self.request('/api/attachments/upload?id=' + first, raw=b'generated text')
        self.assertEqual(200, code, uploaded)
        code, value = self.request('/api/send', {'id': second, 'text': 'borrow data', 'attachments': [uploaded['path']]})
        self.assertEqual(400, code, value)
        code, value = self.request('/api/drafts', {'id': second, 'revision': 0, 'draft': {'text': 'draft', 'attachments': [uploaded['path']]}, 'stash': None, 'selectedId': second})
        self.assertEqual(400, code, value)
        self.assertTrue(Path(uploaded['path']).is_file())


if __name__ == '__main__':
    unittest.main()
