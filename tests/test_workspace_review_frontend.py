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
  append(...nodes){for(const node of nodes){if(node?.tagName==='FRAGMENT')this.append(...node.children);else this.children.push(node);}}
  replaceChildren(...nodes){this.children=[];this._value='';this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
  showModal(){this.open=true;}
  close(){if(!this.open)return;this.open=false;for(const fn of this.listeners.close||[])fn();}
  focus(){context.document.activeElement=this;}
  set innerHTML(_){throw new Error('Untrusted review content must never become HTML');}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element(id==='changes-run'?'select':id.endsWith('-dialog')?'dialog':'div'));return nodes.get(id);};
const context={assert,console,encodeURIComponent,Date,
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
          assert.match(flatText($('changes-content')),/<script>danger<\/script>/);
        })()""")

    def test_branch_preview_and_creation_send_exactly_one_request_without_ai_dispatch(self):
        self.run_case(r"""(async()=>{
          const calls=[];let created;
          api=async(path,body)=>{
            calls.push({path,body});
            if(body===undefined)return {available:true,reason:'ready'};
            return new Promise(resolve=>created=resolve);
          };
          await WorkspaceProductivityActions.openBranch();
          const first=$('branch-create').onclick();const duplicate=$('branch-create').onclick();
          assert.equal(calls.length,2);assert.equal(calls[0].path,'/api/session/branch?id=A');
          assert.equal(calls[1].path,'/api/session/branch');assert.deepEqual(calls[1].body,{id:'A'});
          created({ok:true,session:child});await first;await duplicate;
          assert.equal(sessions.filter(row=>row.id==='child').length,1);
          assert.equal(selectedSessions.length,1);assert.equal(selectedSessions[0],'child');assert.equal(renderCount,1);
          assert.equal(sessions.find(row=>row.id==='child').messages,undefined);
          assert.equal(sessions.find(row=>row.id==='child').artifacts,undefined);
          assert.ok(calls.every(row=>row.path.startsWith('/api/session/branch')));
        })()""")

    def test_created_branch_retained_if_panel_closed_during_request(self):
        self.run_case(r"""(async()=>{
          let created;
          api=async(path,body)=>body===undefined?{available:true}:new Promise(resolve=>created=resolve);
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
          api=async(path,body)=>body===undefined?{available:true}:new Promise(resolve=>created=resolve);
          await WorkspaceProductivityActions.openBranch();const pending=$('branch-create').onclick();
          active={...active,id:'B'};WorkspaceProductivityActions.contextChanged();
          created({ok:true,session:child});await pending;
          assert.equal(sessions.filter(row=>row.id==='child').length,1);
          assert.equal(active.id,'B');assert.equal(selectedSessions.length,0);
        })()""")

    def test_stale_branch_preview_and_busy_or_demo_states_never_create(self):
        self.run_case(r"""(async()=>{
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve);};
          const preview=WorkspaceProductivityActions.openBranch();$('branch-cancel').onclick();
          reply({available:true});await preview;
          assert.equal($('branch-dialog').open,false);assert.equal($('branch-create').disabled,true);
          for(const state of ['running','approval','question','starting']){
            active.state=state;WorkspaceProductivityActions.update();
            assert.equal($('branch-open').disabled,true);await WorkspaceProductivityActions.openBranch();
          }
          active.state='done';boot.demo=true;await WorkspaceProductivityActions.openBranch();
          boot.demo=false;sending=true;await WorkspaceProductivityActions.openBranch();
          assert.equal(calls,1);
        })()""")


if __name__ == '__main__':
    unittest.main()
