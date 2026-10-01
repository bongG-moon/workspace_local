"""Run shipped task removal UI against cancellation and delayed responses."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
sessions=['A','B'].map((id,index)=>({id,title:'업무 '+id,workspace:'C:/'+id,pinned:false,created:index+1,updated:index+1,state:'idle',messages:[],seq:0}));
boot={};active=sessions[0];let notices=[];toast=text=>notices.push(text);renderSessions();
const remove=id=>[...$('sessions').children].find(row=>row.dataset.sessionId===id).querySelector('.session-remove');
"""


@unittest.skipUnless(NODE, 'Node.js is required for task list UI checks')
class SessionVisibilityFrontendTests(unittest.TestCase):
    def run_case(self, code):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+code],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_cancel_confirmation_sends_nothing_and_preserves_draft_and_list(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='아직 보내지 않은 질문';attachments=['C:/첨부.csv'];let calls=0;api=async()=>{calls++;};
          const pending=remove('A').onclick();assert.equal($('action-dialog').open,true);
          assert.match($('action-message').textContent,/대화 기록과 작업 폴더의 파일은 그대로/);
          assert.equal(document.activeElement,$('action-cancel'));$('action-cancel').onclick();
          assert.equal(await pending,false);assert.equal(calls,0);assert.equal(sessions.length,2);assert.equal(active.id,'A');
          assert.equal($('prompt').value,'아직 보내지 않은 질문');assert.equal(attachments[0],'C:/첨부.csv');
        })()""")

    def test_confirm_current_task_removal_returns_home_preserving_conversation_draft(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='보존할 초안';attachments=['C:/첨부.csv'];let calls=[];
          api=async(path,body)=>{calls.push({path,body});return {ok:true,hiddenId:'A',historyPreserved:true};};
          const pending=remove('A').onclick();$('action-confirm').onclick();assert.equal(await pending,true);
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/session/hide');assert.equal(calls[0].body.confirmed,true);
          assert.equal(active,null);assert.equal($('welcome').hidden,false);assert.equal(sessions.map(s=>s.id).join(','),'B');
          assert.equal(drafts.get('A').text,'보존할 초안');assert.equal(drafts.get('A').attachments[0],'C:/첨부.csv');
          assert.equal(document.activeElement,$('home-button'));assert.equal($('sessions').children.length,1);
        })()""")

    def test_blocked_removal_keeps_task_draft_and_shows_backend_action(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='대기 중 초안';api=async()=>{throw Error('이어 할 일에 남은 요청을 먼저 완료하거나 취소해 주세요.');};
          const pending=remove('A').onclick();$('action-confirm').onclick();assert.equal(await pending,false);
          assert.equal(active.id,'A');assert.equal(sessions.length,2);assert.equal($('prompt').value,'대기 중 초안');
          assert.equal(hiddenSessionIds.size,0);assert.equal(hidingSessionIds.size,0);assert.match(notices[0],/이어 할 일/);
        })()""")

    def test_delayed_removal_does_not_switch_another_task_and_stale_bootstrap_cannot_restore_it(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const old=[...sessions];
          const pending=remove('A').onclick();$('action-confirm').onclick();await Promise.resolve();
          active=sessions[1];$('prompt').value='B의 초안';reply({ok:true});assert.equal(await pending,true);
          assert.equal(active.id,'B');assert.equal($('prompt').value,'B의 초안');assert.equal(sessions.length,1);
          sessions=old;renderSessions();assert.equal(sessions.length,1);assert.equal(sessions[0].id,'B');
        })()""")

    def test_delayed_selection_of_removed_task_is_ignored_and_current_poll_resumes(self):
        self.run_case(r"""(async()=>{
          active=sessions[1];let reply,polls=[];api=()=>new Promise(resolve=>reply=resolve);poll=(id,seq,signal)=>polls.push(id);
          const pending=selectSession('A');hiddenSessionIds.add('A');sessions=sessions.filter(s=>s.id!=='A');
          reply({id:'A',messages:[],state:'idle'});assert.equal(await pending,false);
          assert.equal(active.id,'B');assert.equal(polls.join(','),'B');
        })()""")

    def test_known_hidden_inbox_task_does_not_touch_running_poll_or_draft(self):
        self.run_case(r"""(async()=>{
          active=sessions[1];active.state='running';active.seq=8;hiddenSessionIds.add('A');
          $('prompt').value='B 후속 질문';attachments=['C:/B/input.csv'];streaming.set('B-stream',{text:'기존 답변'});
          const original=pollController=new AbortController();let calls=0,resets=0;
          api=async()=>{calls++;};globalThis.WorkspaceStream={reset(){resets++;}};
          await assert.rejects(selectSession('A'),/목록에서 삭제/);
          assert.equal(calls,0);assert.equal(resets,0);assert.equal(pollController,original);assert.equal(original.signal.aborted,false);
          assert.equal(active.id,'B');assert.equal(active.state,'running');assert.equal($('prompt').value,'B 후속 질문');
          assert.equal(attachments[0],'C:/B/input.csv');assert.equal(streaming.get('B-stream').text,'기존 답변');
        })()""")

    def test_unknown_hidden_or_failed_lookup_resumes_current_poll_without_tearing_down_stream(self):
        self.run_case(r"""(async()=>{
          active=sessions[1];active.state='running';active.seq=12;$('prompt').value='작성 중 질문';attachments=['C:/B/source.csv'];
          streaming.set('message:0',{text:'진행 중 답변'});let polls=[],resets=0;
          globalThis.WorkspaceStream={reset(){resets++;}};poll=(id,seq,signal)=>polls.push({id,seq,signal});
          const previous=pollController=new AbortController();api=async()=>{throw Error('업무 목록에서 삭제한 항목입니다.');};
          await assert.rejects(selectSession('A'),/목록에서 삭제/);
          assert.equal(previous.signal.aborted,true);assert.equal(polls.length,1);assert.equal(polls[0].id,'B');assert.equal(polls[0].seq,12);
          assert.equal(polls[0].signal.aborted,false);assert.equal(active.id,'B');assert.equal(active.state,'running');
          assert.equal(resets,0);assert.equal(streaming.get('message:0').text,'진행 중 답변');
          assert.equal($('prompt').value,'작성 중 질문');assert.equal(attachments[0],'C:/B/source.csv');
        })()""")

    def test_stale_failed_lookup_does_not_restart_poll_after_newer_selection(self):
        self.run_case(r"""(async()=>{
          active=sessions[1];let rejectA,resolveB,polls=[];
          api=path=>path.includes('id=A')?new Promise((resolve,reject)=>rejectA=reject):new Promise(resolve=>resolveB=resolve);
          poll=(id,seq,signal)=>polls.push({id,seq,signal});refreshFiles=()=>{};refreshResults=()=>{};
          const old=selectSession('A'),latest=selectSession('B');
          resolveB({...sessions[1],seq:29});assert.equal(await latest,true);const controller=pollController;
          rejectA(Error('오래된 목록 항목'));assert.equal(await old,false);
          assert.equal(active.id,'B');assert.equal(polls.length,1);assert.equal(polls[0].seq,29);
          assert.equal(pollController,controller);assert.equal(controller.signal.aborted,false);
        })()""")


if __name__ == '__main__':
    unittest.main()
