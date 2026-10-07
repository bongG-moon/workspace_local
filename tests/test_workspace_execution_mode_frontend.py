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

    def test_launcher_version_is_separate_from_actual_privilege_and_no_startup_network(self):
        self.run_case("""
          let calls=0;WorkspaceExecutionMode.configure({api:async()=>{calls++;}});
          WorkspaceExecutionMode.start('normal',{appVersion:'0.23.21',entryVersion:'0.23.19',repairRecommended:true});
          assert.match($('execution-mode-current').textContent,/일반/);
          assert.match($('launcher-version-info').textContent,/0.23.21.*0.23.19/);
          assert.match($('launcher-migration-note').textContent,/예전 EXE/);
          assert.equal(calls,0);assert.equal(timers.size,0);
          WorkspaceExecutionMode.start('administrator',{appVersion:'0.23.21',entryVersion:'0.23.21',repairRecommended:false});
          assert.match($('execution-mode-current').textContent,/관리자/);
          assert.match($('launcher-migration-note').textContent,/Windows/);
        """, modules=('execution-mode',))

    def test_prepare_shortcut_is_explicit_single_flight_and_uses_get_for_progress(self):
        self.run_case("""
          const calls=[];let resolveFirst;
          const request=async(path,body,signal)=>{calls.push({path,body,signal});
            if(calls.length===1)return await new Promise(resolve=>{resolveFirst=resolve;});
            return {launcher:{status:'ready',message:'바로가기 완료'}};};
          WorkspaceExecutionMode.configure({api:request});WorkspaceExecutionMode.configure({api:request});
          emit($('launcher-prepare'),'click');emit($('launcher-prepare'),'click');
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/launcher/prepare');
          assert.equal(JSON.stringify(calls[0].body),'{}');assert.equal($('launcher-prepare').disabled,true);
          resolveFirst({launcher:{status:'preparing'}});await settle();
          assert.equal(timers.size,1);flushTimers();await settle();
          assert.equal(calls.length,2);assert.equal(calls[1].path,'/api/app-update');
          assert.equal(calls[1].body,undefined);assert.equal($('launcher-prepare').disabled,false);
          assert.equal($('launcher-prepare-message').textContent,'바로가기 완료');assert.equal(timers.size,0);
        """, modules=('execution-mode',))

    def test_prepare_error_restores_button_without_touching_draft_or_privilege(self):
        self.run_case("""
          $('prompt').value='작성 중 질문';WorkspaceExecutionMode.start('normal');
          WorkspaceExecutionMode.configure({api:async()=>{throw new Error('현재 버전 미게시');}});
          emit($('launcher-prepare'),'click');await settle();
          assert.equal($('launcher-prepare').disabled,false);
          assert.match($('launcher-prepare-message').textContent,/미게시/);
          assert.equal($('prompt').value,'작성 중 질문');assert.equal($('execution-mode-current').dataset.mode,'normal');
          assert.equal(timers.size,0);
        """, modules=('execution-mode',))
