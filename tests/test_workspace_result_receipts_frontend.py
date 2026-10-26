"""Run the shipped read-receipt observer against deterministic view changes."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
HARNESS = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const frames=new Map(),timers=new Map(),windows=new Map(),documents=new Map();
const clock={now:10000,id:0},view={focused:true,secondary:false,modal:false,locked:false};
class FakeDate extends Date {static now(){return clock.now;}}
class Element {
 constructor(id=''){this.id=id;this.hidden=false;this.isConnected=true;this.children=[];this.dataset={};this.parent=null;this.handlers=new Map();this.rect={top:20,bottom:100,left:0,right:400,width:400,height:80};const classes=new Set();this.classList={contains:value=>classes.has(value),add:(...values)=>values.forEach(v=>classes.add(v)),remove:value=>classes.delete(value)};}
 addEventListener(name,fn){this.handlers.set(name,fn);}
 getBoundingClientRect(){return {...this.rect};}
 contains(node){return node===this||this.children.some(child=>child.contains(node));}
 append(node){node.parent=this;this.children.push(node);}
}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);};
const area=get('work-area');area.rect={top:0,bottom:400,left:0,right:400,width:400,height:400};
const requests=[],renders={count:0};
const context={assert,console,Date:FakeDate,clock,view,nodes,requests,renders,
 $:get,sessions:[],active:null,selectionGeneration:1,appClosed:false,innerHeight:600,innerWidth:500,
 secondaryViewOpen:()=>view.secondary,renderSessions:()=>renders.count++,
 WorkspaceUpgrade:{isLocked:()=>view.locked},
 document:{visibilityState:'visible',hasFocus:()=>view.focused,querySelector:selector=>selector==='dialog[open]'&&view.modal?{}:null,elementFromPoint:()=>context.hit,addEventListener:(name,fn)=>documents.set(name,fn)},
 addEventListener:(name,fn)=>windows.set(name,fn),
 requestAnimationFrame:fn=>{const id=++clock.id;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
 setTimeout:(fn,delay)=>{const id=++clock.id;timers.set(id,{at:clock.now+delay,fn});return id;},clearTimeout:id=>timers.delete(id),
 api:async(path,body)=>{requests.push({path,body});return {id:body.id,resultReceipt:{...context.active.resultReceipt,seenRevision:body.revision}};},
 hit:null,
};
context.flushFrames=()=>{for(let n=0;frames.size;n++){assert.ok(n<30,'bounded render frames');const batch=[...frames.values()];frames.clear();for(const fn of batch)fn();}};
context.advance=ms=>{context.flushFrames();clock.now+=ms;for(const [id,timer]of [...timers])if(timer.at<=clock.now){timers.delete(id);timer.fn();}context.flushFrames();};
context.settle=()=>new Promise(resolve=>setImmediate(resolve));
context.fireWindow=name=>windows.get(name)?.();context.fireDocument=name=>documents.get(name)?.();
context.message=(runId,{streaming=false,role='assistant',top=20}={})=>{const node=new Element();node.dataset.runId=runId;node.classList.add('message',role);if(streaming)node.classList.add('streaming');node.rect={top,bottom:top+80,left:0,right:400,width:400,height:80};get('conversation').append(node);context.hit=node;return node;};
context.footer=runId=>{const node=new Element();node.dataset.runId=runId;node.dataset.state='done';node.classList.add('request-timing');get('conversation').append(node);context.hit=node;return node;};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'result-receipts.js'});
Promise.resolve(vm.runInContext('(async()=>{'+process.argv[3]+'})()',context,{filename:'scenario.js'})).catch(error=>{console.error(error.stack||error);process.exitCode=1;});
'''
SETUP = r'''
const receipt=(revision=1,seenRevision=0,runId='run-1')=>({revision,seenRevision,runId});
const task=(id='A',resultReceipt=receipt())=>({id,state:'done',resultReceipt});
active=task();sessions=[active];
const receipts=WorkspaceResultReceipts;
'''


@unittest.skipUnless(NODE, 'Node.js is required for read-receipt frontend tests')
class WorkspaceResultReceiptsFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT/'local_app/web/result-receipts.js'), SETUP+script],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_visible_completed_answer_acks_after_dwell_and_keeps_execution_state(self):
        self.run_case(r'''
          message('run-1');receipts.viewReady();advance(299);assert.equal(requests.length,0);
          advance(1);assert.equal(requests.length,1);await settle();flushFrames();
          assert.equal(requests[0].path,'/api/session/result-read');assert.deepEqual(requests[0].body,{id:'A',revision:1});
          assert.equal(active.state,'done');assert.equal(receipts.unread(active),false);
          for(let i=0;i<5;i++){receipts.changed();advance(300);}assert.equal(requests.length,1);
        ''')

    def test_background_modal_secondary_and_locked_views_do_not_ack(self):
        self.run_case(r'''
          message('run-1');
          const cases=[
            [()=>document.visibilityState='hidden',()=>document.visibilityState='visible'],
            [()=>view.focused=false,()=>view.focused=true],
            [()=>view.modal=true,()=>view.modal=false],
            [()=>view.secondary=true,()=>view.secondary=false],
            [()=>view.locked=true,()=>view.locked=false],
            [()=>$('task-view').hidden=true,()=>$('task-view').hidden=false],
            [()=>$('conversation').hidden=true,()=>$('conversation').hidden=false],
            [()=>appClosed=true,()=>appClosed=false]
          ];
          for(const [hide,restore]of cases){hide();receipts.viewReady();advance(300);await settle();assert.equal(requests.length,0);restore();receipts.reset();}
          receipts.viewReady();advance(300);await settle();assert.equal(requests.length,1);
        ''')

    def test_scroll_out_or_obscured_result_is_not_read_until_visible(self):
        self.run_case(r'''
          const result=message('run-1',{top:500});receipts.viewReady();advance(300);assert.equal(requests.length,0);
          result.rect.top=20;result.rect.bottom=100;hit={};receipts.changed();advance(300);assert.equal(requests.length,0);
          hit=result;receipts.changed();advance(300);await settle();assert.equal(requests.length,1);
        ''')

    def test_blur_or_navigation_during_dwell_cancels_the_read(self):
        self.run_case(r'''
          message('run-1');receipts.viewReady();advance(200);view.focused=false;fireWindow('blur');advance(200);assert.equal(requests.length,0);
          view.focused=true;fireWindow('focus');advance(200);selectionGeneration++;advance(100);assert.equal(requests.length,0);
          receipts.viewReady();advance(300);await settle();assert.equal(requests.length,1);
        ''')

    def test_attention_metadata_does_not_claim_a_streaming_or_unconsumed_final_answer(self):
        self.run_case(r'''
          active.resultReceipt=null;receipts.viewReady();const stream=message('run-1',{streaming:true});
          receipts.apply('A',receipt());advance(300);assert.equal(requests.length,0);
          stream.classList.remove('streaming');receipts.changed({content:true});advance(300);assert.equal(requests.length,0);
          receipts.completed(receipt());advance(300);await settle();assert.equal(requests.length,1);
        ''')

    def test_duplicate_visibility_checks_share_one_pending_read(self):
        self.run_case(r'''
          let reply;api=(path,body)=>{requests.push({path,body});return new Promise(resolve=>reply=resolve);};
          message('run-1');receipts.viewReady();advance(300);
          for(let i=0;i<6;i++){receipts.completed(receipt());receipts.changed();advance(300);}
          assert.equal(requests.length,1);assert.equal(receipts.unread(active),true);
          reply({id:'A',resultReceipt:receipt(1,1)});await settle();advance(300);
          assert.equal(requests.length,1);assert.equal(receipts.unread(active),false);
        ''')

    def test_late_ack_for_previous_result_cannot_clear_new_result(self):
        self.run_case(r'''
          let reply;api=(path,body)=>{requests.push({path,body});return new Promise(resolve=>reply=resolve);};
          message('run-1');receipts.viewReady();advance(300);assert.equal(requests[0].body.revision,1);
          receipts.apply('A',receipt(2,0,'run-2'));view.focused=false;
          reply({id:'A',resultReceipt:receipt(1,1)});await settle();flushFrames();
          assert.equal(active.resultReceipt.revision,2);assert.equal(active.resultReceipt.runId,'run-2');assert.equal(active.resultReceipt.seenRevision,1);
          assert.equal(receipts.unread(active),true);assert.equal(requests.length,1);
          const restored=receipts.mergeSession(task('A',receipt(1,0)));assert.equal(restored.resultReceipt.revision,2);assert.equal(restored.resultReceipt.seenRevision,1);
        ''')

    def test_failed_ack_keeps_marker_with_event_driven_backoff(self):
        self.run_case(r'''
          api=async(path,body)=>{requests.push({path,body});throw new Error('storage unavailable');};
          message('run-1');receipts.viewReady();advance(300);await settle();flushFrames();assert.equal(receipts.unread(active),true);
          for(let i=0;i<4;i++){receipts.changed();advance(1000);}assert.equal(requests.length,1);
          advance(2000);assert.equal(requests.length,1,'no autonomous retry timer');
          api=async(path,body)=>{requests.push({path,body});return {id:body.id,resultReceipt:receipt(1,1)};};
          receipts.changed();advance(300);await settle();flushFrames();
          assert.equal(requests.length,2);assert.equal(receipts.unread(active),false);
        ''')

    def test_new_run_preserves_previous_unread_until_that_answer_is_seen(self):
        self.run_case(r'''
          const previous=message('run-1',{top:-150});active.state='running';active.lastRunId='run-2';
          message('run-2',{streaming:true});receipts.viewReady();advance(300);
          assert.equal(requests.length,0);assert.equal(receipts.unread(active),true);assert.equal(active.state,'running');
          previous.rect.top=20;previous.rect.bottom=100;hit=previous;receipts.changed();advance(300);await settle();
          assert.equal(requests.length,1);assert.equal(requests[0].body.revision,1);assert.equal(active.state,'running');assert.equal(active.lastRunId,'run-2');
        ''')

    def test_tool_only_completion_uses_done_footer_not_running_user_bubble(self):
        self.run_case(r'''
          message('run-1',{role:'user'});receipts.viewReady();advance(300);assert.equal(requests.length,0);
          footer('run-1');receipts.changed({content:true});advance(300);await settle();assert.equal(requests.length,1);
        ''')

    def test_invalid_receipts_and_wrong_task_ack_do_not_mark_read(self):
        self.run_case(r'''
          for(const value of [null,{},receipt(0,0),receipt(1,2),receipt(1,-1),receipt(1,0,''),receipt(1,0,'x'.repeat(65)),receipt(1.5,0)])assert.equal(receipts.normalize(value),null);
          assert.equal(receipts.unread({}),false);
          api=async(path,body)=>{requests.push({path,body});return {id:'B',resultReceipt:receipt(1,1)};};
          message('run-1');receipts.viewReady();advance(300);await settle();flushFrames();assert.equal(receipts.unread(active),true);assert.equal(active.resultReceipt.seenRevision,0);
        ''')

    def test_ack_for_previous_task_does_not_change_current_task_or_its_receipt(self):
        self.run_case(r'''
          let reply;api=(path,body)=>{requests.push({path,body});return new Promise(resolve=>reply=resolve);};
          message('run-1');receipts.viewReady();advance(300);
          const previous=active;active=task('B',receipt(4,2,'run-b'));active.state='running';sessions.push(active);selectionGeneration++;receipts.reset();
          reply({id:'A',resultReceipt:receipt(1,1)});await settle();flushFrames();
          assert.equal(receipts.unread(previous),false);assert.equal(active.id,'B');assert.equal(active.state,'running');
          assert.equal(active.resultReceipt.revision,4);assert.equal(active.resultReceipt.seenRevision,2);assert.equal(requests.length,1);
        ''')

    def test_stale_metadata_after_ack_does_not_recreate_unread_marker(self):
        self.run_case(r'''
          message('run-1');receipts.viewReady();advance(300);await settle();flushFrames();
          assert.equal(receipts.unread(active),false);
          receipts.apply('A',receipt(1,0));receipts.completed(receipt(1,0));advance(300);await settle();
          assert.equal(receipts.unread(active),false);assert.equal(active.resultReceipt.seenRevision,1);assert.equal(requests.length,1);
        ''')

    def test_failed_navigation_resume_does_not_promote_ahead_metadata_to_rendered_result(self):
        self.run_case(r'''
          active.resultReceipt=null;message('run-1');receipts.viewReady();
          receipts.apply('A',receipt());
          const checkpoint=receipts.reset();assert.equal(checkpoint.id,'A');assert.equal(checkpoint.receipt,null);
          selectionGeneration++;receipts.resume(checkpoint);advance(300);await settle();
          assert.equal(requests.length,0);assert.equal(receipts.unread(active),true);
          receipts.completed(receipt());advance(300);await settle();
          assert.equal(requests.length,1);assert.equal(requests[0].body.revision,1);assert.equal(receipts.unread(active),false);
        ''')

    def test_failed_navigation_resume_preserves_only_previously_rendered_revision(self):
        self.run_case(r'''
          view.focused=false;const previous=message('run-1');receipts.viewReady();
          receipts.apply('A',receipt(2,0,'run-2'));message('run-2');
          const checkpoint=receipts.reset();assert.equal(checkpoint.receipt.revision,1);
          selectionGeneration++;view.focused=true;hit=previous;receipts.resume(checkpoint);advance(300);await settle();flushFrames();
          assert.equal(requests.length,1);assert.equal(requests[0].body.revision,1);
          assert.equal(active.resultReceipt.revision,2);assert.equal(active.resultReceipt.seenRevision,1);assert.equal(receipts.unread(active),true);
          advance(300);await settle();assert.equal(requests.length,1);
        ''')


if __name__ == '__main__':
    unittest.main()
