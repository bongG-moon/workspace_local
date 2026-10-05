"""Restart the current CLI connection without replaying a request or losing drafts."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE
from tests.test_workspace_controls_frontend import SETUP

ROOT = Path(__file__).resolve().parents[1]
RESTART_SETUP = SETUP + r"""
boot={demo:false};active.connection.connected=true;active.requests=[];active.seq=7;
const snapshot=(overrides={})=>({...active,messages:[{role:'assistant',text:'기존 답변'}],requests:[],choice:null,
  state:'done',seq:20,connection:{...active.connection,connected:true,restarting:false,sessionId:'same-cli-session'},...overrides});
poll=async()=>{};
"""


@unittest.skipUnless(NODE, "Node.js is required for restart UI checks")
class WorkspaceRestartFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), RESTART_SETUP + script],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_idle_restart_is_control_only_and_keeps_live_draft_and_attachments(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='작성 중인 요청';attachments=['C:/source.zip'];
          let reply;const calls=[];api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const pending=restartConnection();assert.equal(calls.length,1);
          assert.equal(calls[0].path,'/api/restart-connection');assert.deepEqual(calls[0].body,{id:'A'});
          assert.equal(active.state,'done');assert.equal(started,null);assert.equal($('send').disabled,true);
          assert.equal($('model-apply').disabled,true);assert.equal($('restart-connection').disabled,true);
          assert.equal($('connection-badge').textContent,'Claude 재시작 중');
          $('prompt').value+=' 추가 편집';attachments.push('C:/keep.exe');
          reply({ok:true,session:snapshot(),connection:{connected:true}});const result=await pending;
          assert.equal(result.ok,true);assert.equal(calls.length,1);assert.equal(active.state,'done');
          assert.equal($('prompt').value,'작성 중인 요청 추가 편집');assert.equal(attachments.length,2);
          assert.equal($('send').disabled,false);assert.equal($('connection-badge').textContent,'업무 연결됨');
          assert.equal(restartingConnections.size,0);
        })()""")

    def test_busy_restart_requires_styled_confirmation_and_clears_old_requests_after_success(self):
        self.run_case(r"""(async()=>{
          active.state='approval';active.requests=[{id:'approval-1',tool:'Bash',input:{command:'fixture'}}];
          active.choice=choice();renderRequest(active.requests[0]);$('prompt').value='초안';
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {ok:true,session:snapshot({state:'stopped'}),workflowPaused:true};};
          const pending=restartConnection();assert.equal(calls.length,0);assert.equal($('action-dialog').open,true);
          assert.match($('action-title').textContent,/현재 작업을 중지/);assert.equal($('action-confirm').textContent,'중지하고 재시작');
          assert.equal($('requests').children[0].inert,true);$('action-confirm').onclick();await pending;
          assert.deepEqual(calls[0].body,{id:'A',stopRunning:true});assert.equal(calls.length,1);
          assert.equal(active.state,'stopped');assert.equal(active.choice,null);assert.equal($('requests').children.length,0);
          assert.equal($('prompt').value,'초안');assert.match($('toast').textContent,/일시 정지/);
        })()""")

    def test_cancel_never_stops_or_replays_the_request(self):
        self.run_case(r"""(async()=>{
          active.state='running';let calls=0;api=async()=>{calls++;};
          const pending=restartConnection();$('action-cancel').onclick();assert.equal(await pending,null);
          assert.equal(calls,0);assert.equal(active.state,'running');assert.equal(restartingConnections.size,0);
          assert.equal($('stop').disabled,false);assert.equal($('connection-badge').textContent,'업무 연결됨');
        })()""")

    def test_server_busy_race_prompts_before_one_authorized_retry(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});if(calls.length===1){const err=new Error('확인 필요');err.code='restart_requires_stop';throw err;}return {ok:true,session:snapshot({state:'stopped'})};};
          const pending=restartConnection();await Promise.resolve();await Promise.resolve();
          assert.equal(calls.length,1);assert.equal($('action-dialog').open,true);$('action-confirm').onclick();await pending;
          assert.equal(calls.length,2);assert.deepEqual(calls[0].body,{id:'A'});assert.deepEqual(calls[1].body,{id:'A',stopRunning:true});
        })()""")

    def test_start_failure_refreshes_actual_connection_and_keeps_draft(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='남겨둘 내용';attachments=['C:/keep.txt'];const calls=[];
          api=async(path,body)=>{calls.push(path);if(body)throw new Error('새 Claude 실행 실패');return snapshot({state:'error',connection:{connected:false,restarting:false,capabilities:{}}});};
          const result=await restartConnection();assert.equal(result.ok,false);
          assert.deepEqual(calls,['/api/restart-connection','/api/session?id=A']);assert.equal(active.connection.connected,false);
          assert.equal(active.state,'error');assert.match($('connection-restart-message').textContent,/새 Claude 실행 실패/);
          assert.equal($('prompt').value,'남겨둘 내용');assert.equal(attachments[0],'C:/keep.txt');
          assert.equal($('restart-connection').disabled,false);assert.equal(restartingConnections.size,0);
        })()""")

    def test_switching_tasks_keeps_other_task_usable_and_ignores_late_reply(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const original=snapshot(),pending=restartConnection();
          selectionGeneration++;active={...snapshot(),id:'B',title:'업무 B'};$('prompt').value='B 초안';attachments=['B.txt'];setStatus('done');
          assert.equal(connectionRestarting(),false);assert.equal($('send').disabled,false);assert.equal(WorkspaceConnectionRestart.isAny(),true);
          $('toast').textContent='B 알림';reply({ok:true,session:original});assert.equal(await pending,null);
          assert.equal(active.id,'B');assert.equal($('prompt').value,'B 초안');assert.equal(attachments[0],'B.txt');assert.equal($('toast').textContent,'B 알림');
          assert.equal(WorkspaceConnectionRestart.isAny(),false);
        })()""")

    def test_leave_and_return_to_same_task_does_not_apply_old_view_reply(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const pending=restartConnection();
          selectionGeneration+=2;active={...snapshot(),title:'다시 선택한 현재 화면'};
          assert.equal(connectionRestarting(),true);assert.equal(await restartConnection(),null);
          $('toast').textContent='새 화면 알림';reply({ok:true,session:snapshot({title:'오래된 제목'})});assert.equal(await pending,null);
          assert.equal(active.title,'다시 선택한 현재 화면');assert.equal($('toast').textContent,'새 화면 알림');
        })()""")

    def test_restart_refreshes_inventory_and_completion_without_changing_task_id(self):
        self.run_case(r"""(async()=>{
          let catalogs=0,completions=0;
          globalThis.WorkspaceCapabilities={isOpen:()=>false,contextChanged:reload=>{if(reload)catalogs++;}};
          globalThis.WorkspaceComposer={close(){},connectionChanged(){completions++;}};
          api=async()=>({ok:true,session:snapshot()});await restartConnection();
          assert.equal(active.id,'A');assert.equal(catalogs,1);assert.equal(completions,1);
        })()""")

    def test_demo_no_task_and_overlapping_operations_never_restart(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};boot.demo=true;renderConnection(active.connection);
          assert.equal($('settings-restart-connection').disabled,true);assert.match($('connection-restart-message').textContent,/체험 화면/);assert.equal(await restartConnection(),null);
          boot.demo=false;for(const flag of ['model','effort','connection','approval','upload']){
            modelChanging=flag==='model';effortChanging=flag==='effort';connectionPreparing=flag==='connection';
            if(flag==='approval')pendingRequestAnswers.set('A',1);
            globalThis.WorkspaceAttachments={isUploading:()=>flag==='upload'};
            assert.equal(await restartConnection(),null);pendingRequestAnswers.clear();
          }
          modelChanging=effortChanging=connectionPreparing=false;active=null;renderConnection(null);
          assert.equal($('restart-connection').hidden,true);assert.equal(await restartConnection(),null);assert.equal(calls,0);
        })()""")

    def test_new_connected_event_cannot_enable_controls_while_restart_is_pending(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const pending=restartConnection();
          handleEvent({type:'connected',data:{connected:true,restarting:true,model:'new',capabilities:{setModel:true}}});
          assert.equal($('connection-badge').textContent,'Claude 재시작 중');assert.equal($('model-apply').disabled,true);
          assert.equal(await setModel('wrong-time'),null);assert.equal(await setPermissionMode('auto'),null);
          reply({ok:true,session:snapshot()});await pending;assert.equal($('model-apply').disabled,false);
        })()""")

    def test_reselected_task_unlocks_from_the_observed_restart_finished_event(self):
        self.run_case(r"""(()=>{
          active.connection.restarting=true;setStatus('done');assert.equal($('send').disabled,true);
          handleEvent({type:'connection_restart_finished',data:{state:'done',connection:{connected:true,restarting:false,
            model:'newly-observed',capabilities:{setModel:true}}}});
          assert.equal($('send').disabled,false);assert.equal(active.connection.model,'newly-observed');
          assert.equal($('connection-badge').textContent,'업무 연결됨');
        })()""")

    def test_pending_approval_cannot_be_answered_during_restart_confirmation(self):
        self.run_case(r"""(async()=>{
          active.state='approval';const request={id:'r1',tool:'Bash',input:{command:'fixture'}};
          active.requests=[request];renderRequest(request);let calls=0;api=async()=>{calls++;};
          const pending=restartConnection(),card=$('requests').children[0];
          for(const button of descendants(card,'BUTTON'))await button.onclick?.();
          assert.equal(calls,0);$('action-cancel').onclick();await pending;
          assert.equal(card.inert,false);assert.equal($('restart-connection').disabled,false);
        })()""")


if __name__ == '__main__':
    unittest.main()
