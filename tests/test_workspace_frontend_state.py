"""Execute the shipped UI state machine with deterministic, delayed HTTP replies.

No browser, network, installed Claude configuration, or model calls are needed.
The small DOM double exercises interaction state, not visual layout.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') {
    this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={};
    this.value=''; this.textContent=''; this.hidden=false; this.disabled=false;
    this.readOnly=false; this.checked=false; this.attributes={}; this.parent=null;this.isConnected=true;
    this.scrollHeight=100; this.scrollTop=0; this.clientHeight=100;
    const classes=new Set();
    this.classList={add:(...names)=>names.forEach(n=>classes.add(n)),
      remove:(...names)=>names.forEach(n=>classes.delete(n)),contains:n=>classes.has(n),
      toggle:(name,force)=>{const add=force===undefined?!classes.has(name):force;add?classes.add(name):classes.delete(name);return add;}};
    Object.defineProperty(this,'className',{set:value=>{classes.clear();String(value).split(/\s+/).forEach(n=>classes.add(n));},get:()=>[...classes].join(' ')});
  }
  append(...nodes){for(const n of nodes){if(n&&typeof n==='object'){if(n.parent)n.parent.children=n.parent.children.filter(c=>c!==n);n.parent=this;n.isConnected=true;}this.children.push(n);}}
  prepend(...nodes){for(const n of [...nodes].reverse()){if(n&&typeof n==='object'){if(n.parent)n.parent.children=n.parent.children.filter(c=>c!==n);n.parent=this;n.isConnected=true;}this.children.unshift(n);}}
  replaceChildren(...nodes){for(const n of this.children)if(n&&typeof n==='object')n.parent=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;this.isConnected=false;}}
  setAttribute(name,value){this.attributes[name]=String(value);}
  querySelector(selector){const matches=n=>selector.startsWith('.')?n.classList?.contains(selector.slice(1)):n.tagName===selector.toUpperCase();for(const child of this.children){if(matches(child))return child;const nested=child.querySelector?.(selector);if(nested)return nested;}return null;}
  focus(){context.document.activeElement=this;} showModal(){this.open=true;} close(value=''){this.returnValue=value;this.open=false;this.onclose?.();}
  closest(selector){for(let n=this;n;n=n.parent)if(n.tagName===selector.toUpperCase())return n;return null;}
  scrollIntoView(){this.scrolledIntoView=true;}
  get firstChild(){return this.children[0];}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,
  location:{hash:''},history:{replaceState(){}},
  sessionStorage:{getItem(){return 'test';},setItem(){}},
  document:{body:new Element('body'),activeElement:null,getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),
    querySelector:selector=>get(selector),querySelectorAll:()=>[],addEventListener(){}},
  setInterval(){},setTimeout(){return 1;},clearTimeout(){},requestAnimationFrame:fn=>fn(),
  fetch:async()=>({ok:true,json:async()=>({sessions:[],demo:false})}),
  confirm:()=>false};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});
const scenario=process.argv[3];
setImmediate(async()=>{
  try {await vm.runInContext(scenario,context,{filename:'scenario.js'});}
  catch(error){console.error(error.stack||error);process.exitCode=1;}
});
"""


@unittest.skipUnless(NODE, "Node.js is required for the shipped UI state regression checks")
class WorkspaceFrontendStateTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), javascript],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_delayed_pin_response_never_changes_selected_workspace(self):
        self.run_case(r"""(async()=>{
          active={id:'A',title:'A',state:'running',pinned:false};
          sessions=[{...active},{id:'B',title:'B',state:'idle'}];
          let resolveReply; api=()=>new Promise(resolve=>resolveReply=resolve);
          const pending=updateSession('A',{pinned:true});
          active={id:'B',title:'B',state:'idle',messages:[{text:'B conversation'}]};
          resolveReply({session:{id:'A',title:'A pinned',pinned:true,updated:10,state:'running'}});
          await pending;
          assert.equal(active.id,'B');assert.equal(active.messages[0].text,'B conversation');
          assert.equal(sessions.find(row=>row.id==='A').pinned,true);
        })()""")

    def test_delayed_metadata_reply_preserves_newer_completed_state_and_text(self):
        self.run_case(r"""(async()=>{
          active={id:'A',title:'A',state:'running',messages:[]};sessions=[{...active}];
          let resolveReply;api=()=>new Promise(resolve=>resolveReply=resolve);
          const pending=updateSession('A',{title:'renamed'});
          active.state='done';active.messages.push({text:'completed reply'});sessions[0].state='done';
          resolveReply({session:{id:'A',title:'renamed',pinned:false,updated:10,state:'running',messages:[]}});
          await pending;
          assert.equal(active.state,'done');assert.equal(active.title,'renamed');
          assert.equal(active.messages[0].text,'completed reply');assert.equal(sessions[0].state,'done');
        })()""")

    def test_submit_locks_composer_and_preserves_stream_until_acknowledgement(self):
        self.run_case(r"""(async()=>{
          active={id:'A',title:'A',state:'idle',trusted:true};sessions=[{...active}];
          $('prompt').value='request';attachments=['input.csv'];drafts.set('A',{text:'request'});
          let resolveReply;api=()=>new Promise(resolve=>resolveReply=resolve);
          refreshSessionMeta=async()=>{};
          const pending=submit();
          assert.equal($('prompt').readOnly,true);assert.equal($('attach').disabled,true);
          assert.equal($('attach-path').disabled,true);
          renderDelta({messageId:'answer',index:2,text:'already streamed prefix'});
          const bubble=streaming.get('answer:2');
          resolveReply({ok:true});await pending;
          assert.equal(streaming.get('answer:2'),bubble);
          assert.equal(bubble.querySelector('.message-body').textContent,'already streamed prefix');
          assert.equal($('prompt').value,'');assert.equal(attachments.length,0);
          assert.equal($('prompt').readOnly,false);assert.equal($('attach').disabled,false);
          assert.equal($('conversation').children.filter(n=>n.classList.contains('user')).length,1);
        })()""")

    def test_failed_submit_preserves_input_and_attachments_without_retry(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle',trusted:true};sessions=[{...active}];
          $('prompt').value='keep this draft';attachments=['keep.csv'];let calls=0;
          api=async()=>{calls++;throw new Error('connection failed');};
          await submit();
          assert.equal(calls,1);assert.equal($('prompt').value,'keep this draft');
          assert.equal(attachments[0],'keep.csv');assert.equal($('prompt').readOnly,false);
          assert.equal($('conversation').children.filter(n=>n.classList.contains('user')).length,0);
        })()""")

    def test_delayed_submit_ack_does_not_clear_other_workspaces_draft(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle',trusted:true};sessions=[{...active}];
          $('prompt').value='A request';attachments=['A.csv'];
          let resolveReply;api=()=>new Promise(resolve=>resolveReply=resolve);
          const pending=submit();
          active={id:'B',state:'idle',trusted:true};$('prompt').value='B draft';attachments=['B.csv'];
          resolveReply({ok:true});await pending;
          assert.equal(active.id,'B');assert.equal($('prompt').value,'B draft');assert.equal(attachments[0],'B.csv');
        })()""")

    def test_stopped_event_removes_expired_approvals_and_keeps_partial_text(self):
        self.run_case(r"""(()=>{
          active={id:'A',state:'approval'};sessions=[{...active}];
          $('requests').append(el('section','stale permission'));
          renderDelta({messageId:'answer',index:2,text:'received partial answer'});
          const bubble=streaming.get('answer:2');
          handleEvent({type:'status',data:{state:'stopped',label:'stopped'}});
          assert.equal(active.state,'stopped');assert.equal($('requests').children.length,0);
          assert.equal(streaming.size,0);assert.equal(bubble.classList.contains('streaming'),false);
          assert.equal(bubble.querySelector('.message-body').textContent,'received partial answer');
          assert.equal(bubble.querySelector('.message-interrupted').textContent,'중지 전까지 받은 내용');
        })()""")

    def test_delayed_model_reply_only_updates_model_not_current_running_state(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'done',modelOverride:null,connection:{model:'original',capabilities:{setModel:true}}};
          let resolveReply;api=()=>new Promise(resolve=>resolveReply=resolve);
          const pending=setModel('chosen');active.state='running';active.messages=[{text:'new turn'}];
          resolveReply({modelOverride:'chosen',session:{id:'A',state:'done',messages:[],connection:{model:'chosen'}}});
          await pending;
          assert.equal(active.state,'running');assert.equal(active.messages[0].text,'new turn');
          assert.equal(active.modelOverride,'chosen');assert.equal(active.connection.model,'chosen');
          assert.equal($('model-apply').disabled,true);
        })()""")


if __name__ == "__main__":
    unittest.main()
