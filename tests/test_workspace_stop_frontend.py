"""Stop lifecycle feedback never replays the draft or assumes CLI disconnect."""
import unittest

import test_workspace_frontend_state as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for stop lifecycle UI checks')
class WorkspaceStopFrontendTests(unittest.TestCase):
    run_case = frontend.WorkspaceFrontendStateTests.run_case

    def test_soft_stop_waits_for_confirmation_and_keeps_live_connection(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'running',trusted:true,connectionStopped:false,
            connection:{connected:true,model:'kept-model',capabilities:{setModel:true}},messages:[]};sessions=[{...active}];
          $('prompt').value='follow-up draft';attachments=['keep.csv'];
          refreshFiles=refreshResults=refreshSessionMeta=async()=>{};
          let reply,calls=[];api=(path,data)=>{calls.push({path,data});return new Promise(resolve=>reply=resolve);};
          const operation=$('stop').onclick();
          assert.deepEqual(calls,[{path:'/api/stop',data:{id:'A'}}]);
          assert.equal($('status').dataset.state,'stopping');assert.equal($('send').disabled,true);
          assert.equal($('stop').disabled,true);assert.equal($('prompt').readOnly,false);
          assert.equal($('connection-badge').textContent,'작업 중지 중');
          handleEvent({type:'result',data:{stopState:null,connectionStopped:false}});
          assert.equal(active.state,'stopping');assert.doesNotMatch($('status-text').textContent,/요청을 마쳤/);
          await submit();await $('stop').onclick();assert.equal(calls.length,1);
          reply({ok:true,session:{id:'A',state:'running',stopState:'stopping',connectionStopped:false,connection:active.connection}});await operation;
          assert.equal($('send').disabled,true);assert.equal($('recovery-actions').hidden,true);
          handleEvent({type:'status',data:{state:'stopped',stopState:'stopped',connectionStopped:false,cleanupRetryable:false}});
          assert.equal(active.state,'stopped');assert.equal($('send').disabled,false);
          assert.equal(active.connection.connected,true);assert.equal(active.connection.model,'kept-model');
          assert.equal($('connection-badge').textContent,'업무 연결됨');
          assert.equal($('prompt').value,'follow-up draft');assert.deepEqual(attachments,['keep.csv']);
          assert.equal(calls.length,1);
          api=async(path,data)=>{calls.push({path,data});return {ok:true};};
          await submit();assert.equal(calls.length,2);assert.equal(calls[1].path,'/api/send');
          assert.equal(calls[1].data.text,'follow-up draft');
        })()''')

    def test_typed_pending_send_keeps_draft_without_reconnect_banner_or_replay(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'done',trusted:true,connection:{connected:true},messages:[]};sessions=[{...active}];
          $('prompt').value='keep this';attachments=['input.csv'];
          let calls=0;api=async()=>{calls++;throw Object.assign(new Error('transient stop'),{code:'stop_in_progress',stopState:'stopping',connectionStopped:false});};
          await submit();
          assert.equal(calls,1);assert.equal(active.state,'stopping');
          assert.equal($('send').disabled,true);assert.equal($('prompt').value,'keep this');
          assert.deepEqual(attachments,['input.csv']);assert.equal($('error-banner').hidden,true);
          assert.equal($('recovery-actions').hidden,true);assert.equal($('connection-badge').textContent,'작업 중지 중');
          await submit();await reconnect();await setModel('other');assert.equal(calls,1);
        })()''')

    def test_cleanup_failure_retry_has_correct_action_and_keeps_draft_on_rejection(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'done',trusted:true,connection:{connected:false},messages:[]};sessions=[{...active}];
          $('prompt').value='kept';attachments=['kept.csv'];
          let calls=[];api=async(path,data)=>{calls.push({path,data});throw Object.assign(new Error('raw cleanup detail'),{code:'stop_cleanup_unverified',stopState:null,cleanupRetryable:true,connectionStopped:false});};
          await submit();
          assert.equal(stopState(),'failed');assert.equal($('send').disabled,true);
          assert.equal($('connection-badge').textContent,'중지 확인 필요');
          assert.equal($('reconnect').textContent,'중지 다시 시도');assert.equal($('connection-settings').hidden,true);
          assert.doesNotMatch($('error-banner').textContent,/raw cleanup/);
          await $('reconnect').onclick();assert.equal(calls.length,2);
          assert.deepEqual(calls[1],{path:'/api/stop',data:{id:'A'}});
          assert.equal(pendingConfirmation,null);assert.equal($('reconnect').disabled,false);
          assert.equal(stopState(),'failed');assert.equal($('prompt').value,'kept');
          assert.deepEqual(attachments,['kept.csv']);
          api=async(path,data)=>{calls.push({path,data});return {ok:true,session:{id:'A',state:'stopped',stopState:'stopped',connectionStopped:true,cleanupRetryable:false,connection:{connected:false}}};};
          await $('reconnect').onclick();assert.equal(calls.length,3);
          assert.equal($('send').disabled,false);assert.equal($('recovery-actions').hidden,true);
          assert.equal($('prompt').value,'kept');assert.equal(active.connection.connected,false);
          assert.equal(calls.filter(call=>call.path==='/api/send').length,1);
        })()''')

    def test_late_rejected_send_cannot_undo_newer_confirmed_stop(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'done',trusted:true,connection:{connected:true},messages:[]};sessions=[{...active}];
          $('prompt').value='keep';refreshFiles=refreshResults=async()=>{};
          let reject,calls=0;api=()=>{calls++;return new Promise((_,fail)=>reject=fail);};
          const operation=submit();
          handleEvent({type:'status',data:{state:'stopping',stopState:'stopping'}});
          handleEvent({type:'status',data:{state:'stopped',stopState:'stopped',connectionStopped:false}});
          reject(Object.assign(new Error('old pending'),{code:'stop_in_progress',stopState:'stopping'}));await operation;
          assert.equal(stopState(),'stopped');assert.equal(active.state,'stopped');
          assert.equal($('send').disabled,false);assert.equal($('recovery-actions').hidden,true);
          assert.equal(calls,1);assert.equal($('prompt').value,'keep');
        })()''')

    def test_stop_reply_cannot_change_another_tasks_draft_or_status(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'running',connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='A draft';let reply;api=()=>new Promise(resolve=>reply=resolve);
          const operation=requestStop();
          active={id:'B',state:'idle',connection:{connected:true}};selectionGeneration++;$('prompt').value='B draft';setStatus('idle');
          reply({ok:true,session:{id:'A',state:'stopped',stopState:'stopped',connectionStopped:false}});await operation;
          assert.equal(active.id,'B');assert.equal(active.state,'idle');assert.equal($('prompt').value,'B draft');
          assert.equal(stopStates.get('A').state,'stopped');assert.equal($('send').disabled,false);
        })()''')

    def test_idle_disconnect_releases_only_selected_connection_and_keeps_settings(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'idle',connectionStopped:false,connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='draft';attachments=['draft.csv'];showDialog('settings-dialog');setStatus('idle');
          let calls=[];api=async(path,data)=>{calls.push({path,data});return {ok:true,session:{id:'A',state:'stopped',stopState:'stopped',connectionStopped:true,connection:{connected:false}}};};
          await $('settings-disconnect').onclick();
          assert.deepEqual(calls,[{path:'/api/stop',data:{id:'A',disconnect:true}}]);
          assert.equal(pendingConfirmation,null);assert.equal($('settings-dialog').open,true);
          assert.equal($('settings-disconnect').disabled,true);assert.equal($('send').disabled,false);
          assert.equal($('prompt').value,'draft');assert.deepEqual(attachments,['draft.csv']);
        })()''')

    def test_busy_disconnect_cancellation_does_not_stop_or_clear_draft(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'running',connectionStopped:false,connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='draft';showDialog('settings-dialog');setStatus('running');
          let calls=0;api=async()=>{calls++;};
          const operation=$('settings-disconnect').onclick();assert.equal(calls,0);
          $('action-cancel').onclick();await operation;
          assert.equal(calls,0);assert.equal(active.state,'running');assert.equal($('prompt').value,'draft');
          assert.equal($('settings-dialog').open,true);assert.equal(stopState(),null);
        })()''')


if __name__ == '__main__':
    unittest.main()
