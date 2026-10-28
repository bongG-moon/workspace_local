"""Background display saves must not block queue controls or overwrite newer state."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from local_app.history import HistoryStore
from local_app.server import LocalApp
from local_app.work_queue import WorkQueue


class ProgressHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', command=['not-started'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.item.update(lastRunId='run-one', state='running')
        self.writer = self.app._progress_history
        self.writer.delay = 60  # Deterministic batching; flush explicitly advances it.

    def emit(self, identifier='tool-one', **fields):
        self.app.emit(self.sid, 'tool_activity', dict(id=identifier, tool='Read', target='report.csv',
            state='requested', startedAt=1.0, runId=self.item['lastRunId'], **fields))

    def saved(self):
        return json.loads((self.app.state / 'history-sessions' / (self.sid + '.json')).read_text('utf-8'))

    def test_burst_coalesces_to_one_body_write_and_keeps_latest_records(self):
        self.item['messages'] = [{'role': 'assistant', 'text': '가' * 100000} for _ in range(5)]
        with patch.object(self.app.history, '_write', wraps=self.app.history._write) as write:
            for index in range(30):
                self.emit(str(index))
            self.assertEqual(0, write.call_count)
            self.assertEqual(1, len(self.writer.pending))
            self.assertTrue(self.writer.flush())
        self.assertEqual(1, write.call_count)
        self.assertEqual(30, len(self.saved()['toolActivity']))
        self.assertEqual(500000, sum(len(row['text']) for row in self.saved()['messages']))
        self.assertTrue(self.item['_historySaved'])

    def test_slow_display_disk_write_does_not_block_cancel_reorder_or_apply(self):
        other = self.app.create(str(self.root), True)['id']
        item = self.app.get(other)
        item.update(state='running')
        interrupted = []
        item['bridge'] = SimpleNamespace(closed=False, cleanup_complete=False,
            interrupt=lambda: interrupted.append(True), close=lambda: True)
        rows = []
        for text in ('첫 줄\n두 번째 줄', '남길 요청', '지금 반영'):
            rows = self.app.dispatch.action(other, {'action': 'enqueue', 'text': text, 'attachments': []})['queue']
        draft = {'text': '아직 작성 중\n내용', 'attachments': [str(self.root / 'draft.csv')]}
        self.app.drafts.update({'id': other, 'revision': 0, 'draft': draft, 'stash': None, 'selectedId': other})
        entered, release = threading.Event(), threading.Event()
        original = self.app.history._write
        def delayed(path, payload):
            if path.name == self.sid + '.json':
                entered.set()
                self.assertTrue(release.wait(4))
            return original(path, payload)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.app.history, '_write', side_effect=delayed):
            self.emit()
            saving = pool.submit(self.writer.flush)
            try:
                self.assertTrue(entered.wait(2))
                def controls():
                    self.app.dispatch.action(other, {'action': 'cancel', 'requestId': rows[0]['id']})
                    self.app.dispatch.action(other, {'action': 'reorder', 'order': [rows[2]['id'], rows[1]['id']]})
                    return self.app.dispatch.action(other, {'action': 'apply_now', 'requestId': rows[2]['id'],
                        'editRevision': rows[2].get('editRevision', 0), 'clientRequestId': 'apply-once'})
                result = pool.submit(controls).result(timeout=1)
                self.assertEqual([rows[2]['id'], rows[1]['id']], [row['id'] for row in result['queue']])
                self.assertEqual([True], interrupted)
                # A repeated HTTP action still only interrupts once.
                again = self.app.dispatch.action(other, {'action': 'apply_now', 'requestId': rows[2]['id'],
                    'editRevision': rows[2].get('editRevision', 0), 'clientRequestId': 'apply-once'})
                self.assertTrue(again['alreadyApplied'])
                self.assertEqual([True], interrupted)
                self.assertEqual(draft, self.app.drafts.snapshot()['entries'][0]['draft'])
            finally:
                release.set()
            self.assertTrue(saving.result(timeout=2))
        persisted = WorkQueue(self.app.state).snapshot(other)
        self.assertEqual([rows[2]['id'], rows[1]['id']], [row['id'] for row in persisted['queue']])

    def test_assistant_snapshot_never_exposes_response_before_durable_save(self):
        # Reads may wait or return the previous snapshot, but must not expose
        # a response whose body is still being committed to disk.
        bridge = SimpleNamespace(closed=False, cleanup_complete=False, close=lambda: True)
        self.item['bridge'] = bridge
        entered, release = threading.Event(), threading.Event()
        original = self.app.history._write
        def delayed(path, payload):
            if path.name == self.sid + '.json':
                entered.set()
                self.assertTrue(release.wait(4))
            return original(path, payload)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.app.history, '_write', side_effect=delayed):
            saving = pool.submit(self.app.emit, self.sid, 'assistant', {'text': 'SYNTHETIC_UNCOMMITTED'},
                                 _bridge=bridge, _origin=(self.item, self.item['lastRunId']))
            try:
                self.assertTrue(entered.wait(2))
                snapshot = pool.submit(lambda: self.app.public(self.app.get(self.sid)))
                try:
                    early = snapshot.result(timeout=.15)
                except TimeoutError:
                    pass  # Waiting is the existing implementation, not a performance target.
                else:
                    self.assertNotIn('SYNTHETIC_UNCOMMITTED', [row['text'] for row in early['messages']])
                self.assertFalse(saving.done())
            finally:
                release.set()
            saving.result(timeout=2)
            snapshot.result(timeout=2)
        self.assertEqual('SYNTHETIC_UNCOMMITTED', self.saved()['messages'][-1]['text'])

    def test_assistant_event_stays_before_later_same_session_event(self):
        bridge = SimpleNamespace(closed=False, cleanup_complete=False, close=lambda: True)
        self.item['bridge'] = bridge
        entered, release, later_started = threading.Event(), threading.Event(), threading.Event()
        original = self.app.history._write
        def delayed(path, payload):
            if path.name == self.sid + '.json':
                entered.set()
                self.assertTrue(release.wait(4))
            return original(path, payload)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.app.history, '_write', side_effect=delayed):
            saving = pool.submit(self.app.emit, self.sid, 'assistant', {'text': 'first'},
                                 _bridge=bridge, _origin=(self.item, self.item['lastRunId']))
            try:
                self.assertTrue(entered.wait(2))
                def publish_later():
                    later_started.set()
                    self.app.emit(self.sid, 'notice', {'message': 'later'})
                later = pool.submit(publish_later)
                self.assertTrue(later_started.wait(2))
            finally:
                release.set()
            saving.result(timeout=2)
            later.result(timeout=2)
        relevant = [event['type'] for event in self.item['events'] if event['type'] in {'assistant', 'notice'}]
        self.assertEqual(['assistant', 'notice'], relevant)

    def test_sync_terminal_snapshot_cannot_be_overwritten_by_older_inflight_save(self):
        entered, release = threading.Event(), threading.Event()
        original = self.app.history._write
        def delayed(path, payload):
            if threading.current_thread().name == 'workspace-history':
                entered.set()
                self.assertTrue(release.wait(4))
            return original(path, payload)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.app.history, '_write', side_effect=delayed):
            self.emit()
            saving = pool.submit(self.writer.flush)
            try:
                self.assertTrue(entered.wait(2))
                def finish():
                    self.app.emit(self.sid, 'assistant', {'text': '최종 답변'})
                    self.app.emit(self.sid, 'result', {'sessionId': 'conversation'})
                terminal = pool.submit(finish)
            finally:
                release.set()
            terminal.result(timeout=2)
            self.assertTrue(saving.result(timeout=2))
        self.assertEqual('최종 답변', self.saved()['messages'][-1]['text'])
        self.assertEqual('interrupted', self.saved()['toolActivity'][0]['state'])
        self.assertEqual('conversation', self.saved()['sessionId'])

    def test_pending_display_save_is_invalidated_by_sync_edit(self):
        self.emit()
        self.item['title'] = '바뀐 이름'
        self.app.save(self.sid)
        self.assertTrue(self.writer.flush())
        self.assertEqual('바뀐 이름', self.saved()['title'])
        self.assertEqual({}, self.writer.pending)

    def test_stop_error_and_result_flush_latest_activity_synchronously(self):
        for kind, data in [('status', {'state': 'stopped'}), ('error', {'message': 'fixture'}), ('result', {})]:
            with self.subTest(kind=kind):
                self.item.update(state='running', lastRunId='run-' + kind)
                self.emit('tool-' + kind)
                self.app.emit(self.sid, kind, data)
                record = next(row for row in self.saved()['toolActivity'] if row['id'] == 'tool-' + kind)
                self.assertEqual('interrupted', record['state'])
                self.assertTrue(self.writer.flush())

    def test_close_drains_pending_and_joins_worker(self):
        self.emit()
        self.assertTrue(self.app.close())
        self.assertFalse(self.writer.thread.is_alive())
        self.assertEqual('tool-one', self.saved()['toolActivity'][0]['id'])

    def test_transient_write_failure_preserves_memory_warns_and_retries_at_close(self):
        self.emit()
        with patch.object(self.app.history, '_write', side_effect=OSError('fixture disk unavailable')):
            self.assertFalse(self.writer.flush())
        self.assertEqual('tool-one', self.item['toolActivity'][0]['id'])
        self.assertFalse(self.item['_historySaved'])
        self.assertIn('진행 기록', self.app.bootstrap()['historyWarning'])
        self.assertTrue(self.app.close())
        self.assertEqual('tool-one', self.saved()['toolActivity'][0]['id'])
        self.assertFalse(self.writer.failed())

    def test_deleted_chat_cannot_be_recreated_by_pending_progress(self):
        self.sid = self.app.create(kind='chat')['id']
        self.item = self.app.get(self.sid)
        self.item.update(lastRunId='chat-run', state='running')
        self.emit()
        self.assertTrue(self.app.delete_chat(self.sid, confirmed=True)['ok'])
        self.assertTrue(self.writer.flush())
        self.assertFalse((self.app.state / 'history-sessions' / (self.sid + '.json')).exists())
        self.assertNotIn(self.sid, [row['id'] for row in HistoryStore(self.app.state).load()])

    def test_failed_sync_replacement_retains_retry_for_invalidated_pending_save(self):
        self.emit()
        self.item['title'] = '보존해야 할 최신 이름'
        with patch.object(self.app.history, '_write', side_effect=OSError('fixture disk unavailable')):
            with self.assertRaises(OSError):
                self.app.save(self.sid)
        self.assertEqual({}, self.writer.pending)
        self.assertTrue(self.writer.failed())
        self.assertTrue(self.app.close())
        self.assertEqual('보존해야 할 최신 이름', self.saved()['title'])
        self.assertEqual('tool-one', self.saved()['toolActivity'][0]['id'])


if __name__ == '__main__':
    unittest.main()
