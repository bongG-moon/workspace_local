"""ZIP import is explicit, lazy and isolated from the active conversation."""
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, "Node.js is required for frontend import checks")
class WorkspaceExtensionImportFrontendTests(unittest.TestCase):
    def test_new_confirmation_during_pending_fetch_defers_without_cancelling(self):
        self.run_case("""(async()=>{
          $('settings-dialog').open=false;let allowed=true,resolve;
          WorkspaceExtensionImport.attach({api:async()=>new Promise(r=>resolve=r),
            openDialog:id=>$(id).showModal(),isClosed:()=>false,canOpenExternal:()=>allowed});
          const pending=WorkspaceExtensionImport.openPending('incoming');await flush();
          allowed=false;resolve(preview);assert.equal(await pending,false);
          assert.ok(!$('extension-import-dialog').open);
          assert.equal(WorkspaceExtensionImport.isBusy(),false);
          allowed=true;const retry=WorkspaceExtensionImport.openPending('incoming');await flush();
          resolve(preview);assert.equal(await retry,true);
          assert.equal($('extension-import-dialog').open,true);
        })()""")

    def test_file_open_review_works_without_settings_and_never_auto_installs(self):
        self.run_case("""(async()=>{
          $('settings-dialog').open=false;responder=async()=>preview;
          assert.equal(await WorkspaceExtensionImport.openPending('file-open'),true);
          assert.equal(calls[0].path,'/api/extensions/import/pending');
          assert.equal($('extension-import-dialog').open,true);
          assert.equal(calls.filter(x=>x.path.endsWith('/confirm')).length,0);
          WorkspaceExtensionImport.settingsClosed();
          assert.equal($('extension-import-dialog').open,true);
          assert.equal(draft.text,'작성 중인 요청');assert.equal(queue.length,2);
          await $('extension-import-cancel').onclick();await flush();
          assert.equal(calls[1].path,'/api/extensions/import/cancel');
          assert.equal(WorkspaceExtensionImport.isBusy(),false);
        })()""")

    def run_case(self, scenario, *, app=False):
        setup = """
          const $=id=>document.getElementById(id),calls=[];
          $('settings-dialog').tagName='DIALOG';$('settings-dialog').open=true;
          $('extension-import-dialog').tagName='DIALOG';
          const draft={text:'작성 중인 요청',attachments:['keep.pdf']},queue=['one','two'];
          let closed=false,refreshes=0,refreshError=null;
          const preview={token:'one-use',extension:{id:'sample.store',name:'Feature store',version:'1.2.3'},
            destination:'C:/Users/Example/Extensions/sample.store',packageSha256:'a'.repeat(64),
            serverUrl:'http://store.example.test',warnings:['HTTP 연결에서는 KEY가 암호화되지 않습니다.']};
          let responder=async path=>path.endsWith('/prepare')?preview:path.endsWith('/confirm')?{installed:true}:{};
          WorkspaceExtensionImport.attach({api:async(path,data)=>{calls.push({path,data});return responder(path,data);},
            openDialog:id=>$(id).showModal(),isClosed:()=>closed,
            refresh:async()=>{refreshes++;if(refreshError)throw refreshError;}});
          const flush=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
          const open=()=>WorkspaceExtensionImport.open();
        """
        result = subprocess.run(
            [NODE, "-", str(ROOT / ("local_app/web/app.js" if app else "local_app/web/extension-import.js")),
             ("" if app else setup) + scenario],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_initial_request_and_cancelled_picker_does_not_install(self):
        self.run_case("""(async()=>{
          assert.equal(calls.length,0);responder=async()=>({cancelled:true});await open();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/extensions/import/prepare');
          assert.equal(JSON.stringify(calls[0].data),'{}');assert.equal(refreshes,0);
          assert.equal(WorkspaceExtensionImport.isBusy(),false);assert.equal($('extension-import-open').disabled,false);
          assert.ok(!$('extension-import-dialog').open);
        })()""")

    def test_review_uses_text_and_shows_origin_warnings_before_explicit_install(self):
        self.run_case("""(async()=>{
          preview.extension.name='<img src=x onerror=alert(1)>';preview.replacing=true;await open();
          assert.equal($('extension-import-name').textContent,preview.extension.name);
          assert.equal($('extension-import-name').children.length,0);
          assert.equal($('extension-import-destination').textContent,preview.destination);
          assert.equal($('extension-import-server').textContent,preview.serverUrl);
          assert.equal($('extension-import-warnings').children[0].textContent,preview.warnings[0]);
          assert.equal($('extension-import-warnings').hidden,false);
          assert.equal($('extension-import-confirm').textContent,'업데이트');assert.equal(calls.length,1);
          assert.equal(document.activeElement,$('extension-import-cancel'));
          await $('extension-import-confirm').onclick();
          assert.equal(calls.length,2);assert.equal(JSON.stringify(calls[1].data),JSON.stringify({token:'one-use',confirmed:true}));
          assert.equal(refreshes,1);assert.equal($('extension-import-dialog').open,false);
          assert.match($('extension-import-status').textContent,/설치를 완료/);
          assert.equal(draft.text,'작성 중인 요청');assert.equal(draft.attachments[0],'keep.pdf');assert.equal(queue.length,2);
        })()""")

    def test_close_cancel_and_escape_discard_each_preview_exactly_once(self):
        self.run_case("""(async()=>{
          for(const close of [()=>$('extension-import-close').onclick(),()=>$('extension-import-cancel').onclick(),
              ()=>$('extension-import-dialog').oncancel({preventDefault(){}}),()=>$('extension-import-dialog').close()]){
            await open();close();close();await flush();
            assert.equal($('extension-import-dialog').open,false);assert.equal(WorkspaceExtensionImport.isBusy(),false);
          }
          assert.equal(calls.filter(x=>x.path.endsWith('/cancel')).length,4);
          assert.equal(calls.filter(x=>x.path.endsWith('/confirm')).length,0);
        })()""")

    def test_prepare_and_confirm_double_clicks_are_single_requests(self):
        self.run_case("""(async()=>{
          let reply;responder=async()=>new Promise(resolve=>reply=resolve);
          const waiting=open();await open();assert.equal(calls.length,1);reply(preview);await waiting;
          const installing=$('extension-import-confirm').onclick();await $('extension-import-confirm').onclick();
          assert.equal(calls.length,2);assert.equal($('extension-import-cancel').disabled,true);
          $('extension-import-dialog').oncancel({preventDefault(){}});assert.equal($('extension-import-dialog').open,true);
          reply({installed:true});await installing;assert.equal(refreshes,1);assert.equal(calls.length,2);
        })()""")

    def test_delayed_picker_after_settings_close_is_discarded_without_review(self):
        self.run_case("""(async()=>{
          let reply;responder=async path=>path.endsWith('/prepare')?new Promise(resolve=>reply=resolve):{};
          const waiting=open();$('settings-dialog').close();WorkspaceExtensionImport.settingsClosed();
          reply(preview);await waiting;assert.ok(!$('extension-import-dialog').open);
          assert.equal(calls.filter(x=>x.path.endsWith('/cancel')).length,1);
          assert.equal(refreshes,0);assert.equal(WorkspaceExtensionImport.isBusy(),false);
        })()""")

    def test_prepare_error_is_recoverable_and_never_installs(self):
        self.run_case("""(async()=>{
          responder=async()=>{throw Error('파일 형식을 확인해 주세요.');};await open();
          assert.match($('extension-import-status').textContent,/파일 형식/);
          assert.equal($('extension-import-open').disabled,false);
          responder=async()=>preview;await open();assert.equal($('extension-import-dialog').open,true);
          assert.equal(calls.filter(x=>x.path.endsWith('/confirm')).length,0);
        })()""")

    def test_failed_confirm_is_not_retried_and_close_reenables_new_import(self):
        self.run_case("""(async()=>{
          await open();responder=async path=>{if(path.endsWith('/confirm'))throw Error('연결이 끊겼어요.');return {};};
          await $('extension-import-confirm').onclick();await $('extension-import-confirm').onclick();await flush();
          assert.equal(calls.filter(x=>x.path.endsWith('/confirm')).length,1);
          assert.equal(calls.filter(x=>x.path.endsWith('/cancel')).length,1);
          assert.equal($('extension-import-confirm').hidden,true);
          assert.match($('extension-import-result').textContent,/설치 완료를 확인하지 못/);
          $('extension-import-cancel').onclick();await flush();
          assert.equal($('extension-import-open').disabled,false);assert.equal(refreshes,0);
        })()""")

    def test_catalog_refresh_failure_reports_installed_without_reinstalling(self):
        self.run_case("""(async()=>{
          await open();refreshError=Error('메뉴 불러오기 실패');await $('extension-import-confirm').onclick();
          assert.match($('extension-import-result').textContent,/설치는 완료했지만/);
          assert.equal(calls.filter(x=>x.path.endsWith('/confirm')).length,1);
          assert.equal(calls.filter(x=>x.path.endsWith('/cancel')).length,0);
          assert.equal($('extension-import-confirm').hidden,true);assert.equal(refreshes,1);
        })()""")

    def test_shutdown_during_confirm_does_not_cancel_committing_package_or_refresh(self):
        self.run_case("""(async()=>{
          await open();let reply;responder=async()=>new Promise(resolve=>reply=resolve);
          const installing=$('extension-import-confirm').onclick();closed=true;WorkspaceExtensionImport.stop();
          reply({installed:true});await installing;
          assert.equal(calls.length,2);assert.equal(refreshes,0);assert.equal($('extension-import-dialog').open,false);
        })()""")

    def test_app_loads_import_module_only_on_click_and_refresh_preserves_session(self):
        self.run_case("""(async()=>{
          document.head=document.createElement('head');$('settings-dialog').open=true;
          active={id:'keep',state:'running',title:'Keep task'};attachments=[{id:'file-keep'}];$('prompt').value='keep draft';
          let hooks,opened=0;globalThis.WorkspaceExtensionImport={attach:options=>hooks=options,open:async()=>{opened++;},isBusy:()=>false};
          assert.equal(document.head.children.length,0);
          const loading=$('extension-import-open').onclick();await $('extension-import-open').onclick();
          assert.equal(document.head.children.length,1);assert.equal(document.head.children[0].src,'/extension-import.js');
          document.head.children[0].onload();await loading;assert.equal(opened,1);
          let mounted;globalThis.WorkspaceExtensions={mount:options=>mounted=options};
          const extensions=[{id:'sample.store',name:'Store',version:'1'}];
          api=async path=>{assert.equal(path,'/api/extensions');return {extensions,warnings:[]};};
          const refreshing=hooks.refresh();for(let i=0;i<8;i++)await Promise.resolve();
          assert.equal(document.head.children[1].src,'/extensions.js');document.head.children[1].onload();await refreshing;
          assert.equal(mounted.extensions,extensions);assert.equal(active.id,'keep');assert.equal(active.state,'running');
          assert.equal($('prompt').value,'keep draft');assert.equal(attachments[0].id,'file-keep');
        })()""", app=True)


if __name__ == "__main__":
    unittest.main()
