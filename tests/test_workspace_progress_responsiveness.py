"""Slow progress storage/clients must not monopolize queue-control admission."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from local_app.server import Handler, LocalApp, Server


def record(key='one', run='run-one'):
    return dict(key=key, runId=run, time=3.0, kind='tool_result',
                title='Read', text='reported evidence')


class ProgressResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.item.update(lastRunId='run-one', state='running')
        self.bridge = SimpleNamespace(_tool_activity_run_id='run-one', closed=False,
            cleanup_complete=True, close=lambda: True)
        self.item['bridge'] = self.bridge
        rows = self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': 'keep', 'attachments': []})['queue']
        self.request_id = rows[0]['id']
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(lambda: self.executor.shutdown(wait=True))

    def cancel(self):
        return self.app.dispatch.action(self.sid, {'action': 'cancel', 'requestId': self.request_id})

    def gated(self, target):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test storage gate was not released')
            return target(*args, **kwargs)
        return entered, release, slow

    def test_slow_progress_commit_does_not_block_queue_cancel_or_metadata(self):
        # Warm committed metadata. New evidence may be writing while the UI
        # reads the last durable count, without opening the database again.
        self.app.emit(self.sid, 'progress_record', record())
        entered, release, slow = self.gated(self.app.progress_logs._connect)
        with patch.object(self.app.progress_logs, '_connect', side_effect=slow):
            writer = self.executor.submit(self.app._emit_bridge, self.sid, self.bridge,
                                          'progress_record', record('two'))
            self.assertTrue(entered.wait(2))
            control = self.executor.submit(self.cancel)
            snapshot = self.executor.submit(self.app.public, self.item)
            try:
                self.assertEqual([], control.result(.5)['queue'])
                self.assertEqual(1, snapshot.result(.5)['progress']['count'])
            finally:
                release.set()
            writer.result(2)
        self.assertEqual(2, self.app.public(self.item)['progress']['count'])
        self.assertEqual(2, len(self.app.progress_logs.page(self.sid)['records']))

    def test_slow_progress_commit_keeps_reorder_apply_and_draft_available(self):
        for value in ('second', 'apply this'):
            rows = self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': value, 'attachments': []})['queue']
        self.app.drafts.update({'id': self.sid, 'revision': 0,
            'draft': {'text': 'still composing\nsecond line', 'attachments': []}, 'stash': None,
            'selectedId': self.sid})
        interrupted = []
        self.bridge.interrupt = lambda: interrupted.append(True)
        self.app.demo = False
        self.app.emit(self.sid, 'progress_record', record())
        entered, release, slow = self.gated(self.app.progress_logs._connect)
        with patch.object(self.app.progress_logs, '_connect', side_effect=slow):
            writer = self.executor.submit(self.app._emit_bridge, self.sid, self.bridge,
                                          'progress_record', record('two'))
            self.assertTrue(entered.wait(2))
            try:
                order = [row['id'] for row in reversed(rows)]
                reordered = self.executor.submit(self.app.dispatch.action, self.sid,
                    {'action': 'reorder', 'order': order}).result(.5)
                self.assertEqual(order, [row['id'] for row in reordered['queue']])
                selected = reordered['queue'][0]
                applied = self.executor.submit(self.app.dispatch.action, self.sid,
                    {'action': 'apply_now', 'requestId': selected['id'],
                     'editRevision': selected['editRevision'], 'clientRequestId': 'storage-slow-test'}).result(.5)
                self.assertEqual(selected['id'], applied['applyingId'])
                self.assertEqual([True], interrupted)
                self.assertEqual(3, len(applied['queue']))
            finally:
                release.set()
            writer.result(2)
        self.assertEqual('still composing\nsecond line', self.app.drafts.snapshot()['entries'][0]['draft']['text'])

    def test_late_commit_keeps_original_evidence_without_new_turn_notification(self):
        self.app.emit(self.sid, 'progress_record', record())
        entered, release, slow = self.gated(self.app.progress_logs._connect)
        with patch.object(self.app.progress_logs, '_connect', side_effect=slow):
            writer = self.executor.submit(self.app._emit_bridge, self.sid, self.bridge,
                                          'progress_record', record('two'))
            self.assertTrue(entered.wait(2))
            try:
                with self.app.lock:
                    self.item.update(lastRunId='run-two', events=[])
                    self.bridge._tool_activity_run_id = 'run-two'
            finally:
                release.set()
            writer.result(2)
        self.assertEqual([], self.item['events'])
        page = self.app.progress_logs.page(self.sid)
        self.assertEqual(['run-one', 'run-one'], [row['runId'] for row in page['records']])

    def test_chat_deletion_waits_for_admitted_progress_and_does_not_recreate_files(self):
        sid = self.app.create(kind='chat')['id']
        item = self.app.get(sid)
        item.update(lastRunId='run-one', state='running')
        self.app.emit(sid, 'progress_record', record())
        entered, release, slow = self.gated(self.app.progress_logs._connect)
        with patch.object(self.app.progress_logs, '_connect', side_effect=slow):
            writer = self.executor.submit(self.app.emit, sid, 'progress_record', record('two'))
            self.assertTrue(entered.wait(2))
            deletion = self.executor.submit(self.app.delete_chat, sid, True)
            try:
                with self.assertRaises(TimeoutError):
                    deletion.result(.1)
                self.assertEqual([], self.executor.submit(self.cancel).result(.5)['queue'])
            finally:
                release.set()
            writer.result(2)
            self.assertTrue(deletion.result(2)['ok'])
        self.assertNotIn(sid, self.app.sessions)
        self.assertFalse(list((self.app.state / 'progress').glob(sid + '*')))
        self.assertFalse(self.app.progress_logs.metadata(sid)['available'])

    def test_unavailable_storage_metadata_does_not_reopen_on_every_poll(self):
        self.app.progress_logs.forget(self.sid)
        with patch.object(self.app.progress_logs, '_connect', side_effect=OSError('temporarily unavailable')) as opened:
            first = self.app.public(self.item)['progress']
            for _ in range(20):
                self.assertEqual(first, self.app.public(self.item)['progress'])
            self.assertEqual(1, opened.call_count)
        self.assertFalse(first['available'])
        self.assertTrue(first['truncated'])
        self.app.emit(self.sid, 'progress_record', record())
        self.assertEqual(1, self.app.public(self.item)['progress']['count'])

    def start_server(self):
        server = Server(self.app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def shutdown():
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.addCleanup(shutdown)
        return server

    def fetch(self, server, route):
        request = Request(server.origin + route, headers={'Authorization': 'Bearer ' + self.app.token})
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_slow_event_socket_does_not_hold_queue_controls(self):
        self.app.emit(self.sid, 'assistant_delta', {'text': 'stream'})
        server = self.start_server()
        entered, release = threading.Event(), threading.Event()
        original = Handler.reply
        def slow(handler, data, *args, **kwargs):
            if handler.path.startswith('/api/events?'):
                entered.set()
                release.wait(5)
            return original(handler, data, *args, **kwargs)
        with patch.object(Handler, 'reply', slow):
            reader = self.executor.submit(self.fetch, server, '/api/events?' + urlencode({'id': self.sid}))
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual([], self.executor.submit(self.cancel).result(.5)['queue'])
            finally:
                release.set()
            self.assertTrue(reader.result(2)['events'])

    def test_slow_event_reply_keeps_snapshot_consistent_with_its_sequence(self):
        self.app.emit(self.sid, 'connected', {'model': 'before', 'capabilities': {'setModel': True}})
        sequence = self.item['seq']
        server = self.start_server()
        entered, release = threading.Event(), threading.Event()
        original = Handler.reply
        def slow(handler, data, *args, **kwargs):
            if handler.path.startswith('/api/events?'):
                entered.set()
                release.wait(5)
            return original(handler, data, *args, **kwargs)
        with patch.object(Handler, 'reply', slow):
            reader = self.executor.submit(self.fetch, server, '/api/events?' + urlencode({'id': self.sid}))
            self.assertTrue(entered.wait(2))
            try:
                with self.app.lock:
                    self.item['connection']['capabilities']['setModel'] = False
                self.app.emit(self.sid, 'model_changed', {'model': 'after'})
            finally:
                release.set()
            response = reader.result(2)
        self.assertEqual(sequence, response['seq'])
        connected = next(row['data'] for row in response['events'] if row['type'] == 'connected')
        self.assertEqual('before', connected['model'])
        self.assertTrue(connected['capabilities']['setModel'])
        response = self.fetch(server, '/api/events?' + urlencode({'id': self.sid, 'after': sequence}))
        self.assertGreater(response['seq'], sequence)
        self.assertEqual('after', response['events'][0]['data']['model'])

    def test_slow_progress_page_does_not_hold_queue_controls(self):
        self.app.emit(self.sid, 'progress_record', record())
        server = self.start_server()
        entered, release, slow = self.gated(self.app.progress_logs.page)
        with patch.object(self.app.progress_logs, 'page', side_effect=slow):
            reader = self.executor.submit(self.fetch, server, '/api/progress?' + urlencode({'id': self.sid}))
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual([], self.executor.submit(self.cancel).result(.5)['queue'])
            finally:
                release.set()
            self.assertEqual(1, len(reader.result(2)['records']))


if __name__ == '__main__':
    unittest.main()
