import unittest
from tests import test_workspace_productivity_frontend as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for execution privilege display checks')
class ExecutionModeFrontendTests(unittest.TestCase):
    def run_case(self, script, modules=()):
        return frontend.WorkspaceProductivityFrontendTests.run_case(self, '(async()=>{' + script + '})()', modules)

    def test_privilege_label_reads_verified_bootstrap_without_polling_or_switch_handlers(self):
        self.run_case("""
          let calls=0;api=async()=>{calls++;};
          WorkspaceExecutionMode.start('normal');
          assert.match($('execution-mode-current').textContent,/일반/);
          assert.equal($('execution-mode-current').dataset.mode,'normal');
          WorkspaceExecutionMode.start('administrator');
          assert.match($('execution-mode-current').textContent,/관리자/);
          assert.equal($('execution-mode-current').dataset.mode,'administrator');
          openSettings();$('settings-dialog').close();openSettings();
          assert.equal(calls,0);assert.equal(timers.size,0);
          assert.equal(typeof WorkspaceExecutionMode.attach,'undefined');
          assert.equal(typeof WorkspaceExecutionMode.settingsOpened,'undefined');
        """, modules=('execution-mode',))

    def test_unknown_privilege_never_claims_administrator_or_changes_any_task(self):
        self.run_case("""
          $('prompt').value='작성 중 요청';attachments=['C:/work/report.csv'];
          $('settings-dialog').open=true;
          const before=JSON.stringify(active);
          WorkspaceExecutionMode.start({current:'administrator',state:'requesting'});
          assert.equal($('execution-mode-current').dataset.mode,'unknown');
          assert.match($('execution-mode-current').textContent,/확인하지 못/);
          assert.equal($('settings-dialog').open,true);assert.equal(JSON.stringify(active),before);
          assert.equal($('prompt').value,'작성 중 요청');assert.equal(attachments[0],'C:/work/report.csv');
          assert.equal(timers.size,0);
        """, modules=('execution-mode',))
