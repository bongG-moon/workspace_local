"""First-run naming owns only an explicitly unnamed work entry, never its folder."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_app.server import LocalApp


class HoldingBridge:
    closed = False
    cleanup_complete = False

    def send(self, prompt):
        self.prompt = prompt

    def close(self):
        self.closed = self.cleanup_complete = True
        return True


class WorkTitleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / '기존 업무 폴더'
        self.folder.mkdir()
        self.open_app()

    def open_app(self):
        self.app = LocalApp(self.root / 'state', command=['never-started'], managed_workspace_root=self.root / '업무들')
        self.addCleanup(self.app.close)

    def submit(self, sid, text='원문을 그대로 제목으로 쓰지 말고 점검표를 정리해 줘.'):
        item = self.app.get(sid)
        item['bridge'] = HoldingBridge()
        self.app.send(sid, text, [])
        self.assertEqual(text, item['bridge'].prompt)
        return item['lastRunId']

    def test_explicit_name_and_manually_entered_default_are_both_protected(self):
        for title in ('설비 점검 주간 검토', '새 업무', '새 업무 (2)'):
            with self.subTest(title=title):
                sid = self.app.create(str(self.folder), True, title=title)['id']
                run = self.submit(sid)
                self.assertFalse(self.app.pending_work_title(sid,run))
                self.assertFalse(self.app.apply_work_title(sid,run,'모델이 제안한 이름'))
                self.assertEqual(title,self.app.get(sid)['title'])
        fresh = self.app.create(str(self.folder), True)
        self.app.update_session(fresh['id'],{'title':fresh['title']})
        run = self.submit(fresh['id'])
        self.assertFalse(self.app.pending_work_title(fresh['id'],run))
        self.assertEqual('manual',self.app.get(fresh['id'])['titleSource'])

    def test_empty_managed_work_gets_one_title_without_renaming_its_folder(self):
        first = self.app.create(trusted=True,managed=True)
        other = self.app.create(str(self.folder),True,title='설비 점검')
        sid = first['id']; run = self.submit(sid)
        self.assertEqual('새 업무', self.app.get(sid)['title'])
        self.assertEqual(0,self.app.public(self.app.get(sid))['titleRevision'])
        self.assertFalse(self.app.apply_work_title(other['id'],run,'잘못된 업무'))
        self.assertFalse(self.app.apply_work_title(sid,'다른 실행','다른 실행의 제목'))
        self.assertFalse(self.app.apply_work_title(sid,run,'제목\n다음 줄'))
        self.app.update_session(sid,{'pinned':True})
        activity_time = self.app.get(sid)['updated']
        self.assertTrue(self.app.pending_work_title(sid,run))
        self.assertTrue(self.app.apply_work_title(sid,run,'설비 점검'))
        self.assertFalse(self.app.apply_work_title(sid,run,'중복 완료'))
        item = self.app.get(sid)
        self.assertEqual(activity_time,item['updated'])
        self.assertEqual(('설비 점검 (2)','generated',1),(item['title'],item['titleSource'],item['titleRevision']))
        self.assertEqual(first['workspace'],item['workspace'])
        self.assertTrue(Path(item['workspace']).is_dir())
        self.assertEqual(1,len([ev for ev in item['events'] if ev['type']=='title_updated']))
        for meta in [self.app.public(item),next(x for x in self.app.bootstrap()['sessions'] if x['id']==sid),
                     next(x for x in self.app.attention()['sessions'] if x['id']==sid)]:
            self.assertEqual((item['title'],1),(meta['title'],meta['titleRevision']))
        self.app.close(); self.open_app()
        restored = self.app.get(sid)
        self.assertEqual((first['workspace'],'설비 점검 (2)','generated',1),
                         (restored['workspace'],restored['title'],restored['titleSource'],restored['titleRevision']))
        self.assertFalse(self.app.pending_work_title(sid,run))

    def test_running_manual_rename_always_wins_over_concurrent_model_result(self):
        sid = self.app.create(str(self.folder),True)['id']; run = self.submit(sid); barrier = threading.Barrier(2)
        def propose():
            barrier.wait()
            return self.app.apply_work_title(sid,run,'모델 제안')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(propose)
            barrier.wait()
            self.app.update_session(sid,{'title':'설비 점검 주간 검토'})
            applied = future.result(timeout=5)
        item = self.app.get(sid)
        self.assertEqual(('설비 점검 주간 검토','manual',2 if applied else 1),
                         (item['title'],item['titleSource'],item['titleRevision']))
        self.assertFalse(self.app.apply_work_title(sid,run,'늦은 모델 결과'))
        revision = item['titleRevision']
        self.app.update_session(sid,{'pinned':True})
        self.assertEqual(revision,item['titleRevision'])
        self.app.close();self.open_app()
        self.assertEqual('설비 점검 주간 검토',self.app.get(sid)['title'])

    def test_legacy_empty_work_and_restarted_first_run_are_not_renamed(self):
        old = self.app.create(str(self.folder),True)
        item = self.app.get(old['id'])
        item.pop('titleSource');item.pop('titleRevision')
        self.app.save(old['id'])
        pending = self.app.create(str(self.folder),True)
        original_run = self.submit(pending['id'])
        self.app.close();self.open_app()
        self.assertFalse(self.app.apply_work_title(pending['id'],original_run,'종료 전 제안'))
        run = self.submit(old['id'])
        self.assertFalse(self.app.apply_work_title(old['id'],run,'이전 업무 덮어쓰기'))
        self.assertEqual(old['title'],self.app.get(old['id'])['title'])

    def test_second_turn_does_not_steal_or_cancel_a_pending_first_title(self):
        sid = self.app.create(str(self.folder),True)['id']; first = self.submit(sid)
        self.app.get(sid)['state'] = 'done'
        second = self.submit(sid,'첫 번째 요청 뒤에 이어서 실행할 다음 요청')
        self.assertNotEqual(first,second)
        self.assertTrue(self.app.pending_work_title(sid,first))
        self.assertFalse(self.app.apply_work_title(sid,second,'두 번째 요청의 제안'))
        self.assertTrue(self.app.apply_work_title(sid,first,'첫 요청의 설비 점검'))
        self.assertFalse(self.app.apply_work_title(sid,first,'중복 제안'))
        self.assertEqual('첫 요청의 설비 점검',self.app.get(sid)['title'])

    def test_failed_metadata_save_restores_title_ownership_revision_and_event(self):
        sid = self.app.create(str(self.folder),True)['id']; run = self.submit(sid)
        with patch.object(self.app,'save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.app.update_session(sid,{'title':'수동 변경','pinned':True})
        item = self.app.get(sid)
        self.assertEqual(('새 업무','default',0,False),(item['title'],item['titleSource'],item['titleRevision'],item['pinned']))
        self.assertFalse(any(event['type']=='title_updated' for event in item['events']))
        self.assertTrue(self.app.pending_work_title(sid,run))

    def test_archive_restore_retains_user_title_and_retires_pending_generation(self):
        sid = self.app.create(str(self.folder),True)['id']; first = self.submit(sid)
        self.app.get(sid)['state'] = 'done'
        self.app.hide_session(sid,True)
        self.app.restore_session(sid)
        self.assertFalse(self.app.apply_work_title(sid,first,'보관 전에 제안한 제목'))
        self.app.update_session(sid,{'title':'보관할 업무'})
        self.app.hide_session(sid,True)
        restored = self.app.restore_session(sid)['session']
        self.assertEqual(('보관할 업무',1),(restored['title'],restored['titleRevision']))
