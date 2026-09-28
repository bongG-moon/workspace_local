"""Shutdown feedback follows the server acknowledgement, not the button click."""
import unittest

import test_workspace_frontend_state as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for shipped UI checks')
class WorkspaceShutdownFrontendTests(unittest.TestCase):
    run_case = frontend.WorkspaceFrontendStateTests.run_case

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


if __name__ == '__main__':
    unittest.main()
