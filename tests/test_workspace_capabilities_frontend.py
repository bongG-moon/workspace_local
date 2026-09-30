"""Catalog interaction regressions using the shipped JS and a small DOM double.

All replies are synthetic. No browser, Claude process, profile, or network is used.
"""
from pathlib import Path
import subprocess
import unittest

from test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]
CATALOG_SETUP = r"""
// Model the few extra DOM facilities used by the catalog, without executing HTML.
Object.defineProperty(Element.prototype, 'innerHTML', {
  set() { throw new Error('Catalog metadata must never become HTML'); }
});
const baseQuery = Element.prototype.querySelector;
Element.prototype.querySelector = function(selector) {
  if (selector !== '[data-count]') return baseQuery.call(this, selector);
  for (const child of this.children) {
    if (Object.hasOwn(child.attributes || {}, 'data-count')) return child;
    const nested = child.querySelector?.(selector);
    if (nested) return nested;
  }
  return null;
};
for (const type of ['skills', 'tools', 'mcp', 'commands']) {
  const count = new Element('span'); count.setAttribute('data-count', type);
  get(`capabilities-${type}-tab`).append(count);
}
get('capabilities-summary').hidden = true;
context.flush = () => new Promise(resolve => setImmediate(resolve));
context.flatText = function flatText(node) {
  return [node.textContent || '', ...(node.children || []).map(flatText)].join(' ');
};
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), context, {filename:'capabilities.js'});
const scenario=process.argv[3];
"""
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", CATALOG_SETUP)
FIXTURE = r"""
const fixture = (id, options = {}) => ({
  schemaVersion:1, sessionId:id, workspace:'C:\\fixture\\' + id,
  status:options.status || 'live', notice:'목록 ' + id,
  runtime:{state:options.state || 'live', model:'synthetic-model', groups:{
    skills:{reported:true,items:[{name:id + '-skill',description:'설명 ' + id,invocation:'/test:' + id}]},
    tools:{reported:true,items:[{name:'Read',description:'',kind:'builtin'}]},
    mcp:{reported:true,items:[{name:'synthetic-server',status:'connected',description:''}]},
    commands:{reported:true,items:[{name:'test-command',description:'원본 명령',invocation:'/test-command'}]},
    ...(options.groups || {})
  }},
  installed:options.installed || {state:'discovered',skills:[],limited:false,notice:'설치 발견은 실행 증거가 아닙니다.'},
  warnings:[]
});
active = {id:'A', title:'업무 A', workspace:'C:\\fixture\\A', state:'idle', trusted:true};
sessions = [{...active}];
const modern = (id, options = {}) => ({...fixture(id, options), schemaVersion:2,
  context:{scope: id ? 'folder' : 'common', workspace:id ? 'C:\\fixture\\' + id : null}});
"""


@unittest.skipUnless(NODE, "Node.js is required for catalog UI state checks")
class WorkspaceCapabilitiesFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), FIXTURE + javascript,
             str(ROOT / "local_app/web/capabilities.js")],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_late_previous_workspace_reply_cannot_replace_current_catalog(self):
        self.run_case(r"""(async()=>{
          const pending=[];
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,body,signal,resolve}));
          WorkspaceCapabilities.open();
          active={...active,id:'B',title:'업무 B',workspace:'C:\\fixture\\B'};
          WorkspaceCapabilities.open();
          assert.equal(pending.length,2);assert.equal(pending[0].signal.aborted,true);
          pending[1].resolve(fixture('B'));await flush();
          assert.match(flatText($('capabilities-list')),/B-skill/);
          pending[0].resolve(fixture('A'));await flush();
          assert.match(flatText($('capabilities-list')),/B-skill/);
          assert.doesNotMatch(flatText($('capabilities-list')),/A-skill/);
          assert.match($('capabilities-context').textContent,/업무 B/);
          assert.equal($('capabilities-refresh').disabled,false);
        })()""")

    def test_close_aborts_pending_read_and_late_reply_does_not_reopen(self):
        self.run_case(r"""(async()=>{
          let reply,signal;
          api=(_,body,currentSignal)=>{signal=currentSignal;return new Promise(resolve=>reply=resolve);};
          WorkspaceCapabilities.open();WorkspaceCapabilities.close();
          assert.equal(signal.aborted,true);assert.equal(WorkspaceCapabilities.isOpen(),false);
          reply(fixture('A'));await flush();
          assert.equal($('capabilities-view').hidden,true);
          assert.equal(document.querySelector('.app').classList.contains('catalog-open'),false);
          assert.equal($('capabilities-list').children.length,0);
          assert.equal($('chat-title').textContent,'업무 A');
        })()""")

    def test_catalog_navigation_search_and_refresh_only_read_inventory(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{calls.push({path,body});return fixture('A');};
          WorkspaceCapabilities.open();await flush();
          for(const type of ['tools','mcp','commands','skills'])$('capabilities-'+type+'-tab').onclick();
          $('capabilities-search').value='test:A';$('capabilities-search').oninput();
          await $('capabilities-refresh').onclick();
          WorkspaceCapabilities.close();
          assert.equal(calls.length,2);
          assert.ok(calls.every(call=>call.path==='/api/capabilities?scope=common&id=A'&&call.body===undefined));
          assert.equal(active.state,'idle');
        })()""")

    def test_keyboard_tabs_update_panel_accessible_label_and_roving_focus(self):
        self.run_case(r"""(async()=>{
          api=async()=>fixture('A');WorkspaceCapabilities.open();await flush();
          $('capabilities-tools-tab').onclick();
          assert.equal($('capabilities-panel').attributes['aria-labelledby'],'capabilities-tools-tab');
          assert.equal($('capabilities-tools-tab').attributes['aria-selected'],'true');
          assert.equal($('capabilities-skills-tab').tabIndex,-1);
          let prevented=false;
          $('capabilities-tools-tab').onkeydown({key:'End',preventDefault(){prevented=true;}});
          assert.equal(prevented,true);
          assert.equal($('capabilities-panel').attributes['aria-labelledby'],'capabilities-commands-tab');
          assert.equal($('capabilities-commands-tab').tabIndex,0);
          assert.equal($('capabilities-tools-tab').tabIndex,-1);
          assert.equal($('capabilities-summary').hidden,false);
          assert.match(flatText($('capabilities-summary')),/synthetic-model/);
        })()""")

    def test_omitted_list_is_unknown_while_reported_empty_list_is_zero(self):
        self.run_case(r"""(async()=>{
          api=async()=>fixture('A',{groups:{
            skills:{reported:false,items:[]},tools:{reported:true,items:[]}
          },installed:{state:'unavailable',skills:[]}});
          WorkspaceCapabilities.open();await flush();
          assert.match($('capabilities-empty').textContent,/전달받지 못/);
          assert.match($('capabilities-warning').textContent,/없다는 뜻은 아닙니다/);
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'미확인');
          $('capabilities-tools-tab').onclick();
          assert.match($('capabilities-empty').textContent,/목록이 비어/);
          assert.equal($('capabilities-warning').hidden,true);
          assert.equal($('capabilities-tools-tab').querySelector('[data-count]').textContent,'0');
        })()""")

    def test_installed_skill_count_excludes_collapsed_helpers_and_references_before_connection(self):
        self.run_case(r"""(async()=>{
          const skills=Array.from({length:48},(_,i)=>({name:'skill-'+i,description:'일반 설명',
            invocation:'company:skill-'+i,source:'company',userInvocable:true,kind:'skill'}));
          skills.push(...Array.from({length:3},(_,i)=>({name:'helper-'+i,description:'내부 설명',
            invocation:'codex:helper-'+i,source:'plugin',userInvocable:false,kind:'internal'})));
          skills.push({name:'knowledge',description:'참고 설명',invocation:'',kind:'reference',userInvocable:true});
          const groups=Object.fromEntries(['skills','tools','mcp','commands'].map(type=>[type,{reported:false,items:[]}]));
          api=async()=>fixture('A',{status:'awaiting-runtime',state:'unavailable',groups,
            installed:{state:'discovered',skills,limited:false}});
          WorkspaceCapabilities.open();await flush();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'48');
          const groupsEl=$('capabilities-list').children;
          assert.equal(groupsEl.length,3);assert.equal(groupsEl[0].children[1].children.length,48);
          assert.equal(groupsEl[1].tagName,'DETAILS');assert.equal(groupsEl[1].open,false);
          assert.equal(groupsEl[2].tagName,'DETAILS');assert.equal(groupsEl[2].open,false);
          assert.match(flatText(groupsEl[1]),/내부 보조 스킬 · 3/);
          assert.match(flatText(groupsEl[2]),/참고 자료 · 1/);
          assert.equal(groupsEl[1].querySelector('code'),null);assert.equal(groupsEl[2].querySelector('code'),null);
          assert.match(flatText(groupsEl[1]),/플러그인: codex/);
          assert.equal($('capabilities-warning').hidden,true);
          for(const type of ['tools','mcp','commands']) {
            assert.equal($('capabilities-'+type+'-tab').querySelector('[data-count]').textContent,'연결 전');
            $('capabilities-'+type+'-tab').onclick();
            assert.match($('capabilities-empty').textContent,/선택한 업무의 Claude 연결이 준비/);
          }
        })()""")

    def test_skill_count_merges_only_exact_invocations_and_keeps_namespace_owners(self):
        self.run_case(r"""(async()=>{
          api=async()=>fixture('A',{groups:{skills:{reported:true,items:[
            {name:'one:report',invocation:'/one:report',description:''},
            {name:'two:report',invocation:'/two:report',description:'둘'},
            {name:'report',invocation:'/report',description:'일반'},
            {name:'codex:helper',invocation:'/codex:helper',description:''}
          ]}},installed:{state:'discovered',skills:[
            {name:'report',invocation:'one:report',description:'설치 설명',source:'plugin',kind:'skill'},
            {name:'helper',invocation:'codex:helper',description:'내부 설명',source:'plugin',kind:'internal',userInvocable:false}
          ]}});
          WorkspaceCapabilities.open();await flush();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'3');
          const main=$('capabilities-list').children[0],details=$('capabilities-list').children[1];
          assert.equal(main.children[1].children.length,3);assert.match(flatText(main),/설치 설명/);
          assert.match(flatText(main),/플러그인: one/);assert.match(flatText(main),/플러그인: two/);
          assert.match(flatText(main),/설치 정보도 확인됨/);assert.doesNotMatch(flatText(main),/helper/);
          assert.equal(details.querySelector('code'),null);assert.equal(details.open,false);
          $('capabilities-search').value='one:report';$('capabilities-search').oninput();
          assert.equal($('capabilities-list').children[0].children[1].children.length,1);
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'3');
          $('capabilities-search').value='codex';$('capabilities-search').oninput();
          assert.equal($('capabilities-list').children.length,1);
          assert.equal($('capabilities-list').children[0].open,true);
        })()""")

    def test_no_selection_and_reported_empty_have_distinct_readable_count_labels(self):
        self.run_case(r"""(async()=>{
          active=null;
          const groups=Object.fromEntries(['skills','tools','mcp','commands'].map(type=>[type,{reported:false,items:[]}]));
          api=async()=>fixture(null,{status:'no-session',state:'unavailable',groups,
            installed:{state:'not-selected',skills:[]}});
          WorkspaceCapabilities.open();await flush();
          for(const type of ['skills','tools','mcp','commands'])assert.equal($('capabilities-'+type+'-tab').querySelector('[data-count]').textContent,'연결 전');
          assert.match($('capabilities-empty').textContent,/먼저 새 업무/);
          active={id:'A',title:'업무 A',workspace:'C:\\fixture\\A',state:'idle'};
          api=async()=>fixture('A',{groups:{skills:{reported:true,items:[]},tools:{reported:false,items:[]}},
            installed:{state:'discovered',skills:[]}});
          await $('capabilities-refresh').onclick();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'0');
          assert.equal($('capabilities-tools-tab').querySelector('[data-count]').textContent,'미확인');
          assert.equal($('capabilities-skills-tab').attributes['aria-label'],'스킬 · 0개');
        })()""")

    def test_unavailable_installed_inventory_keeps_its_specific_explanation(self):
        self.run_case(r"""(async()=>{
          for(const [state,notice] of [
            ['needs-trust','업무 폴더를 다시 확인하면 설치된 스킬을 조회할 수 있습니다.'],
            ['unavailable','이 폴더의 설치된 스킬 목록을 확인하지 못했습니다.'],
            ['demo','체험 모드에서는 실제 설치 정보와 설정을 읽지 않습니다.'],
            ['not-selected','업무를 선택하면 해당 폴더의 설치된 스킬을 확인할 수 있습니다.']
          ]) {
            api=async()=>fixture('A',{installed:{state,notice,skills:[]}});
            WorkspaceCapabilities.open();await flush();
            const text=$('capabilities-installed-note').textContent;
            assert.ok(text.includes(notice));assert.match(text,/목록만 조회/);
            assert.doesNotMatch(text,/설치 정보와 연결에서 확인한 스킬을 함께/);
            WorkspaceCapabilities.close();
          }
        })()""")

    def test_metadata_markup_remains_literal_text_and_never_creates_elements(self):
        self.run_case(r"""(async()=>{
          const dangerous='<img src=x onerror=alert(1)>',description='<script>bad()</script>';
          api=async()=>fixture('A',{groups:{skills:{reported:true,items:[{
            name:dangerous,description,invocation:'/literal:<svg onload=bad()>'
          }]}}});
          WorkspaceCapabilities.open();await flush();
          const list=$('capabilities-list');
          assert.match(flatText(list),/<img src=x onerror=alert\(1\)>/);
          assert.match(flatText(list),/<script>bad\(\)<\/script>/);
          assert.equal(list.querySelector('img'),null);assert.equal(list.querySelector('script'),null);
          assert.equal(list.querySelector('svg'),null);
        })()""")

    def test_open_close_preserves_current_task_draft_attachments_and_conversation(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='아직 보내지 않은 요청';attachments=['C:\\fixture\\input.csv'];
          const draft={text:'저장된 초안',attachments:['draft.csv']};drafts.set('A',draft);
          const message=el('article','이전 답변','assistant');$('conversation').append(message);
          const selected=active;
          api=async()=>fixture('A');WorkspaceCapabilities.open();await flush();WorkspaceCapabilities.close();
          assert.equal(active,selected);assert.equal($('prompt').value,'아직 보내지 않은 요청');
          assert.equal(attachments[0],'C:\\fixture\\input.csv');assert.equal(drafts.get('A'),draft);
          assert.equal($('conversation').children[0],message);
          assert.equal($('chat-title').textContent,'업무 A');
        })()""")

    def test_stopped_connection_uses_historical_labels_without_authorization_claims(self):
        self.run_case(r"""(async()=>{
          api=async()=>fixture('A',{state:'last-seen',status:'last-seen'});
          WorkspaceCapabilities.open();await flush();$('capabilities-mcp-tab').onclick();
          const text=flatText($('capabilities-list'));
          assert.match(text,/마지막 연결/);assert.match(text,/마지막 상태: 연결됨/);
          assert.doesNotMatch(text,/승인됨|사용 가능|현재 연결에서 확인/);
          assert.match($('capabilities-installed-note').textContent,/권한이나 성공을 보장하지/);
        })()""")

    def test_common_is_independent_of_active_task_and_folder_query_captures_explicit_context(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{calls.push({path,body});return modern(null,{status:'installed-only'});};
          WorkspaceCapabilities.open();await flush();
          assert.equal(calls[0].path,'/api/capabilities?scope=common&id=A');
          active={...active,id:'B',title:'업무 B',workspace:'C:\\fixture\\B'};
          sessions.push({...active});
          WorkspaceCapabilities.contextChanged(true);await flush();
          assert.equal(calls.length,1);
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          assert.equal(calls[1].path,'/api/capabilities?scope=folder&workspace=C%3A%5Cfixture%5CB&id=B');
          active={...active,id:'C',title:'업무 C',workspace:'C:\\fixture\\C'};
          WorkspaceCapabilities.contextChanged(true);await flush();
          await $('capabilities-refresh').onclick();
          assert.equal(calls[2].path,calls[1].path);
          assert.match($('capabilities-context').textContent,/업무 B/);
          assert.doesNotMatch($('capabilities-context').textContent,/업무 C/);
          assert.equal(active.id,'C');assert.ok(calls.every(call=>call.body===undefined));
        })()""")

    def test_common_shows_connection_counts_without_claiming_global_tools(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{
            calls.push({path,body});const reply=modern('A');reply.context={scope:'common',workspace:null};
            reply.runtime.source={sessionId:'A',title:'업무 A',workspace:'C:\\fixture\\A'};
            reply.installed={state:'discovered',skills:[{name:'common-skill',invocation:'common',candidateId:'user:1',kind:'skill',source:'user',userInvocable:true}],
              diagnostics:{code:'metadata_read',summary:'정상 메타데이터'}};return reply;
          };
          WorkspaceCapabilities.open();await flush();
          assert.equal(calls[0].path,'/api/capabilities?scope=common&id=A');
          assert.equal($('capabilities-folder-label').textContent,'연결 기준 업무');assert.equal($('capabilities-folder').hidden,false);
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'1');
          for(const type of ['tools','mcp','commands']){
            $('capabilities-'+type+'-tab').onclick();
            assert.equal($('capabilities-'+type+'-tab').querySelector('[data-count]').textContent,'1');
            assert.match(flatText($('capabilities-summary')),/연결 기준 · 업무 A/);
          }
          assert.match($('capabilities-context').textContent,/공통 설치 스킬.*연결 근거: 업무 A/);
          assert.ok(calls.every(call=>call.body===undefined));assert.equal(active.id,'A');
        })()""")

    def test_common_connection_select_keeps_same_folder_sessions_distinct_and_rejects_old_reply(self):
        self.run_case(r"""(async()=>{
          const pending=[];sessions.push({id:'A2',title:'같은 폴더의 다른 연결',workspace:active.workspace},
            {id:'B',title:'다른 폴더 연결',workspace:'D:\\other'});
          $('prompt').value='보존할 질문';attachments=['keep.csv'];const original=active;
          selectSession=()=>{throw Error('must not select a task');};
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,body,signal,resolve}));
          WorkspaceCapabilities.open();const selector=$('capabilities-folder');
          assert.equal(selector.children.filter(row=>row.value).length,3);
          selector.value='A2';selector.onchange();assert.match(pending[1].path,/scope=common&id=A2$/);
          selector.value='B';selector.onchange();assert.match(pending[2].path,/scope=common&id=B$/);
          assert.equal(pending[0].signal.aborted,true);assert.equal(pending[1].signal.aborted,true);
          pending[2].resolve(modern('B'));await flush();pending[1].resolve(modern('A2'));pending[0].resolve(modern('A'));await flush();
          assert.match(flatText($('capabilities-list')),/B-skill/);assert.doesNotMatch(flatText($('capabilities-list')),/A-skill|A2-skill/);
          assert.match($('capabilities-context').textContent,/다른 폴더 연결/);
          assert.equal(active,original);assert.equal($('prompt').value,'보존할 질문');assert.equal(attachments[0],'keep.csv');
          assert.ok(pending.every(call=>call.body===undefined));
        })()""")

    def test_removed_common_connection_clears_pending_source_and_returns_to_current_task(self):
        self.run_case(r"""(async()=>{
          const pending=[];sessions.push({id:'B',title:'조회 연결',workspace:'D:\\other'});
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,signal,resolve}));
          WorkspaceCapabilities.open();$('capabilities-folder').value='B';$('capabilities-folder').onchange();
          sessions=sessions.filter(row=>row.id!=='B');WorkspaceCapabilities.contextChanged();
          assert.equal(pending.length,3);assert.match(pending[2].path,/id=A$/);assert.equal(pending[1].signal.aborted,true);
          pending[2].resolve(modern('A'));await flush();pending[1].resolve(modern('B'));await flush();
          assert.equal($('capabilities-folder').value,'A');assert.match(flatText($('capabilities-list')),/A-skill/);
          assert.doesNotMatch(flatText($('capabilities-list')),/B-skill/);
        })()""")

    def test_common_refreshes_selected_runtime_when_connection_reports_arrive(self):
        self.run_case(r"""(async()=>{
          const calls=[];let live=false,timer;
          api=async path=>{calls.push(path);return live?modern('A'):modern('A',{status:'awaiting-runtime',state:'unavailable',groups:{
            tools:{reported:false,items:[]},mcp:{reported:false,items:[]},commands:{reported:false,items:[]}}});};
          WorkspaceCapabilities.open();await flush();assert.equal($('capabilities-tools-tab').querySelector('[data-count]').textContent,'연결 전');
          live=true;setTimeout=fn=>{timer=fn;return 1;};WorkspaceCapabilities.contextChanged(true);
          assert.equal(typeof timer,'function');await timer();await flush();
          assert.equal(calls.length,2);assert.ok(calls.every(path=>path==='/api/capabilities?scope=common&id=A'));
          for(const kind of ['tools','mcp','commands'])assert.equal($('capabilities-'+kind+'-tab').querySelector('[data-count]').textContent,'1');
        })()""")

    def test_scope_switch_ignores_late_reply_and_empty_folder_does_not_request_task(self):
        self.run_case(r"""(async()=>{
          const pending=[];active=null;sessions=[];
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,signal,resolve}));
          WorkspaceCapabilities.open();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();
          assert.equal(pending.length,1);assert.equal(pending[0].signal.aborted,true);
          pending[0].resolve(modern(null));await flush();
          assert.equal($('capabilities-list').children.length,0);
          assert.match($('capabilities-status').textContent,/새 업무/);
          assert.equal($('capabilities-folder').disabled,true);
          assert.equal($('capabilities-folder').value,'');
          assert.match($('capabilities-installed-note').textContent,/별도로 표시/);
          $('capabilities-scope-select').value='common';$('capabilities-scope-select').onchange();
          pending[1].resolve(modern(null));await flush();
          assert.equal(pending[1].path,'/api/capabilities?scope=common');
          assert.match($('capabilities-context').textContent,/공통/);
        })()""")

    def test_folder_dropdown_changes_only_catalog_and_preserves_choice_across_scope_and_close(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{calls.push({path,body});return modern('A');};
          sessions.push({id:'B',title:'업무 B',workspace:'D:\\other\\B'});
          $('prompt').value='보존할 초안';attachments=['original.csv'];const selected=active;
          const draft={text:'저장된 초안',attachments:['draft.csv']};drafts.set('A',draft);
          const message=el('article','기존 대화');$('conversation').append(message);
          selectSession=()=>{throw Error('catalog must not switch tasks');};
          showDialog=()=>{throw Error('catalog must not open a dialog');};
          WorkspaceCapabilities.open();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          const folder=$('capabilities-folder');
          const option=folder.children.find(item=>item.textContent.includes('D:\\other\\B'));
          assert.ok(option);folder.value=option.value;folder.onchange();await flush();
          const expected='/api/capabilities?scope=folder&workspace=D%3A%5Cother%5CB&id=B';
          assert.equal(calls.at(-1).path,expected);
          assert.equal(active,selected);assert.equal(active.trusted,true);
          assert.equal($('prompt').value,'보존할 초안');assert.equal(attachments[0],'original.csv');
          assert.equal(drafts.get('A'),draft);assert.equal($('conversation').children[0],message);
          $('capabilities-scope-select').value='common';$('capabilities-scope-select').onchange();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          assert.equal(calls.at(-1).path,expected);
          WorkspaceCapabilities.close();
          WorkspaceCapabilities.open();await flush();
          assert.equal(calls.at(-1).path,expected);
          assert.ok(calls.every(call=>call.path.startsWith('/api/capabilities?')&&call.body===undefined));
        })()""")

    def test_folder_dropdown_fast_switch_ignores_old_reply_and_reply_after_close(self):
        self.run_case(r"""(async()=>{
          const pending=[];
          sessions.push({id:'B',title:'업무 B',workspace:'C:\\fixture\\B'});
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,body,signal,resolve}));
          WorkspaceCapabilities.open();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();
          const folder=$('capabilities-folder'),option=folder.children.find(item=>item.textContent.includes('C:\\fixture\\B'));
          folder.value=option.value;folder.onchange();
          assert.equal(pending.length,3);assert.equal(pending[0].signal.aborted,true);assert.equal(pending[1].signal.aborted,true);
          pending[2].resolve(modern('B'));await flush();
          pending[1].resolve(modern('A'));pending[0].resolve(modern(null));await flush();
          assert.match(flatText($('capabilities-list')),/B-skill/);
          assert.doesNotMatch(flatText($('capabilities-list')),/A-skill/);
          assert.match($('capabilities-context').textContent,/업무 B/);
          $('capabilities-refresh').onclick();WorkspaceCapabilities.close();
          assert.equal(pending[3].signal.aborted,true);
          pending[3].resolve(modern('late'));await flush();
          assert.equal($('capabilities-view').hidden,true);assert.equal(WorkspaceCapabilities.isOpen(),false);
          assert.doesNotMatch(flatText($('capabilities-list')),/late-skill/);
        })()""")

    def test_folder_dropdown_deduplicates_windows_paths_and_keeps_explicit_runtime_source(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          sessions.push({id:'A2',title:'같은 폴더 최신 업무',workspace:'c:/FIXTURE/a/',updated:30},
            {id:'B',title:'다른 업무',workspace:'D:\\another\\A',updated:20});
          api=async path=>{calls.push(path);return modern('A');};
          WorkspaceCapabilities.open();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          const options=$('capabilities-folder').children.filter(item=>item.value);
          assert.equal(options.length,2);
          assert.ok(options.some(item=>item.textContent.includes('C:\\fixture\\A')));
          assert.ok(options.some(item=>item.textContent.includes('D:\\another\\A')));
          assert.match(calls.at(-1),/id=A$/);
          active={...sessions.find(item=>item.id==='A2')};
          const count=calls.length;WorkspaceCapabilities.contextChanged();await flush();
          await $('capabilities-refresh').onclick();
          assert.equal(calls.length,count+1);assert.match(calls.at(-1),/id=A$/);
          assert.match($('capabilities-context').textContent,/연결 근거: 업무 A/);
          sessions=sessions.filter(item=>item.id!=='A');
          WorkspaceCapabilities.contextChanged();await flush();
          assert.match(calls.at(-1),/id=A2$/);
          assert.match($('capabilities-context').textContent,/같은 폴더 최신 업무/);
        })()""")

    def test_folder_dropdown_without_active_task_defaults_to_newest_existing_directory(self):
        self.run_case(r"""(async()=>{
          active=null;
          sessions=[{id:'old',title:'이전 업무',workspace:'C:\\old',updated:1},
            {id:'new',title:'최근 업무',workspace:'C:\\new',created:9},
            {id:'new-older',title:'같은 폴더 이전 업무',workspace:'c:/NEW/',updated:3}];
          const calls=[];api=async path=>{calls.push(path);return modern('new');};
          WorkspaceCapabilities.open();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          assert.match(calls.at(-1),/workspace=C%3A%5Cnew&id=new$/);
          assert.equal($('capabilities-folder').disabled,false);
          assert.equal($('capabilities-folder').children.filter(item=>item.value).length,2);
          assert.equal(active,null);
        })()""")

    def test_folder_dropdown_session_changes_update_options_and_clear_removed_selection(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return modern('A');};
          WorkspaceCapabilities.open();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          sessions.push({id:'B',title:'추가한 업무',workspace:'C:\\fixture\\B'});
          renderSessions();await flush();
          const folder=$('capabilities-folder'),option=folder.children.find(item=>item.textContent.includes('C:\\fixture\\B'));
          assert.ok(option);folder.value=option.value;folder.onchange();await flush();
          const beforeRename=calls.length;sessions.find(item=>item.id==='B').title='새 업무 이름';
          renderSessions();await flush();
          assert.equal(calls.length,beforeRename);assert.match($('capabilities-context').textContent,/새 업무 이름/);
          sessions=sessions.filter(item=>item.id!=='B');renderSessions();await flush();
          assert.equal(folder.value,'');assert.equal(calls.length,beforeRename);
          assert.equal($('capabilities-list').children.length,0);
          await $('capabilities-refresh').onclick();assert.equal(calls.length,beforeRename);
          $('capabilities-scope-select').value='common';$('capabilities-scope-select').onchange();await flush();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();await flush();
          assert.match(calls.at(-1).path,/id=A$/);
          assert.ok(calls.every(call=>call.path.startsWith('/api/capabilities?')&&call.body===undefined));
        })()""")

    def test_folder_dropdown_removed_selection_rejects_pending_result_and_unknown_option(self):
        self.run_case(r"""(async()=>{
          const pending=[];
          sessions.push({id:'B',title:'업무 B',workspace:'C:\\fixture\\B'});
          api=(path,body,signal)=>new Promise(resolve=>pending.push({path,signal,resolve}));
          WorkspaceCapabilities.open();
          $('capabilities-scope-select').value='folder';$('capabilities-scope-select').onchange();
          const folder=$('capabilities-folder'),option=folder.children.find(item=>item.textContent.includes('C:\\fixture\\B'));
          folder.value=option.value;folder.onchange();
          sessions=sessions.filter(item=>item.id!=='B');renderSessions();await flush();
          assert.equal(pending.length,3);assert.equal(pending[2].signal.aborted,true);assert.equal(folder.value,'');
          pending[2].resolve(modern('B'));await flush();
          assert.equal($('capabilities-list').children.length,0);
          folder.value='C:\\not-a-session';folder.onchange();await flush();
          assert.equal(pending.length,3);assert.equal(folder.value,'');
          WorkspaceCapabilities.close();pending[0].resolve(modern(null));pending[1].resolve(modern('A'));await flush();
        })()""")

    def test_candidate_identity_preserves_same_invocations_and_runtime_has_separate_count(self):
        self.run_case(r"""(async()=>{
          api=async()=>modern('A',{groups:{skills:{reported:true,items:[
            {name:'report',invocation:'/report'},
            {name:'report',invocation:'/report',candidateId:'project:bbb22222'}
          ]}},installed:{state:'discovered',skills:[
            {name:'report',invocation:'report',candidateId:'user:aaa11111',source:'user',userInvocable:true,kind:'skill'},
            {name:'report',invocation:'report',candidateId:'project:bbb22222',source:'project',userInvocable:true,kind:'skill'},
            {name:'helper',invocation:'helper',candidateId:'user:ccc33333',userInvocable:false,kind:'internal'}
          ]}});
          WorkspaceCapabilities.open();await flush();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'2');
          const groups=$('capabilities-list').children;
          assert.equal(groups[0].children[1].children.length,2);
          assert.doesNotMatch(flatText(groups[0].children[1].children[0]),/설치 정보도 확인됨/);
          assert.match(flatText(groups[0].children[1].children[1]),/설치 정보도 확인됨/);
          assert.match(flatText(groups[0]),/aaa11111/);assert.match(flatText(groups[0]),/bbb22222/);
          assert.match(flatText(groups[2]),/연결 보고 목록 · 2/);assert.equal(groups[2].open,false);
          assert.match(flatText($('capabilities-summary')),/일반 설치 후보 2개/);
          assert.match(flatText($('capabilities-summary')),/연결 보고 2개/);
          $('capabilities-search').value='project';$('capabilities-search').oninput();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'2');
        })()""")

    def test_partial_zero_is_unknown_complete_zero_is_zero_and_context_is_disclosed(self):
        self.run_case(r"""(async()=>{
          const groups=Object.fromEntries(['skills','tools','mcp','commands'].map(type=>[type,{reported:false,items:[]}]))
          let installed={state:'partial',skills:[],limited:true,context:{configRootSource:'environment',actualCliContextVerified:false},
            diagnostics:{code:'ready',summary:'정상 등록을 확인했습니다.'}};
          api=async()=>modern(null,{status:'installed-only',state:'unavailable',groups,installed});
          WorkspaceCapabilities.open();await flush();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'미확인');
          assert.match($('capabilities-empty').textContent,/전체가 0개라는 뜻은 아닙니다/);
          assert.match($('capabilities-installed-note').textContent,/앱 환경에서 지정된 설정 기준/);
          assert.match($('capabilities-installed-note').textContent,/설정 일치는 미확인/);
          assert.doesNotMatch($('capabilities-warning').textContent,/정상 등록/);
          installed={...installed,state:'discovered',limited:false};await $('capabilities-refresh').onclick();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'0');
          assert.equal($('capabilities-warning').hidden,true);
          assert.match($('capabilities-empty').textContent,/일반 스킬이 없어요/);
          installed={...installed,state:'unavailable'};await $('capabilities-refresh').onclick();
          assert.equal($('capabilities-skills-tab').querySelector('[data-count]').textContent,'미확인');
          assert.doesNotMatch(flatText($('capabilities-summary')),/연결 보고 수를 표시합니다/);
        })()""")

    def test_tool_groups_use_provenance_and_preserve_unknown_original_names(self):
        self.run_case(r"""(async()=>{
          api=async()=>modern('A',{groups:{tools:{reported:true,items:[
            {name:'Read',displayName:'파일 읽기',kind:'builtin',description:'파일 내용을 읽습니다.',descriptionSource:'reference',classificationSource:'reference'},
            {name:'DesignSync',kind:'unknown',description:'',descriptionSource:'unknown',classificationSource:'unknown'},
            {name:'CompanyTool',kind:'harness',description:'실제 설명',descriptionSource:'runtime',classificationSource:'runtime'},
            {name:'mcp__research__lookup',kind:'mcp',server:'research',descriptionSource:'unknown',classificationSource:'qualified-name'}
          ]}}});
          WorkspaceCapabilities.open();await flush();$('capabilities-tools-tab').onclick();
          assert.equal($('capabilities-tools-tab').querySelector('[data-count]').textContent,'4');
          const groups=$('capabilities-list').children;
          assert.equal(groups.length,4);assert.match(flatText(groups[0]),/설명: 연결에서 제공/);
          assert.match(flatText(groups[2]),/DesignSync/);assert.match(flatText(groups[2]),/종류 미확인/);
          assert.equal(groups[3].tagName,'DETAILS');assert.equal(groups[3].open,false);
          assert.match(flatText(groups[3]),/파일 읽기/);assert.match(flatText(groups[3]),/Read/);
          assert.match(flatText(groups[3]),/공식 도구명 대조/);
          $('capabilities-search').value='Read';$('capabilities-search').oninput();
          assert.equal($('capabilities-list').children.length,1);assert.equal($('capabilities-list').children[0].open,true);
        })()""")


if __name__ == "__main__":
    unittest.main()
