"""Explicit table excerpts must stay in their owning task and survive a delayed send."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from local_app.drafts import DraftStore, StaleTableReference
from local_app.file_preview import build_preview
from local_app.server import LocalApp
from local_app.work_queue import WorkQueue


class TableReferenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.workspace = root / 'same folder'
        self.workspace.mkdir()
        self.file = self.workspace / '점검.csv'
        self.file.write_text('설비,검사수,비고\nA,120,"첫 줄\n둘째 줄"\nB,0,정지\n', encoding='utf-8')
        self.app = LocalApp(root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.a, self.b = [self.app.create(str(self.workspace), True)['id'] for _ in range(2)]

    def quote(self, row=2):
        version = build_preview(self.file)['version']
        return self.app.table_reference(self.a, str(self.file), version, row)['text']

    def test_bound_logical_row_and_session_restart(self):
        quote = self.quote()
        self.assertIn('데이터 2행', quote)
        self.assertIn('설비: B | 검사수: 0 | 비고: 정지', quote)
        self.assertEqual(quote, self.quote())
        self.app.drafts = DraftStore(self.app.state)
        self.app.validate_table_sources(self.a, '요약해 줘\n\n' + quote)
        with self.assertRaises(StaleTableReference):
            self.app.validate_table_sources(self.b, quote)
        self.assertEqual([], self.app.get(self.a)['messages'])

    def test_changed_table_cannot_be_added_or_sent_and_other_task_can_proceed(self):
        version = build_preview(self.file)['version']
        quote = self.quote()
        self.file.write_text('설비,검사수,비고\nB,2,변경\n', encoding='utf-8')
        with self.assertRaises(StaleTableReference):
            self.app.table_reference(self.a, str(self.file), version, 1)
        with self.assertRaises(StaleTableReference):
            self.app.send(self.a, quote, [])
        self.assertEqual([], self.app.get(self.a)['messages'])
        with patch.object(self.app, 'allowed_file', side_effect=AssertionError('unrelated file access')):
            self.app.validate_table_sources(self.b, '다른 질문')

    def test_queued_excerpt_stops_before_dispatch_when_another_task_changes_source(self):
        quote = self.quote()
        self.app.dispatch.action(self.a, {'action':'enqueue','text':quote,'attachments':[]})
        self.file.unlink()
        self.app.dispatch.pump()
        snapshot = self.app.dispatch.snapshot(self.a)
        self.assertEqual('needs_review', snapshot['queue'][0]['status'])
        self.assertEqual('table_source_changed', snapshot['queue'][0]['reason'])
        self.assertIn('표', snapshot['pauseReason'])
        self.assertEqual([], self.app.get(self.a)['messages'])
        with self.assertRaises(StaleTableReference):
            self.app.dispatch.action(self.a, {'action':'enqueue','text':quote,'attachments':[]})

    def test_wrong_type_and_outside_file_not_disclosed(self):
        outside = self.workspace.parent / 'other.csv'
        outside.write_text('a,b\nsecret,1\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.app.table_reference(self.a, str(outside), build_preview(outside)['version'], 1)
        with self.assertRaises(ValueError):
            self.quote(True)
        with self.assertRaises(ValueError):
            self.quote(3)

    def review(self, action, reference_id, **data):
        return self.app.review_table_reference(self.a, {'id': self.a, 'action': action, 'referenceId': reference_id, **data})

    def test_manual_replacement_is_bound_to_reviewed_version_not_position_or_duplicate_value(self):
        original = self.app.table_reference(self.a, str(self.file), build_preview(self.file)['version'], 2)
        number = original['referenceId']
        self.file.write_text('설비,검사수,비고\nB,9,새 값\nD,1,다른 설비\nB,3,중복 이름\n', encoding='utf-8')
        review = self.review('review', number)
        self.assertEqual({'referenceId', 'previousRowNumber', 'path', 'version'}, set(review))
        self.assertEqual(2, review['previousRowNumber'])
        self.assertEqual(str(self.file), review['path'])
        self.assertNotIn('rowNumber', review)
        with self.assertRaises(ValueError):
            self.review('replace', number, version=review['version'])
        with self.assertRaises(ValueError):
            self.review('replace', number, version=review['version'], rowNumber=1, path=str(self.file))
        new = self.review('replace', number, version=review['version'], rowNumber=3)
        self.assertEqual(number, new['replaces'])
        self.assertNotEqual(number, new['referenceId'])
        self.assertIn('설비: B | 검사수: 3 | 비고: 중복 이름', new['text'])
        self.assertEqual(new, self.review('replace', number, version=review['version'], rowNumber=3))
        self.assertEqual({'ok': True}, self.app.review_table_reference(self.a, {'id':self.a,'action':'check','text':new['text']}))
        with self.assertRaises(StaleTableReference) as stale:
            self.app.validate_table_sources(self.a, original['text'])
        self.assertEqual(number, stale.exception.reference_id)
        self.assertTrue(stale.exception.can_review)
        self.assertEqual([], self.app.get(self.a)['messages'])

    def test_deleted_changed_again_foreign_and_untrusted_sources_cannot_be_confirmed(self):
        number = self.app.table_reference(self.a, str(self.file), build_preview(self.file)['version'], 1)['referenceId']
        self.app.drafts = DraftStore(self.app.state)
        review = self.review('review', number)
        self.file.write_text('설비,검사수\nZ,100\n', encoding='utf-8')
        with self.assertRaises(StaleTableReference) as changed:
            self.review('replace', number, version=review['version'], rowNumber=1)
        self.assertEqual(number, changed.exception.reference_id)
        self.assertTrue(changed.exception.can_review)
        with patch.object(self.app, '_table_preview', side_effect=AssertionError('foreign file read')):
            with self.assertRaises(StaleTableReference) as foreign:
                self.app.review_table_reference(self.b, {'id':self.b,'action':'review','referenceId':number})
        self.assertFalse(foreign.exception.can_review)
        self.app.get(self.a)['trusted'] = False
        with self.assertRaises(StaleTableReference) as trust:
            self.review('review', number)
        self.assertFalse(trust.exception.can_review)
        self.app.get(self.a)['trusted'] = True
        self.file.unlink()
        with self.assertRaises(StaleTableReference) as deleted:
            self.review('replace', number, version=review['version'], rowNumber=1)
        self.assertFalse(deleted.exception.can_review)

    def test_repaired_queue_requires_explicit_resume_even_after_reload(self):
        old = self.app.table_reference(self.a, str(self.file), build_preview(self.file)['version'], 2)
        dispatch = self.app.dispatch
        entry = dispatch.action(self.a, {'action':'enqueue','text':old['text'],'attachments':[]})['queue'][0]
        self.file.write_text('설비,검사수\nB,24\n', encoding='utf-8')
        dispatch.pump()
        with self.assertRaisesRegex(ValueError, '최신 표'):
            dispatch.action(self.a, {'action':'resume'})
        edit = {'action':'update','requestId':entry['id'],'text':old['text'],'attachments':[]}
        with self.assertRaises(StaleTableReference):
            dispatch.action(self.a, edit)
        review = self.review('review', old['referenceId'])
        new = self.review('replace', old['referenceId'], version=review['version'], rowNumber=1)
        saved = dispatch.action(self.a, {**edit, 'text':new['text']})
        self.assertTrue(saved['tableResumeRequired'])
        self.assertTrue(saved['paused'])
        self.assertEqual(['queued'], [r['status'] for r in saved['queue']])
        dispatch.queue = WorkQueue(dispatch.queue.root)
        dispatch.queue.resume(self.a)                 # Internal resume from steering.
        with self.assertRaisesRegex(ValueError, '이어 실행'):
            dispatch.action(self.a, {'action':'apply_now','requestId':entry['id'],'editRevision':1,'clientRequestId':'early'})
        dispatch.pump()
        self.assertEqual([], self.app.get(self.a)['messages'])
        resumed = dispatch.action(self.a, {'action':'resume'})
        self.assertFalse(resumed['tableResumeRequired'])
        self.assertFalse(resumed['paused'])
        dispatch.pump()
        self.assertEqual(1, sum(m.get('text') == new['text'] for m in self.app.get(self.a)['messages']))

    def test_unknown_delivery_is_never_editable_as_a_table_repair(self):
        queue = self.app.dispatch.queue
        entry = queue.enqueue(self.a, '확인 필요', [])
        claimed = queue.claim(self.a, self.app.get(self.a), {})
        self.assertEqual(entry['id'], claimed['id'])
        queue.failed(entry['id'])
        with self.assertRaisesRegex(ValueError, '전송 전'):
            queue.edit(self.a, entry['id'], '바꿀 수 없음', [])

    def test_previously_attached_external_path_must_still_be_authorized(self):
        source = self.workspace.parent / 'selected.csv'
        source.write_text('lot,count\nA,5\n', encoding='utf-8')
        item = self.app.get(self.a)
        item['attachments'] = [str(source)]
        issued = self.app.table_reference(self.a, str(source), build_preview(source)['version'], 1)
        reviewed = self.review('review', issued['referenceId'])
        def revoke_while_reading(path):
            item['attachments'] = []
            return build_preview(path)
        with patch('local_app.server.build_preview', side_effect=revoke_while_reading):
            with self.assertRaises(StaleTableReference) as failure:
                self.review('replace', issued['referenceId'], version=reviewed['version'], rowNumber=1)
        self.assertFalse(failure.exception.can_review)
        self.assertNotIn(str(source), str(failure.exception))

    def test_schedule_edit_requires_a_fresh_quote_and_sends_nothing(self):
        original = self.app.table_reference(self.a, str(self.file), build_preview(self.file)['version'], 1)
        dispatch = self.app.dispatch
        schedule = {'kind':'once','runAt':dispatch.queue.clock()+3600}
        data = {'action':'schedule','text':original['text'],'attachments':[],'schedule':schedule}
        scheduled = dispatch.action(self.a, data)['schedules'][0]
        edit = {**data, 'action':'schedule_update','requestId':scheduled['id']}
        self.file.write_text('설비,검사수\nA,12\n', encoding='utf-8')
        with self.assertRaises(StaleTableReference):
            dispatch.action(self.a, edit)
        review = self.review('review', original['referenceId'])
        new = self.review('replace', original['referenceId'], version=review['version'], rowNumber=1)
        updated = dispatch.action(self.a, {**edit, 'text':new['text']})['schedules'][0]
        self.assertEqual(new['text'], updated['text'])
        self.assertEqual(scheduled['id'], updated['id'])
        self.assertEqual([], self.app.get(self.a)['messages'])

    def test_ranges_preserve_source_order_exact_cells_and_distinct_overlaps(self):
        self.file.write_bytes('날짜,제외 열,센서\n2026-01-01,내부 값," 첫 줄\n둘째 줄 "\n2026-01-02,둘,누락\n2026-01-03,셋,"a\u2028b"\n'.encode('utf-8'))
        version = build_preview(self.file)['version']
        def select(rows, columns):
            return self.app.table_reference(self.a, str(self.file), version, rows=rows, columns=columns)
        chosen = select([3,1], [3,1])
        header, body = chosen['text'].splitlines()
        self.assertIn('데이터 3,1행 · 3,1열', header)
        self.assertEqual({'열':[[3,'센서'],[1,'날짜']], '행':[[3,['a\u2028b','2026-01-03']],[1,[' 첫 줄\n둘째 줄 ','2026-01-01']]]}, json.loads(body))
        self.assertNotIn('제외 열', body);self.assertNotIn('누락', body);self.assertNotIn('내부 값', body)
        self.assertEqual(chosen, select([3,1], [3,1]))
        ids = {select([3,1], [1])['referenceId'], select([3], [3,1])['referenceId'],
               self.app.table_reference(self.a, str(self.file), version, 3)['referenceId'], chosen['referenceId']}
        self.assertEqual(4, len(ids))
        self.app.drafts = DraftStore(self.app.state)
        review = self.review('review', chosen['referenceId'])
        self.assertEqual([3,1], review['previousRows']);self.assertEqual([3,1], review['previousColumns'])
        self.file.write_text('날짜,센서\n다른 기록,x\n2026-01-03,9\n', encoding='utf-8')
        with self.assertRaises(StaleTableReference):
            self.app.validate_table_sources(self.a, chosen['text'])
        with self.assertRaises(StaleTableReference):
            self.review('replace', chosen['referenceId'], version=version, rows=[1], columns=[1])
        updated = self.review('replace', chosen['referenceId'], version=build_preview(self.file)['version'], rows=[2], columns=[2])
        self.assertEqual({'열':[[2,'센서']],'행':[[2,['9']]]}, json.loads(updated['text'].splitlines()[1]))
        self.assertEqual(chosen['referenceId'], updated['replaces'])

    def test_range_limits_reject_instead_of_shortening_or_including_extra_cells(self):
        version = build_preview(self.file)['version']
        for rows, columns in (([],[1]),([1],[]),([1,1],[1]),([1],[3,3]),([True],[1]),([2],[4]),([3],[1]),([1],['1']),([1],None)):
            with self.subTest(rows=rows,columns=columns), self.assertRaises(ValueError):
                self.app.table_reference(self.a, str(self.file), version, rows=rows, columns=columns)
        self.file.write_text('센서,짧음\n' + 'z'*8001 + ',8\n', encoding='utf-8')
        version = build_preview(self.file)['version']
        with self.assertRaisesRegex(ValueError, '8,000'):
            self.app.table_reference(self.a, str(self.file), version, rows=[1], columns=[1])
        good = self.app.table_reference(self.a, str(self.file), version, rows=[1], columns=[2])
        self.assertEqual([[1,['8']]], json.loads(good['text'].splitlines()[1])['행'])

    def test_migration_preserves_old_ids_and_never_reuses_deleted_receipts(self):
        version = build_preview(self.file)['version']
        with closing(sqlite3.connect(self.app.drafts.path)) as db, db:
            db.execute('CREATE TABLE table_sources(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, path TEXT, version TEXT, row_number INTEGER, UNIQUE(owner,path,version,row_number))')
            db.executemany('INSERT INTO table_sources VALUES(?,?,?,?,?)', [(7,self.a,str(self.file),version,1),(70,self.a,str(self.file),version,2)])
            db.execute('DELETE FROM table_sources WHERE id=70')
        self.assertEqual(1,self.review('review',7)['previousRowNumber'])
        new = self.app.table_reference(self.a,str(self.file),version,rows=[1],columns=[2])
        self.assertGreater(new['referenceId'],70)
        same = self.app.table_reference(self.a,str(self.file),version,1)
        self.assertEqual(7,same['referenceId']);self.assertEqual(2,len(same['text'].splitlines()))
        self.app.validate_table_sources(self.a,same['text'])
        converted = self.review('replace', 7, version=version, rows=[1], columns=[2])
        self.assertEqual((7,new['referenceId']),(converted['replaces'],converted['referenceId']))
        restored = self.review('replace', new['referenceId'], version=version, rowNumber=1)
        self.assertEqual(same['text'],restored['text'])
        with self.assertRaises(StaleTableReference):
            self.review('review',70)
