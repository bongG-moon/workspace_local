"""Work-home recency uses conversation activity, independently of list edits."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from local_app.history import last_activity_at, row
from local_app.server import LocalApp


class RecentActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        demo = patch('local_app.demo.run', lambda *_: None)
        demo.start()
        self.addCleanup(demo.stop)

    def test_accepted_request_and_finished_reply_advance_activity_without_delta_writes(self):
        with patch('local_app.server.time.time', return_value=2000000000):
            self.app.send(self.sid, '보고서를 정리해 주세요', [])
        self.assertEqual(2000000000, self.item['lastActivityAt'])
        with patch('local_app.server.time.time', return_value=2000000001), patch.object(self.app, 'save') as save:
            self.app.emit(self.sid, 'assistant_delta', {'text': '작성 중'})
        save.assert_not_called()
        self.assertEqual(2000000000, self.item['lastActivityAt'])
        with patch('local_app.server.time.time', return_value=2000000002):
            self.app.emit(self.sid, 'assistant', {'text': '보고서를 정리했습니다.'})
        self.assertEqual(2000000002, self.item['lastActivityAt'])
        with patch('local_app.server.time.time', return_value=2000000003):
            self.app.emit(self.sid, 'result', {})
        self.assertEqual(2000000003, self.item['lastActivityAt'])
        for payload in (self.app.public(self.item),
                        self.app.bootstrap()['sessions'][0], self.app.attention()['sessions'][0]):
            self.assertEqual(2000000003, payload['lastActivityAt'])

    def test_metadata_controls_and_legacy_pin_do_not_promote_activity(self):
        self.item.pop('lastActivityAt', None)
        self.item['created'], self.item['updated'] = 90, 100
        with patch('local_app.server.time.time', return_value=200):
            self.app.update_session(self.sid, {'title': '새 이름', 'pinned': True})
        self.assertEqual(200, self.item['updated'])
        self.assertEqual(100, self.item['lastActivityAt'])
        for kind, data in (
            ('connected', {'sessionId': str(uuid.uuid4())}),
            ('model_changed', {'model': 'new-model', 'modelOverride': 'new-model'}),
            ('permission_mode_changed', {'permissionMode': 'plan', 'permissionModeOverride': 'plan'}),
            ('effort_changed', {'effort': 'high'}),
        ):
            with self.subTest(kind=kind), patch('local_app.server.time.time', return_value=300):
                self.app.emit(self.sid, kind, data)
                self.assertEqual(100, self.item['lastActivityAt'])
        self.app.public(self.item)
        self.app.bootstrap()
        self.app.attention()
        self.assertEqual(100, self.item['lastActivityAt'])

    def test_errors_during_controls_are_not_work_activity_but_request_errors_are(self):
        self.item['lastActivityAt'] = 100
        for flag in ('_connecting', '_modelUpdating', '_permissionUpdating'):
            self.item[flag] = True
            with patch('local_app.server.time.time', return_value=200):
                self.app.emit(self.sid, 'error', {'message': '설정 확인 실패'})
            self.item.pop(flag)
            self.assertEqual(100, self.item['lastActivityAt'])
        with patch('local_app.server.time.time', return_value=300):
            self.app.send(self.sid, '업무 요청', [])
        with patch('local_app.server.time.time', return_value=400):
            self.app.emit(self.sid, 'error', {'message': '요청 처리 실패'})
        self.assertEqual(400, self.item['lastActivityAt'])

    def test_question_and_choice_activity_does_not_add_history_saves(self):
        with patch('local_app.server.time.time', return_value=100):
            self.app.send(self.sid, '확인할 업무', [])
        with patch('local_app.server.time.time', return_value=110), patch.object(self.app, 'save') as save:
            self.app.emit(self.sid, 'request', {'id': 'question-1', 'tool': 'AskUserQuestion', 'input': {}})
        self.assertEqual(110, self.item['lastActivityAt'])
        save.assert_not_called()
        with patch('local_app.server.time.time', return_value=120):
            self.app.emit(self.sid, 'choice', {'schemaVersion': 1, 'id': 'choice-1',
                                              'kind': 'html-report-style', 'responseMode': 'next-user-message',
                                              'allowCustom': True, 'question': '어떤 형식인가요?',
                                              'options': [{'id': 'minimalism', 'label': '간단한 문서'}]})
        self.assertEqual(120, self.item['lastActivityAt'])

    def test_activity_survives_restart_and_index_views_do_not_hydrate_bodies(self):
        self.item.update(lastActivityAt=100, updated=900)
        self.app.save(self.sid)
        index = json.loads((self.app.state / 'history-index.json').read_text('utf-8'))
        self.assertEqual(100, index['sessions'][0]['lastActivityAt'])
        restored = LocalApp(self.app.state, demo=True)
        self.addCleanup(restored.close)
        self.assertNotIn('messages', restored.sessions[self.sid])
        with patch.object(restored.history, 'hydrate', side_effect=AssertionError('No transcript scan')):
            self.assertEqual(100, restored.bootstrap()['sessions'][0]['lastActivityAt'])
            self.assertEqual(100, restored.attention()['sessions'][0]['lastActivityAt'])
        self.assertEqual(100, restored.public(restored.get(self.sid))['lastActivityAt'])


class ActivityNormalizationTests(unittest.TestCase):
    def test_optional_invalid_activity_falls_back_without_poisoning_history(self):
        item = dict(id=str(uuid.uuid4()), title='기존 업무', workspace='C:/fixture', created=1,
                    updated=2, pinned=False, sessionId=None, messages=[])
        for value in (None, True, False, '3', float('nan'), float('inf'), -1, 1e12):
            with self.subTest(value=value):
                normalized = row({**item, 'lastActivityAt': value})
                self.assertEqual(2, normalized['lastActivityAt'])
                json.dumps(normalized, allow_nan=False)
        self.assertEqual(2, row(item)['lastActivityAt'])
        self.assertEqual(0, last_activity_at({}))
        self.assertEqual(1, last_activity_at({'updated': float('nan'), 'created': 1}))


if __name__ == '__main__':
    unittest.main()
