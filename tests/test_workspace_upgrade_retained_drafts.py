"""Updates retain distinct old and new drafts without requiring a restore/delete."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from local_app.history import HistoryStore
from local_app.server import LocalApp
from local_app.upgrade_handoff import UpgradeHandoff, preserve_recovery


class RetainedUpgradeDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.app.state), True)['id']
        self.old = self.draft('previous\n한글', 'D:/이전 자료.zip')
        self.new = self.draft('current\n수정 중', 'E:/현재 자료.xlsx')
        self.old_id = uuid.uuid4().hex
        self.rid = uuid.uuid4().hex
        self.seed(self.old_id, self.old)
        self.upgrade = self.app.upgrade = UpgradeHandoff(self.app, '0.23.33')

    def draft(self, text, path):
        return {'sessionId': self.sid, 'mode': 'work', 'drafts': [
            {'id': self.sid, 'text': text, 'attachments': [path]}], 'stashes': [
            {'id': self.sid, 'text': '😀stash ' + text, 'attachments': [path],
             'selectionStart': 0, 'selectionEnd': 2}]}

    def seed(self, rid, draft):
        record = {'schemaVersion': 1, 'status': 'ready', 'requestId': rid,
                  'sourceVersion': '0.23.31', 'targetVersion': '0.23.32', 'snapshot': draft}
        HistoryStore(self.app.state)._write(self.app.state / 'upgrade-drafts.json',
                                         json.dumps(record).encode('utf-8'))

    def action(self, name, **kwargs):
        return self.upgrade.action({'action': name, 'requestId': self.rid, **kwargs})

    def prepare(self):
        return self.action('prepare', targetVersion='0.23.34')

    def capture(self):
        return self.action('capture', revision=self.upgrade.status()['upgrade']['revision'], snapshot=self.new)

    def archive_path(self):
        return self.app.state / 'upgrade-recovery' / (self.old_id + '.json')

    def test_update_preserves_both_snapshots_and_current_restore_has_priority(self):
        queued = self.app.dispatch.queue.enqueue(self.sid, 'do not replay')
        self.app.dispatch.queue.pause(self.sid, 'stopped')
        queue_bytes = self.app.dispatch.queue.path.read_bytes()
        self.assertEqual('capture', self.prepare()['upgrade']['stage'])
        self.assertTrue(self.capture()['ok'])
        self.assertTrue(self.action('commit')['closed'])
        self.assertEqual(queue_bytes, self.app.dispatch.queue.path.read_bytes())
        self.assertEqual(queued['id'], self.app.dispatch.queue.snapshot(self.sid)['queue'][0]['id'])
        restored = UpgradeHandoff(self.app, '0.23.34')
        self.assertEqual(self.new, restored.restore['snapshot'])
        self.assertNotIn('retained', restored.restore)
        result = restored.action({'action': 'restored', 'requestId': self.rid})
        self.assertEqual(self.old, result['upgradeRestore']['snapshot'])
        self.assertTrue(result['upgradeRestore']['retained'])
        self.assertTrue(self.archive_path().exists())
        # A repeated ACK for the new record cannot delete the previous record.
        with self.assertRaises(ValueError):
            restored.action({'action': 'restored', 'requestId': self.rid})
        self.assertTrue(self.archive_path().exists())
        self.assertEqual([], self.app.get(self.sid)['messages'])

    def test_cancel_and_reopen_preserve_previous_without_restoring_or_sending(self):
        self.prepare()
        self.capture()
        self.action('cancel')
        restored = UpgradeHandoff(self.app, '0.23.33')
        self.assertEqual(self.old, restored.restore['snapshot'])
        self.assertTrue(restored.restore['retained'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        self.assertEqual([], self.app.get(self.sid)['messages'])

    def test_archive_failure_does_not_remove_original_or_prepare_shutdown(self):
        original = self.upgrade.path.read_bytes()
        with patch('local_app.upgrade_handoff.preserve_recovery', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(original, self.upgrade.path.read_bytes())
        self.assertIsNone(self.upgrade.pending)
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_new_capture_failure_keeps_old_archive_and_current_app_usable(self):
        self.prepare()
        with patch.object(HistoryStore, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.capture()
        self.assertEqual(self.old, json.loads(self.archive_path().read_text())['snapshot'])
        self.assertFalse(self.action('commit')['closed'])
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_old_record_ack_while_new_capture_exists_cannot_delete_new_capture(self):
        self.prepare()
        self.capture()
        captured = self.upgrade.path.read_bytes()
        self.upgrade.action({'action': 'restored', 'requestId': self.old_id})
        self.assertEqual(captured, self.upgrade.path.read_bytes())
        self.assertFalse(self.archive_path().exists())
        self.assertTrue(self.action('commit')['closed'])
        self.assertEqual(self.new, UpgradeHandoff(self.app, '0.23.34').restore['snapshot'])

    def test_archive_collision_never_overwrites_a_different_snapshot(self):
        old_restore = deepcopy(self.upgrade.restore)
        path = preserve_recovery(self.app.state, old_restore)
        before = path.read_bytes()
        self.assertEqual(path, preserve_recovery(self.app.state, old_restore))
        old_restore['snapshot'] = self.new
        with self.assertRaises(ValueError):
            preserve_recovery(self.app.state, old_restore)
        self.assertEqual(before, path.read_bytes())

    def test_changed_original_after_copy_is_not_removed(self):
        original_preserve = preserve_recovery
        def replace_after_copy(state, restore):
            result = original_preserve(state, restore)
            self.seed(uuid.uuid4().hex, self.new)
            return result
        with patch('local_app.upgrade_handoff.preserve_recovery', side_effect=replace_after_copy):
            with self.assertRaisesRegex(ValueError, '바뀌었습니다'):
                self.prepare()
        self.assertEqual(self.new, json.loads(self.upgrade.path.read_text())['snapshot'])
        self.assertEqual(self.old, json.loads(self.archive_path().read_text())['snapshot'])
        self.assertIsNone(self.upgrade.pending)

    def test_unreadable_archive_is_preserved_without_blocking_valid_new_capture(self):
        self.prepare()
        self.action('cancel')
        self.archive_path().write_text('{unreadable')
        self.upgrade = self.app.upgrade = UpgradeHandoff(self.app, '0.23.33')
        self.assertIsNotNone(self.upgrade.warning)
        self.assertEqual('capture', self.prepare()['upgrade']['stage'])
        self.assertTrue(self.capture()['ok'])
        self.assertEqual('{unreadable', self.archive_path().read_text())

    def test_individual_large_snapshots_do_not_share_capture_size_limit(self):
        self.old = {'sessionId': self.sid, 'drafts': [
            {'id': self.sid, 'text': 'x' * 100000, 'attachments': []},
            {'id': 'home:chat', 'text': 'y' * 100000, 'attachments': []}]}
        self.new = deepcopy(self.old)
        self.new['drafts'][0]['text'] = 'z' * 100000
        self.seed(self.old_id, self.old)
        self.upgrade = self.app.upgrade = UpgradeHandoff(self.app, '0.23.33')
        self.prepare()
        self.assertTrue(self.capture()['ok'])
        self.assertTrue(self.action('commit')['closed'])
        self.assertEqual(self.old, json.loads(self.archive_path().read_text())['snapshot'])

    def test_repeated_update_without_cleanup_retains_all_distinct_versions(self):
        self.prepare()
        self.capture()
        # Persist ready as if the first new app started with an unresolved draft.
        self.upgrade._write(self.upgrade.pending, 'ready')
        self.upgrade = self.app.upgrade = UpgradeHandoff(self.app, '0.23.34')
        second = deepcopy(self.new)
        second['drafts'][0]['text'] = 'third revision'
        self.rid = uuid.uuid4().hex
        self.prepare()
        self.new = second
        self.capture()
        self.assertTrue(self.action('commit')['closed'])
        restored = UpgradeHandoff(self.app, '0.23.35')
        snapshots = []
        while restored.restore:
            snapshots.append(restored.restore['snapshot']['drafts'][0]['text'])
            restored.action({'action': 'restored', 'requestId': restored.restore['requestId']})
        self.assertCountEqual(['previous\n한글', 'current\n수정 중', 'third revision'], snapshots)

    def test_duplicate_saved_copy_is_acknowledged_with_matching_current_record_only(self):
        preserve_recovery(self.app.state, self.upgrade.restore)
        self.upgrade.action({'action': 'restored', 'requestId': self.old_id})
        self.assertIsNone(self.upgrade.restore)
        self.assertFalse(self.upgrade.path.exists())
        self.assertFalse(self.archive_path().exists())


if __name__ == '__main__':
    unittest.main()
