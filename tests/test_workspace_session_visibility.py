"""List-only task removal never deletes transcripts, workspaces or queued work."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.server import LocalApp, Server
from local_app.session_visibility import SessionVisibility


class IdleBridge:
    closed = False
    cleanup_complete = False
    busy = False
    stopping = False
    pending = {}

    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.closed = self.cleanup_complete = True
        return True


class SessionVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True, title='검토 업무')['id']
        self.item = self.app.get(self.sid)
        self.item.update(sessionId=str(uuid.uuid4()), messages=[{'role': 'user', 'text': '원본 요청'}])
        self.app.save(self.sid)
        (self.root / '보고서.txt').write_text('업무 파일 원본', encoding='utf-8')
        (self.root / 'original-claude.jsonl').write_text('{"message":"원본 세션"}\n', encoding='utf-8')

    def originals(self):
        paths = [self.root / '보고서.txt', self.root / 'original-claude.jsonl',
                 self.app.state / 'history-index.json', *self.app.state.joinpath('history-sessions').glob('*.json')]
        return {path: path.read_bytes() for path in paths}

    def test_confirmation_required_without_writes_or_closing_connection(self):
        bridge = self.item['bridge'] = IdleBridge()
        before = self.originals()
        for confirmed in (False, None, 'true', 1):
            with self.subTest(confirmed=confirmed), self.assertRaises(ValueError):
                self.app.hide_session(self.sid, confirmed)
        self.assertEqual(before, self.originals())
        self.assertFalse(self.app.session_visibility.path.exists())
        self.assertEqual(0, bridge.close_calls)

    def test_duplicate_confirm_is_idempotent_and_late_question_keeps_visible_task(self):
        bridge = self.item['bridge'] = IdleBridge()
        close = bridge.close
        def late_question():
            self.item['requests']['late'] = {'id': 'late'}
            return close()
        bridge.close = late_question
        with self.assertRaisesRegex(ValueError, '확인할 내용'):
            self.app.hide_session(self.sid, True)
        self.assertFalse(self.app.session_visibility.contains(self.sid))
        self.item['requests'].clear()
        self.assertTrue(self.app.hide_session(self.sid, True)['ok'])
        stored = self.app.session_visibility.path.read_bytes()
        self.assertTrue(self.app.hide_session(self.sid, True)['ok'])
        self.assertEqual(stored, self.app.session_visibility.path.read_bytes())

    def test_hide_persists_only_visibility_releases_idle_cli_and_preserves_originals(self):
        bridge = self.item['bridge'] = IdleBridge()
        other = self.app.create(str(self.root), True, title='남길 업무')['id']
        self.app.reorder_session(self.sid, other, 'before')
        before = self.originals()
        result = self.app.hide_session(self.sid, True)
        self.assertTrue(result['historyPreserved'])
        self.assertEqual(1, bridge.close_calls)
        self.assertEqual(before, self.originals())
        self.assertIsNone(self.item['bridge'])
        self.assertEqual([other], [row['id'] for row in self.app.bootstrap()['sessions']])
        self.assertNotIn(self.sid, self.app.bootstrap()['sessionOrder']['ids'])
        with self.assertRaises(ValueError):
            self.app.get(self.sid)
        restored = LocalApp(self.app.state, demo=True)
        self.addCleanup(restored.close)
        self.assertIn(self.sid, restored.sessions)
        self.assertNotIn(self.sid, [row['id'] for row in restored.bootstrap()['sessions']])
        self.assertEqual(before, self.originals())

    def test_explicit_same_session_import_unhides_same_record(self):
        self.app.hide_session(self.sid, True)
        before = self.originals()
        record = {key: self.item[key] for key in ('sessionId', 'workspace')}
        with patch.object(self.app, 'import_sessions', return_value=record), patch('local_app.server.runtime_context', return_value={'configRoot': str(self.root)}):
            response = self.app.import_session(self.item['sessionId'])
        self.assertTrue(response['restored'])
        self.assertTrue(response['existing'])
        self.assertEqual(self.sid, response['session']['id'])
        self.assertFalse(response['session']['trusted'])
        self.assertEqual(before, self.originals())
        self.assertEqual(1, len(self.app.sessions))

    def test_busy_pending_and_inflight_controls_block_without_closing(self):
        bridge = self.item['bridge'] = IdleBridge()
        for values in ({'state': 'running'}, {'state': 'starting'}, {'state': 'approval'}, {'state': 'question'},
                       {'requests': {'request': {}}}, {'choice': {'id': 'question'}},
                       {'verification': {'state': 'needs-review'}}, {'verification': {'state': 'checking'}},
                       {'_connecting': True}, {'_modelUpdating': True}, {'_dispatchClaim': 'claim'},
                       {'_choiceAnswerClaim': 'claim'}):
            with self.subTest(values=values):
                prior = {key: self.item.get(key) for key in values}
                self.item.update(values)
                with self.assertRaises(ValueError):
                    self.app.hide_session(self.sid, True)
                self.item.update(prior)
        bridge.busy = True
        with self.assertRaises(ValueError):
            self.app.hide_session(self.sid, True)
        bridge.busy = False
        self.assertEqual(0, bridge.close_calls)
        self.assertFalse(self.app.session_visibility.path.exists())

    def test_queue_and_future_or_paused_schedules_require_explicit_resolution(self):
        context = self.app.dispatch.context(self.item)
        queued = self.app.dispatch.queue.enqueue(self.sid, '보낼 요청', context=context)
        with self.assertRaisesRegex(ValueError, '이어 할 일'):
            self.app.hide_session(self.sid, True)
        self.assertEqual('queued', self.app.dispatch.queue.snapshot(self.sid)['queue'][0]['status'])
        self.app.dispatch.queue.cancel(self.sid, queued['id'])
        schedule = self.app.dispatch.queue.add_schedule(self.sid, '예약 요청', [], kind='once', run_at=time.time()+3600, context=context)
        for paused in (False, True):
            if paused:
                self.app.dispatch.queue.set_schedule_enabled(self.sid, schedule['id'], False)
            with self.assertRaisesRegex(ValueError, '실행 예약'):
                self.app.hide_session(self.sid, True)
        self.app.dispatch.queue.cancel_schedule(self.sid, schedule['id'])
        self.assertTrue(self.app.hide_session(self.sid, True)['ok'])

    def test_removal_reservation_blocks_prepare_send_queue_and_dispatch_races(self):
        entered, finish = threading.Event(), threading.Event()
        bridge = self.item['bridge'] = IdleBridge()
        normal_close = bridge.close
        def close():
            entered.set()
            finish.wait(4)
            return normal_close()
        bridge.close = close
        result = []
        thread = threading.Thread(target=lambda: result.append(self.app.hide_session(self.sid, True)))
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            for call in (lambda: self.app.connect(self.sid), lambda: self.app.send(self.sid, '요청', []),
                         lambda: self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': '이어 하기'}),
                         lambda: self.app.dispatch.action(self.sid, {'action': 'schedule', 'text': '예약', 'schedule': {'kind': 'once', 'runAt': time.time()+3600}})):
                with self.assertRaises(ValueError):
                    call()
            self.app.dispatch.pump()
            self.assertEqual([], self.app.dispatch.queue.snapshot(self.sid)['queue'])
        finally:
            finish.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['ok'])
        self.assertEqual([{'role': 'user', 'text': '원본 요청'}], self.item['messages'])

    def test_close_failure_or_atomic_write_failure_keeps_visible_history(self):
        bridge = self.item['bridge'] = IdleBridge()
        before = self.originals()
        with patch.object(bridge, 'close', return_value=False), self.assertRaises(ValueError):
            self.app.hide_session(self.sid, True)
        self.assertEqual(self.sid, self.app.bootstrap()['sessions'][0]['id'])
        with patch('local_app.session_visibility.HistoryStore._write', side_effect=OSError('write failure')), self.assertRaises(OSError):
            self.app.hide_session(self.sid, True)
        self.assertEqual(before, self.originals())
        self.assertFalse(self.app.session_visibility.contains(self.sid))
        self.assertNotIn('_removingFromList', self.item)

    def test_corrupt_metadata_is_preserved_and_never_overwritten(self):
        for raw in (b'bad', b'['*20000+b']'*20000, json.dumps({'schemaVersion': 1, 'hiddenIds': [self.sid]*2}).encode()):
            self.app.session_visibility.path.write_bytes(raw)
            store = SessionVisibility(self.app.state)
            self.assertTrue(store.warning)
            with self.assertRaises(ValueError):
                store.set_hidden(self.sid, True)
            self.assertEqual(raw, store.path.read_bytes())

    def test_hide_route_requires_auth_and_explicit_confirmation(self):
        server = Server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for authorized, confirmed, expected in ((False, True, 403), (True, False, 400), (True, True, 200)):
                headers = {'Content-Type': 'application/json', 'Origin': server.origin}
                if authorized:
                    headers['Authorization'] = 'Bearer '+self.app.token
                request = Request(server.origin+'/api/session/hide', headers=headers,
                                  data=json.dumps({'id': self.sid, 'confirmed': confirmed}).encode())
                try:
                    response = urlopen(request, timeout=5)
                except HTTPError as exc:
                    response = exc
                with response:
                    self.assertEqual(expected, response.status, response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
