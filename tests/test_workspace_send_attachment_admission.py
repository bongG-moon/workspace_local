"""Attachment metadata failures cannot allocate CLI slots or consume a choice."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.idle_connections import occupies_slot
from local_app.server import LocalApp
from tests.test_workspace_idle_connections import MemoryBridge


class SendAttachmentAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-send-storage-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', command=['fixture-never-run'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.item = self.create()
        self.path = self.app.attachment_store.save(self.item['id'], 'copy.zip', io.BytesIO(b'copy'), 4)['path']

    def create(self):
        return self.app.get(self.app.create(str(self.root), True)['id'])

    def test_failed_manifest_does_not_strand_three_empty_bridges_or_consume_choices(self):
        rows = [self.item, self.create(), self.create()]
        for row in rows:
            row.update(choice={'id': 'choice'}, verification={'state': 'needs-review'},
                       _choiceSourceKey='source', trusted=False)
        with patch.object(self.app.attachment_store, '_write_index', side_effect=OSError('disk full')), \
                patch('local_app.server.ClaudeSession') as factory:
            for row in rows:
                with self.assertRaisesRegex(OSError, 'disk full'):
                    self.app.send(row['id'], 'follow up', [self.path], trusted=True)
                self.assertIsNone(row.get('bridge'))
                self.assertFalse(occupies_slot(row))
                self.assertEqual({'id': 'choice'}, row['choice'])
                self.assertEqual({'state': 'needs-review'}, row['verification'])
                self.assertEqual('source', row['_choiceSourceKey'])
                self.assertFalse(row['trusted'])
                self.assertEqual([], row['messages'])
                self.assertEqual([], row['attachments'])
                self.assertFalse(any(event['type'] == 'choice_closed' for event in row['events']))
        factory.assert_not_called()
        self.assertTrue(self.app.connection_capacity_available(self.create()))

    def test_failed_manifest_precedes_idle_resume_and_preserves_saved_modes(self):
        old = MemoryBridge(); old.close()
        self.item.update(bridge=old, state='done', sessionId=old.session_id,
                         _idleReleased=True, _requireResumeIdentity=True,
                         _idleRestoreControls={'model': 'chosen', 'effort': 'high'})
        with patch.object(self.app.attachment_store, '_write_index', side_effect=OSError('disk full')), \
                patch.object(self.app, 'connect') as connect, patch('local_app.server.ClaudeSession') as factory:
            with self.assertRaises(OSError):
                self.app.send(self.item['id'], 'continue', [self.path])
        connect.assert_not_called(); factory.assert_not_called()
        self.assertIs(old, self.item['bridge'])
        self.assertTrue(self.item['_idleReleased'])
        self.assertEqual({'model': 'chosen', 'effort': 'high'}, self.item['_idleRestoreControls'])
        self.assertEqual('done', self.item['state'])
        self.assertEqual([], self.item['messages'])

    def test_failed_manifest_keeps_existing_connection_and_retry_sends_once(self):
        bridge = MemoryBridge()
        self.item.update(bridge=bridge, state='done', choice={'id': 'choice'},
                         verification={'state': 'needs-review'})
        with patch.object(self.app.attachment_store, '_write_index', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.app.send(self.item['id'], 'continue', [self.path])
        self.assertEqual([], bridge.sent)
        self.assertEqual({'id': 'choice'}, self.item['choice'])
        self.assertEqual({'state': 'needs-review'}, self.item['verification'])
        self.app.send(self.item['id'], 'continue', [self.path])
        self.assertEqual(1, len(bridge.sent))
        self.assertEqual(1, len(self.item['messages']))
        self.assertEqual([self.path], self.item['attachments'])

    def test_invalid_or_duplicate_requests_do_not_mark_attachment_used(self):
        cases = [
            ({}, {'text': ''}),
            ({}, {'attachments': ['relative.zip']}),
            ({'trusted': False}, {}),
            ({'state': 'running'}, {}),
            ({'_modelUpdating': True}, {}),
            ({'_permissionUpdating': True}, {}),
            ({'_dispatchClaim': 'other'}, {}),
            ({'_choiceAnswerClaim': ('choice', 'pending')}, {}),
            ({}, {'_choice_claim': ('expired', 'answer')}),
        ]
        with patch.object(self.app.attachment_store, 'mark_used') as mark, \
                patch('local_app.server.ClaudeSession') as factory:
            for changes, request in cases:
                with self.subTest(changes=changes, request=request):
                    item = self.create(); item.update(changes)
                    with self.assertRaises(ValueError):
                        self.app.send(item['id'], **{'text': 'request', 'attachments': [self.path], **request})
            self.app.error = 'invalid runtime'
            with self.assertRaisesRegex(ValueError, 'invalid runtime'):
                self.app.send(self.item['id'], 'request', [self.path])
            self.app.error = None
        mark.assert_not_called(); factory.assert_not_called()

    def test_invalid_resume_is_rejected_before_attachment_protection(self):
        with patch.object(self.app, '_resume_options', side_effect=ValueError('invalid resume')), \
                patch.object(self.app.attachment_store, 'mark_used') as mark, \
                patch('local_app.server.ClaudeSession') as factory:
            with self.assertRaisesRegex(ValueError, 'invalid resume'):
                self.app.send(self.item['id'], 'continue', [self.path])
        mark.assert_not_called(); factory.assert_not_called()

    def test_normal_send_validates_files_once_and_does_not_load_attachment_inventory(self):
        with patch.object(self.app.attachment_store, '_load', side_effect=AssertionError('inventory read')), \
                patch.object(self.app, 'validate_attachments', wraps=self.app.validate_attachments) as validate, \
                patch('local_app.server.ClaudeSession', side_effect=MemoryBridge):
            self.app.send(self.item['id'], 'no files', [])
        self.assertEqual(1, validate.call_count)
        self.assertEqual(['no files'], self.item['bridge'].sent)


if __name__ == '__main__':
    unittest.main()
