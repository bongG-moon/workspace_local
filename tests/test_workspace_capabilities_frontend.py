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
          assert.ok(calls.every(call=>call.path==='/api/capabilities?id=A'&&call.body===undefined));
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
            assert.match($('capabilities-empty').textContent,/첫 요청으로 Claude에 연결/);
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


if __name__ == "__main__":
    unittest.main()
