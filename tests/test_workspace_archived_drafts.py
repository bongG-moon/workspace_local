"""Archived work skips autosave without blocking active conversations or exit."""
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.server import LocalApp, Server

ROOT = Path(__file__).resolve().parents[1]


class ArchivedDraftTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='arc-', dir=ROOT / 'build')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.archived = self.app.create(str(self.root), True, title='보관할 업무')['id']
        self.other = self.app.create(str(self.root), True, title='남은 업무')['id']
        self.chat = self.app.create(kind='chat')['id']
        self.attachment = self.root / '참고.csv'
        self.attachment.write_text('항목,값\n예시,1\n', encoding='utf-8')
        self.original_attachment = self.attachment.read_bytes()
        self.row = {
            'id': self.archived, 'revision': 0,
            'draft': {'text': '보관 후에도 유지할 초안', 'attachments': [str(self.attachment)]},
            'stash': {'text': '나중에 이어 쓸 입력', 'attachments': [str(self.attachment)],
                      'selectionStart': 0, 'selectionEnd': 3},
            'selectedId': self.archived,
        }
        self.app.hide_session(self.archived, True)

    def saved(self, sid=None):
        sid = sid or self.archived
        return next(row for row in self.app.drafts.snapshot()['entries'] if row['id'] == sid)

    @contextmanager
    def serving(self):
        server = Server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def request(self, server, body=None, *, path='/api/drafts', auth=True, origin=None):
        headers = {'Content-Type': 'application/json', 'Origin': origin or server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(server.origin + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_archived_draft_is_skipped_while_other_work_is_selected(self):
        body = {**self.row, 'selectedId': self.other}
        original = deepcopy(body)
        before = self.app.drafts.snapshot()
        result = self.app.save_draft(body)
        self.assertEqual({'id': self.archived, 'revision': 0,
                          'skipped': True, 'reason': 'archived_work'}, result)
        self.assertEqual(original, body)
        self.assertEqual(before, self.app.drafts.snapshot())
        self.assertTrue(self.app.session_visibility.contains(self.archived))
        self.assertEqual(self.original_attachment, self.attachment.read_bytes())

    def test_archived_selection_does_not_write_or_unhide(self):
        before = self.app.drafts.snapshot()
        self.assertTrue(self.app.save_draft(self.row)['skipped'])
        self.assertEqual(before, self.app.drafts.snapshot())
        self.assertTrue(self.app.session_visibility.contains(self.archived))
        self.assertEqual(self.archived, self.row['selectedId'])

    def test_archived_noop_ignores_storage_failure_and_does_not_read_or_write_store(self):
        self.app.drafts.warning = 'storage unavailable'
        with patch.object(self.app.drafts, 'update', side_effect=OSError('disk full')) as update, \
                patch.object(self.app.drafts, 'snapshot', side_effect=OSError('unreadable')) as snapshot:
            self.assertTrue(self.app.save_draft(self.row)['skipped'])
        update.assert_not_called()
        snapshot.assert_not_called()

    def test_normal_draft_is_not_rejected_for_stale_archived_selection(self):
        self.app.save_draft({**self.row, 'id': self.other})
        self.assertEqual('home:work', self.app.drafts.snapshot()['selectedId'])
        self.assertEqual(self.row['draft'], self.saved(self.other)['draft'])

    def test_unknown_draft_or_selection_is_rejected_without_writing(self):
        for field in ('id', 'selectedId'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.app.save_draft({**self.row, field: str(uuid.uuid4())})
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_pending_deletion_and_removal_guards_still_apply_to_archived_work(self):
        item = self.app.sessions[self.archived]
        for flag in ('_deleting', '_removingFromList'):
            for field in ('id', 'selectedId'):
                with self.subTest(flag=flag, field=field):
                    body = {**self.row, 'id': self.other, 'selectedId': self.other, field: self.archived}
                    item[flag] = True
                    try:
                        with self.assertRaises(ValueError):
                            self.app.save_draft(body)
                    finally:
                        item.pop(flag, None)
        self.app.chat_spaces.pending[self.archived] = {'id': self.archived}
        try:
            with self.assertRaises(ValueError):
                self.app.save_draft(self.row)
        finally:
            self.app.chat_spaces.pending.pop(self.archived)
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_general_chat_cannot_gain_the_archived_work_exception(self):
        self.app.session_visibility.set_hidden(self.chat, True)
        try:
            for field in ('id', 'selectedId'):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.app.save_draft({**self.row, 'selectedId': self.other, field: self.chat})
        finally:
            self.app.session_visibility.set_hidden(self.chat, False)
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_general_chat_pending_deletion_cannot_be_saved_or_selected(self):
        self.app.chat_spaces.pending[self.chat] = {'id': self.chat}
        try:
            for field in ('id', 'selectedId'):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.app.save_draft({**self.row, 'selectedId': self.other, field: self.chat})
        finally:
            self.app.chat_spaces.pending.pop(self.chat)
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_archived_skip_never_opens_irrelevant_attachments(self):
        with patch.object(self.app.attachment_store, 'owner', side_effect=OSError('old attachment unavailable')) as owner:
            self.assertTrue(self.app.save_draft({**self.row, 'selectedId': self.other})['skipped'])
        owner.assert_not_called()
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_visible_draft_still_protects_other_chat_attachment_copies(self):
        with patch.object(self.app.attachment_store, 'owner', return_value=self.chat):
            with self.assertRaisesRegex(ValueError, '다른 일반 대화'):
                self.app.save_draft({**self.row, 'id': self.other, 'selectedId': self.other})
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_draft_save_does_not_hydrate_archived_conversation(self):
        hydrate = self.app.history.hydrate

        def visible_history_only(item):
            if item['id'] == self.archived:
                raise AssertionError('draft save opened archived history')
            return hydrate(item)

        with patch.object(self.app.history, 'hydrate', side_effect=visible_history_only):
            self.app.save_draft({**self.row, 'selectedId': self.other})
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_switching_between_evicted_work_sessions_saves_draft_without_reading_either_body(self):
        self.app.restore_session(self.archived)
        source, selected = self.app.sessions[self.archived], self.app.sessions[self.other]
        self.app.history.compact(source)
        self.app.history.compact(selected)
        access = self.app._history_access.copy()
        body = {**self.row, 'selectedId': self.other}
        result = self.app.save_draft(body)
        saved = self.app.drafts.snapshot()
        self.assertEqual(self.other, saved['selectedId'])
        self.assertEqual(self.row['draft'], self.saved(self.archived)['draft'])
        self.assertEqual(self.row['stash'], self.saved(self.archived)['stash'])
        with patch.dict(selected, {'_deleting': True}), self.assertRaisesRegex(ValueError, '삭제 중'):
            self.app.save_draft({**body, 'revision': result['revision'], 'draft': None})
        self.assertEqual(saved, self.app.drafts.snapshot())
        self.assertTrue(source.get('_historyUnloaded') and selected.get('_historyUnloaded'))
        self.assertEqual(access, self.app._history_access)

    def test_existing_draft_survives_skipped_save_and_restore_after_restart(self):
        self.app.restore_session(self.archived)
        self.app.save_draft(self.row)
        self.app.hide_session(self.archived, True)
        before = self.app.drafts.snapshot()
        original_db = self.app.drafts.path.read_bytes()
        result = self.app.save_draft({**self.row, 'revision': 1,
            'draft': {'text': '보관 후 늦게 도착한 초안', 'attachments': []}})
        self.assertTrue(result['skipped'])
        self.assertEqual(original_db, self.app.drafts.path.read_bytes())
        reopened = LocalApp(self.app.state, demo=True)
        self.addCleanup(reopened.close)
        self.assertEqual([self.archived], reopened.draft_snapshot()['archivedIds'])
        self.assertTrue(reopened.restore_session(self.archived)['restored'])
        self.assertEqual([], reopened.draft_snapshot()['archivedIds'])
        self.assertEqual(before, reopened.drafts.snapshot())
        row = next(row for row in before['entries'] if row['id'] == self.archived)
        self.assertEqual(self.row['draft'], row['draft'])
        self.assertEqual(self.row['stash'], row['stash'])
        self.assertEqual(self.original_attachment, self.attachment.read_bytes())
        self.assertNotIn(self.archived, reopened.session_visibility.hidden)
        self.assertIn(str(self.attachment), reopened.attachment_references())

    def test_saving_archived_draft_does_not_restore_execution_access(self):
        self.app.save_draft(self.row)
        with self.assertRaisesRegex(ValueError, '보관한 업무'):
            self.app.get(self.archived)
        with self.assertRaisesRegex(ValueError, '보관한 업무'):
            self.app.send(self.archived, '실행하지 않아야 하는 요청', [])

    def test_archived_save_no_longer_blocks_remaining_work_and_chat_sends(self):
        self.app.save_draft({**self.row, 'selectedId': self.other})
        # Keep actual send admission and transcript mutations while replacing
        # only the demo's delayed canned worker with deterministic completion.
        with patch('local_app.demo.run'):
            self.app.send(self.other, '남은 업무의 요청', [])
        self.app.send(self.chat, '일반 대화의 요청', [])
        self.assertEqual('남은 업무의 요청', self.app.get(self.other)['messages'][-1]['text'])
        self.assertEqual('일반 대화의 요청', next(row['text'] for row in self.app.get(self.chat)['messages']
                                               if row.get('role') == 'user'))
        self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_http_archived_draft_keeps_capability_and_origin_boundaries(self):
        with self.serving() as server:
            body = {**self.row, 'selectedId': self.other}
            self.assertEqual(self.request(server, body, auth=False)[0], 403)
            self.assertEqual(self.request(server, body, origin='https://unrelated.example')[0], 403)
            self.assertEqual([], self.app.drafts.snapshot()['entries'])
            code, result = self.request(server, body)
            self.assertEqual(code, 200)
            self.assertEqual(self.archived, result['id'])
            self.assertTrue(result['skipped'])
            self.assertEqual([], self.app.drafts.snapshot()['entries'])

    def test_http_stale_archived_selection_is_repaired_without_exposing_archived_session(self):
        with self.serving() as server:
            code, result = self.request(server, {**self.row, 'id': self.other})
            self.assertEqual(code, 200)
            self.assertEqual(self.other, result['id'])
            self.assertEqual('home:work', self.request(server)[1]['selectedId'])
            self.assertEqual(self.row['draft'], self.saved(self.other)['draft'])
            code, boot = self.request(server, path='/api/bootstrap')
            self.assertEqual(code, 200)
            self.assertNotIn(self.archived, [row['id'] for row in boot['sessions']])
            code, error = self.request(server, path='/api/session?id=' + self.archived)
            self.assertEqual(code, 400)
            self.assertIn('보관한 업무', error['error'])

    def test_http_draft_recovery_allows_other_work_and_general_chat_requests(self):
        with self.serving() as server:
            self.assertEqual(self.request(server, {**self.row, 'selectedId': self.other})[0], 200)
            with patch('local_app.demo.run'):
                code, result = self.request(server, {'id': self.other, 'text': '다른 업무 요청',
                    'attachments': [], 'trusted': True}, path='/api/send')
            self.assertEqual((code, result), (200, {'ok': True}))
            code, result = self.request(server, {'id': self.chat, 'text': '다른 대화 요청',
                'attachments': []}, path='/api/send')
            self.assertEqual((code, result), (200, {'ok': True}))
            self.assertEqual([], self.app.drafts.snapshot()['entries'])


if __name__ == '__main__':
    unittest.main()
