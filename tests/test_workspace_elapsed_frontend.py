"""Elapsed time belongs to an accepted request, not the selected view."""
import unittest

from tests import test_workspace_frontend_state as frontend


SETUP = r'''
let now=200000;Date.now=()=>now;
const a={id:'A',kind:'work',title:'A',workspace:'C:/A',state:'running',messages:[],requests:[],seq:0,lastRunId:'run-A',runStartedAt:100};
const b={id:'B',kind:'work',title:'B',workspace:'C:/B',state:'running',messages:[],requests:[],seq:0,lastRunId:'run-B',runStartedAt:180};
const c={id:'C',kind:'chat',title:'C',workspace:'C:/C',state:'running',messages:[],requests:[],seq:0,lastRunId:'run-C',runStartedAt:150};
sessions=[a,b,c];poll=()=>{};refreshInspector=()=>{};refreshSessionMeta=async()=>{};
const calls=[];api=async(path,body)=>{calls.push({path,body});return {...[a,b,c].find(row=>row.id===decodeURIComponent(path.split('id=')[1]))};};
'''


@unittest.skipUnless(frontend.NODE, 'Node.js is required')
class WorkspaceElapsedFrontendTests(unittest.TestCase):
    def run_case(self, code):
        frontend.WorkspaceFrontendStateTests.run_case(self, '(async()=>{' + SETUP + code + '})()')

    def test_switch_between_running_tasks_preserves_elapsed_and_draft(self):
        self.run_case(r'''
          await selectSession('A');assert.equal(started,100000);
          assert.equal($('elapsed').textContent,'100초');
          $('prompt').value='작성 중 요청';attachments=['C:/A/keep.txt'];
          now+=5000;await selectSession('B');assert.equal($('elapsed').textContent,'25초');
          now+=5000;await selectSession('A');assert.equal($('elapsed').textContent,'110초');
          assert.equal($('prompt').value,'작성 중 요청');assert.deepEqual(attachments,['C:/A/keep.txt']);
          assert.equal(active.lastRunId,'run-A');assert.equal(active.state,'running');
          assert.ok(calls.every(call=>call.path.startsWith('/api/session?')),'switching must never stop or resend');
        ''')

    def test_home_mode_switch_and_fresh_view_use_same_start(self):
        self.run_case(r'''
          await selectSession('A');now+=2000;showHome();assert.equal($('elapsed').textContent,'');
          now+=3000;await selectSession('A');assert.equal($('elapsed').textContent,'105초');
          await selectSession('C');assert.equal($('elapsed').textContent,'55초');
          now+=5000;await switchMode('work');assert.equal(active.id,'A');assert.equal($('elapsed').textContent,'110초');
          started=null;await selectSession('C');assert.equal($('elapsed').textContent,'60초');
        ''')

    def test_approval_wait_and_resume_keep_request_start(self):
        self.run_case(r'''
          await selectSession('A');setStatus('approval');assert.equal($('elapsed').textContent,'응답 대기');
          now+=30000;setStatus('question');assert.equal($('elapsed').textContent,'응답 대기');
          setStatus('running');assert.equal($('elapsed').textContent,'130초');
          applyStopState({stopState:'stopping'});setStatus('stopping');assert.equal($('elapsed').textContent,'130초');
          applyStopState({stopState:'stopped'});setStatus('stopped');assert.equal($('elapsed').textContent,'');
        ''')

    def test_followup_resets_only_for_new_run_and_progress_cannot_reset_it(self):
        self.run_case(r'''
          await selectSession('A');now+=1000;
          handleEvent({type:'status',data:{state:'starting',runId:'followup',runStartedAt:201}});
          assert.equal($('elapsed').textContent,'0초');assert.equal(active.lastRunId,'followup');
          now+=12000;
          for(let i=0;i<50;i++)handleEvent({type:'status',data:{state:'running',label:'진행 중 '+i,runId:'followup',runStartedAt:201}});
          assert.equal($('elapsed').textContent,'12초');
          handleEvent({type:'result',data:{lastRunId:'followup'}});assert.equal($('elapsed').textContent,'');
          handleEvent({type:'status',data:{state:'running',runId:'next',runStartedAt:213}});
          assert.equal($('elapsed').textContent,'0초');
        ''')

    def test_event_recovery_uses_snapshot_run_without_sending(self):
        self.run_case(r'''
          await selectSession('A');now+=10000;
          restoreEventSnapshot({...active,seq:400,runStartedAt:100},'A');assert.equal($('elapsed').textContent,'110초');
          restoreEventSnapshot({...active,seq:800,lastRunId:'queued-next',runStartedAt:205},'A');assert.equal($('elapsed').textContent,'5초');
          assert.ok(calls.every(call=>call.path.startsWith('/api/session?')));
        ''')

    def test_queued_request_and_permission_events_share_new_run_clock(self):
        self.run_case(r'''
          await selectSession('A');now+=1000;
          handleEvent({type:'queued_user',data:{requestId:'queued-1',text:'follow up',runId:'queue-run',runStartedAt:201}});
          handleEvent({type:'request',data:{id:'approval-1',tool:'Read',input:{},runId:'queue-run',runStartedAt:201}});
          assert.equal($('elapsed').textContent,'응답 대기');
          now+=20000;
          handleEvent({type:'request_closed',data:{id:'approval-1',state:'running',runId:'queue-run',runStartedAt:201}});
          assert.equal($('elapsed').textContent,'20초');assert.equal(active.lastRunId,'queue-run');
          assert.equal(active.messages.filter(row=>row.requestId==='queued-1').length,1);
        ''')

    def test_missing_timestamp_never_reuses_previous_run_or_negative_time(self):
        self.run_case(r'''
          await selectSession('A');
          handleEvent({type:'status',data:{state:'running',runId:'legacy-next'}});
          assert.equal($('elapsed').textContent,'0초');now+=5000;renderElapsed();assert.equal($('elapsed').textContent,'5초');
          handleEvent({type:'status',data:{state:'running',runId:'future-clock',runStartedAt:210}});
          assert.equal($('elapsed').textContent,'0초');
          setStatus('error');assert.equal($('elapsed').textContent,'');assert.equal(started,null);
        ''')


if __name__ == '__main__':
    unittest.main()
