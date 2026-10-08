"""Durable drafts never execute work; row updates stay bounded and transactional."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from local_app.drafts import DraftStore, DraftConflict


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DraftStore(self.root)
        self.sid = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def row(self, **values):
        return {'id': self.sid, 'revision': 0, 'draft': {'text': '이어 쓸 내용', 'attachments': ['C:/fixture/file.zip']},
                'stash': None, 'selectedId': self.sid, **values}

    def test_read_empty_does_not_create_database(self):
        self.assertEqual([], self.store.snapshot()['entries'])
        self.assertFalse(self.store.path.exists())

    def test_restart_restores_unicode_stash_and_attachment_references_without_execution(self):
        stash = {'text': '보관😀', 'attachments': ['C:/fixture/retained.exe'], 'selectionStart': 2, 'selectionEnd': 4}
        self.store.update(self.row(stash=stash))
        reopened = DraftStore(self.root)
        state = reopened.snapshot()
        self.assertEqual(self.sid, state['selectedId'])
        self.assertEqual('이어 쓸 내용', state['entries'][0]['draft']['text'])
        self.assertEqual(stash, state['entries'][0]['stash'])
        self.assertEqual({'C:/fixture/file.zip', 'C:/fixture/retained.exe'}, reopened.reference_paths())

    def test_cleared_draft_tombstone_rejects_old_delayed_write(self):
        first = self.store.update(self.row())
        cleared = self.store.update(self.row(revision=first['revision'], draft=None))
        with self.assertRaises(DraftConflict): self.store.update(self.row())
        state = DraftStore(self.root).snapshot()['entries'][0]
        self.assertIsNone(state['draft'])
        self.assertEqual(cleared['revision'], state['revision'])

    def test_retry_after_lost_ack_is_idempotent_and_not_a_second_revision(self):
        response = self.store.update(self.row())
        self.assertEqual(response, self.store.update(self.row()))
        self.assertEqual(response, self.store.update(self.row(revision=response['revision'])))

    def test_competing_windows_do_not_overwrite_each_other(self):
        self.store.update(self.row())
        other = DraftStore(self.root)
        with self.assertRaises(DraftConflict): other.update(self.row(draft={'text':'stale','attachments':[]}))
        self.assertEqual('이어 쓸 내용', self.store.snapshot()['entries'][0]['draft']['text'])

    def test_limit_failure_preserves_previous_saved_row(self):
        response = self.store.update(self.row())
        with patch('local_app.drafts.MAX_TOTAL', 20), self.assertRaises(ValueError):
            self.store.update(self.row(revision=response['revision'], draft={'text':'x'*100,'attachments':[]}))
        self.assertEqual('이어 쓸 내용', self.store.snapshot()['entries'][0]['draft']['text'])

    def test_corrupt_database_is_preserved_and_save_refused(self):
        self.store.path.write_bytes(b'not-a-database')
        with self.assertRaises(ValueError): self.store.snapshot()
        with self.assertRaises(ValueError): self.store.update(self.row())
        self.assertEqual(b'not-a-database', self.store.path.read_bytes())

    def test_only_changed_row_is_written_and_connection_is_closed(self):
        first = self.store.update(self.row())
        second = str(uuid.uuid4())
        self.store.update(self.row(id=second, draft={'text':'other','attachments':[]}))
        with sqlite3.connect(self.store.path) as db:
            db.execute("CREATE TRIGGER keep_other BEFORE UPDATE ON drafts WHEN OLD.id='"+second+"' BEGIN SELECT RAISE(ABORT, 'other row rewritten'); END")
        db.close()
        self.store.update(self.row(revision=first['revision'], draft={'text':'changed','attachments':[]}))
        renamed = self.root/'renamed.sqlite3'
        self.store.path.rename(renamed)  # Windows permits this only once owned connections are closed.
        self.assertTrue(renamed.exists())

    def test_invalid_paths_and_stash_offsets_are_rejected(self):
        for value in [self.row(id='../escape'), self.row(revision=True),
                      self.row(draft={'text':'a','attachments':['bad\0path']}),
                      self.row(stash={'text':'a','attachments':[],'selectionStart':0,'selectionEnd':8})]:
            with self.assertRaises(ValueError): self.store.update(value)


if __name__ == '__main__': unittest.main()
