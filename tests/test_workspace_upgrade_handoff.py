"""Cooperative restart contracts, with no Claude process or personal state."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.server import AppClosing, LocalApp, Server
from local_app.upgrade_handoff import CAPTURE_SECONDS, LEASE_SECONDS, UpgradeHandoff, snapshot


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.now = 0
        self.upgrade = self.app.upgrade
        self.upgrade.clock = lambda: self.now
        self.rid = uuid.uuid4().hex
        self.target = '99.0.0'
        self.sid = self.app.create(str(self.app.state), True)['id']
        self.draft = {'sessionId': self.sid, 'drafts': [
            {'id': self.sid, 'text': '한글\n  /effort high @보고서.xlsx  ', 'attachments': ['C:\\업무\\보고서.xlsx']},
            {'id': 'home', 'text': '', 'attachments': []}]}

    def action(self, action, **values):
        return self.upgrade.action({'action': action, 'requestId': self.rid, **values})

    def prepare(self):
        return self.action('prepare', targetVersion=self.target)

    def capture(self):
        revision = self.upgrade.status()['upgrade']['revision']
        return self.action('capture', revision=revision, snapshot=deepcopy(self.draft))

    def test_prepare_never_closes_or_freezes_admission(self):
        result = self.prepare()
        self.assertEqual('capture', result['upgrade']['stage'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        with self.app.operation(upgrade_change=True):
            self.assertEqual('waiting', self.upgrade.status()['upgrade']['stage'])
        self.assertEqual('capture', self.upgrade.status()['upgrade']['stage'])

    def test_idle_requires_ack_even_if_no_native_window(self):
        self.prepare()
        self.assertEqual('headless', self.app.window_state()['mode'])
        self.assertFalse(self.action('commit')['closed'])
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_explicit_headless_can_capture_empty_snapshot(self):
        self.app._upgrade_headless = True
        self.assertEqual('captured', self.prepare()['upgrade']['stage'])
        self.assertTrue(self.action('commit')['closed'])
        restored = UpgradeHandoff(self.app, self.target).restore
        self.assertEqual({'sessionId': None, 'drafts': []}, restored['snapshot'])

    def test_exact_drafts_saved_then_restored_once_without_running(self):
        self.prepare()
        self.assertTrue(self.capture()['ok'])
        self.assertIsNone(UpgradeHandoff(self.app, self.target).restore)
        self.assertTrue(self.action('commit', targetVersion=self.target)['closed'])
        restored = UpgradeHandoff(self.app, self.target)
        self.assertEqual(self.draft, restored.restore['snapshot'])
        self.assertEqual(self.rid, restored.restore['requestId'])
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertIsNone(UpgradeHandoff(self.app, '98.0.0').restore)
        with self.assertRaises(ValueError):
            restored.action({'action': 'restored', 'requestId': uuid.uuid4().hex})
        self.assertTrue(restored.path.exists())
        self.assertTrue(restored.action({'action': 'restored', 'requestId': self.rid})['ok'])
        self.assertFalse(restored.path.exists())

    def test_all_sessions_busy_even_if_not_current_or_visible(self):
        other = self.app.create(str(self.app.state), True)['id']
        item = self.app.sessions[other]
        for field, value in [('state', 'starting'), ('state', 'running'), ('state', 'approval'), ('state', 'question'),
                             ('requests', {'r': {}}), ('choice', {'id': 'choice'}),
                             ('verification', {'state': 'checking'}), ('verification', {'state': 'needs-review'}),
                             ('_connecting', True), ('_modelUpdating', True), ('_dispatchClaim', 'claim'),
                             ('_choiceAnswerClaim', 'claim'), ('_removingFromList', True)]:
            with self.subTest(field=field, value=value):
                previous = item.get(field)
                item[field] = value
                self.prepare()
                self.assertEqual('waiting', self.upgrade.status()['upgrade']['stage'])
                self.assertFalse(self.action('commit')['closed'])
                self.action('cancel')
                if previous is None:
                    item.pop(field, None)
                else:
                    item[field] = previous

    def test_active_lifecycle_operation_prevents_commit(self):
        self.prepare()
        self.capture()
        entered, release = threading.Event(), threading.Event()
        def held_request():
            with self.app.operation(upgrade_change=True):
                entered.set()
                release.wait(3)
        worker = threading.Thread(target=held_request)
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            self.assertFalse(self.action('commit')['closed'])
            self.assertFalse(self.app.shutdown_status()['closing'])
        finally:
            release.set()
            worker.join(3)
        self.assertEqual('capture', self.upgrade.status()['upgrade']['stage'])

    def test_completed_concurrent_mutation_invalidates_old_capture(self):
        self.prepare()
        self.capture()
        revision = self.upgrade.status()['upgrade']['revision']
        with self.app.operation(upgrade_change=True):
            self.app.sessions[self.sid]['title'] = '새 이름'
        result = self.action('commit')
        self.assertFalse(result['closed'])
        self.assertEqual('capture', result['upgrade']['stage'])
        self.assertGreater(result['upgrade']['revision'], revision)
        self.assertFalse(self.action('capture', revision=revision, snapshot=self.draft)['ok'])
        self.draft['drafts'][0]['text'] = '나중에 쓴 내용'
        self.capture()
        self.assertTrue(self.action('commit')['closed'])
        self.assertEqual(self.draft, UpgradeHandoff(self.app, self.target).restore['snapshot'])

    def test_read_only_heartbeat_does_not_erase_captured_drafts(self):
        self.prepare()
        self.capture()
        captured = self.upgrade.status()['upgrade']
        with self.app.operation():
            self.assertEqual(captured, self.upgrade.status()['upgrade'])
        self.assertEqual(captured, self.upgrade.status()['upgrade'])

    def test_other_version_draft_record_cannot_be_overwritten(self):
        self.prepare()
        self.capture()
        self.action('commit')
        previous = self.upgrade.path.read_bytes()
        mismatch = UpgradeHandoff(self.app, '98.0.0')
        self.assertIn(self.target, mismatch.warning)
        self.assertIsNone(mismatch.restore)
        with self.assertRaises(ValueError):
            mismatch.action({'action': 'prepare', 'requestId': uuid.uuid4().hex, 'targetVersion': '100.0.0'})
        self.assertEqual(previous, self.upgrade.path.read_bytes())

    def test_cli_work_event_invalidates_capture_before_it_finishes(self):
        self.prepare()
        self.capture()
        self.app.emit(self.sid, 'status', {'state': 'running'})
        self.assertEqual('waiting', self.upgrade.status()['upgrade']['stage'])
        self.app.emit(self.sid, 'status', {'state': 'done'})
        self.assertEqual('capture', self.upgrade.status()['upgrade']['stage'])

    def test_commit_seals_admission_before_normal_close(self):
        self.prepare()
        self.capture()
        entered, release = threading.Event(), threading.Event()
        actual_close = self.app.close
        def delayed_close():
            entered.set()
            release.wait(3)
            return actual_close()
        results = []
        with patch.object(self.app, 'close', delayed_close):
            worker = threading.Thread(target=lambda: results.append(self.action('commit')))
            worker.start()
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(AppClosing):
                    with self.app.operation(upgrade_change=True):
                        self.fail('Late operation was admitted')
                with self.assertRaises(ValueError):
                    self.action('cancel')
                self.assertEqual(self.draft, UpgradeHandoff(self.app, self.target).restore['snapshot'])
            finally:
                release.set()
                worker.join(3)
        self.assertTrue(results[0]['closed'])

    def test_capture_write_failure_never_closes_or_claims_capture(self):
        self.prepare()
        with patch.object(self.upgrade, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.capture()
        self.assertEqual('capture', self.upgrade.status()['upgrade']['stage'])
        self.assertFalse(self.action('commit')['closed'])
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_ready_write_failure_leaves_app_and_drafts_usable(self):
        self.prepare()
        self.capture()
        with patch.object(self.upgrade, '_write', side_effect=OSError('disk full')), patch.object(self.app, 'close') as close:
            result = self.action('commit')
            close.assert_not_called()
        self.assertFalse(result['closed'])
        self.assertEqual('failed', result['upgrade']['stage'])
        with self.app.operation(upgrade_change=True):
            pass
        self.assertEqual(self.draft, json.loads(self.upgrade.path.read_text(encoding='utf-8'))['snapshot'])

    def test_cleanup_failure_never_claims_closed_and_retains_recovery(self):
        self.prepare()
        self.capture()
        with patch.object(self.app, 'close', return_value=False):
            result = self.action('commit')
        self.assertFalse(result['closed'])
        self.assertEqual('failed', result['upgrade']['stage'])
        self.assertEqual(self.draft, UpgradeHandoff(self.app, self.target).restore['snapshot'])

    def test_cancel_expiry_and_capture_timeout_do_not_close(self):
        self.prepare()
        self.capture()
        self.assertEqual('cancelled', self.action('cancel')['upgrade']['stage'])
        self.assertFalse(self.action('commit')['closed'])
        self.prepare()
        self.now = CAPTURE_SECONDS + 1
        self.assertEqual('expired', self.upgrade.status()['upgrade']['stage'])
        self.app.sessions[self.sid]['state'] = 'running'
        self.prepare()
        self.now += LEASE_SECONDS + 1
        self.assertEqual('expired', self.upgrade.status()['upgrade']['stage'])
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_prepare_is_idempotent_and_foreign_requests_cannot_replace(self):
        first = self.prepare()
        self.assertEqual(first, self.prepare())
        with self.assertRaises(ValueError):
            self.upgrade.action({'action': 'prepare', 'requestId': uuid.uuid4().hex, 'targetVersion': self.target})
        with self.assertRaises(ValueError):
            self.upgrade.status(uuid.uuid4().hex)
        self.capture()
        with self.assertRaises(ValueError):
            self.action('commit', targetVersion='98.0.0')
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_duplicate_capture_only_acknowledges_same_revision_and_exact_draft(self):
        self.prepare()
        revision = self.upgrade.status()['upgrade']['revision']
        captured = self.capture()
        before = self.upgrade.path.read_bytes()
        self.assertEqual(captured, self.action('capture', revision=revision, snapshot=deepcopy(self.draft)))
        self.assertEqual(before, self.upgrade.path.read_bytes())
        other = deepcopy(self.draft)
        other['drafts'][0]['text'] = '다른 창에서 아직 보내지 않은 초안'
        result = self.action('capture', revision=revision, snapshot=other)
        self.assertFalse(result['ok'])
        self.assertEqual('cancelled', result['upgrade']['stage'])
        self.assertFalse(self.action('commit')['closed'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        self.assertEqual(before, self.upgrade.path.read_bytes())

    def test_invalid_or_stale_duplicate_capture_cancels_before_close(self):
        for payload in ({'revision': 999, 'snapshot': self.draft}, {'revision': 2, 'snapshot': {'invalid': True}}):
            self.prepare()
            self.capture()
            result = self.action('capture', **payload)
            self.assertFalse(result['ok'])
            self.assertEqual('cancelled', result['upgrade']['stage'])
            self.assertFalse(self.app.shutdown_status()['closing'])

    def test_inflight_queue_protected_future_and_paused_work_preserved(self):
        for status in ('dispatching', 'submitted'):
            with patch.object(self.app.dispatch.queue, 'snapshot', return_value={'queue': [{'status': status}], 'schedules': []}):
                self.assertEqual('waiting', self.prepare()['upgrade']['stage'])
                self.action('cancel')
        # Pending and future entries remain durably stored; ordinary restart
        # pauses them. They need not be deleted to switch app versions.
        with patch.object(self.app.dispatch.queue, 'snapshot', return_value={
                'queue': [{'status': 'queued'}], 'schedules': [{'enabled': True, 'nextRunAt': 9999999999}]}):
            self.assertEqual('capture', self.prepare()['upgrade']['stage'])

    def test_busy_bridge_and_uncertain_queue_prevent_handoff(self):
        for value in (SimpleNamespace(busy=True), SimpleNamespace(pending={'x': 1}),
                      SimpleNamespace(stopping=True, cleanup_complete=False)):
            self.app.sessions[self.sid]['bridge'] = value
            self.assertEqual('waiting', self.prepare()['upgrade']['stage'])
            self.action('cancel')
        self.app.sessions[self.sid]['bridge'] = None
        with patch.object(self.app.dispatch.queue, 'snapshot', return_value={'warning': 'unreadable', 'queue': []}):
            self.assertEqual('waiting', self.prepare()['upgrade']['stage'])

    def test_no_truncation_no_arbitrary_destination_or_settings(self):
        self.assertEqual(self.draft, snapshot(self.draft))
        invalid = []
        huge = deepcopy(self.draft)
        huge['drafts'][0]['text'] = 'x' * 100001
        invalid.append(huge)
        invalid.append({**self.draft, 'destination': 'C:\\other.json'})
        duplicate = deepcopy(self.draft)
        duplicate['drafts'].append(deepcopy(duplicate['drafts'][0]))
        invalid.append(duplicate)
        traversal = deepcopy(self.draft)
        traversal['drafts'][0]['id'] = '../outside'
        invalid.append(traversal)
        too_many = deepcopy(self.draft)
        too_many['drafts'][0]['attachments'] *= 13
        invalid.append(too_many)
        utf8_huge = deepcopy(self.draft)
        utf8_huge['drafts'][0]['text'] = '한' * 100000
        invalid.append(utf8_huge)
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                snapshot(value)

    def test_corrupt_existing_record_is_preserved_and_blocks_overwrite(self):
        self.upgrade.path.write_text('{broken', encoding='utf-8')
        self.app.upgrade = UpgradeHandoff(self.app, self.target)
        self.assertIsNotNone(self.app.upgrade.warning)
        with self.assertRaises(ValueError):
            self.app.upgrade.action({'action': 'prepare', 'requestId': self.rid, 'targetVersion': self.target})
        self.assertEqual('{broken', self.upgrade.path.read_text(encoding='utf-8'))

    def test_optional_stashes_preserve_utf16_selection_and_roundtrip_without_execution(self):
        legacy = snapshot(self.draft)
        self.assertNotIn('stashes', legacy)
        self.draft['stashes'] = [
            {'id': self.sid, 'text': 'A😀한글', 'attachments': ['C:\\자료\\원본.png'], 'selectionStart': 1, 'selectionEnd': 5},
            {'id': 'home', 'text': '미전송 요청', 'attachments': [], 'selectionStart': 0, 'selectionEnd': 0}]
        expected = deepcopy(self.draft)
        validated = snapshot(self.draft)
        self.draft['stashes'][0]['attachments'].append('C:\\changed.png')
        self.assertEqual(expected, validated)
        self.draft = expected
        self.prepare()
        self.assertTrue(self.capture()['ok'])
        self.assertTrue(self.action('commit')['closed'])
        restored = UpgradeHandoff(self.app, self.target)
        self.assertEqual(expected, restored.restore['snapshot'])
        self.assertEqual([], self.app.get(self.sid)['messages'])

    def test_stash_validation_rejects_shape_ids_offsets_and_duplicates_without_filtering(self):
        stash = {'id': self.sid, 'text': '😀', 'attachments': [], 'selectionStart': 0, 'selectionEnd': 2}
        invalid = [None, {}, [dict(stash, selectionStart=True)], [dict(stash, selectionEnd=3)],
                   [dict(stash, selectionStart=-1)], [dict(stash, selectionStart=2, selectionEnd=1)],
                   [dict(stash, selectionEnd=1.0)], [dict(stash, id='../escape')],
                   [dict(stash, attachments=[''])], [dict(stash, arbitrary=True)],
                   [{key: value for key, value in stash.items() if key != 'selectionStart'}],
                   [stash, deepcopy(stash)],
                   [dict(stash, id=str(uuid.uuid4())) for _ in range(51)]]
        for value in invalid:
            with self.subTest(value=str(value)[:100]), self.assertRaises(ValueError):
                snapshot({**self.draft, 'stashes': value})
        only_stash = {'sessionId': None, 'drafts': [], 'stashes': [dict(stash, id='home')]}
        self.assertEqual(only_stash, snapshot(only_stash))

    def test_snapshot_limit_includes_stashes_and_reports_invalid_unicode_cleanly(self):
        value = deepcopy(self.draft)
        value['drafts'][0]['text'] = 'a' * 90000
        value['stashes'] = [{'id': self.sid, 'text': '한' * 90000, 'attachments': [], 'selectionStart': 0, 'selectionEnd': 90000}]
        with self.assertRaises(ValueError):
            snapshot(value)
        value['stashes'][0]['text'] = '\ud800'
        value['stashes'][0]['selectionEnd'] = 1
        with self.assertRaises(ValueError):
            snapshot(value)

    def test_authenticated_routes_busy_conflict_and_successful_shutdown(self):
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def call(path, body=None, **headers):
            data = None if body is None else json.dumps(body).encode('utf-8')
            return urlopen(Request(server.origin + path, data=data, headers={
                'Authorization': 'Bearer ' + self.app.token, 'Content-Type': 'application/json', **headers}), timeout=4)
        for path, body in [('/api/upgrade', None), ('/api/upgrade', {'action': 'prepare', 'requestId': self.rid, 'targetVersion': self.target})]:
            with self.assertRaises(HTTPError) as failed:
                call(path, body, Authorization='Bearer wrong')
            self.assertEqual(403, failed.exception.code)
        for headers in ({'Origin': 'https://other.invalid'}, {'Host': 'other.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.assertRaises(HTTPError) as failed:
                call('/api/upgrade', **headers)
            self.assertEqual(403, failed.exception.code)
        with call('/api/bootstrap') as response:
            self.assertEqual(1, json.load(response)['upgradeProtocol'])
        with call('/api/upgrade', {'action': 'prepare', 'requestId': self.rid, 'targetVersion': self.target}) as response:
            prepared = json.load(response)
        with call('/api/attention') as response:
            self.assertEqual(prepared['upgrade'], json.load(response)['upgrade'])
        with self.assertRaises(HTTPError) as waiting:
            call('/api/upgrade', {'action': 'commit', 'requestId': self.rid})
        self.assertEqual(409, waiting.exception.code)
        self.capture()
        with call('/api/upgrade', {'action': 'commit', 'requestId': self.rid, 'targetVersion': self.target}) as response:
            self.assertTrue(json.load(response)['closed'])
        worker.join(3)
        self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
