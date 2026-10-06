import unittest
from tests import test_workspace_productivity_frontend as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for execution settings UI checks')
class ExecutionModeFrontendTests(unittest.TestCase):
    def run_case(self, script, modules=()):
        return frontend.WorkspaceProductivityFrontendTests.run_case(self, '(async()=>{' + script + '})()', modules)
    def test_general_mode_has_no_restart_or_background_poll_without_settings(self):
        self.run_case("""
          const calls=[];
          WorkspaceExecutionMode.attach({api:async(...args)=>{calls.push(args);return {current:'normal',supported:true,state:'idle'};},confirm:async()=>true});
          WorkspaceExecutionMode.start({current:'normal',supported:true,state:'idle'});
          assert.equal($('execution-mode-select').value,'normal');
          assert.equal($('execution-mode-apply').disabled,true);
          assert.equal(calls.length,0);assert.equal(timers.size,0);
        """,modules=('execution-mode',))

    def test_admin_confirm_cancel_does_not_request_restart(self):
        self.run_case("""
          const calls=[];
          WorkspaceExecutionMode.attach({api:async(...args)=>calls.push(args),confirm:async()=>false});
          WorkspaceExecutionMode.start({current:'normal',supported:true,state:'idle'});
          $('execution-mode-select').value='administrator';$('execution-mode-select').onchange();
          await $('execution-mode-apply').onclick();
          assert.equal(calls.length,0);assert.equal($('execution-mode-apply').disabled,false);
          assert.match($('execution-mode-description').textContent,/Claude/);
        """,modules=('execution-mode',))

    def test_selection_restart_does_not_send_work_and_closes_settings_for_capture(self):
        self.run_case("""
          const calls=[];
          WorkspaceExecutionMode.attach({api:async(...args)=>{calls.push(args);return {current:'normal',supported:true,state:'requesting',target:'administrator'};},confirm:async()=>true});
          WorkspaceExecutionMode.start({current:'normal',supported:true,state:'idle'});
          $('settings-dialog').open=true;
          $('execution-mode-select').value='administrator';$('execution-mode-select').onchange();
          await $('execution-mode-apply').onclick();
          assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/execution-mode');
          assert.equal(calls[0][1].mode,'administrator');assert.equal($('settings-dialog').open,false);
          assert.equal($('execution-mode-select').disabled,true);assert.equal($('execution-mode-apply').disabled,true);
          assert.match($('execution-mode-current').textContent,/일반/);
        """,modules=('execution-mode',))

    def test_normal_return_does_not_change_claude_approval_mode(self):
        self.run_case("""
          const calls=[];let confirms=0;
          WorkspaceExecutionMode.attach({api:async(...args)=>{calls.push(args);return {current:'administrator',supported:true,state:'requesting',target:'normal'};},confirm:async()=>{confirms++;return true;}});
          WorkspaceExecutionMode.start({current:'administrator',supported:true,state:'idle'});
          $('execution-mode-select').value='normal';$('execution-mode-select').onchange();
          await $('execution-mode-apply').onclick();
          assert.equal(confirms,0);assert.equal(calls[0][1].mode,'normal');
          assert.equal(calls.length,1);
        """,modules=('execution-mode',))
