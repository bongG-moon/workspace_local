"""User-triggered external-session refresh stays scoped to the selected conversation."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(NODE, 'Node.js is required for the shipped UI checks')
class WorkspaceSessionSyncFrontendTests(unittest.TestCase):
    def run_case(self, scenario):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), scenario],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_button_only_for_selected_native_session_and_disabled_while_busy(self):
        self.run_case(r"""(()=>{
          active=null;renderSessionSync();assert.equal($('session-sync').hidden,true);
          active={id:'a',sessionId:null,state:'idle'};renderSessionSync();assert.equal($('session-sync').hidden,true);
          active.sessionId='native-id';renderSessionSync();assert.equal($('session-sync').hidden,true);
          sessionSyncAvailability={owner:'a',available:true};renderSessionSync();assert.equal($('session-sync').hidden,false);
          assert.equal($('session-sync').disabled,false);
          active.state='running';renderSessionSync();assert.equal($('session-sync').hidden,true);assert.equal($('session-sync').disabled,true);
          active.state='idle';currentMode='chat';renderSessionSync();assert.equal($('session-sync').hidden,false);
        })()""")

    def test_success_refreshes_only_current_conversation_and_keeps_input(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-id',state:'idle',seq:1,messages:[],requests:[]};sessionSyncAvailability={owner:'a',available:true};
          $('prompt').value='작성 중인 요청';attachments=['current.csv'];let request;
          api=async(path,data)=>{if(path==='/api/session/import-status')return {available:false};request={path,data};return {ok:true,added:2,session:{id:'a',seq:3,messages:[],requests:[]},warnings:[]};};
          let restored;restoreEventSnapshot=(value,id)=>{restored={value,id};active.seq=value.seq;};
          renderSessionSync();await syncExternalSession();
          assert.equal(request.path,'/api/session/import-updates');assert.equal(request.data.id,'a');
          assert.equal(restored.id,'a');assert.equal(restored.value.seq,3);
          assert.equal($('prompt').value,'작성 중인 요청');assert.deepEqual(attachments,['current.csv']);
          assert.match($('session-sync-note').textContent,/2개/);assert.equal($('session-sync').disabled,false);
        })()""")

    def test_switching_tasks_during_import_does_not_replace_new_view(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle',seq:1,messages:[],requests:[]};sessionSyncAvailability={owner:'a',available:true};renderSessionSync();
          let resolve;api=()=>new Promise(r=>resolve=r);let restores=0;restoreEventSnapshot=()=>restores++;
          const promise=syncExternalSession();assert.equal($('session-sync').disabled,true);
          assert.equal($('send').disabled,true);
          active={id:'b',sessionId:'native-b',state:'idle',seq:4,messages:[],requests:[]};selectionGeneration++;
          $('prompt').value='다른 업무의 초안';resolve({ok:true,added:1,session:{id:'a',seq:2,messages:[],requests:[]},warnings:[]});
          await promise;assert.equal(restores,0);assert.equal(active.id,'b');assert.equal($('prompt').value,'다른 업무의 초안');
          assert.notEqual($('session-sync-note').dataset.owner,'b');
        })()""")

    def test_error_and_no_new_messages_keep_existing_content(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle',seq:1,messages:[],requests:[]};sessionSyncAvailability={owner:'a',available:true};
          $('prompt').value='유지';renderSessionSync();api=async()=>{throw Error('원본 기록을 확인해 주세요.');};
          await syncExternalSession();assert.match($('session-sync-note').textContent,/원본 기록/);
          assert.equal($('session-sync-note').dataset.error,'true');assert.equal($('prompt').value,'유지');
          sessionSyncAvailability={owner:'a',available:true};renderSessionSync();api=async()=>({ok:true,added:0,warnings:[]});await syncExternalSession();
          assert.match($('session-sync-note').textContent,/새로 불러올 대화가 없어요/);
          assert.equal($('session-sync-note').dataset.error,'false');assert.equal($('prompt').value,'유지');
        })()""")

    def test_availability_is_scoped_and_only_shown_for_confirmed_updates(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};
          let finish;api=()=>new Promise(resolve=>finish=resolve);
          startSessionSyncProbe();assert.equal($('session-sync').hidden,true);
          finish({available:true});await Promise.resolve();assert.equal($('session-sync').hidden,false);
          active={id:'b',sessionId:'native-b',state:'idle'};selectionGeneration++;
          renderSessionSync();assert.equal($('session-sync').hidden,true);
          startSessionSyncProbe();finish({available:false});await Promise.resolve();assert.equal($('session-sync').hidden,true);
        })()""")

    def test_probe_failure_uses_fixed_safe_message_and_successful_recheck_clears_it(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};let calls=0;
          api=async()=>{calls++;if(calls===1)throw Error('secret-token C:\\private\\session.json');return {available:true};};
          startSessionSyncProbe();await Promise.resolve();await Promise.resolve();
          const note=$('session-sync-note');assert.equal(note.hidden,false);assert.equal(note.dataset.error,'true');assert.equal(note.dataset.kind,'probe');
          assert.match(note.textContent,/다시 선택/);assert.doesNotMatch(note.textContent,/secret-token|private|session.json/);
          assert.equal($('session-sync').hidden,true);
          selectionGeneration++;startSessionSyncProbe();await Promise.resolve();
          assert.equal(calls,2);assert.equal(note.hidden,true);assert.equal($('session-sync').hidden,false);
        })()""")

    def test_late_probe_failure_cannot_pollute_new_selection_or_closed_app(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};let fail;
          api=()=>new Promise((_,reject)=>fail=reject);startSessionSyncProbe();
          active={id:'b',sessionId:'native-b',state:'idle'};selectionGeneration++;
          sessionSyncNotice('b','다른 업무 안내');fail(Error('late private error'));await Promise.resolve();await Promise.resolve();
          assert.equal($('session-sync-note').dataset.owner,'b');assert.equal($('session-sync-note').textContent,'다른 업무 안내');
          startSessionSyncProbe();appClosed=true;fail(Error('closed private error'));await Promise.resolve();await Promise.resolve();
          assert.equal($('session-sync-note').textContent,'다른 업무 안내');
        })()""")

    def test_selection_recheck_does_not_clear_completed_import_notice(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};sessionSyncNotice('a','다른 곳에서 이어 쓴 대화 2개를 불러왔어요.');
          api=async()=>({available:false});selectionGeneration++;startSessionSyncProbe();await Promise.resolve();
          assert.equal($('session-sync-note').hidden,false);assert.match($('session-sync-note').textContent,/2개/);
          assert.equal($('session-sync').hidden,true);
        })()""")

    def test_probe_result_from_previous_task_is_discarded(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};let finish;
          api=()=>new Promise(resolve=>finish=resolve);startSessionSyncProbe();
          active={id:'b',sessionId:'native-b',state:'idle'};selectionGeneration++;
          finish({available:true});await Promise.resolve();renderSessionSync();
          assert.equal($('session-sync').hidden,true);
        })()""")

    def test_probe_runs_once_per_selection_without_a_polling_timer(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle'};let requests=0,timers=0;
          api=async()=>{requests++;return {available:true};};
          const originalTimer=setTimeout;setTimeout=(fn,delay)=>{if(delay===15000)timers++;return originalTimer(fn,delay);};
          startSessionSyncProbe();await Promise.resolve();
          assert.equal(requests,1);assert.equal(timers,0);assert.equal($('session-sync').hidden,false);
          setStatus('done');setStatus('idle');await Promise.resolve();
          assert.equal(requests,1);assert.equal(timers,0);
          selectionGeneration++;startSessionSyncProbe();await Promise.resolve();
          assert.equal(requests,2);assert.equal(timers,0);
        })()""")

    def test_selected_busy_session_is_checked_once_after_it_finishes(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'running'};let requests=0;
          api=async()=>{requests++;return {available:true};};
          startSessionSyncProbe();await Promise.resolve();assert.equal(requests,0);
          setStatus('done');await Promise.resolve();assert.equal(requests,1);
          assert.equal($('session-sync').hidden,false);
          setStatus('idle');await Promise.resolve();assert.equal(requests,1);
        })()""")

    def test_other_window_sync_snapshot_skips_events_already_included(self):
        self.run_case(r"""(async()=>{
          active={id:'a',sessionId:'native-a',state:'idle',seq:0,messages:[],requests:[]};
          $('prompt').value='사용자가 작성 중';attachments=['draft.csv'];
          const controller=new AbortController(),seen=[];let eventCalls=0,restores=0;
          const snapshot={id:'a',seq:2,state:'done',messages:[{role:'assistant',text:'이미 포함된 답변'}],requests:[]};
          api=async(path)=>{
            if(path.startsWith('/api/events')){
              if(eventCalls++===0)return {events:[{seq:1,type:'history_synced',data:{}},{seq:2,type:'assistant',data:{text:'이미 포함된 답변'}}]};
              controller.abort();return {events:[]};
            }
            assert.match(path,/api\/session/);return snapshot;
          };
          handleEvent=event=>seen.push(event.seq);
          restoreEventSnapshot=(value,id)=>{assert.equal(id,'a');restores++;Object.assign(active,value);};
          await poll('a',0,controller.signal);
          assert.equal(restores,1);assert.deepEqual(seen,[]);assert.equal(active.seq,2);
          assert.equal($('prompt').value,'사용자가 작성 중');assert.deepEqual(attachments,['draft.csv']);
        })()""")


if __name__ == '__main__':
    unittest.main()
