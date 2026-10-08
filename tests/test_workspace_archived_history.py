"""Archived list admission and index-only startup without transcript loss."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from local_app.history import HistoryStore, KEYS
from local_app.server import LocalApp
from local_app.session_visibility import SessionVisibility


class ArchivedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'

    def rows(self, count):
        return [dict(id=str(uuid.uuid4()), title=f'보관 업무 {i}', workspace=str(self.root),
                     created=float(i), updated=float(i), pinned=False, sessionId=None,
                     messages=[{'role': 'user', 'text': f'원래 대화 {i}'}], artifacts=[])
                for i in range(count)]

    def app(self):
        result = LocalApp(self.state, demo=True)
        self.addCleanup(result.close)
        return result

    def archive(self, rows):
        HistoryStore(self.state).save(rows)
        self.state.joinpath('session-visibility.json').write_text(
            json.dumps({'schemaVersion': 1, 'hiddenIds': [row['id'] for row in rows]}), encoding='utf-8')

    def test_five_hundred_hidden_do_not_prevent_new_task_or_restart(self):
        rows = self.rows(500)
        self.archive(rows)
        originals = {path: path.read_bytes() for path in self.state.joinpath('history-sessions').glob('*.json')}
        app = self.app()
        self.assertEqual([], app.bootstrap()['sessions'])
        created = app.create(str(self.root), True)
        self.assertEqual(501, len(app.sessions))
        self.assertEqual(1, app.archived_sessions()['activeCount'])
        restarted = self.app()
        self.assertEqual(501, len(restarted.sessions))
        self.assertEqual([created['id']], [row['id'] for row in restarted.bootstrap()['sessions']])
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})

    def test_index_only_startup_search_and_bootstrap_read_no_bodies(self):
        rows = self.rows(500)
        self.archive(rows)
        from local_app import history
        original = history.read
        with patch.object(history, 'read', wraps=original) as read:
            app = self.app()
            app.bootstrap()
            page = app.archived_sessions('보관 업무', offset=50)
            app.attention()
        bodies = [call.args[0] for call in read.call_args_list if call.args[0].parent.name == 'history-sessions']
        self.assertEqual([], bodies)
        self.assertEqual(500, page['total'])
        self.assertEqual(50, len(page['sessions']))
        self.assertEqual(0, sum('messages' in row for row in app.sessions.values()))

    def test_restore_pre_cli_record_does_not_trust_or_run_anything(self):
        rows = self.rows(1)
        self.archive(rows)
        original = self.root / 'original-claude.jsonl'
        original.write_bytes(b'original transcript\n')
        app = self.app()
        body = self.state / 'history-sessions' / (rows[0]['id'] + '.json')
        before = body.read_bytes()
        with patch.object(app.dispatch, 'pump') as pump:
            result = app.restore_session(rows[0]['id'])
            pump.assert_not_called()
        self.assertTrue(result['restored'])
        self.assertFalse(result['session']['trusted'])
        self.assertIsNone(result['session']['sessionId'])
        self.assertEqual(rows[0]['messages'], result['session']['messages'])
        self.assertEqual(before, body.read_bytes())
        self.assertEqual(b'original transcript\n', original.read_bytes())
        self.assertFalse(app.restore_session(rows[0]['id'])['restored'])

    def test_active_cap_blocks_restore_and_creation_without_changing_archive(self):
        rows = self.rows(501)
        HistoryStore(self.state).save(rows)
        visibility = SessionVisibility(self.state)
        visibility.set_hidden(rows[-1]['id'], True)
        before = visibility.path.read_bytes()
        app = self.app()
        for call in (lambda: app.create(str(self.root), True), lambda: app.restore_session(rows[-1]['id'])):
            with self.assertRaisesRegex(ValueError, '500개'):
                call()
        self.assertEqual(before, visibility.path.read_bytes())
        self.assertTrue(app.session_visibility.contains(rows[-1]['id']))

    def test_restore_failure_preserves_archive_and_body(self):
        rows = self.rows(1)
        self.archive(rows)
        app = self.app()
        body = self.state / 'history-sessions' / (rows[0]['id'] + '.json')
        before = body.read_bytes()
        with patch.object(app.session_visibility, 'set_hidden', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                app.restore_session(rows[0]['id'])
        self.assertTrue(app.session_visibility.contains(rows[0]['id']))
        self.assertEqual(before, body.read_bytes())
        body.write_bytes(b'broken')
        app.history.compact(app.sessions[rows[0]['id']])
        with self.assertRaises(ValueError):
            app.restore_session(rows[0]['id'])
        self.assertTrue(app.session_visibility.contains(rows[0]['id']))
        self.assertEqual(b'broken', body.read_bytes())

    def test_legacy_index_migrates_without_reading_or_replacing_unopened_bodies(self):
        rows = self.rows(12)
        rows[0]['importedConfigRoot'] = str(self.root)
        rows[0]['verification'] = {'state': 'needs-review'}
        rows[0]['lastRunId'] = 'previous-run'
        store = HistoryStore(self.state)
        store.save(rows)
        index = self.state / 'history-index.json'
        index.write_text(json.dumps({'schemaVersion': 1, 'sessions': [{key: row.get(key) for key in KEYS} for row in rows]}), encoding='utf-8')
        before = {path: path.read_bytes() for path in self.state.joinpath('history-sessions').glob('*.json')}
        app = self.app()
        self.assertTrue(app.sessions[rows[0]['id']]['_historyMetadataPending'])
        self.assertEqual(12, app.attention()['pendingHistoryCount'])
        self.assertTrue(all(row['historyDetailsPending'] for row in app.bootstrap()['sessions']))
        app.save()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        converted = json.loads(index.read_text(encoding='utf-8'))
        self.assertEqual(2, converted['schemaVersion'])
        self.assertTrue(all(row['metadataComplete'] is False for row in converted['sessions']))
        loaded = app.get(rows[0]['id'])
        self.assertEqual(str(self.root), loaded['importedConfigRoot'])
        self.assertEqual('needs-review', loaded['verification']['state'])
        self.assertEqual('previous-run', loaded['lastRunId'])
        self.assertNotIn('_historyMetadataPending', loaded)
        self.assertEqual(11, app.attention()['pendingHistoryCount'])
        app.save(loaded['id'])
        restarted = self.app()
        self.assertEqual('needs-review', restarted.sessions[loaded['id']]['verification']['state'])
        self.assertTrue(restarted.sessions[rows[1]['id']]['_historyMetadataPending'])

    def test_failed_index_migration_leaves_old_index_and_unopened_bodies(self):
        rows = self.rows(2)
        self.archive(rows)
        index = self.state / 'history-index.json'
        index.write_text(json.dumps({'schemaVersion': 1, 'sessions': [{key: row.get(key) for key in KEYS} for row in rows]}), encoding='utf-8')
        before = index.read_bytes()
        bodies = {path: path.read_bytes() for path in self.state.joinpath('history-sessions').glob('*.json')}
        app = self.app()
        with patch.object(app.history, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): app.save()
        self.assertEqual(before, index.read_bytes())
        self.assertEqual(bodies, {path: path.read_bytes() for path in bodies})

    def test_body_authoritative_after_interrupted_body_index_save(self):
        rows = self.rows(1)
        HistoryStore(self.state).save(rows)
        app = self.app()
        item = app.get(rows[0]['id'])
        item['title'] = '바뀐 업무'
        item['verification'] = {'state': 'needs-review'}
        write = app.history._write
        def fail_index(path, data):
            if path.name == 'history-index.json': raise OSError('interrupted')
            return write(path, data)
        with patch.object(app.history, '_write', side_effect=fail_index), self.assertRaises(OSError):
            app.save(item['id'])
        restarted = self.app()
        restored = restarted.get(item['id'])
        self.assertEqual('바뀐 업무', restored['title'])
        self.assertEqual('needs-review', restored['verification']['state'])

    def test_index_serialization_skips_unchanged_metadata_during_body_only_save(self):
        rows = self.rows(4)
        store = HistoryStore(self.state)
        store.save(rows)
        rows[0]['messages'].append({'role': 'assistant', 'text': '추가 내용'})
        original = json.dumps
        with patch('local_app.history.json.dumps', wraps=original) as dumps:
            store.save(rows, rows[0]['id'])
        self.assertEqual(1, dumps.call_count, 'only the changed body is serialized')

    def test_choice_and_branch_metadata_survive_index_only_load_and_cache_reload(self):
        rows = self.rows(1)
        rows[0]['choice'] = {'schemaVersion': 1, 'id': 'choice-a', 'kind': 'html-report-style',
                             'responseMode': 'next-user-message', 'question': '스타일을 골라 주세요',
                             'allowCustom': True, 'options': [{'id': 'minimalism', 'label': '미니멀리즘'}]}
        rows[0]['branch'] = {'scope': 'latest', 'status': 'pending', 'sourceTaskId': str(uuid.uuid4()),
                            'sourceSessionId': str(uuid.uuid4()), 'childSessionId': str(uuid.uuid4()),
                            'sourceFingerprint': 'a' * 64, 'configRoot': str(self.root),
                            'sourceTitle': '원본 업무', 'createdAt': 1.0}
        HistoryStore(self.state).save(rows)
        app = self.app()
        item = app.sessions[rows[0]['id']]
        self.assertNotIn('messages', item)
        self.assertEqual('choice-a', item['choice']['id'])
        self.assertEqual(rows[0]['branch'], item['branch'])
        app.get(item['id'])
        app.history.compact(item)
        self.assertEqual('choice-a', app.get(item['id'])['choice']['id'])
        self.assertEqual(rows[0]['branch'], item['branch'])


if __name__ == '__main__':
    unittest.main()
