"""Storage lifecycle proofs use isolated files, no user's data or Claude calls."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from local_app.attachments import AttachmentStore, UNUSED_GRACE_SECONDS


class AttachmentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = AttachmentStore(self.root / 'state')
        self.sid = str(uuid.uuid4())
        self.future = time.time() + UNUSED_GRACE_SECONDS + 100

    def upload(self, name='copy.zip', data=b'1234'):
        return self.store.save(self.sid, name, io.BytesIO(data), len(data))['path']

    def test_quota_recovered_only_for_old_unused_copies_and_original_unchanged(self):
        original = self.root / 'original.zip'
        original.write_bytes(b'original')
        with patch('local_app.attachments.MAX_STORAGE', 6):
            path = self.upload()
            with self.assertRaisesRegex(ValueError, '512 MB'):
                self.upload('next.exe')
            self.assertEqual(0, self.store.cleanup()['removedCount'])
            result = self.store.cleanup(now=self.future)
            self.assertEqual((1, 4, 0), (result['removedCount'], result['removedBytes'], result['usedBytes']))
            self.assertFalse(Path(path).exists())
            self.assertTrue(Path(self.upload('next.exe')).is_file())
        self.assertEqual(b'original', original.read_bytes())

    def test_draft_queue_schedule_history_refs_and_ever_sent_survive_restart(self):
        protected = [self.upload(f'{kind}.zip') for kind in ('draft', 'queue', 'schedule', 'history')]
        sent = self.upload('sent.zip')
        removable = self.upload('cancel.zip')
        self.assertEqual(1, self.store.mark_used([sent]))
        self.store = AttachmentStore(self.store.root.parent)
        self.store.status()
        result = self.store.cleanup(protected, now=self.future)
        self.assertEqual(1, result['removedCount'])
        self.assertFalse(Path(removable).exists())
        self.assertTrue(all(Path(path).exists() for path in protected + [sent]))
        # Even after the history reference is compacted, a sent attachment stays.
        result = self.store.cleanup(now=self.future)
        self.assertEqual(4, result['removedCount'])
        self.assertTrue(Path(sent).exists())
        self.assertEqual(4, result['protectedBytes'])

    def test_legacy_untracked_file_is_counted_but_never_reclaimed(self):
        legacy = self.store.root / self.sid / uuid.uuid4().hex / 'old.zip'
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b'old')
        new = self.upload()
        result = self.store.cleanup(now=self.future)
        self.assertEqual((1, 3, 3), (result['removedCount'], result['usedBytes'], result['retainedBytes']))
        self.assertTrue(legacy.is_file())
        self.assertFalse(Path(new).exists())
        self.store = AttachmentStore(self.store.root.parent)
        self.store.status()
        self.assertEqual(0, self.store.cleanup(now=self.future)['removedCount'])

    def test_external_modification_is_retained_and_missing_files_refresh_usage(self):
        path = Path(self.upload())
        path.write_bytes(b'externally modified')
        result = self.store.cleanup(now=self.future)
        self.assertEqual(0, result['removedCount'])
        self.assertEqual(path.stat().st_size, result['retainedBytes'])
        path.unlink()
        self.assertGreater(self.store.status()['usedBytes'], 0)
        self.assertEqual(0, self.store.status(refresh=True)['usedBytes'])

    def test_prepare_and_cached_status_do_not_rescan_files(self):
        with patch.object(self.store, '_scan', wraps=self.store._scan) as scan:
            self.upload()
            for _ in range(50):
                self.store.prepare('next.zip', 1)
                self.store.status()
            self.assertEqual(1, scan.call_count)
            self.store.status(refresh=True)
            self.assertEqual(2, scan.call_count)

    def test_ordinary_send_without_copied_attachment_does_not_load_inventory(self):
        with patch.object(self.store, '_load', side_effect=AssertionError('unexpected storage IO')):
            self.assertEqual(0, self.store.mark_used([]))
            self.assertEqual(0, self.store.mark_used([str(self.root / 'original.zip')]))

    def test_upload_reservation_blocks_oversubscription_but_not_status_or_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        class PausedSource:
            def read(inner, size):
                entered.set()
                if not release.wait(3):
                    raise AssertionError('metadata blocked the upload')
                return b'1234'
        def upload():
            try:
                result.append(self.store.save(self.sid, 'inflight.zip', PausedSource(), 4))
            except BaseException as error:
                result.append(error)
        with patch('local_app.attachments.MAX_STORAGE', 6):
            thread = threading.Thread(target=upload)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(4, self.store.status()['uploadingBytes'])
                with self.assertRaises(ValueError):
                    self.upload('other.zip')
                self.assertEqual(0, self.store.cleanup(now=self.future)['removedCount'])
            finally:
                release.set()
                thread.join(3)
        self.assertEqual(1, len(result))
        self.assertIsInstance(result[0], dict)
        self.assertEqual(b'1234', Path(result[0]['path']).read_bytes())
        self.assertEqual(4, self.store.status()['usedBytes'])
        self.assertEqual(0, self.store.status()['uploadingCount'])

    def test_failed_upload_releases_reservation_and_cannot_become_owned_copy(self):
        with self.assertRaises(ValueError):
            self.store.save(self.sid, 'short.zip', io.BytesIO(b'1'), 4)
        self.assertEqual(0, self.store.status()['uploadingBytes'])
        self.assertEqual(0, self.store.status()['usedBytes'])
        self.assertEqual([], list(self.store.root.rglob('short.zip')))

    def test_failed_mark_used_is_retried_before_transmission_can_continue(self):
        path = self.upload()
        original = self.store._write_index
        with patch.object(self.store, '_write_index', side_effect=OSError('full disk')):
            with self.assertRaises(OSError):
                self.store.mark_used([path])
        # Retry has no new in-memory mutation, but must persist the sticky flag.
        with patch.object(self.store, '_write_index', wraps=original) as write:
            self.store.mark_used([path])
            self.assertEqual(1, write.call_count)
        restarted = AttachmentStore(self.store.root.parent)
        restarted.status()
        self.assertEqual(0, restarted.cleanup(now=self.future)['removedCount'])
        self.assertTrue(Path(path).is_file())

    def test_corrupt_manifest_preserves_copy_and_blocks_new_provenance(self):
        path = self.upload()
        self.store.index.write_text('{broken', encoding='utf-8')
        restarted = AttachmentStore(self.store.root.parent)
        restarted.status()
        result = restarted.cleanup(now=self.future)
        self.assertTrue(result['warning'])
        self.assertEqual(0, result['removedCount'])
        self.assertTrue(Path(path).exists())
        with self.assertRaises(ValueError):
            restarted.prepare('new.zip', 1)
        self.assertEqual('{broken', self.store.index.read_text(encoding='utf-8'))

    def test_tampered_manifest_cannot_name_original_or_parent_path(self):
        original = self.root / 'original.zip';original.write_bytes(b'original')
        self.store.index.parent.mkdir(parents=True)
        self.store.index.write_text(json.dumps({'schemaVersion': 1, 'copies': [
            {'path': '../../original.zip', 'created': 0, 'used': False, 'identity': [0, 0, 8, 0]}
        ]}), encoding='utf-8')
        self.assertTrue(self.store.status()['warning'])
        self.assertEqual(0, self.store.cleanup(now=self.future)['removedCount'])
        self.assertEqual(b'original', original.read_bytes())

    def test_reparse_group_is_rejected_before_cleanup(self):
        path = Path(self.upload())
        original_lstat = Path.lstat
        class Reparse:
            st_file_attributes = 0x400
        def lstat(candidate, *args, **kwargs):
            if candidate == path.parent:
                result = original_lstat(candidate, *args, **kwargs)
                class Wrapped:
                    st_file_attributes = 0x400
                    def __getattr__(self, name):
                        return getattr(result, name)
                return Wrapped()
            return original_lstat(candidate, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat):
            with self.assertRaises(ValueError):
                self.store.cleanup(now=self.future)
        self.assertEqual(b'1234', path.read_bytes())

    def test_zero_byte_uploads_have_bounded_count(self):
        with patch('local_app.attachments.MAX_COPIES', 2):
            self.upload('one.zip', b'')
            self.upload('two.zip', b'')
            with self.assertRaises(ValueError):
                self.upload('three.zip', b'')
            self.assertEqual(2, self.store.cleanup(now=self.future)['removedCount'])
            self.upload('three.zip', b'')

    def test_manual_cleanup_batches_preserve_refs_and_finish_on_later_clicks(self):
        paths = [self.upload(f'copy-{number}.zip') for number in range(205)]
        self.store.mark_used([paths[0]])
        with patch('local_app.attachments.time.monotonic', return_value=0), \
                patch.object(self.store, '_scan', side_effect=AssertionError('cleanup must not rescan')):
            first = self.store.cleanup([paths[1]], now=self.future)
            self.assertEqual((100, 103, True), (first['removedCount'], first['remainingCount'], first['batchLimited']))
            second = self.store.cleanup([paths[1]], now=self.future)
            third = self.store.cleanup([paths[1]], now=self.future)
        self.assertEqual((100, 3), (second['removedCount'], third['removedCount']))
        self.assertEqual(0, third['remainingCount'])
        self.assertTrue(all(Path(path).is_file() for path in paths[:2]))
        self.assertFalse(any(Path(path).exists() for path in paths[2:]))

    def test_cleanup_time_budget_is_checked_between_files(self):
        for index in range(3):
            self.upload(f'copy-{index}.zip')
        with patch('local_app.attachments.time.monotonic', side_effect=[0, .101]):
            result = self.store.cleanup(now=self.future)
        self.assertEqual((1, 2, True), (result['removedCount'], result['remainingCount'], result['batchLimited']))

    def test_cleanup_without_inventory_requires_explicit_status_check(self):
        with patch.object(self.store, '_scan', side_effect=AssertionError('unexpected inventory walk')):
            with self.assertRaisesRegex(ValueError, '먼저 확인'):
                self.store.cleanup(now=self.future)


if __name__ == '__main__':
    unittest.main()
