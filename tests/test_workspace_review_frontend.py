"""Exercise the shipped review/branch UI with delayed, synthetic API replies.

The DOM double checks state and request ownership, not visual rendering. These
tests never read a Claude profile, execute a CLI, or use a network connection.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') {
    this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={};
    this.attributes={}; this.textContent=''; this._value=''; this.disabled=false;
    this.hidden=false; this.open=false; this.isConnected=true; this.listeners={};
  }
  get value(){return this.tagName==='SELECT'&&!this._value ? this.children[0]?.value||'' : this._value;}
  set value(value){this._value=value;}
  get firstElementChild(){return this.children[0];}
  querySelectorAll(selector){const found=[];for(const node of this.children){
    if(node.className?.split(' ').includes(selector.slice(1)))found.push(node);
    found.push(...node.querySelectorAll(selector));
  }return found;}
  scrollIntoView(){this.scrolled=true;}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
  dispatchEvent(event){for(const fn of this.listeners[event.type]||[])fn(event);}
  append(...nodes){for(const node of nodes){
    if(node?.tagName==='FRAGMENT')this.append(...node.children);
    else if(typeof node==='string')this.children.push(Object.assign(new Element('#text'),{textContent:node}));
    else this.children.push(node);
  }}
  replaceChildren(...nodes){this.children=[];this._value='';this.textContent='';this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
  showModal(){this.open=true;}
  close(){if(!this.open)return;this.open=false;for(const fn of this.listeners.close||[])fn();}
  focus(){context.document.activeElement=this;}
  set innerHTML(_){throw new Error('Untrusted review content must never become HTML');}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element(id==='changes-run'?'select':id.endsWith('-dialog')?'dialog':'div'));return nodes.get(id);};
get('context-notice').hidden=true;get('handoff-origin').hidden=true;
const context={assert,console,encodeURIComponent,Date,Event,
  active:{id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'done',sessionId:'native-session-A'},
  boot:{demo:false},sending:false,appClosed:false,sessions:[],selectedSessions:[],toasts:[],renderCount:0,
  document:{activeElement:new Element('button'),createDocumentFragment:()=>new Element('fragment')},
  $:get,el:(tag,text,kind)=>{const node=new Element(tag);node.textContent=text||'';node.className=kind||'';return node;},
  when:stamp=>'시각 '+stamp,showDialog:id=>get(id).showModal(),
  api:async()=>{throw new Error('Unexpected API request');},
  flush:()=>new Promise(resolve=>setImmediate(resolve)),
  flatText:node=>[node.textContent||'',...node.children.map(context.flatText)].join(' '),
};
context.renderSessions=()=>context.renderCount++;
context.selectSession=async id=>{context.selectedSessions.push(id);context.active=context.sessions.find(row=>row.id===id);};
context.toast=message=>context.toasts.push(message);
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'productivity.js'});
setImmediate(async()=>{
  try{await vm.runInContext(process.argv[3],context,{filename:'scenario.js'});}
  catch(error){console.error(error.stack||error);process.exitCode=1;}
});
"""
FIXTURE = r"""
sessions=[{...active}];
const file=(id='same-file',name='report.html')=>({id,name,change:'modified',textAvailable:true});
const run=(runId,files=[file()],status='complete')=>({runId,files,status,startedAt:1,finishedAt:2,before:{},after:{}});
const answer=text=>({status:'text',version:'captured',diff:'-old\n+'+text+'\n'});
const child={id:'child',title:'분기 대화',workspace:'C:/fixture/A',state:'idle',messages:[{text:'inherited'}],artifacts:[{name:'old'}]};
"""


@unittest.skipUnless(NODE, 'Node.js is required for productivity UI state checks')
class WorkspaceProductivityFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/productivity.js'), FIXTURE + javascript],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_same_file_in_different_runs_rejects_out_of_order_response(self):
        self.run_case(r"""(async()=>{
          const pending=[];
          api=async(path,body)=>{
            assert.equal(body,undefined);
            if(path.startsWith('/api/changes?'))return {runs:[run('first'),run('second')]};
            return new Promise(resolve=>pending.push({path,resolve}));
          };
          await WorkspaceProductivityActions.openChanges();
          assert.equal(pending.length,1);assert.match(pending[0].path,/run=first/);
          $('changes-run').value='second';$('changes-run').onchange();
          assert.equal(pending.length,2);assert.match(pending[1].path,/run=second/);
          pending[1].resolve(answer('SECOND CAPTURE'));await flush();
          pending[0].resolve(answer('FIRST STALE CAPTURE'));await flush();
          assert.match(flatText($('changes-content')),/SECOND CAPTURE/);
          assert.doesNotMatch(flatText($('changes-content')),/FIRST STALE/);
          assert.equal($('changes-run').value,'second');
        })()""")

    def test_same_file_run_round_trip_also_rejects_first_pending_response(self):
        self.run_case(r"""(async()=>{
          const pending=[];
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('first'),run('second')]}:
            new Promise(resolve=>pending.push(resolve));
          await WorkspaceProductivityActions.openChanges();
          $('changes-run').value='second';$('changes-run').onchange();
          $('changes-run').value='first';$('changes-run').onchange();
          assert.equal(pending.length,3);
          pending[2](answer('CURRENT FIRST'));await flush();
          pending[0](answer('OLD FIRST'));pending[1](answer('OLD SECOND'));await flush();
          assert.match(flatText($('changes-content')),/CURRENT FIRST/);
          assert.doesNotMatch(flatText($('changes-content')),/OLD FIRST|OLD SECOND/);
        })()""")

    def test_switching_to_empty_capturing_run_cannot_restore_previous_diff(self):
        self.run_case(r"""(async()=>{
          let reply;
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('first'),run('waiting',[],'capturing')]}:
            new Promise(resolve=>reply=resolve);
          await WorkspaceProductivityActions.openChanges();
          $('changes-run').value='waiting';$('changes-run').onchange();
          reply(answer('STALE'));await flush();
          assert.equal($('changes-layout').hidden,true);
          assert.equal($('changes-content').children.length,0);
          assert.match($('changes-empty').textContent,/업무가 끝나면/);
        })()""")

    def test_close_discards_pending_list_and_releases_content(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);
          const request=WorkspaceProductivityActions.openChanges();
          $('changes-close').onclick();reply({runs:[run('late')]});await request;
          assert.equal($('changes-dialog').open,false);
          assert.equal($('changes-run').children.length,0);
          assert.equal($('changes-files').children.length,0);
          assert.equal($('changes-content').children.length,0);
        })()""")

    def test_context_change_rejects_old_list_and_comparison_replies(self):
        self.run_case(r"""(async()=>{
          let oldDiff;
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('A-run')]}:
            new Promise(resolve=>oldDiff=resolve);
          await WorkspaceProductivityActions.openChanges();
          active={...active,id:'B',title:'업무 B'};WorkspaceProductivityActions.contextChanged();
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('B-run')]}:answer('B CONTENT');
          await WorkspaceProductivityActions.openChanges();await flush();
          oldDiff(answer('OLD A CONTENT'));await flush();
          assert.match(flatText($('changes-content')),/B CONTENT/);
          assert.doesNotMatch(flatText($('changes-content')),/OLD A/);
          assert.equal($('changes-run').value,'B-run');
        })()""")

    def test_late_previous_workspace_list_does_not_replace_new_workspace_runs(self):
        self.run_case(r"""(async()=>{
          let oldList;
          api=()=>new Promise(resolve=>oldList=resolve);
          const oldRequest=WorkspaceProductivityActions.openChanges();
          active={...active,id:'B',title:'업무 B'};WorkspaceProductivityActions.contextChanged();
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('B-run')]}:answer('B CONTENT');
          await WorkspaceProductivityActions.openChanges();await flush();
          oldList({runs:[run('OLD-A-run')]});await oldRequest;await flush();
          assert.equal($('changes-run').value,'B-run');
          assert.equal($('changes-run').children.length,1);
          assert.match(flatText($('changes-content')),/B CONTENT/);
          assert.equal($('changes-dialog').open,true);
        })()""")

    def test_metadata_limits_have_korean_copy_and_no_html_interpretation(self):
        self.run_case(r"""(async()=>{
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('one',[file('x','<img onerror=x>.html')])]}:
            {status:'metadata_only',reason:'diff_work_limit',diff:'<script>danger</script>'};
          await WorkspaceProductivityActions.openChanges();await flush();
          assert.match($('changes-note').textContent,/비교 범위/);
          assert.doesNotMatch($('changes-note').textContent,/diff_work_limit/);
          assert.equal($('changes-file-name').textContent,'<img onerror=x>.html');
          assert.equal($('changes-content').children.length,0);
        })()""")

    def test_targeted_explorer_numbering_split_navigation_and_draft_only(self):
        self.run_case(r"""(async()=>{
          let closePanel, opener, saved, panel;
          setPanel=value=>panel=value;
          WorkspaceResultExplorer={openCustom:opts=>{
            assert.equal(opts.node,$('changes-review'));opener=opts.opener;closePanel=opts.onClose;
            return ()=>closePanel();
          }};
          saveDraft=()=>saved=$('prompt').value;
          const trigger=new Object(), calls=[];
          const row=(kind,beforeLine,afterLine,text)=>({kind,beforeLine,afterLine,text,ending:'\n'});
          const hunks=[
            {beforeStart:1,beforeCount:2,afterStart:1,afterCount:3,rows:[
              row('context',1,1,'  retained  '),row('remove',2,null,'old'),
              row('add',null,2,'<script>danger</script>'),row('add',null,3,'next')]},
            {beforeStart:19,beforeCount:1,afterStart:20,afterCount:1,rows:[
              row('remove',19,null,'partial value'),row('add',null,20,'new value')]}
          ];
          hunks[0].rows[3].pairedBeforeLine=2;
          hunks[1].rows[1].text='🔧 new value';hunks[1].rows[1].highlights=[[2,5]];
          api=async(path,body)=>{
            assert.equal(body,undefined);calls.push(path);
            if(path.startsWith('/api/changes?'))return {runs:[run('x',[file('first','other.py'),file('second','src/code.py')])]};
            return {status:'text',hunks,truncated:false};
          };
          const target={sessionId:'A',runId:'x',path:'C:\\fixture\\A\\src\\CODE.py',opener:trigger};
          await WorkspaceProductivityActions.openChanges(target);await flush();
          assert.equal(opener,trigger);assert.equal(panel,'results');assert.equal($('changes-dialog').open,false);
          assert.deepEqual(calls,['/api/changes?id=A&run=x','/api/changes/file?id=A&run=x&file=second']);
          assert.equal($('changes-file-name').textContent,'src/code.py');
          assert.equal($('changes-unified').attributes['aria-pressed'],'true');
          assert.equal($('changes-position').textContent,'1 / 2');
          const content=$('changes-content');
          assert.equal(content.querySelectorAll('.patch-remove')[0].children[0].textContent,2);
          assert.match(flatText(content),/<script>danger<\/script>/);
          $('changes-split').onclick();
          const hunk=content.querySelectorAll('.patch-hunk')[0];
          const pairs=hunk.querySelectorAll('.patch-pair');assert.equal(pairs.length,3);
          assert.match(pairs[1].children[0].className,/patch-blank/);
          assert.equal(pairs[1].children[1].children[0].textContent,2);
          assert.equal(pairs[2].children[0].children[0].textContent,2);
          assert.equal(pairs[2].children[1].children[0].textContent,3);
          assert.equal(content.querySelectorAll('.patch-edit')[0].textContent,'new');
          $('changes-next').onclick();assert.equal($('changes-position').textContent,'2 / 2');
          assert.equal($('changes-next').disabled,true);
          $('prompt').value='기존 초안';$('changes-question').onclick();
          assert.match(saved,/기존 초안\n\nsrc\/code.py/);assert.match(saved,/@@ -19,1 \+20,1 @@/);
          assert.match(saved,/\+🔧 new value/);assert.doesNotMatch(saved,/danger/);
          assert.equal(calls.length,2);assert.equal(document.activeElement,$('prompt'));
        })()""")

    def test_unavailable_exact_target_never_loads_a_different_file_or_run(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async path=>{calls.push(path);assert.ok(path.startsWith('/api/changes?'));return {runs:[run('keep')]};};
          await WorkspaceProductivityActions.openChanges({sessionId:'wrong',runId:'keep',path:'report.html'});
          assert.equal(calls.length,0);
          await WorkspaceProductivityActions.openChanges({sessionId:'A',runId:'expired',path:'report.html'});
          assert.match($('changes-empty').textContent,/만료/);
          await WorkspaceProductivityActions.openChanges({sessionId:'A',runId:'keep',path:'C:/another/report.html'});
          assert.match($('changes-empty').textContent,/선택한 파일/);
          assert.equal($('changes-content').children.length,0);assert.equal(calls.length,2);
        })()""")

    def test_replaced_explorer_discards_pending_file_and_does_not_reopen(self):
        self.run_case(r"""(async()=>{
          let dismissed, reply;setPanel=()=>{};
          WorkspaceResultExplorer={openCustom:opts=>(dismissed=opts.onClose,()=>dismissed())};
          api=async path=>path.startsWith('/api/changes?')?{runs:[run('one')]}:new Promise(done=>reply=done);
          await WorkspaceProductivityActions.openChanges();
          dismissed();reply(answer('STALE'));await flush();
          assert.equal($('changes-content').children.length,0);assert.equal($('changes-run').children.length,0);
        })()""")

    def test_direct_branch_click_creates_once_without_preview_or_ai_dispatch(self):
        self.run_case(r"""(async()=>{
          active.lastRunId='finished';
          const calls=[];let created;
          api=async(path,body)=>{
            calls.push({path,body});
            return new Promise(resolve=>created=resolve);
          };
          $('branch-open').onclick();
          assert.equal($('branch-dialog').open,true);assert.equal($('branch-create').disabled,false);
          assert.equal(calls.length,0);
          const first=$('branch-create').onclick();
          assert.equal($('branch-create').disabled,true);assert.equal($('handoff-create').disabled,true);
          await $('branch-create').onclick();await $('handoff-create').onclick();
          assert.deepEqual(calls,[{path:'/api/session/branch',body:{id:'A'}}]);
          created({ok:true,session:child});await first;
          assert.equal(sessions.filter(row=>row.id==='child').length,1);
          assert.equal(selectedSessions.length,1);assert.equal(selectedSessions[0],'child');assert.equal(renderCount,1);
          assert.equal(sessions.find(row=>row.id==='child').messages,undefined);
          assert.equal(sessions.find(row=>row.id==='child').artifacts,undefined);
        })()""")

    def test_created_branch_retained_if_panel_closed_during_request(self):
        self.run_case(r"""(async()=>{
          let created;
          api=async(path,body)=>{
            assert.equal(path,'/api/session/branch');assert.deepEqual(body,{id:'A'});
            return new Promise(resolve=>created=resolve);
          };
          await WorkspaceProductivityActions.openBranch();
          const pending=$('branch-create').onclick();$('branch-close').onclick();
          created({ok:true,session:child});await pending;
          assert.equal(sessions.filter(row=>row.id==='child').length,1);
          assert.equal(renderCount,1);assert.equal(active.id,'A');
          assert.equal(selectedSessions.length,0);assert.equal(toasts.length,0);
          assert.equal($('branch-dialog').open,false);
        })()""")

    def test_created_branch_retained_without_switching_later_active_task(self):
        self.run_case(r"""(async()=>{
          let created;
          api=async(path,body)=>{
            assert.equal(path,'/api/session/branch');assert.deepEqual(body,{id:'A'});
            return new Promise(resolve=>created=resolve);
          };
          await WorkspaceProductivityActions.openBranch();const pending=$('branch-create').onclick();
          active={...active,id:'B'};WorkspaceProductivityActions.contextChanged();
          created({ok:true,session:child});await pending;
          assert.equal(sessions.filter(row=>row.id==='child').length,1);
          assert.equal(active.id,'B');assert.equal(selectedSessions.length,0);
        })()""")

    def test_created_branch_remains_in_list_if_opening_it_fails(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {session:child};};
          selectSession=async id=>{assert.equal(id,child.id);throw Error('선택 실패');};
          $('branch-open').onclick();await $('branch-create').onclick();
          assert.equal($('branch-dialog').open,false);assert.equal(active.id,'A');
          assert.equal(sessions.filter(row=>row.id===child.id).length,1);
          assert.match(toasts.at(-1),/업무 목록에서 열어/);
          await $('branch-create').onclick();
          assert.deepEqual(calls,[{path:'/api/session/branch',body:{id:'A'}}]);
        })()""")

    def test_closed_menu_and_busy_or_demo_states_never_create(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=()=>{calls++;};
          await WorkspaceProductivityActions.openBranch();$('branch-cancel').onclick();
          await $('branch-create').onclick();assert.equal($('branch-dialog').open,false);
          for(const state of ['running','approval','question','starting']){
            active.state=state;WorkspaceProductivityActions.update();
            assert.equal($('branch-open').disabled,true);await WorkspaceProductivityActions.openBranch();
          }
          active.state='done';boot.demo=true;await WorkspaceProductivityActions.openBranch();
          boot.demo=false;sending=true;await WorkspaceProductivityActions.openBranch();
          assert.equal(calls,0);
        })()""")

    def test_context_notice_is_claimed_once_only_over_sixty_after_visible_work_completes(self):
        self.run_case(r"""(async()=>{
          let claims=0;
          active.lastRunId='finished';
          api=async(path,body)=>{
            assert.equal(path,'/api/session/context-notice');assert.deepEqual(body,{id:'A'});
            claims++;return {show:true,contextNoticeShown:true};
          };
          const update=WorkspaceProductivityActions.update;
          active.contextUsage={usedTokens:60,windowTokens:100,percent:60};
          for(let i=0;i<500;i++)update();
          active.contextUsage={usedTokens:62,windowTokens:100,percent:62};
          active.kind='chat';update();active.kind='work';
          active.state='running';update();active.state='done';
          document.hidden=true;update();document.hidden=false;
          assert.equal(claims,0);assert.equal($('context-notice').hidden,true);
          for(let i=0;i<500;i++)update();
          await flush();assert.equal(claims,1);assert.equal($('context-notice').hidden,false);
          assert.match($('context-notice-title').textContent,/62%/);
          $('context-stay').onclick();update();
          active={...active,lastRunId:'another'};update();
          assert.equal(claims,1);assert.equal($('context-notice').hidden,true);
        })()""")

    def test_delayed_notice_is_delivered_only_in_owning_work_and_click_starts_summary_once(self):
        self.run_case(r"""(async()=>{
          const source={...active,lastRunId:'finished',contextUsage:{usedTokens:62,windowTokens:100,percent:62}};
          active=source;const calls=[];let claim,finish;
          sessionMetadata=item=>({id:item.id,title:item.title,workspace:item.workspace});
          api=async(path,body)=>{
            calls.push(path);assert.deepEqual(body,{id:'A'});
            if(path.endsWith('context-notice'))return new Promise(done=>claim=done);
            assert.equal(path,'/api/session/continue');return new Promise(done=>finish=done);
          };
          WorkspaceProductivityActions.update();active={id:'B',state:'idle'};
          WorkspaceProductivityActions.contextChanged();claim({show:true,contextNoticeShown:true});await flush();
          assert.equal($('context-notice').hidden,true);
          active={...source,contextNoticeShown:true};WorkspaceProductivityActions.update();
          assert.equal($('context-notice').hidden,false);
          const pending=$('context-continue').onclick();$('context-continue').onclick();
          assert.equal($('handoff-create').disabled,true);assert.match($('handoff-status').textContent,/정리/);
          assert.deepEqual(calls,['/api/session/context-notice','/api/session/continue']);
          finish({session:child});await pending;assert.deepEqual([...selectedSessions],['child']);
          assert.equal(sessions[0].messages,undefined);
        })()""")

    def test_manual_handoff_without_meter_handles_failure_and_navigation(self):
        self.run_case(r"""(async()=>{
          active.messages=[{role:'user',text:'request'},{role:'assistant',text:'reply'}];
          const source=active,calls=[];let finish,retry=false;
          sessionMetadata=item=>({id:item.id,title:item.title,workspace:item.workspace});
          api=async(path,body)=>{
            calls.push(path);assert.equal(path,'/api/session/continue');assert.deepEqual(body,{id:'A'});
            if(!retry)throw Error('요약을 준비하지 못했어요.');
            return new Promise(done=>finish=done);
          };
          $('branch-open').onclick();assert.equal(calls.length,0);
          assert.equal($('branch-create').disabled,false);assert.equal($('handoff-create').disabled,false);
          await $('handoff-create').onclick();assert.equal(active,source);
          assert.match($('handoff-status').textContent,/준비하지 못/);assert.equal($('handoff-create').disabled,false);
          retry=true;const pending=$('handoff-create').onclick();
          assert.equal($('branch-create').disabled,true);await $('branch-create').onclick();
          active={id:'B',state:'idle'};WorkspaceProductivityActions.contextChanged();
          finish({session:child});await pending;
          assert.equal(active.id,'B');assert.equal(selectedSessions.length,0);assert.equal(sessions[0].id,child.id);
          assert.deepEqual(calls,['/api/session/continue','/api/session/continue']);
        })()""")

    def test_handoff_intro_shows_safe_summary_on_new_work_and_links_back(self):
        self.run_case(r"""(async()=>{
          let renders=0;renderText=(node,text)=>{renders++;node.textContent=text;};
          sessions=[{id:'A',title:'자료 검토'},{id:'B'}];
          active={...active,id:'B',sessionId:null,state:'idle',messages:[],handoff:{
            sourceTaskId:'A',sourceTitle:'자료 검토',summary:'## 진행한 일\n시료 확인\n## 결정 사항\nA 선택\n## 남은 일\n합계 검토'
          }};
          WorkspaceProductivityActions.update();WorkspaceProductivityActions.update();
          assert.equal(renders,1);assert.equal($('handoff-origin').hidden,false);
          assert.equal($('handoff-origin').open,true);assert.match($('handoff-summary').textContent,/합계 검토/);
          assert.match($('handoff-source-open').textContent,/자료 검토/);$('handoff-source-open').onclick();
          assert.equal(selectedSessions[0],'A');
          WorkspaceProductivityActions.update();assert.equal($('handoff-origin').hidden,true);
        })()""")

    def test_palette_focuses_direct_branch_button_and_failure_can_be_retried(self):
        self.run_case(r"""(async()=>{
          const calls=[];const error='복사할 대화 기록을 찾을 수 없어요.';
          api=async(path,body)=>{
            calls.push({path,body});
            if(calls.length===1)throw Error(error);
            return {session:child};
          };
          await WorkspaceProductivityActions.openBranch();
          assert.equal(calls.length,0);assert.equal(document.activeElement,$('branch-create'));
          await $('branch-create').onclick();
          assert.equal($('branch-status').textContent,error);assert.equal($('branch-dialog').open,true);
          assert.equal($('branch-create').disabled,false);assert.equal(sessions.length,1);
          assert.equal(active.id,'A');assert.equal(selectedSessions.length,0);
          await $('branch-create').onclick();
          assert.equal(active.id,'child');assert.equal($('branch-dialog').open,false);
          assert.deepEqual(calls,Array(2).fill({path:'/api/session/branch',body:{id:'A'}}));
        })()""")


if __name__ == '__main__':
    unittest.main()
