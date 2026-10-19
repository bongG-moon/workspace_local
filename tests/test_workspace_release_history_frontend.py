"""Read-only history: lazy requests, stale replies, safe rendering and selection."""
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


SNAPSHOT = {
    "status": "ready", "currentVersion": "0.23.0",
    "source": {"provider": "gitlab", "label": "사내 GitLab"},
    "entries": [
        {"version": "0.23.0", "title": "간편 화면", "notes": "화면을 편하게 사용해요.",
         "publishedAt": None, "origin": "bundled", "url": ""},
        {"version": "0.22.0", "title": "이전 버전", "notes": "이전 변경 내용",
         "publishedAt": "2026-10-01T00:00:00Z", "origin": "published",
         "url": "https://gitlab.example/team/workspace/-/releases/v0.22.0"},
    ], "checkedAt": 1790899200, "error": None,
}


@unittest.skipUnless(NODE, "Node.js is required for release history UI checks")
class WorkspaceReleaseHistoryFrontendTests(unittest.TestCase):
    def run_case(self, scenario):
        harness = HARNESS.replace(
            "const nodes=new Map();",
            "const timers=new Map();let nextTimer=1;const nodes=new Map();",
        ).replace(
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,",
            "const context={assert,console,URL,URLSearchParams,AbortController,Date,Map,Set,timers,"
            "tick:async()=>{const item=timers.entries().next().value;assert.ok(item);timers.delete(item[0]);item[1]();for(let i=0;i<12;i++)await Promise.resolve();},",
        ).replace(
            "setInterval(){},setTimeout(){return 1;},clearTimeout(){},",
            "setInterval(){throw Error('No permanent polling');},setTimeout(fn){const id=nextTimer++;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},",
        )
        setup = """
          const $=id=>document.getElementById(id),calls=[];
          $('settings-dialog').tagName='DIALOG';$('settings-dialog').open=true;
          $('settings-dialog').append($('release-history-open'));
          let responder=async()=>fixture;
          WorkspaceReleaseHistory.attach({api:async(path,data,signal)=>{calls.push({path,data,signal});return responder(data,signal);},
            openDialog:id=>$(id).showModal()});
          const open=()=>{$('release-history-open').focus();return $('release-history-open').onclick();};
          const select=version=>{$('release-history-version').value=version;$('release-history-version').onchange();};
          const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
        """
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/release-history.js"),
             "const fixture=" + json.dumps(SNAPSHOT) + ";" + setup + scenario],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_separate_catalog_defaults_to_server_and_switches_without_fetch(self):
        self.run_case("""(async()=>{
          fixture.bundledEntries=[fixture.entries[0]];
          await open();
          assert.equal($('release-history-version').value,'0.22.0');
          assert.equal($('release-history-notes').textContent,'이전 변경 내용');
          const count=calls.length;
          $('release-history-catalog').value='bundled';$('release-history-catalog').onchange();
          assert.equal($('release-history-version').value,'0.23.0');
          assert.equal(calls.length,count);
          assert.match($('release-history-help').textContent,/별도/);
          $('release-history-catalog').value='published';$('release-history-catalog').onchange();
          assert.equal($('release-history-version').value,'0.22.0');
        })()""")

    def test_attach_does_not_fetch_and_open_gets_cache_then_checks_once(self):
        self.run_case("""(async()=>{
          assert.equal(calls.length,0);assert.equal(timers.size,0);
          assert.equal($('release-history-open').hidden,false);
          await open();assert.equal(calls.length,2);
          assert.equal(calls[0].data,undefined);
          assert.equal(JSON.stringify(calls[1].data),JSON.stringify({action:'check',force:false}));
          assert.ok(calls.every(call=>call.path==='/api/app-update-history'));
          assert.equal(timers.size,0);assert.equal(document.activeElement,$('release-history-close'));
          await $('release-history-open').onclick();assert.equal(calls.length,2);
        })()""")

    def test_cached_notes_display_before_slow_check_and_current_version_selected(self):
        self.run_case("""(async()=>{
          let reply;responder=async data=>data?new Promise(resolve=>reply=resolve):fixture;
          const pending=open();await flush();
          assert.equal($('release-history-version').value,'0.23.0');
          assert.equal($('release-history-notes').textContent,fixture.entries[0].notes);
          assert.equal($('release-history-current').hidden,false);
          assert.match($('release-history-meta').textContent,/앱 변경 이력 · 앱에 포함된 변경 내역/);
          reply(fixture);await pending;
        })()""")

    def test_previous_version_selection_is_read_only_and_uses_published_label(self):
        self.run_case("""(async()=>{
          await open();select('0.22.0');
          assert.equal($('release-history-current').hidden,true);
          assert.equal($('release-history-detail-title').textContent,'이전 버전');
          assert.match($('release-history-meta').textContent,/사내 배포 설명 · 배포 기록일/);
          assert.equal($('release-history-link').hidden,true);
          assert.equal($('release-history-link').getAttribute('href'),'#');assert.equal(calls.length,2);
        })()""")

    def test_internal_legacy_and_explicit_deployment_notes_are_preserved(self):
        self.run_case("""(async()=>{
          for(const extra of [{},{noteSource:'deployment'}]){
            responder=async()=>({...fixture,entries:[{...fixture.entries[1],...extra,notes:'사내 배포 담당자가 작성한 실제 설명'}]});
            await open();assert.equal($('release-history-notes').textContent,'사내 배포 담당자가 작성한 실제 설명');
            assert.match($('release-history-meta').textContent,/사내 배포 설명 · 배포 기록일/);
            assert.match($('release-history-version').children[0].textContent,/사내 배포 설명/);
            assert.match($('release-history-help').textContent,/사내 서버.*설치본의 설명/);
            $('release-history-dismiss').onclick();
          }
        })()""")

    def test_package_record_overrides_bundled_notes_in_either_order(self):
        self.run_case("""(async()=>{
          const bundled={...fixture.entries[0],notes:'개발자 기본 변경 설명'};
          const published={...bundled,origin:'published',noteSource:'package',notes:'사용하면 안 되는 설명',publishedAt:'2026-10-01T00:00:00Z'};
          for(const entries of [[bundled,published],[published,bundled]]){
            responder=async()=>({...fixture,entries});await open();
            assert.equal($('release-history-version').children.length,1);
            assert.equal($('release-history-notes').textContent,'당시 배포 설명을 확인할 수 없습니다.');
            assert.equal($('release-history-notes').dataset.empty,'true');
            assert.match($('release-history-meta').textContent,/배포 파일 기록 · 패키지 등록일/);
            assert.doesNotMatch($('release-history-meta').textContent,/배포 기록일/);
            assert.match($('release-history-help').textContent,/조회 권한·지원 범위/);
            $('release-history-dismiss').onclick();
          }
        })()""")

    def test_native_release_body_is_plain_text_and_date_is_release_registration(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,entries:[{...fixture.entries[1],noteSource:'gitlab-release',notes:'사내 릴리스 설명 <img src=x onerror=alert(1)>'}]});
          await open();assert.match($('release-history-meta').textContent,/GitLab 릴리스 설명 · 릴리스 등록일/);
          assert.equal($('release-history-notes').textContent,'사내 릴리스 설명 <img src=x onerror=alert(1)>');
          assert.equal($('release-history-notes').children.length,0);
          assert.equal($('release-history-notes').dataset.empty,'false');
        })()""")

    def test_refresh_updates_same_version_provenance_without_changing_selection(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,entries:[{...fixture.entries[1],noteSource:'package',notes:''}]});
          await open();assert.match($('release-history-version').children[0].textContent,/배포 파일 기록/);
          responder=async()=>({...fixture,entries:[{...fixture.entries[1],noteSource:'gitlab-release',notes:'서버에서 조회한 실제 변경 설명'}]});
          await $('release-history-refresh').onclick();
          assert.equal($('release-history-version').value,'0.22.0');
          assert.match($('release-history-version').children[0].textContent,/GitLab 릴리스 설명/);
          assert.equal($('release-history-notes').textContent,'서버에서 조회한 실제 변경 설명');
          assert.equal(calls.length,3);
        })()""")

    def test_unknown_provenance_is_rejected_and_missing_dates_do_not_claim_bundle(self):
        self.run_case("""(async()=>{
          for(const noteSource of ['wrong','',null,{}]){
            responder=async()=>({...fixture,entries:[{...fixture.entries[1],noteSource}]});
            await open();assert.equal($('release-history-version').children.length,0);$('release-history-dismiss').onclick();
          }
          for(const [noteSource,label] of [['package','패키지 등록일'],['gitlab-release','릴리스 등록일'],['deployment','배포 기록일']]){
            responder=async()=>({...fixture,entries:[{...fixture.entries[1],noteSource,publishedAt:null,notes:''}]});
            await open();assert.ok($('release-history-meta').textContent.includes(label+' 확인 필요'));
            assert.doesNotMatch($('release-history-meta').textContent,/앱에 포함된/);$('release-history-dismiss').onclick();
          }
        })()""")

    def test_github_remains_generic_and_bundled_provenance_cannot_be_relabelled(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,source:{provider:'github'},entries:[{...fixture.entries[1],noteSource:'deployment'}]});
          await open();assert.match($('release-history-meta').textContent,/배포 이력/);
          assert.doesNotMatch($('release-history-meta').textContent,/사내|패키지|릴리스 등록일/);$('release-history-dismiss').onclick();
          responder=async()=>({...fixture,entries:[{...fixture.entries[0],noteSource:'package'}]});
          await open();assert.equal($('release-history-notes').textContent,fixture.entries[0].notes);
          assert.match($('release-history-meta').textContent,/앱 변경 이력 · 앱에 포함된 변경 내역/);
        })()""")

    def test_only_loading_open_dialog_polls_local_gets_then_stops_ready(self):
        self.run_case("""(async()=>{
          let loading=true;responder=async()=>({...fixture,status:loading?'loading':'ready'});
          await open();assert.equal(timers.size,1);select('0.22.0');
          await tick();assert.equal(calls.length,3);assert.equal(calls[2].data,undefined);
          assert.equal($('release-history-version').value,'0.22.0');
          loading=false;await tick();assert.equal(timers.size,0);assert.equal(calls.length,4);
          assert.equal($('release-history-version').value,'0.22.0');
        })()""")

    def test_polling_is_bounded_and_explicit_retry_can_start_again(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,status:'loading'});await open();
          for(let i=0;i<30;i++)await tick();
          assert.equal(timers.size,0);assert.match($('release-history-status').textContent,/오래 걸리고/);
          assert.equal($('release-history-refresh').disabled,false);
          responder=async()=>fixture;await $('release-history-refresh').onclick();
          assert.equal(JSON.stringify(calls.at(-1).data),JSON.stringify({action:'check',force:true}));
        })()""")

    def test_close_stops_polling_and_restores_trigger_focus(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,status:'loading'});await open();assert.equal(timers.size,1);
          let prevented=false;$('release-history-dialog').oncancel({preventDefault(){prevented=true;}});
          assert.equal(prevented,true);assert.equal(timers.size,0);
          assert.equal(document.activeElement,$('release-history-open'));
          assert.equal($('release-history-open').getAttribute('aria-expanded'),'false');
        })()""")

    def test_closed_and_reopened_dialog_ignores_stale_reply_and_does_not_post(self):
        self.run_case("""(async()=>{
          let reply;responder=()=>new Promise(resolve=>reply=resolve);
          const old=open();const signal=calls[0].signal;
          $('release-history-dismiss').onclick();assert.equal(signal.aborted,true);
          responder=async()=>({...fixture,entries:[{...fixture.entries[0],title:'새 응답'}]});
          await open();reply(fixture);await old;
          assert.equal($('release-history-detail-title').textContent,'새 응답');
          assert.equal(calls.length,3);assert.equal(calls.filter(call=>call.data).length,1);
        })()""")

    def test_offline_keeps_cached_notes_and_retry_is_explicit(self):
        self.run_case("""(async()=>{
          responder=async data=>{if(data)throw Error('오프라인');return fixture;};await open();
          assert.equal($('release-history-notes').textContent,fixture.entries[0].notes);
          assert.match($('release-history-status').textContent,/오프라인.*저장된 변경 내역/);
          assert.equal(timers.size,0);assert.equal($('release-history-open').hidden,false);
          responder=async()=>fixture;await $('release-history-refresh').onclick();assert.equal(calls.length,3);
        })()""")

    def test_malformed_response_and_empty_list_are_recoverable(self):
        self.run_case("""(async()=>{
          responder=async()=>({status:'ready',entries:[]});await open();
          assert.equal($('release-history-content').hidden,true);assert.equal($('release-history-empty').hidden,false);
          assert.equal($('release-history-version').disabled,true);
          responder=async()=>({status:'surprise'});await $('release-history-refresh').onclick();
          assert.match($('release-history-status').textContent,/확인하지 못했어요/);assert.equal(timers.size,0);
        })()""")

    def test_html_payloads_remain_text_and_unsafe_links_are_hidden(self):
        self.run_case(r"""(async()=>{
          for(const url of ['javascript:alert(1)','data:text/html,x','http://gitlab.example/x',
            'https://user:secret@gitlab.example/x','https://gitlab.example/x?q=x','https://gitlab.example/x#x',
            'https://gitlab.example/\\\\x','https://gitlab.example/\n/x']){
            responder=async()=>({...fixture,source:{provider:'github'},currentVersion:'0.22.0',entries:[{...fixture.entries[1],url,
              title:'<svg onload=alert(1)>',notes:'<script>alert(1)</script>'}]});
            await open();assert.equal($('release-history-detail-title').textContent,'<svg onload=alert(1)>');
            assert.equal($('release-history-notes').children.length,0);
            assert.equal($('release-history-notes').textContent,'<script>alert(1)</script>');
            assert.equal($('release-history-link').hidden,true);$('release-history-dismiss').onclick();
          }
        })()""")

    def test_bundled_unknown_provider_and_untrusted_gitlab_links_are_never_clickable(self):
        self.run_case("""(async()=>{
          for(const [provider,origin] of [['gitlab','bundled'],['gitlab','published'],['unknown','published']]){
            responder=async()=>({...fixture,source:{provider},entries:[{...fixture.entries[1],origin}]});
            await open();assert.equal($('release-history-link').hidden,true);$('release-history-dismiss').onclick();
          }
        })()""")

    def test_github_links_are_limited_to_exact_version_repository(self):
        self.run_case("""(async()=>{
          for(const allowed of [false,true]){
            const url=allowed?'https://github.com/bongG-moon/workspace_local/releases/tag/v0.22.0':'https://github.com/other/repository/releases/tag/v0.22.0';
            responder=async()=>({...fixture,source:{provider:'github'},entries:[{...fixture.entries[1],url}]});
            await open();assert.equal($('release-history-link').hidden,!allowed);$('release-history-dismiss').onclick();
          }
        })()""")

    def test_entries_are_bounded_deduplicated_and_only_stable_versions(self):
        self.run_case("""(async()=>{
          responder=async()=>({...fixture,entries:Array.from({length:50},(_,i)=>({...fixture.entries[0],version:`1.0.${i}`}))});
          await open();assert.equal($('release-history-version').children.length,30);$('release-history-dismiss').onclick();
          responder=async()=>({...fixture,entries:['0.22.0','0.22.0','0.22.1-beta','v0.22.0','0.022.0'].map(version=>({...fixture.entries[0],version}))});
          await open();assert.equal($('release-history-version').children.length,1);
        })()""")

    def test_closed_owner_falls_back_to_settings_focus(self):
        self.run_case("""(async()=>{
          await open();$('settings-dialog').open=false;$('release-history-dismiss').onclick();
          assert.equal(document.activeElement,$('settings-open'));
        })()""")

    def test_stop_aborts_inflight_request_and_blocks_further_opens(self):
        self.run_case("""(async()=>{
          let reply;responder=()=>new Promise(resolve=>reply=resolve);const pending=open();
          WorkspaceReleaseHistory.stop();assert.equal(calls[0].signal.aborted,true);reply(fixture);await pending;
          $('release-history-dismiss').onclick();await open();assert.equal(calls.length,1);assert.equal(timers.size,0);
        })()""")

    def test_keyboard_tab_wraps_both_ends_of_visible_dialog_controls(self):
        self.run_case("""(async()=>{
          await open();const first=$('release-history-close'),last=$('release-history-dismiss');
          for(const node of [first,last])node.getClientRects=()=>[{}];
          $('release-history-dialog').querySelectorAll=()=>[first,last];
          let prevented=0;first.focus();$('release-history-dialog').onkeydown({key:'Tab',shiftKey:true,preventDefault(){prevented++;}});
          assert.equal(document.activeElement,last);
          $('release-history-dialog').onkeydown({key:'Tab',shiftKey:false,preventDefault(){prevented++;}});
          assert.equal(document.activeElement,first);assert.equal(prevented,2);
        })()""")

    def test_markup_keeps_history_available_and_install_flow_separate(self):
        html = (ROOT / "local_app/web/index.html").read_text(encoding="utf-8")
        script = (ROOT / "local_app/web/release-history.js").read_text(encoding="utf-8")
        css = (ROOT / "local_app/web/release-history.css").read_text(encoding="utf-8")
        self.assertRegex(html, r'<button id="release-history-open"[^>]*>업데이트 내역</button>')
        self.assertNotIn('hidden', html.split('<button id="release-history-open"')[1].split('>')[0])
        self.assertIn('aria-labelledby="release-history-title"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertNotIn('innerHTML', script)
        self.assertNotIn('action:"install"', script)
        self.assertNotIn('setInterval', script)
        self.assertIn('white-space:pre-wrap', css)
        self.assertIn('WorkspaceReleaseHistory?.attach', (ROOT / "local_app/web/app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
