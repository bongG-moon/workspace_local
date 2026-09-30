import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from local_app.history import HistoryStore
from local_app.server import LocalApp


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def records(self, count=15):
        return [dict(id=str(uuid.uuid4()), title=f'업무 {i}', workspace=str(self.root),
                     created=float(i), updated=float(i), pinned=False, sessionId=None,
                     messages=[{'role':'assistant', 'text':str(i) + '가'*100000}],
                     artifacts=[], lastRunId=None) for i in range(count)]

    def app(self):
        app = LocalApp(self.root, demo=True)
        self.addCleanup(app.close)
        return app

    def test_startup_drops_bodies_and_selection_is_lru_bounded(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        app = self.app()
        self.assertEqual(15, len(app.bootstrap()['sessions']))
        self.assertTrue(all('messages' not in row for row in app.sessions.values()))
        for row in rows:
            loaded = app.get(row['id'])
            self.assertEqual(row['messages'][0]['text'][:100000], loaded['messages'][0]['text'])
        self.assertLessEqual(sum('messages' in row for row in app.sessions.values()), 8)
        self.assertEqual(rows[0]['messages'][0]['text'][:100000], app.public(app.get(rows[0]['id']))['messages'][0]['text'])

    def test_save_does_not_replace_unloaded_history_with_empty_body(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        untouched = self.root/'history-sessions'/(rows[-1]['id']+'.json')
        before = untouched.read_bytes()
        app = self.app()
        app.update_session(rows[0]['id'], {'title':'바뀐 이름'})
        app.save()
        self.assertEqual(before, untouched.read_bytes())
        self.assertEqual('바뀐 이름', HistoryStore(self.root).load()[0]['title'])

    def test_corrupt_body_on_demand_is_preserved_without_writes(self):
        rows = self.records(1)
        HistoryStore(self.root).save(rows)
        app = self.app()
        path = self.root/'history-sessions'/(rows[0]['id']+'.json')
        path.write_bytes(b'broken')
        with self.assertRaises(ValueError): app.get(rows[0]['id'])
        app.save()
        self.assertEqual(b'broken', path.read_bytes())
        self.assertTrue(app.history.warning)

    def test_legacy_is_kept_loaded_until_migration(self):
        rows = self.records(12)
        path = self.root/'history.json'
        path.write_text(json.dumps(rows), encoding='utf-8')
        before = path.read_bytes()
        app = self.app()
        for row in rows: app.get(row['id'])
        self.assertTrue(all('messages' in item for item in app.sessions.values()))
        app.save(rows[0]['id'])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(12, len(HistoryStore(self.root).load()))

    def test_running_task_stays_loaded_and_saved_trusted_closed_task_can_evict(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        app = self.app()
        live = app.get(rows[0]['id']); live['state'] = 'running'
        trusted = app.get(rows[1]['id']); trusted['trusted'] = True
        trusted['bridge'] = SimpleNamespace(closed=True, cleanup_complete=True, close=lambda: True)
        for row in rows[2:]: app.get(row['id'])
        self.assertIn('messages', live)
        self.assertNotIn('messages', trusted)
        self.assertTrue(trusted['trusted'])
        self.assertEqual(rows[1]['messages'][0]['text'][:100000], app.get(rows[1]['id'])['messages'][0]['text'])
        self.assertLessEqual(sum('messages' in row for row in app.sessions.values()), 8)

    def test_eviction_releases_only_saved_body_and_keeps_runtime_fields(self):
        rows = self.records()
        rows[0]['artifacts'] = [{'path': str(self.root/'result.txt'), 'name': 'result.txt', 'change': 'created',
                                  'runId': 'first', 'observedAt': 1.0, 'size': 10}]
        HistoryStore(self.root).save(rows)
        app = self.app()
        target = app.get(rows[0]['id'])
        target.update(trusted=True, state='done', seq=23,
                      _sessionControls={'model': 'selected', 'permissionMode': 'plan'},
                      _allowBypass=True, events=[{'seq': 23, 'type': 'result', 'data': {}}],
                      choice={'id': 'current-choice'}, attachments=['keep-runtime-reference.txt'])
        preserved = {key: target[key] for key in ('trusted', 'state', 'seq', '_sessionControls', '_allowBypass',
                                                 'events', 'choice', 'attachments')}
        body_path = self.root/'history-sessions'/(target['id']+'.json')
        before = body_path.read_bytes()
        for row in rows[1:]: app.get(row['id'])
        self.assertTrue(target['_historyUnloaded'])
        self.assertNotIn('messages', target)
        self.assertNotIn('artifacts', target)
        self.assertEqual(1, target['_artifactCount'])
        for key, value in preserved.items(): self.assertEqual(value, target[key])
        loaded = app.get(target['id'])
        self.assertEqual(rows[0]['messages'][0]['text'][:100000], loaded['messages'][0]['text'])
        self.assertEqual(rows[0]['artifacts'], loaded['artifacts'])
        for key, value in preserved.items(): self.assertEqual(value, loaded[key])
        self.assertEqual(before, body_path.read_bytes(), 'cache activity must not write history')

    def test_unpersisted_body_and_live_or_unclean_connections_are_not_evicted(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        app = self.app()
        unsaved = dict(self.records(1)[0], trusted=True, state='idle', requests={}, events=[], seq=0, bridge=None)
        unsaved['messages'] = [{'role': 'user', 'text': 'not on disk yet'}]
        app.sessions[unsaved['id']] = unsaved
        app.get(unsaved['id'])
        live = app.get(rows[0]['id'])
        live['bridge'] = SimpleNamespace(closed=False, cleanup_complete=False, close=lambda: True)
        unclean = app.get(rows[1]['id'])
        unclean['bridge'] = SimpleNamespace(closed=True, cleanup_complete=False, close=lambda: True)
        for row in rows[2:]: app.get(row['id'])
        for item in (unsaved, live, unclean): self.assertIn('messages', item)
        self.assertEqual('not on disk yet', unsaved['messages'][0]['text'])
        self.assertFalse((self.root/'history-sessions'/(unsaved['id']+'.json')).exists())

    def test_failed_save_protects_modified_body_from_stale_disk_reload(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        app = self.app()
        target = app.get(rows[0]['id'])
        self.assertTrue(target['_historySaved'])
        target.update(state='done', trusted=True)
        target['messages'].append({'role': 'assistant', 'text': 'new result not saved'})
        with patch.object(app.history, '_write', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError): app.save(target['id'])
        for row in rows[1:]: app.get(row['id'])
        self.assertFalse(target.get('_historySaved'))
        self.assertIn('messages', target)
        self.assertEqual('new result not saved', target['messages'][-1]['text'])
        app.save(target['id'])
        self.assertTrue(target['_historySaved'])
        for row in rows[1:]: app.get(row['id'])
        self.assertTrue(target['_historyUnloaded'])
        self.assertEqual('new result not saved', app.get(target['id'])['messages'][-1]['text'])

    def test_transient_admission_and_pending_permissions_prevent_eviction(self):
        rows = self.records(20)
        HistoryStore(self.root).save(rows)
        app = self.app()
        protected = []
        for row, key in zip(rows, ('_connecting', '_modelUpdating', '_dispatchClaim', '_choiceAnswerClaim', 'requests')):
            item = app.get(row['id'])
            item[key] = True if key != 'requests' else {'pending': {'tool': 'Read'}}
            protected.append(item)
        for row in rows[5:]: app.get(row['id'])
        for item in protected: self.assertIn('messages', item)

    def test_public_projection_holds_lock_while_reading_loaded_body(self):
        rows = self.records(1)
        HistoryStore(self.root).save(rows)
        app = self.app()
        entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
        original = app._public
        def delayed(item):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(item)
        result = []
        def contend():
            with app.lock: acquired.set()
        with patch.object(app, '_public', side_effect=delayed):
            publisher = threading.Thread(target=lambda: result.append(app.public(app.sessions[rows[0]['id']])))
            publisher.start()
            self.assertTrue(entered.wait(2))
            contender = threading.Thread(target=contend)
            contender.start()
            self.assertFalse(acquired.wait(.05))
            release.set()
            publisher.join(2); contender.join(2)
        self.assertFalse(publisher.is_alive())
        self.assertTrue(acquired.is_set())
        self.assertEqual(rows[0]['messages'][0]['text'][:100000], result[0]['messages'][0]['text'])

    def test_metadata_navigation_never_hydrates_all_bodies(self):
        rows = self.records()
        HistoryStore(self.root).save(rows)
        app = self.app()
        with patch.object(app.history, 'hydrate', wraps=app.history.hydrate) as hydrate:
            app.bootstrap(); app.attention(); app.reorder_session(rows[0]['id'], rows[1]['id'], 'after')
            self.assertEqual(0, hydrate.call_count)


if __name__ == '__main__': unittest.main()
