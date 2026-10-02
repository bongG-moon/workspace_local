"""Release discovery stays quiet; installation requires an explicit user action."""
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


CURRENT = {
    "currentVersion": "0.22.0", "status": "current", "autoCheck": True,
    "lastChecked": 1790899200, "release": None, "progress": None,
    "error": None, "canInstall": True,
}
AVAILABLE = {
    **CURRENT, "status": "available",
    "release": {"version": "0.22.1", "title": "더 편해진 업무 공간",
                "notes": "새 기능\n- 작성한 요청을 보관합니다.",
                "publishedAt": "2026-10-03T00:00:00Z", "url": "https://untrusted.example/"},
}


@unittest.skipUnless(NODE, "Node.js is required for app update UI checks")
class WorkspaceAppUpdatesFrontendTests(unittest.TestCase):
    def run_case(self, javascript, initial=CURRENT, warning=None):
        harness = HARNESS.replace(
            "const nodes=new Map();",
            "Element.prototype.removeAttribute=function(name){delete this.attributes[name];};\n"
            "const updateTimers=new Map(),updateEvents=new Map(),updateWindowEvents=new Map();let nextUpdateTimer=1;\nconst nodes=new Map();",
        ).replace(
            "setInterval(){},setTimeout(){return 1;},clearTimeout(){},",
            "setInterval(){},setTimeout(fn,delay){const id=nextUpdateTimer++;updateTimers.set(id,{fn,delay});return id;},"
            "clearTimeout(id){updateTimers.delete(id);},",
        ).replace(
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,",
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,updateTimers,"
            "addEventListener:(name,fn)=>updateWindowEvents.set(name,fn),"
            "fireUpdateWindow:name=>updateWindowEvents.get(name)?.(),fireUpdateEvent:name=>updateEvents.get(name)?.(),"
            "runUpdateTimer:async()=>{const next=updateTimers.entries().next().value;"
            "assert.ok(next,'expected a status read timer');updateTimers.delete(next[0]);next[1].fn();"
            "for(let i=0;i<12;i++)await Promise.resolve();},",
        ).replace(
            "({sessions:[],demo:false})",
            "({sessions:[],demo:false,appUpdate:" + json.dumps(initial) + ",appUpdateWarning:" + json.dumps(warning) + "})",
        ).replace(
            "querySelector:selector=>get(selector),querySelectorAll:()=>[],addEventListener(){}},",
            "querySelector:selector=>get(selector),querySelectorAll:()=>[],addEventListener:(name,fn)=>updateEvents.set(name,fn)},",
        ).replace(
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'app-updates.js'});\n"
            "context.WorkspaceStartupHealth={attach(){},bootstrapReady:async()=>{"
            "context.updaterReadyAtBootstrap=['app-update-check','app-update-notes-open','app-update-install']"
            ".every(id=>typeof get(id).onclick==='function');},bootstrapFailed(){}};\n"
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
        )
        javascript = "const available=" + json.dumps(AVAILABLE) + ";const current=" + json.dumps(CURRENT) + ";" + javascript
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), javascript,
             str(ROOT / "local_app/web/app-updates.js")],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_updater_handlers_are_bound_before_bootstrap_readiness_check(self):
        self.run_case("assert.equal(updaterReadyAtBootstrap,true);")

    def test_available_bootstrap_shows_subtle_badge_without_opening_dialog_or_installing(self):
        self.run_case(r"""
          assert.equal($('app-update-sidebar-badge').hidden,false);
          assert.equal($('app-update-settings-badge').hidden,false);
          assert.equal($('app-update-current').textContent,'0.22.0');
          assert.equal($('app-update-latest').textContent,'0.22.1');
          assert.match($('settings-open').attributes['aria-label'],/새 버전/);
          assert.notEqual($('app-update-dialog').open,true);
          assert.notEqual($('settings-dialog').open,true);assert.equal(updateTimers.size,0);
        """, AVAILABLE)

    def test_bounded_startup_reads_local_status_and_notice_arrives_with_settings_closed(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push({path,data});return available;};
          await runUpdateTimer();assert.equal(calls.length,1);
          assert.equal(calls[0].path,'/api/app-update');assert.equal(calls[0].data,undefined);
          assert.equal($('app-update-sidebar-badge').hidden,false);
          assert.notEqual($('app-update-dialog').open,true);assert.equal(updateTimers.size,0);
        })()""", {**CURRENT, "status": "checking"})

    def test_startup_reads_stop_even_if_background_check_keeps_running(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push({path,data});return {...current,status:'checking'};};
          for(let i=0;i<5;i++)await runUpdateTimer();
          assert.equal(calls.length,5);assert.equal(updateTimers.size,0);
          assert.ok(calls.every(row=>row.data===undefined));
        })()""", {**CURRENT, "status": "checking"})

    def test_settings_poll_only_while_open_and_use_reads_not_forced_checks(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push({path,data});return current;};
          openSettings();await runUpdateTimer();assert.equal(calls.length,1);
          assert.equal(calls[0].data,undefined);assert.equal(updateTimers.size,1);
          $('settings-close').onclick();assert.equal(updateTimers.size,0);
          assert.equal($('app-update-current').textContent,'0.22.0');
          assert.equal($('app-update-latest').textContent,'0.22.0');
          assert.match($('app-update-checked').textContent,/2026/);
        })()""")

    def test_returning_to_app_refreshes_badge_once_without_permanent_background_polling(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push(data);return available;};
          assert.equal(updateTimers.size,0);fireUpdateWindow('focus');await runUpdateTimer();
          assert.equal(calls.length,1);assert.equal(calls[0],undefined);
          assert.equal($('app-update-sidebar-badge').hidden,false);assert.equal(updateTimers.size,0);
          document.hidden=true;fireUpdateEvent('visibilitychange');assert.equal(updateTimers.size,0);
          document.hidden=false;fireUpdateEvent('visibilitychange');await runUpdateTimer();
          assert.equal(calls.length,2);assert.equal(updateTimers.size,0);
        })()""")

    def test_bootstrap_warning_is_plain_text_and_survives_later_status_reads(self):
        self.run_case(r"""(async()=>{
          assert.equal($('app-update-warning').hidden,false);
          assert.equal($('app-update-warning').textContent,'<b>설치 위치 기록을 저장하지 못했어요.</b>');
          api=async()=>available;await $('app-update-check').onclick();
          assert.equal($('app-update-warning').hidden,false);
          assert.equal($('app-update-warning').children.length,0);
          assert.equal($('app-update-sidebar-badge').hidden,false);assert.equal(appClosed,false);
        })()""", warning="<b>설치 위치 기록을 저장하지 못했어요.</b>")

    def test_manual_check_and_auto_check_preference_do_not_install(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,data)=>{calls.push({path,data});return {...current,
            autoCheck:data.action==='configure'?data.autoCheck:false,status:data.action==='check'?'available':'disabled',
            release:data.action==='check'?available.release:null};};
          assert.equal($('app-update-auto').checked,true);
          $('app-update-auto').checked=false;await $('app-update-auto').onchange();
          assert.equal($('app-update-auto').checked,false);assert.equal(updateTimers.size,0);
          await $('app-update-check').onclick();
          assert.equal(JSON.stringify(calls.map(row=>row.data)),JSON.stringify([
            {action:'configure',autoCheck:false},{action:'check'}]));
          assert.equal($('app-update-sidebar-badge').hidden,false);
          assert.notEqual($('app-update-dialog').open,true);
        })()""")

    def test_release_notes_are_plain_text_and_only_fixed_github_release_link_is_used(self):
        self.run_case(r"""
          const unsafe='<script>globalThis.compromised=true</script>\n[악성 링크](javascript:alert(1))';
          WorkspaceAppUpdates.observe({...available,release:{...available.release,title:'<img src=x onerror=alert(1)>',notes:unsafe}});
          const origin=$('app-update-notes-open');origin.focus();origin.onclick();
          assert.equal($('app-update-dialog').open,true);assert.equal(document.activeElement,$('app-update-close'));
          assert.equal($('app-update-notes').textContent,unsafe);assert.equal($('app-update-notes').children.length,0);
          assert.equal($('app-update-title').textContent,'<img src=x onerror=alert(1)>');
          assert.equal($('app-update-release-link').attributes.href,'https://github.com/bongG-moon/workspace_local/releases/tag/v0.22.1');
          assert.equal(globalThis.compromised,undefined);
          let prevented=false;$('app-update-dialog').oncancel({preventDefault(){prevented=true;}});
          assert.equal(prevented,true);assert.equal($('app-update-dialog').open,false);assert.equal(document.activeElement,origin);
        """)

    def test_invalid_prerelease_equal_or_older_version_cannot_be_installed(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;return available;};
          for(const version of ['0.22.1-beta','0.22.1/../../elsewhere','0.22.0','0.9.9','v0.22.1']){
            WorkspaceAppUpdates.observe({...available,release:{...available.release,version}});
            assert.equal($('app-update-sidebar-badge').hidden,true);
            assert.equal($('app-update-install').disabled,true);await $('app-update-install').onclick();
          }
          assert.equal(calls,0);
        })()""")

    def test_gitlab_and_unknown_sources_never_expose_remote_release_links(self):
        self.run_case(r"""
          for(const provider of ['gitlab','other']){
            WorkspaceAppUpdates.observe({...available,source:{provider,label:'사내 배포 서버'},
              release:{...available.release,url:'https://private.example/releases?token=SECRET'}});
            assert.equal($('app-update-source').textContent,'사내 배포 서버');
            assert.equal($('app-update-release-link').hidden,true);
            assert.equal($('app-update-release-link').attributes.href,'#');
            assert.equal($('app-update-sidebar-badge').hidden,false);
            assert.equal($('app-update-install').disabled,false);
          }
          WorkspaceAppUpdates.observe({...available,source:{provider:'github',label:'공개 배포'}});
          assert.equal($('app-update-release-link').hidden,false);
          assert.equal($('app-update-release-link').attributes.href,'https://github.com/bongG-moon/workspace_local/releases/tag/v0.22.1');
        """)

    def test_install_is_explicit_locked_and_progress_does_not_interrupt_drafts(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='작성 중인 업무';attachments=['keep.csv'];
          openSettings();$('app-update-notes-open').onclick();
          const calls=[];let reply;api=(path,data)=>{calls.push({path,data});return new Promise(resolve=>reply=resolve);};
          const pending=$('app-update-install').onclick();
          assert.equal($('app-update-install').disabled,true);assert.equal($('app-update-check').disabled,true);
          await $('app-update-install').onclick();assert.equal(calls.length,1);
          assert.equal(JSON.stringify(calls[0].data),JSON.stringify({action:'install',version:'0.22.1'}));
          reply({...available,status:'downloading',progress:43});await pending;
          assert.equal($('app-update-dialog-progress-wrap').hidden,false);
          assert.equal($('app-update-dialog-progress').value,43);
          assert.equal($('app-update-progress-label').textContent,'다운로드 43%');
          $('app-update-dismiss').onclick();$('settings-close').onclick();assert.equal(updateTimers.size,1);
          assert.equal($('prompt').value,'작성 중인 업무');assert.deepEqual(attachments,['keep.csv']);
          WorkspaceAppUpdates.observe({...available,status:'launching',progress:100});
          assert.equal($('app-update-dialog').open,false);assert.equal($('settings-dialog').open,false);
        })()""", AVAILABLE)

    def test_own_launch_closes_settings_for_existing_handoff_but_passive_launch_does_not(self):
        self.run_case(r"""(async()=>{
          openSettings();$('app-update-notes-open').onclick();
          WorkspaceAppUpdates.observe({...available,status:'launching'});
          assert.equal($('settings-dialog').open,true);assert.equal($('app-update-dialog').open,true);
          WorkspaceAppUpdates.observe(available);api=async()=>({...available,status:'launching'});
          await $('app-update-install').onclick();
          assert.equal($('settings-dialog').open,false);assert.equal($('app-update-dialog').open,false);
          assert.equal(appClosed,false);
        })()""", AVAILABLE)

    def test_confirmed_download_failure_allows_only_explicit_retry(self):
        self.run_case(r"""(async()=>{
          toast=()=>{};
          const calls=[];api=async(path,data)=>{calls.push(data);return {...available,status:'error',error:'다운로드 연결을 확인해 주세요.'};};
          await $('app-update-install').onclick();
          assert.equal(calls.length,1);assert.equal($('app-update-install').disabled,false);
          assert.equal($('app-update-retry').hidden,false);assert.match($('app-update-status').textContent,/다운로드 연결/);
          assert.equal(updateTimers.size,0);await $('app-update-install').onclick();assert.equal(calls.length,2);
        })()""", AVAILABLE)

    def test_waiting_handoff_closes_dialogs_only_once_per_explicit_install(self):
        self.run_case(r"""(async()=>{
          openSettings();$('app-update-notes-open').onclick();
          api=async()=>({...available,status:'ready',progress:100});
          await $('app-update-install').onclick();
          assert.equal($('settings-dialog').open,false);assert.equal($('app-update-dialog').open,false);
          openSettings();$('app-update-notes-open').onclick();
          api=async()=>({...available,status:'launching',progress:100});await runUpdateTimer();
          assert.equal($('settings-dialog').open,true);assert.equal($('app-update-dialog').open,true);
          assert.equal(document.activeElement,$('app-update-close'));
          WorkspaceAppUpdates.observe({...available,status:'error',error:'전환을 취소했어요.'});
          assert.equal($('app-update-install').disabled,false);
          await $('app-update-install').onclick();
          assert.equal($('settings-dialog').open,false);assert.equal($('app-update-dialog').open,false);
        })()""", AVAILABLE)

    def test_uncertain_install_reply_requires_status_read_before_an_explicit_retry(self):
        self.run_case(r"""(async()=>{
          toast=()=>{};
          const calls=[];api=async(path,data)=>{calls.push(data);throw Error('lost connection');};
          await $('app-update-install').onclick();await $('app-update-install').onclick();
          assert.equal(calls.length,1);assert.equal($('app-update-install').disabled,true);
          api=async(path,data)=>{calls.push(data);return available;};await $('app-update-check').onclick();
          assert.equal(calls[1],undefined);assert.equal($('app-update-install').disabled,false);
          assert.equal(calls.length,2);assert.equal(updateTimers.size,0);
        })()""", AVAILABLE)

    def test_async_install_failure_notifies_once_after_dialog_close_and_retry_can_notify_again(self):
        self.run_case(r"""(async()=>{
          const notices=[];toast=message=>notices.push(message);
          openSettings();$('app-update-notes-open').onclick();api=async()=>({...available,status:'ready'});
          await $('app-update-install').onclick();assert.equal($('settings-dialog').open,false);
          const failure={...available,status:'error',error:'새 버전 전환을 취소했습니다.'};
          api=async()=>failure;await runUpdateTimer();
          assert.equal(notices.length,1);assert.match(notices[0],/설정의 ‘앱 업데이트’/);
          assert.match(notices[0],/다시 시도/);assert.equal($('app-update-install').disabled,false);
          WorkspaceAppUpdates.observe(failure);assert.equal(notices.length,1);assert.equal(updateTimers.size,0);
          openSettings();$('app-update-notes-open').onclick();api=async()=>({...available,status:'launching'});
          await $('app-update-install').onclick();api=async()=>failure;await runUpdateTimer();
          assert.equal(notices.length,2);assert.notEqual($('app-update-dialog').open,true);
          assert.equal(updateTimers.size,0);
        })()""", AVAILABLE)

    def test_background_check_failure_and_visible_install_failure_do_not_toast(self):
        self.run_case(r"""(async()=>{
          const notices=[];toast=message=>notices.push(message);
          const failure={...available,status:'error',error:'연결을 확인해 주세요.'};
          WorkspaceAppUpdates.observe(failure);assert.equal(notices.length,0);
          api=async()=>failure;await $('app-update-check').onclick();assert.equal(notices.length,0);
          openSettings();$('app-update-notes-open').onclick();
          await $('app-update-install').onclick();assert.equal(notices.length,0);
          assert.equal($('app-update-dialog').open,true);assert.equal($('app-update-dialog-retry').hidden,false);
          assert.match($('app-update-dialog-status').textContent,/연결을 확인/);
        })()""")

    def test_uncertain_install_notice_does_not_repeat_when_read_confirms_failure(self):
        self.run_case(r"""(async()=>{
          const notices=[];toast=message=>notices.push(message);
          openSettings();$('app-update-notes-open').onclick();let reject;
          api=()=>new Promise((resolve,fail)=>reject=fail);
          const pending=$('app-update-install').onclick();$('app-update-dismiss').onclick();$('settings-close').onclick();
          reject(Error('connection lost'));await pending;
          assert.equal(notices.length,1);assert.match(notices[0],/지금 확인/);
          api=async()=>({...available,status:'error',error:'새 버전을 실행하지 못했습니다.'});
          await runUpdateTimer();assert.equal(notices.length,1);assert.equal(updateTimers.size,0);
          assert.equal($('app-update-install').disabled,false);
        })()""", AVAILABLE)

    def test_read_failure_and_configure_failure_preserve_known_version_and_preference(self):
        self.run_case(r"""(async()=>{
          api=async()=>{throw Error('연결 실패');};$('app-update-auto').checked=false;
          await $('app-update-auto').onchange();assert.equal($('app-update-auto').checked,true);
          assert.equal($('app-update-current').textContent,'0.22.0');assert.equal($('app-update-retry').hidden,false);
          api=async(path,data)=>{assert.equal(data,undefined);return current;};await $('app-update-retry').onclick();
          assert.equal($('app-update-retry').hidden,true);assert.equal($('app-update-status').textContent,'최신 버전을 사용하고 있어요.');
          WorkspaceAppUpdates.stop();assert.equal(updateTimers.size,0);
        })()""")

    def test_shipped_markup_has_accessible_update_dialog_and_safe_link(self):
        page = (ROOT / "local_app/web/index.html").read_text(encoding="utf-8")
        script = (ROOT / "local_app/web/app-updates.js").read_text(encoding="utf-8")
        css = (ROOT / "local_app/web/app-updates.css").read_text(encoding="utf-8")
        self.assertLess(page.index('/app-updates.js'), page.index('/app.js'))
        self.assertIn('/app-updates.css', page)
        self.assertIn('aria-labelledby="app-update-title"', page)
        self.assertIn('id="app-update-release-link" class="app-update-release-link" target="_blank" rel="noopener noreferrer"', page)
        self.assertIn('진행 중인 업무를 마친 뒤 전환합니다. 작성 중인 내용은 보관합니다.', page)
        self.assertIn('white-space:pre-wrap', css)
        self.assertNotIn('innerHTML', script)
        self.assertNotIn('setInterval(', script)


if __name__ == "__main__":
    unittest.main()
