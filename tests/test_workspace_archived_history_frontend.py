"""Archive search and restore behavior using shipped JavaScript, no live app."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
const proto=Object.getPrototypeOf($('sessions'));
proto.querySelectorAll=function(selector){let out=[];for(const child of this.children){if(child.tagName===selector.toUpperCase())out.push(child);out.push(...(child.querySelectorAll?.(selector)||[]));}return out;};
proto.close=function(){this.open=false;this.onclose?.();for(const fn of this.listeners?.close||[])fn();};
const oldCreate=document.createElement,oldGet=document.getElementById,created=new Map();
document.createElement=tag=>{const node=oldCreate(tag);Object.defineProperty(node,'id',{get(){return this._id;},set(value){this._id=value;created.set(value,this);}});return node;};
document.getElementById=id=>created.get(id)||oldGet(id);
let timers=[],notices=[],restored=[],calls=[],reply=[];
setTimeout=fn=>{timers.push(fn);return timers.length;};clearTimeout=id=>{timers[id-1]=null;};
const tick=async()=>{await Promise.resolve();await Promise.resolve();};
const hooks={api:(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply.push(resolve));},toast:text=>notices.push(text),onRestore:row=>restored.push(row),showDialog:id=>document.getElementById(id).showModal()};
"""


@unittest.skipUnless(NODE, 'Node.js is required')
class ArchivedHistoryFrontendTests(unittest.TestCase):
    def run_case(self, code):
        module = (ROOT/'local_app/web/archived-tasks.js').read_text(encoding='utf-8')
        script = SETUP + module + '\nconst archive=WorkspaceArchivedTasks.mount(hooks);\n' + code
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), script],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_closed_dialog_discards_late_list_response_and_releases_rows(self):
        self.run_case(r"""(async()=>{
          archive.open();assert.equal(calls.length,1);document.getElementById('archived-tasks-dialog').close();
          reply.shift()({sessions:[{id:'A',title:'old'}],total:1});await tick();
          assert.equal($('archived-tasks-list').children.length,0);assert.equal(calls.length,1);
        })()""")

    def test_search_debounces_and_stale_response_cannot_replace_latest_query(self):
        self.run_case(r"""(async()=>{
          archive.open();const old=reply.shift();$('archived-tasks-query').value='분석';$('archived-tasks-query').oninput();
          assert.equal(calls.length,1);for(const timer of timers)timer?.();assert.equal(calls.length,2);
          assert.match(calls[1].path,/offset=0/);reply.shift()({sessions:[{id:'new',title:'새 결과'}],total:1,activeCount:1,activeLimit:500});await tick();
          old({sessions:[{id:'old',title:'이전 결과'}],total:1});await tick();
          assert.equal($('archived-tasks-list').children[0].children[0].children[0].textContent,'새 결과');
        })()""")

    def test_restore_sends_only_explicit_id_once_and_never_executes_a_request(self):
        self.run_case(r"""(async()=>{
          archive.open();reply.shift()({sessions:[{id:'A',title:'초안 업무',workspace:'C:/work'}],total:1,activeCount:0,activeLimit:500});await tick();
          const button=$('archived-tasks-list').children[0].children[1];button.onclick();button.onclick();
          assert.equal(calls.length,2);assert.equal(calls[1].path,'/api/sessions/restore');assert.equal(calls[1].body.id,'A');
          reply.shift()({ok:true,restored:true,session:{id:'A',trusted:false}});await tick();
          assert.equal(restored.length,1);assert.equal(restored[0].session.trusted,false);
          assert.equal(document.getElementById('archived-tasks-dialog').open,false);assert.equal($('archived-tasks-list').children.length,0);
          assert.equal(calls.length,2);
        })()""")


if __name__ == '__main__': unittest.main()
