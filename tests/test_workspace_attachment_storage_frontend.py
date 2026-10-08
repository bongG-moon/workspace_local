"""Exercise shipped storage dialog controls with a deterministic DOM double."""
import json
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_productivity_frontend import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(NODE, 'Node.js is required')
class AttachmentStorageFrontendTests(unittest.TestCase):
    def run_case(self, script):
        setup = r'''
          showDialog=id=>{const found=document.body.children.find(node=>node.id===id);found.showModal();};
          const storage=()=>document.body.querySelector('.attachment-storage-dialog');
          const controls=()=>storage().querySelector('.attachment-storage-actions').children;
          const status=()=>storage().querySelector('.attachment-storage-message').textContent;
          const sample={usedBytes:12,maxBytes:512,removableBytes:4,removableCount:1,protectedBytes:4,retainedBytes:4,graceBytes:0,preview:[{name:'cancel.zip',size:4}]};
          let calls=[];WorkspaceDraftPersistence={flush:async()=>{calls.push('flush');return true;}};
        '''
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), '(async()=>{' + setup + script + '})()',
                                 json.dumps([str(ROOT / 'local_app/web/attachment-storage.js')])],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_open_flushes_before_status_and_adds_no_background_timers(self):
        self.run_case(r'''
          const before=timers.size;
          WorkspaceAttachmentStorage.mount({api:async(path)=>{calls.push(path);return sample;}});
          await WorkspaceAttachmentStorage.open();
          assert.equal(calls.join('|'),'flush|/api/attachments/storage');
          assert.equal(timers.size,before);assert.equal(controls()[1].disabled,false);
          assert.match(status(),/4 B/);
        ''')

    def test_failed_draft_flush_blocks_read_and_cleanup(self):
        self.run_case(r'''
          WorkspaceDraftPersistence.flush=async()=>false;
          WorkspaceAttachmentStorage.mount({api:async()=>{throw Error('must not read');}});
          await WorkspaceAttachmentStorage.open();
          assert.match(status(),/초안 저장/);assert.equal(controls()[1].disabled,true);
        ''')

    def test_cleanup_requires_confirmation_and_reflushes_before_post(self):
        self.run_case(r'''
          WorkspaceAttachmentStorage.mount({api:async(path)=>{calls.push(path);return path.endsWith('cleanup')?{...sample,usedBytes:8,removableCount:0,removedCount:1,removedBytes:4}:sample;}});
          await WorkspaceAttachmentStorage.open();
          confirmAction=async()=>false;await controls()[1].onclick();
          assert.equal(calls.length,2);
          confirmAction=async()=>true;await controls()[1].onclick();
          assert.equal(calls.join('|'),'flush|/api/attachments/storage|flush|/api/attachments/cleanup');
          assert.match(status(),/1개, 4 B/);assert.equal(controls()[1].disabled,true);
        ''')

    def test_error_shown_without_removing_preview_and_retry_is_available(self):
        self.run_case(r'''
          WorkspaceAttachmentStorage.mount({api:async(path)=>{if(path.endsWith('cleanup'))throw Error('보존 확인 실패');return sample;}});
          await WorkspaceAttachmentStorage.open();confirmAction=async()=>true;await controls()[1].onclick();
          assert.equal(status(),'보존 확인 실패');assert.equal(controls()[0].disabled,false);
          assert.equal(storage().querySelector('.attachment-storage-preview').children.length,1);
        ''')

    def test_closed_app_does_not_open_or_post_after_pending_flush(self):
        self.run_case(r'''
          let closed=true,opened=0,release;
          WorkspaceAttachmentStorage.mount({api:async(path)=>{calls.push(path);return sample;},isClosed:()=>closed,showDialog:id=>{opened++;showDialog(id);}});
          await WorkspaceAttachmentStorage.open();assert.equal(opened,0);assert.equal(calls.length,0);
          closed=false;await WorkspaceAttachmentStorage.open();assert.equal(opened,1);
          confirmAction=async()=>true;WorkspaceDraftPersistence.flush=()=>new Promise(resolve=>release=resolve);
          const operation=controls()[1].onclick();await Promise.resolve();closed=true;release(true);await operation;
          assert.equal(calls.filter(path=>path.endsWith('/cleanup')).length,0);
        ''')

    def test_partial_batch_shows_remaining_without_automatic_cleanup_loop(self):
        self.run_case(r'''
          WorkspaceAttachmentStorage.mount({api:async(path)=>{calls.push(path);return path.endsWith('cleanup')?{...sample,removableCount:3,removedCount:100,removedBytes:400,batchLimited:true}:sample;}});
          await WorkspaceAttachmentStorage.open();confirmAction=async()=>true;await controls()[1].onclick();
          assert.match(status(),/남은 3개/);assert.equal(controls()[1].disabled,false);
          assert.equal(calls.filter(path=>path.endsWith('/cleanup')).length,1);assert.equal(timers.size,0);
        ''')

if __name__ == '__main__':
    unittest.main()
