"""Employee registration gates real UI update actions without routine collection."""
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT
from tests.test_workspace_app_updates_frontend import AVAILABLE, CURRENT


@unittest.skipUnless(NODE, 'Node.js is required for registration UI checks')
class RegistrationFrontendTests(unittest.TestCase):
    def run_case(self, scenario, registration=None, update=CURRENT, before_start=''):
        registration = registration or dict(enabled=True, employeeRegistered=False, needsEmployee=True)
        bootstrap = dict(sessions=[], demo=False, appRegistration=registration, appUpdate=update)
        harness = HARNESS.replace(
            'const nodes=new Map();',
            'Element.prototype.removeAttribute=function(name){delete this.attributes[name];};'
            'const registrationEvents=new Map(),registrationRequests=[];const nodes=new Map();',
        ).replace(
            'const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,',
            'const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,registrationRequests,',
        ).replace(
            'querySelector:selector=>get(selector),querySelectorAll:()=>[],addEventListener(){}},',
            "querySelector:selector=>selector==='dialog[open]'?[...nodes.values()].find(node=>node.open)||null:get(selector),"
            "querySelectorAll:selector=>selector==='dialog[open]'?[...nodes.values()].filter(node=>node.open):[],"
            'addEventListener:(name,fn)=>registrationEvents.set(name,fn),'
            'removeEventListener:(name,fn)=>{if(registrationEvents.get(name)===fn)registrationEvents.delete(name);}},',
        ).replace(
            'fetch:async()=>({ok:true,json:async()=>({sessions:[],demo:false})}),',
            'fetch:async(path,options)=>{registrationRequests.push({path,data:options?.body?JSON.parse(options.body):null});'
            'return {ok:true,json:async()=>path===\'/api/app-update\'?' + json.dumps(update)
            + ':' + json.dumps(bootstrap) + '};},',
        ).replace(
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'registration.js'});"
            "vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context,{filename:'app-updates.js'});"
            "vm.runInContext(" + json.dumps(before_start) + ",context,{filename:'before-start.js'});"
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
        )
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), scenario,
             str(ROOT / 'local_app/web/registration.js'), str(ROOT / 'local_app/web/app-updates.js')],
            input=harness, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_initial_prompt_has_exact_notice_and_no_extra_registration_request(self):
        self.run_case(r"""
          assert.equal($('registration-dialog').open,true);
          assert.equal(document.activeElement,$('registration-input'));
          assert.equal($('registration-save').disabled,true);
          assert.equal($('registration-notice').textContent,'설치 또는 업데이트가 완료되면 결과를 한 번 전송합니다.\n이 집계 기능은 평소 앱 사용 중 사용 정보를 수집하지 않습니다.');
          assert.equal(registrationRequests.filter(row=>row.path==='/api/app-registration').length,0);
        """)

    def test_registered_user_has_no_prompt_or_update_registration_text(self):
        self.run_case(r"""(async()=>{
          assert.notEqual($('registration-dialog').open,true);
          assert.equal($('app-update-registration').hidden,true);
          assert.equal($('app-update-dialog').open,true);
          const calls=[];api=async(path,data)=>{calls.push({path,data});return {...available,status:'downloading'};};
          await $('app-update-install').onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/app-update');
          assert.equal(calls[0].data.action,'install');
        })()""".replace('const calls=[];', 'const available=' + json.dumps(AVAILABLE) + ';const calls=[];'),
                      dict(enabled=True, employeeRegistered=True, needsEmployee=False), AVAILABLE)

    def test_disabled_configuration_has_no_registration_surface_or_request(self):
        self.run_case(r"""
          assert.notEqual($('registration-dialog').open,true);
          assert.equal($('registration-settings').hidden,true);
          assert.equal($('app-update-registration').hidden,true);
          assert.equal(registrationRequests.filter(row=>row.path==='/api/app-registration').length,0);
        """, dict(enabled=False, employeeRegistered=False, needsEmployee=False))

    def test_portable_does_not_auto_prompt_and_missing_id_is_inline_for_explicit_update(self):
        self.run_case(r"""(async()=>{
          assert.notEqual($('registration-dialog').open,true);
          assert.equal($('app-update-dialog').open,true);
          assert.equal($('app-update-registration').hidden,false);
          const calls=[];api=async(path,data)=>{calls.push({path,data});return {};};
          await $('app-update-install').onclick();
          assert.equal(calls.length,0);
          assert.equal(document.activeElement,$('app-update-registration-input'));
          assert.equal($('app-update-registration-error').textContent,'사번을 입력해 주세요.');
        })()""", dict(enabled=True, employeeRegistered=False, needsEmployee=False), AVAILABLE)

    def test_unicode_length_and_python_whitespace_match_server_and_key_is_never_requested(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push({path,data});return {enabled:true,employeeRegistered:true,needsEmployee:false};};
          $('registration-input').value='😀'.repeat(9);$('registration-input').listeners.input[0]();
          assert.equal($('registration-save').disabled,true);
          assert.equal($('registration-count').textContent,'현재 9자');
          $('registration-input').value='\u0085  😀😀😀😀😀😀😀😀 \u001c';$('registration-input').listeners.input[0]();
          assert.equal($('registration-save').disabled,false);
          await $('registration-form').listeners.submit[0]({preventDefault(){}});
          for(let i=0;i<8;i++)await Promise.resolve();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/app-registration');
          assert.deepEqual(Object.keys(calls[0].data),['employeeId']);
          assert.equal(calls[0].data.employeeId,'😀'.repeat(8));
          assert.equal($('registration-input').value,'');assert.equal($('registration-dialog').open,false);
        })()""")

    def test_failed_save_preserves_input_error_and_allows_retry_without_update(self):
        self.run_case(r"""(async()=>{
          $('registration-dialog').close();$('app-update-sidebar-badge').onclick();
          const calls=[];api=async(path,data)=>{calls.push({path,data});throw Error('로컬 저장을 확인해 주세요.');};
          $('app-update-registration-input').value=' 000123 ';
          await $('app-update-install').onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/app-registration');
          assert.equal($('app-update-registration-input').value,' 000123 ');
          assert.equal($('app-update-registration-error').textContent,'로컬 저장을 확인해 주세요.');
          assert.equal($('app-update-install').disabled,false);
        })()""", update=AVAILABLE)

    def test_successful_inline_save_then_one_install_and_no_second_modal(self):
        self.run_case(r"""(async()=>{
          const available=AVAILABLE;
          $('registration-dialog').close();$('app-update-sidebar-badge').onclick();
          const calls=[];api=async(path,data)=>{calls.push({path,data});return path==='/api/app-registration'
            ?{enabled:true,employeeRegistered:true,needsEmployee:false}:{...available,status:'downloading'};};
          $('app-update-registration-input').value=' 000123 ';
          await $('app-update-install').onclick();
          assert.equal(calls.length,2);assert.equal(calls[0].path,'/api/app-registration');
          assert.equal(calls[0].data.employeeId,'000123');assert.equal(calls[1].path,'/api/app-update');
          assert.equal(calls[1].data.action,'install');assert.notEqual($('registration-dialog').open,true);
          assert.equal($('app-update-registration').hidden,true);
        })()""".replace('AVAILABLE', json.dumps(AVAILABLE)), update=AVAILABLE)

    def test_double_click_and_closed_update_dialog_never_dispatch_stale_install(self):
        self.run_case(r"""(async()=>{
          $('registration-dialog').close();$('app-update-sidebar-badge').onclick();
          const calls=[];let resolveSave;api=(path,data)=>{calls.push({path,data});return new Promise(resolve=>resolveSave=resolve);};
          $('app-update-registration-input').value='1234';
          const first=$('app-update-install').onclick();const second=$('app-update-install').onclick();
          assert.equal(calls.length,1);assert.equal($('app-update-install').disabled,true);
          $('app-update-dialog').close();
          resolveSave({enabled:true,employeeRegistered:true,needsEmployee:false});
          await first;await second;assert.equal(calls.length,1);
        })()""", update=AVAILABLE)

    def test_closed_then_reopened_update_dialog_does_not_revive_install_intent(self):
        self.run_case(r"""(async()=>{
          const available=AVAILABLE;
          $('registration-dialog').close();$('app-update-sidebar-badge').onclick();
          const calls=[];let resolveSave;api=(path,data)=>{calls.push({path,data});return path==='/api/app-registration'
            ?new Promise(resolve=>resolveSave=resolve):Promise.resolve({...available,status:'downloading'});};
          $('app-update-registration-input').value='1234';const first=$('app-update-install').onclick();
          $('app-update-dismiss').onclick();$('app-update-sidebar-badge').onclick();
          resolveSave({enabled:true,employeeRegistered:true,needsEmployee:false});await first;
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/app-registration');
          assert.equal($('app-update-install').disabled,false);
        })()""".replace('AVAILABLE', json.dumps(AVAILABLE)), update=AVAILABLE)

    def test_cancel_does_not_reopen_and_settings_changes_never_display_saved_id(self):
        self.run_case(r"""(async()=>{
          $('registration-later').onclick();assert.equal($('registration-dialog').open,false);
          WorkspaceRegistration.contextChanged();await Promise.resolve();
          assert.equal($('registration-dialog').open,false);
          WorkspaceRegistration.observe({enabled:true,employeeRegistered:true,needsEmployee:false});
          $('settings-dialog').showModal();$('registration-settings-open').onclick();
          assert.equal($('registration-title').textContent,'사번 변경');
          assert.equal($('registration-input').value,'');assert.equal($('registration-dialog').open,true);
        })()""")

    def test_first_prompt_waits_for_existing_dialog_and_update_offer_yields(self):
        self.run_case(r"""(async()=>{
          assert.notEqual($('registration-dialog').open,true);
          assert.notEqual($('app-update-dialog').open,true);
          $('action-dialog').close();WorkspaceRegistration.contextChanged();await Promise.resolve();
          assert.equal($('registration-dialog').open,true);
          assert.notEqual($('app-update-dialog').open,true);
        })()""", update=AVAILABLE,
                      before_start="document.getElementById('action-dialog').showModal();")

    def test_handoff_delays_first_prompt_and_focus_events_cannot_reopen_it(self):
        self.run_case(r"""(async()=>{
          assert.notEqual($('registration-dialog').open,true);
          WorkspaceUpgrade.isLocked=()=>false;
          WorkspaceRegistration.contextChanged();await Promise.resolve();
          assert.equal($('registration-dialog').open,true);
          $('registration-input').value='아직 작성 중';
          WorkspaceRegistration.contextChanged();await Promise.resolve();
          assert.equal($('registration-input').value,'아직 작성 중');
          $('registration-later').onclick();
          WorkspaceRegistration.contextChanged();await Promise.resolve();
          assert.equal($('registration-dialog').open,false);
        })()""", before_start="globalThis.WorkspaceUpgrade={attach(){},begin(){return ()=>{};},canStopForUpdate:()=>false,bootstrap:async()=>{},isLocked:()=>true};")

    def test_shutdown_clears_private_input_and_ignores_delayed_save_reply(self):
        self.run_case(r"""(async()=>{
          let resolveSave;api=()=>new Promise(resolve=>resolveSave=resolve);
          $('registration-input').value='1234';$('registration-input').listeners.input[0]();
          $('registration-form').listeners.submit[0]({preventDefault(){}});
          WorkspaceRegistration.stop();assert.equal($('registration-input').value,'');
          resolveSave({enabled:true,employeeRegistered:true,needsEmployee:false});
          for(let i=0;i<8;i++)await Promise.resolve();
          assert.equal($('registration-dialog').open,false);assert.equal($('registration-input').value,'');
        })()""")


if __name__ == '__main__':
    unittest.main()
