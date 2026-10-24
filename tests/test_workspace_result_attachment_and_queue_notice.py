"""Result attachment persistence and visible queue recovery, without Claude calls."""
import json
import subprocess
import unittest
from pathlib import Path

from tests.test_workspace_productivity_frontend import HARNESS, NODE, SETUP

ROOT = Path(__file__).resolve().parents[1]
RESULT_SETUP = r"""
const resultPath='C:/fixture/result.txt';
api=async()=>({lastRunId:'run',artifacts:[{runId:'run',path:resultPath,name:'result.txt'}]});
await refreshResults();
const follow=$('results-list').children[0].querySelector('.result-actions').children[3];
"""


@unittest.skipUnless(NODE, "Node.js is required for frontend state regressions")
class WorkspaceResultAttachmentQueueNoticeTests(unittest.TestCase):
    def run_case(self, script, modules=("attachments",)):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), SETUP + script,
             json.dumps([str(ROOT / f"local_app/web/{module}.js") for module in modules])],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_result_action_saves_attachment_and_preserves_draft_text(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          $('prompt').value='작성 중인 내용\n두 번째 줄';attachments=['C:/keep.txt'];
          follow.onclick();
          assert.equal($('prompt').value,'작성 중인 내용\n두 번째 줄');
          assert.deepEqual([...attachments],['C:/keep.txt',resultPath]);
          assert.equal(drafts.get('A').text,$('prompt').value);
          assert.deepEqual([...drafts.get('A').attachments],['C:/keep.txt',resultPath]);
          assert.match($('toast').textContent,/추가했어요/);
        })()""")

    def test_full_limit_does_not_change_draft_or_claim_success(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          $('prompt').value='보존';attachments=Array.from({length:12},(_,i)=>`C:/old-${i}.txt`);saveDraft();
          const before=JSON.stringify(drafts.get('A'));follow.onclick();
          assert.equal(attachments.length,12);assert.equal(attachments.includes(resultPath),false);
          assert.equal(JSON.stringify(drafts.get('A')),before);assert.match($('toast').textContent,/12개까지/);
          assert.doesNotMatch($('toast').textContent,/추가했어요/);
        })()""")

    def test_empty_draft_result_attachment_is_stored_without_typing(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          $('prompt').value='';attachments=[];follow.onclick();
          assert.equal(drafts.get('A').text,'');assert.deepEqual([...drafts.get('A').attachments],[resultPath]);
          attachments=[];restoreDraft('A');assert.deepEqual([...attachments],[resultPath]);
        })()""")

    def test_result_does_not_compete_with_attachment_upload(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          $('prompt').value='업로드 중 초안';attachments=['C:/keep.txt'];
          let release;api=()=>new Promise(resolve=>release=resolve);
          fetch=async()=>({ok:true,json:async()=>({path:'C:/managed/upload.txt',copied:true})});
          const upload=WorkspaceAttachments.uploadFiles([{name:'upload.txt',size:12}]);
          assert.equal(WorkspaceAttachments.isUploading(),true);follow.onclick();
          assert.equal(attachments.includes(resultPath),false);assert.match($('toast').textContent,/전송이 끝난 뒤/);
          release({ok:true});await upload;assert.deepEqual([...attachments],['C:/keep.txt','C:/managed/upload.txt']);
          assert.equal($('prompt').value,'업로드 중 초안');
        })()""")

    def test_duplicate_path_is_case_insensitive_even_when_full(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          attachments=['c:\\FIXTURE\\RESULT.TXT',...Array.from({length:11},(_,i)=>`C:/old-${i}.txt`)];
          $('prompt').value='원문';saveDraft();const before=JSON.stringify(drafts.get('A'));follow.onclick();
          assert.equal(attachments.length,12);assert.equal(JSON.stringify(drafts.get('A')),before);
          assert.match($('toast').textContent,/이미 첨부/);assert.equal($('prompt').value,'원문');
        })()""")

    def test_stale_result_selection_and_send_cannot_attach(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          $('prompt').value='초안';attachments=['C:/keep.txt'];saveDraft();
          sending=true;follow.onclick();assert.equal(attachments.length,1);sending=false;
          choiceSubmission={};follow.onclick();assert.equal(attachments.length,1);choiceSubmission=null;
          active={id:'B',kind:'work'};selectionGeneration++;follow.onclick();assert.equal(attachments.length,1);
          active={id:'A',kind:'work'};selectionGeneration++;follow.onclick();assert.equal(attachments.length,1);
          assert.equal($('prompt').value,'초안');assert.equal(drafts.get('A').attachments.length,1);
        })()""")

    def test_configured_limit_is_shared_by_result_and_drop(self):
        self.run_case("(async()=>{" + RESULT_SETUP + r"""
          boot.attachmentPolicy={maxAttachments:1};attachments=['C:/keep.txt'];
          follow.onclick();assert.equal(attachments.length,1);assert.match($('toast').textContent,/1개까지/);
          const transfer={types:['application/x-company-workspace-file'],getData(){return this.value;},setData(type,value){this.value=value;}};
          const file=el('div');WorkspaceAttachments.makeDraggable(file,resultPath,'A');
          file.ondragstart({preventDefault(){},dataTransfer:transfer});
          await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:transfer});
          assert.equal(attachments.length,1);assert.match($('toast').textContent,/1개까지/);
        })()""")

    def test_uncertain_queue_has_visible_guidance_without_resending(self):
        self.run_case(r"""(async()=>{
          let writes=0;
          const state={revision:1,paused:true,queue:[{id:'q1',text:'확인할 요청',state:'needs_review'},{id:'q2',text:'다음 요청',state:'queued'}],schedules:[],steer:{supported:true}};
          api=async(path,body)=>{if(body)writes++;return state;};await WorkspaceWorkflow.refresh();
          $('prompt').value='작성 중';attachments=['C:/keep.txt'];
          assert.equal($('inline-queue-notice').hidden,false);assert.match($('inline-queue-notice-text').textContent,/전송 여부 확인/);
          const row=$('inline-queue-list').children[0],button=row.children[1],remove=row.children[2];
          assert.match(flatText(row.children[0]),/확인 필요/);assert.equal(button.disabled,true);
          assert.match(button.title,/확인하기/);assert.equal(button.getAttribute('aria-description'),button.title);
          assert.equal(remove.disabled,false);assert.equal($('inline-queue-list').children[1].children[1].disabled,true);
          $('inline-queue-review').onclick();await settle();assert.equal($('workflow-dialog').open,true);
          assert.match($('workflow-paused').textContent,/확인 없이 자동 재전송하지 않습니다/);assert.equal(writes,0);
          WorkspaceWorkflow.render();assert.equal($('inline-queue-list').children[0],row);assert.equal(row.children[2],remove);
          assert.equal($('prompt').value,'작성 중');assert.equal(attachments[0],'C:/keep.txt');
        })()""", ("workflow",))

    def test_review_beyond_three_visible_rows_still_explains_block(self):
        self.run_case(r"""(async()=>{
          const queue=Array.from({length:4},(_,i)=>({id:'q'+i,text:'질문 '+i,state:i===3?'needs_review':'queued'}));
          api=async()=>({revision:1,queue,schedules:[],steer:{supported:true}});await WorkspaceWorkflow.refresh();
          assert.equal($('inline-queue-list').children.length,3);assert.equal($('inline-queue-notice').hidden,false);
          for(const row of $('inline-queue-list').children){assert.equal(row.children[1].disabled,true);assert.match(row.children[1].title,/전송 여부 확인/);}
          $('inline-queue-review').onclick();assert.equal($('queue-list').children.length,4);
        })()""", ("workflow",))

    def test_recovery_notice_tracks_pause_and_context_without_automatic_execution(self):
        self.run_case(r"""(async()=>{
          active.state='idle';let writes=0;
          let state={revision:1,paused:true,queue:[{id:'q1',text:'대기 요청',state:'queued'}],schedules:[],steer:{supported:true}};
          api=async(path,body)=>{if(body)writes++;return state;};await WorkspaceWorkflow.refresh();
          assert.equal($('inline-queue-notice').hidden,false);assert.match($('inline-queue-notice-text').textContent,/일시 정지/);
          const row=$('inline-queue-list').children[0],button=row.children[1];
          active.trusted=false;WorkspaceWorkflow.render();assert.equal(button.disabled,true);assert.match(button.title,/접근을 확인/);
          active.trusted=true;state={...state,revision:2,paused:false};await WorkspaceWorkflow.refresh();
          assert.equal($('inline-queue-notice').hidden,true);assert.equal(button.disabled,false);assert.equal(writes,0);
          active={id:'B',state:'idle',trusted:false};selectionGeneration++;WorkspaceWorkflow.contextChanged();
          await button.onclick();assert.equal(writes,0);assert.equal($('inline-queue').hidden,true);
        })()""", ("workflow",))


if __name__ == "__main__":
    unittest.main()
