"""Shutdown feedback follows the server acknowledgement, not the button click."""
import unittest
import json
import subprocess

import test_workspace_frontend_state as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for shipped UI checks')
class WorkspaceShutdownFrontendTests(unittest.TestCase):
    run_case = frontend.WorkspaceFrontendStateTests.run_case

    def run_bootstrap_case(self, bootstrap, javascript):
        harness = frontend.HARNESS.replace(
            "json:async()=>({sessions:[],demo:false})",
            "json:async()=>({})".format(json.dumps(bootstrap)),
        )
        self.assertNotEqual(harness, frontend.HARNESS)
        result = subprocess.run(
            [frontend.NODE, '-', str(frontend.ROOT / 'local_app/web/app.js'), javascript],
            input=harness, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_initial_failed_bootstrap_blocks_work_and_shows_recovery(self):
        self.run_bootstrap_case({
            'sessions': [], 'demo': False, 'closing': True, 'closed': False,
            'shutdownState': 'failed',
            'shutdownIssues': [{'sessionId': 'A', 'code': 'descendants_unverified'}],
        }, r'''(async()=>{
          assert.equal(appClosed,true);assert.equal(quitting,false);
          assert.equal($('shutdown-banner').hidden,false);
          assert.equal($('shutdown-banner').dataset.state,'failed');
          assert.equal($('shutdown-retry').textContent,'종료 다시 시도');
          assert.equal($('shutdown-retry').disabled,false);
          assert.match($('shutdown-detail').textContent,/일부 작업/);
          assert.equal($('status-text').textContent,'종료 확인 필요');
          assert.equal($('connection-badge').textContent,'종료 확인 필요');
          assert.equal($('send').disabled,true);assert.equal($('prompt').readOnly,true);
          $('prompt').value='keep draft';attachments=['keep.csv'];
          active={id:'A',state:'idle',connection:{capabilities:{setModel:true,setPermissionMode:true,setEffort:true}}};
          let calls=0;api=async()=>{calls++;throw new Error('must not call');};
          await submit();await reconnect();await $('stop').onclick();
          await setModel('other');await setPermissionMode(null);await setEffort('high');
          await $('attach').onclick();await $('attach-path').onclick();
          await $('new-chat').onclick();assert.equal(await selectSession('A'),false);
          assert.equal(calls,0);assert.equal($('prompt').value,'keep draft');
          assert.deepEqual(attachments,['keep.csv']);
          setStatus('idle');renderConnection(null);
          assert.equal($('status-text').textContent,'종료 확인 필요');
          assert.equal($('connection-badge').textContent,'종료 확인 필요');
          assert.equal($('send').disabled,true);
        })()''')

    def test_recovery_retry_rejects_then_confirms_only_after_server_ack(self):
        self.run_bootstrap_case({
            'sessions': [], 'demo': False, 'closing': True, 'closed': False,
            'shutdownState': 'failed',
        }, r'''(async()=>{
          $('prompt').value='keep draft';attachments=['keep.csv'];
          let calls=0,resolveReply,rejectReply;
          api=(path,data)=>{assert.equal(path,'/api/quit');assert.deepEqual(data,{});calls++;return new Promise((resolve,reject)=>{resolveReply=resolve;rejectReply=reject;});};
          const first=$('shutdown-retry').onclick();
          assert.equal(calls,1);assert.equal(pendingConfirmation,null);
          assert.equal($('shutdown-retry').disabled,true);
          assert.equal($('shutdown-banner').dataset.state,'closing');
          assert.doesNotMatch($('shutdown-text').textContent,/종료가 완료/);
          await $('shutdown-retry').onclick();assert.equal(calls,1);
          rejectReply(new Error('raw process details must not be displayed'));await first;
          assert.equal($('shutdown-retry').disabled,false);
          assert.equal($('shutdown-retry').textContent,'종료 다시 시도');
          assert.equal($('shutdown-banner').dataset.state,'failed');
          assert.doesNotMatch($('shutdown-text').textContent,/raw process/);
          assert.equal($('send').disabled,true);assert.equal(appClosed,true);
          const second=$('shutdown-retry').onclick();assert.equal(calls,2);
          assert.notEqual($('status-text').textContent,'앱 종료 완료');
          resolveReply({ok:true,closed:true});await second;
          assert.equal($('status-text').textContent,'앱 종료 완료');
          assert.equal($('shutdown-retry').hidden,true);
          assert.equal($('shutdown-banner').dataset.state,'closed');
          assert.match($('shutdown-text').textContent,/실행기로 다시/);
          assert.equal($('prompt').value,'keep draft');
          assert.deepEqual(drafts.get('home'),{text:'keep draft',attachments:['keep.csv']});
        })()''')

    def test_closing_bootstrap_checks_status_manually_without_restarting_work(self):
        self.run_bootstrap_case({
            'sessions': [], 'demo': False, 'closing': True, 'closed': False,
            'shutdownState': 'closing',
        }, r'''(async()=>{
          assert.equal(appClosed,true);assert.equal($('send').disabled,true);
          assert.equal($('shutdown-retry').textContent,'종료 상태 확인');
          assert.notEqual($('status-text').textContent,'앱 종료 완료');
          let calls=0;api=async(path,data)=>{calls++;assert.equal(path,'/api/bootstrap');assert.equal(data,undefined);return {closing:true,closed:false,shutdownState:'failed'};};
          await $('shutdown-retry').onclick();assert.equal(calls,1);
          assert.equal($('shutdown-retry').textContent,'종료 다시 시도');
          assert.equal($('shutdown-retry').disabled,false);assert.equal(appClosed,true);
          assert.equal($('send').disabled,true);
          const outcome=await restoreScreenRecovery({sessionId:'A',drafts:[{id:'A',text:'restored draft',attachments:['draft.csv']}]});
          assert.equal($('prompt').value,'restored draft');assert.equal(calls,1);
          assert.equal($('prompt').readOnly,true);
        })()''')

    def test_closed_bootstrap_never_starts_a_ready_app(self):
        self.run_bootstrap_case({
            'sessions': [], 'demo': False, 'closing': True, 'closed': True,
            'shutdownState': 'closed',
        }, r'''(async()=>{
          assert.equal(appClosed,true);assert.equal($('send').disabled,true);
          assert.equal($('status-text').textContent,'앱 종료 완료');
          assert.equal($('shutdown-retry').hidden,true);
          assert.equal($('quit').disabled,true);
          let calls=0;api=async()=>{calls++;};
          await $('quit').onclick();assert.equal(calls,0);
        })()''')

    def test_shutdown_waits_for_cleanup_and_closes_settings(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'running'};sessions=[{...active}];
          $('settings-dialog').showModal();pollController=new AbortController();
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve);};
          const pending=$('quit').onclick();
          assert.equal(calls,0);$('action-confirm').onclick();await Promise.resolve();
          assert.equal($('settings-dialog').open,false);
          assert.equal(pollController.signal.aborted,true);
          assert.equal($('quit').disabled,true);assert.equal($('send').disabled,true);
          assert.equal($('prompt').readOnly,true);
          assert.match($('status-text').textContent,/종료하고/);
          assert.doesNotMatch($('error-banner').textContent,/종료가 완료/);
          await $('quit').onclick();assert.equal(calls,1);
          reply({ok:true,closed:true});await pending;
          assert.equal($('status-text').textContent,'앱 종료 완료');
          assert.match($('error-banner').textContent,/실행기로 다시/);
        })()''')

    def test_old_server_ack_does_not_claim_confirmed_exit(self):
        self.run_case(r'''(async()=>{
          api=async()=>({ok:true});
          const pending=$('quit').onclick();$('action-confirm').onclick();await pending;
          assert.equal($('status-text').textContent,'종료 요청 전달됨');
          assert.doesNotMatch($('error-banner').textContent,/종료가 완료/);
          assert.equal(appClosed,true);
        })()''')

    def test_failed_shutdown_is_visible_and_can_be_retried(self):
        self.run_case(r'''(async()=>{
          api=async()=>{throw new Error('uncertain close');};
          showDialog('settings-dialog');const pending=$('quit').onclick();$('action-confirm').onclick();await pending;
          assert.equal($('settings-dialog').open,false);
          assert.equal($('status-text').textContent,'종료 확인 필요');
          assert.equal($('quit').disabled,false);
          assert.equal($('send').disabled,true);
          assert.equal(quitting,false);
        })()''')

    def test_cancel_does_not_change_connection_or_draft(self):
        self.run_case(r'''(async()=>{
          $('prompt').value='keep draft';
          $('settings-dialog').showModal();
          api=async()=>{throw new Error('must not call');};
          const pending=$('quit').onclick();$('action-cancel').onclick();await pending;
          assert.equal($('settings-dialog').open,true);
          assert.equal($('prompt').value,'keep draft');
          assert.equal(appClosed,false);assert.equal(quitting,false);
        })()''')

    def test_native_cancel_keeps_work_and_consumes_only_confirmation(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'running'};sessions=[{...active}];
          $('prompt').value='keep my draft';attachments=['file.txt'];
          const id='a'.repeat(32),calls=[];api=async(path,data)=>{calls.push([path,data]);return {ok:true,cancelled:true};};
          assert.equal(confirmShutdownChallenge('invalid'),false);
          assert.equal(confirmShutdownChallenge(id),true);
          assert.equal(confirmShutdownChallenge(id),true);
          assert.match($('action-title').textContent,/작업 중인 내용/);
          assert.equal($('action-confirm').textContent,'종료');
          assert.equal(calls.length,0);assert.equal(appClosed,false);
          $('action-cancel').onclick();await Promise.resolve();await Promise.resolve();
          assert.deepEqual(calls,[['/api/quit',{confirmed:false,confirmationId:id}]]);
          assert.equal(appClosed,false);assert.equal(active.state,'running');
          assert.equal($('prompt').value,'keep my draft');assert.deepEqual(attachments,['file.txt']);
        })()''')

    def test_native_confirm_waits_for_cleanup_ack(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'approval'};sessions=[{...active}];
          const id='b'.repeat(32);let resolveReply,calls=0;
          api=(path,data)=>{assert.equal(path,'/api/quit');assert.deepEqual(data,{confirmed:true,confirmationId:id});calls++;return new Promise(resolve=>resolveReply=resolve);};
          assert.equal(confirmShutdownChallenge(id),true);$('action-confirm').onclick();await Promise.resolve();
          assert.equal(calls,1);assert.equal(quitting,true);
          assert.equal($('action-dialog').open,true);assert.equal($('action-cancel').disabled,true);
          assert.equal(appClosed,false);assert.notEqual(shutdownState,'closed');
          resolveReply({ok:true,closed:true,shutdownState:'closed'});
          await Promise.resolve();await Promise.resolve();await Promise.resolve();
          assert.equal(appClosed,true);assert.equal(shutdownState,'closed');
          assert.equal($('action-dialog').open,false);assert.equal(quitting,false);
        })()''')

    def test_expired_native_confirmation_leaves_live_app_usable(self):
        self.run_case(r'''(async()=>{
          active={id:'A',state:'running'};sessions=[{...active}];$('prompt').value='preserved';
          api=async()=>{throw Object.assign(new Error('expired'),{code:'quit_confirmation_expired',closing:false});};
          assert.equal(confirmShutdownChallenge('c'.repeat(32)),true);$('action-confirm').onclick();
          await Promise.resolve();await Promise.resolve();await Promise.resolve();
          assert.equal(appClosed,false);assert.equal(quitting,false);assert.equal(shutdownState,null);
          assert.equal($('action-dialog').open,false);assert.equal($('prompt').value,'preserved');
          assert.match($('toast').textContent,/확인 시간이/);
        })()''')

    def test_native_quit_never_replaces_an_existing_confirmation(self):
        self.run_case(r'''(async()=>{
          const original=confirmAction({title:'existing question',message:'keep this',confirmLabel:'keep'});
          assert.equal(confirmShutdownChallenge('d'.repeat(32)),false);
          assert.equal($('action-title').textContent,'existing question');
          $('action-cancel').onclick();await original;assert.equal(appClosed,false);
        })()''')


if __name__ == '__main__':
    unittest.main()
