"""Saved sidebar order is independent of transcript activity and CLI state."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from local_app.history import HistoryStore
from local_app.server import LocalApp, Server
from local_app.session_order import SessionOrder


class SessionOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = [{'id': str(uuid.uuid4()), 'created': i, 'updated': i, 'pinned': False} for i in range(1, 5)]
        self.store = SessionOrder(self.root)

    def ids(self):
        return [row['id'] for row in self.store.ordered(self.rows)]

    def test_recent_until_first_move_then_activity_and_reload_preserve_manual_order(self):
        a, b, c, d = [row['id'] for row in self.rows]
        self.assertEqual([d, c, b, a], self.ids())
        self.assertFalse(self.store.path.exists())
        self.store.move(self.rows, a, c, 'before')
        self.assertEqual([d, a, c, b], self.ids())
        self.rows[1]['updated'] = 999
        self.store = SessionOrder(self.root)
        self.assertEqual([d, a, c, b], self.ids())
        new = {'id': str(uuid.uuid4()), 'created': 5, 'updated': 5, 'pinned': False}
        self.rows.append(new)
        self.assertEqual([new['id'], d, a, c, b], self.ids())
        self.rows[2]['pinned'] = True
        self.assertEqual([c, new['id'], d, a, b], self.ids())

    def test_same_group_only_and_invalid_requests_cannot_write(self):
        a, b, c, d = [row['id'] for row in self.rows]
        self.rows[0]['pinned'] = self.rows[1]['pinned'] = True
        self.store.move(self.rows, a, b, 'before')
        before = self.store.path.read_bytes()
        for sid, target, position in [(a, c, 'after'), (c, a, 'before'), (a, a, 'before'),
                                      ('missing', b, 'before'), (a, b, 'first'), ([], b, 'after')]:
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                self.store.move(self.rows, sid, target, position)
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual([a, b, d, c], self.ids())

    def test_atomic_replace_failure_preserves_memory_disk_and_history(self):
        a, b, c, d = [row['id'] for row in self.rows]
        self.store.move(self.rows, a, b, 'before')
        before, ids = self.store.path.read_bytes(), self.ids()
        with patch.object(Path, 'replace', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                self.store.move(self.rows, c, a, 'after')
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(ids, self.ids())
        self.assertEqual([], list(self.root.glob('*.tmp')))

    def test_damaged_duplicate_or_oversized_order_is_preserved(self):
        for raw in [b'broken', json.dumps({'schemaVersion': 1, 'ids': [self.rows[0]['id']] * 2}).encode(),
                    b' ' * (64 * 1024 + 1), b'[' * 20000 + b'0' + b']' * 20000]:
            with self.subTest(size=len(raw)):
                self.store.path.write_bytes(raw)
                store = SessionOrder(self.root)
                self.assertTrue(store.snapshot()['warning'])
                with self.assertRaises(ValueError):
                    store.move(self.rows, self.rows[0]['id'], self.rows[1]['id'], 'before')
                self.assertEqual(raw, self.store.path.read_bytes())


class SessionOrderRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.ids = [self.app.create(str(self.root), True, title='업무 ' + str(i))['id'] for i in range(3)]
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, route, data=None, auth=True):
        headers = {'Content-Type': 'application/json', 'Origin': self.server.origin}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + route, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_authenticated_reorder_survives_event_and_restart_without_changing_history(self):
        a, b, c = self.ids
        before = {path: path.read_bytes() for path in self.app.state.rglob('history*.json')}
        before.update({path: path.read_bytes() for path in (self.app.state / 'history-sessions').glob('*.json')})
        body = {'id': a, 'targetId': c, 'position': 'before'}
        self.assertEqual(403, self.request('/api/session/reorder', body, auth=False)[0])
        self.assertFalse(self.app.session_order.path.exists())
        code, result = self.request('/api/session/reorder', body)
        self.assertEqual(200, code, result)
        self.assertEqual([a, c, b], result['sessionOrder']['ids'])
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.app.get(b)['updated'] += 1000
        self.app.save(b)
        self.assertEqual([a, c, b], [row['id'] for row in self.request('/api/bootstrap')[1]['sessions']])
        reloaded = LocalApp(self.app.state, demo=True)
        self.addCleanup(reloaded.close)
        self.assertEqual([a, c, b], [row['id'] for row in reloaded.bootstrap()['sessions']])
        self.assertFalse(reloaded.get(a)['trusted'])
        self.assertTrue(all(item['bridge'] is None and not item['messages'] for item in reloaded.sessions.values()))

    def test_pin_persists_and_server_rechecks_current_group_before_move(self):
        a, b, c = self.ids
        self.assertEqual(200, self.request('/api/session/update', {'id': a, 'pinned': True})[0])
        self.assertEqual(400, self.request('/api/session/reorder', {'id': a, 'targetId': b, 'position': 'before'})[0])
        reloaded = LocalApp(self.app.state, demo=True)
        self.addCleanup(reloaded.close)
        self.assertTrue(reloaded.get(a)['pinned'])
        self.assertEqual(a, reloaded.bootstrap()['sessions'][0]['id'])

    def test_failed_reorder_returns_error_without_changing_saved_order(self):
        a, b, c = self.ids
        self.app.reorder_session(a, c, 'before')
        before = self.app.session_order.snapshot()
        with patch.object(HistoryStore, '_write', side_effect=OSError('synthetic failure')):
            code, result = self.request('/api/session/reorder', {'id': b, 'targetId': a, 'position': 'before'})
        self.assertEqual(400, code, result)
        self.assertEqual(before, self.app.session_order.snapshot())
        self.assertEqual(before, SessionOrder(self.app.state).snapshot())

    def test_failed_pin_does_not_flip_in_memory_or_persist(self):
        sid = self.ids[0]
        before = self.app.get(sid)['updated']
        with patch.object(HistoryStore, '_write', side_effect=OSError('synthetic failure')):
            self.assertEqual(400, self.request('/api/session/update', {'id': sid, 'pinned': True})[0])
        self.assertFalse(self.app.get(sid)['pinned'])
        self.assertEqual(before, self.app.get(sid)['updated'])
        reloaded = LocalApp(self.app.state, demo=True)
        self.addCleanup(reloaded.close)
        self.assertFalse(reloaded.get(sid)['pinned'])


if __name__ == '__main__':
    unittest.main()
