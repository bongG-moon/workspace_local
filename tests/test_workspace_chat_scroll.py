"""Exercise chat follow intent and layout changes without a browser or timers.

The DOM double clamps scrollTop like a browser. ResizeObserver is delivered
explicitly so a layout-induced scroll can be distinguished from user input.
Existing productivity tests retain transport delivery and buffer coverage.
"""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.contains=function(node){for(let n=node;n;n=n.parent)if(n===this)return true;return false;};
Object.defineProperty(Element.prototype,'parentElement',{get(){return this.parent;}});
Element.prototype.matches=function(selector){
  return selector.split(',').some(part=>{
    part=part.trim();
    if(part.startsWith('.'))return this.classList.contains(part.slice(1));
    if(part==='[contenteditable]'||part==='[contenteditable="true"]')return !!this.isContentEditable;
    return part.toUpperCase()===this.tagName;
  });
};
Element.prototype.closest=function(selector){for(let n=this;n;n=n.parent)if(n.matches?.(selector))return n;return null;};
Element.prototype.getBoundingClientRect=function(){return {top:0,bottom:this.clientHeight,left:0,right:this.offsetWidth||600,width:this.offsetWidth||600,height:this.clientHeight};};
Element.prototype.getAttribute=function(name){return this.attributes[name]??null;};
context.document.listeners={};
context.document.addEventListener=function(name,fn){(this.listeners[name]??=[]).push(fn);};
context.listeners={};
context.addEventListener=function(name,fn){(context.listeners[name]??=[]).push(fn);};
context.window=context;
context.frames=new Map();context.timers=new Map();context.observers=[];let serial=0;
context.requestAnimationFrame=fn=>{context.frames.set(++serial,fn);return serial;};
context.cancelAnimationFrame=id=>context.frames.delete(id);
context.setTimeout=fn=>{context.timers.set(++serial,fn);return serial;};
context.clearTimeout=id=>context.timers.delete(id);
context.setInterval=()=>{throw Error('chat following must not start a polling timer');};
context.getComputedStyle=node=>({overflowY:node.overflowY||'visible',overflowX:node.overflowX||'visible'});
context.ResizeObserver=class {
  constructor(callback){this.callback=callback;this.targets=new Set();this.disconnects=0;context.observers.push(this);}
  observe(node){if(node)this.targets.add(node);}
  unobserve(node){this.targets.delete(node);}
  disconnect(){this.targets.clear();this.disconnects++;}
};
const area=get('work-area');
area.scrollHeight=1000;area.clientHeight=200;area.clientWidth=600;area.clientLeft=0;area.offsetWidth=618;area.overflowY='auto';
let position=800;area.scrollWrites=[];
Object.defineProperty(area,'scrollTop',{get:()=>position,set:value=>{position=Math.max(0,Math.min(Number(value),Math.max(0,area.scrollHeight-area.clientHeight)));area.scrollWrites.push(position);}});
get('task-view').append(get('conversation'),get('progress-view'),get('requests'),get('workspace-choice'));
area.append(get('task-view'));
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'rendering.js'});
const scenario=process.argv[3];
""")

SETUP = r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'running',trusted:true,messages:[]};sessions=[{...active}];
const area=$('work-area');
const emit=(node,name,event={})=>{const full={target:node,currentTarget:node,isTrusted:true,...event};for(const fn of node.listeners?.[name]||[])fn(full);};
const flushFrames=()=>{const batch=[...frames.values()];frames.clear();for(const fn of batch)fn();};
const flushTimers=()=>{const batch=[...timers.values()];timers.clear();for(const fn of batch)fn();};
const resize=node=>{for(const observer of observers)if(observer.targets.has(node))observer.callback([{target:node,contentRect:node.getBoundingClientRect()}],observer);};
const layout=height=>{area.scrollHeight=height;resize($('task-view'));};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
WorkspaceStream.changed();flushFrames();area.scrollWrites.length=0;
"""


@unittest.skipUnless(NODE, "Node.js is required for chat follow regressions")
class WorkspaceChatScrollTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), SETUP + script,
             str(ROOT / "local_app/web/rendering.js")],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_growth_and_layout_scroll_keep_latest_pinned(self):
        self.run_case(r"""
          layout(1450);
          // Browser layout/anchoring can dispatch scroll before the follow RAF.
          emit(area,'scroll');
          assert.equal(WorkspaceStream.isFollowing(),true);
          flushFrames();assert.equal(area.scrollTop,1250);
          assert.equal($('latest-response').hidden,true);
          layout(1710);emit(area,'scroll');flushFrames();
          assert.equal(area.scrollTop,1510);
        """)

    def test_tool_and_image_growth_coalesce_to_one_frame_without_polling(self):
        self.run_case(r"""
          assert.equal(observers.length,1);
          assert.ok(observers[0].targets.has(area));
          assert.ok(observers[0].targets.has($('task-view')));
          for(let i=0;i<30;i++){layout(1010+i*10);resize(area);WorkspaceStream.changed();}
          assert.equal(frames.size,1);assert.equal(timers.size,0);
          flushFrames();assert.equal(area.scrollTop,1100);
          assert.equal(area.scrollWrites.length,1);
          assert.equal(frames.size,0);assert.equal(timers.size,0);
        """)

    def test_viewport_resize_keeps_pin_but_preserves_reading_position(self):
        self.run_case(r"""
          area.clientHeight=120;resize(area);flushFrames();assert.equal(area.scrollTop,880);
          emit(area,'wheel',{deltaY:-200});area.scrollTop=450;emit(area,'scroll');
          area.clientHeight=300;resize(area);flushFrames();
          assert.equal(area.scrollTop,450);assert.equal(WorkspaceStream.isFollowing(),false);
          assert.equal($('latest-response').hidden,false);
        """)

    def test_small_upward_wheel_detaches_even_close_to_bottom(self):
        self.run_case(r"""
          WorkspaceStream.changed();emit(area,'wheel',{deltaY:-35});area.scrollTop=765;emit(area,'scroll');
          flushFrames();assert.equal(area.scrollTop,765);assert.equal(WorkspaceStream.isFollowing(),false);
          layout(1200);flushFrames();assert.equal(area.scrollTop,765);
          $('latest-response').onclick();flushFrames();assert.equal(area.scrollTop,1000);
          assert.equal(WorkspaceStream.isFollowing(),true);
          layout(1400);flushFrames();assert.equal(area.scrollTop,1200);
        """)

    def test_layout_after_downward_wheel_rejoins_without_immediately_detaching(self):
        self.run_case(r"""
          emit(area,'wheel',{deltaY:-200});area.scrollTop=300;emit(area,'scroll');
          emit(area,'wheel',{deltaY:600});area.scrollTop=800;emit(area,'scroll');
          assert.equal(WorkspaceStream.isFollowing(),true);
          // A image may grow during the same gesture's short intent window.
          layout(1500);emit(area,'scroll');flushFrames();
          assert.equal(area.scrollTop,1300);assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_native_scrollbar_drag_holds_position_until_dragged_to_bottom(self):
        self.run_case(r"""
          emit(area,'pointerdown',{button:0,clientX:610,clientY:150,pointerType:'mouse'});
          area.scrollTop=420;emit(area,'scroll');
          emit(document,'pointerup',{target:area});
          layout(1500);flushFrames();assert.equal(area.scrollTop,420);
          assert.equal(WorkspaceStream.isFollowing(),false);
          emit(area,'pointerdown',{button:0,clientX:610,clientY:100,pointerType:'mouse'});
          area.scrollTop=1300;emit(area,'scroll');emit(document,'pointerup',{target:area});
          assert.equal(WorkspaceStream.isFollowing(),true);
          layout(1700);flushFrames();assert.equal(area.scrollTop,1500);
        """)

    def test_clicking_content_does_not_convert_layout_scroll_into_user_scroll(self):
        self.run_case(r"""
          emit(area,'pointerdown',{target:$('conversation'),button:0,clientX:200,clientY:100,pointerType:'mouse'});
          layout(1400);emit(area,'scroll');emit(document,'pointerup',{target:$('conversation')});
          flushFrames();assert.equal(area.scrollTop,1200);assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_touch_reading_upward_is_preserved_after_layout_growth(self):
        self.run_case(r"""
          emit(area,'touchstart',{touches:[{identifier:1,clientY:120}]});
          emit(area,'touchmove',{touches:[{identifier:1,clientY:200}]});
          area.scrollTop=650;emit(area,'scroll');
          emit(area,'touchend',{touches:[],changedTouches:[{identifier:1,clientY:200}]});
          layout(1600);flushFrames();assert.equal(area.scrollTop,650);
          assert.equal(WorkspaceStream.isFollowing(),false);
        """)

    def test_keyboard_reading_preserves_position_and_end_rejoins_latest(self):
        self.run_case(r"""
          emit(area,'keydown',{key:'PageUp'});area.scrollTop=500;emit(area,'scroll');
          layout(1300);flushFrames();assert.equal(area.scrollTop,500);
          assert.equal(WorkspaceStream.isFollowing(),false);
          emit(area,'keydown',{key:'End'});area.scrollTop=1100;emit(area,'scroll');
          layout(1450);flushFrames();assert.equal(area.scrollTop,1250);
          assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_nested_code_wheel_and_scroll_do_not_detach_chat(self):
        self.run_case(r"""
          const code=el('pre'),span=el('span','code');code.append(span);$('conversation').append(code);
          code.overflowY='auto';code.scrollHeight=700;code.clientHeight=150;code.scrollTop=200;
          emit(area,'wheel',{target:span,deltaY:-40});code.scrollTop=160;emit(area,'scroll',{target:code});
          layout(1400);flushFrames();assert.equal(area.scrollTop,1200);
          assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_nested_progress_keyboard_touch_and_editable_caret_are_isolated(self):
        self.run_case(r"""
          const body=el('div'),span=el('span','progress');body.append(span);$('progress-view').append(body);
          body.className='progress-body';body.overflowY='auto';body.scrollHeight=900;body.clientHeight=160;body.scrollTop=300;
          emit(area,'keydown',{target:body,key:'PageUp'});body.scrollTop=200;emit(area,'scroll',{target:body});
          emit(area,'touchstart',{target:span,touches:[{identifier:1,clientY:100}]});
          emit(area,'touchmove',{target:span,touches:[{identifier:1,clientY:160}]});
          emit(area,'touchend',{target:span,touches:[]});
          const input=el('textarea');$('requests').append(input);
          emit(area,'keydown',{target:input,key:'ArrowUp'});
          layout(1600);flushFrames();assert.equal(area.scrollTop,1400);
          assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_hidden_window_suspends_visual_follow_and_resumes_when_visible(self):
        self.run_case(r"""
          document.hidden=true;emit(document,'visibilitychange');
          layout(1700);WorkspaceStream.changed();emit(area,'scroll');flushFrames();
          assert.equal(area.scrollTop,800);assert.equal(frames.size,0);assert.equal(timers.size,0);
          document.hidden=false;emit(document,'visibilitychange');flushFrames();
          assert.equal(area.scrollTop,1500);assert.equal(WorkspaceStream.isFollowing(),true);
        """)

    def test_capability_view_cannot_scroll_and_restores_previous_follow_intent(self):
        self.run_case(r"""
          let catalog=false;globalThis.WorkspaceCapabilities={isOpen:()=>catalog};
          WorkspaceStream.suspend();catalog=true;area.scrollTop=0;emit(area,'scroll');
          layout(1800);WorkspaceStream.changed();flushFrames();assert.equal(area.scrollTop,0);
          assert.equal(frames.size,0);assert.equal($('latest-response').hidden,true);
          catalog=false;WorkspaceStream.resume();flushFrames();assert.equal(area.scrollTop,1600);
          emit(area,'wheel',{deltaY:-100});area.scrollTop=1200;emit(area,'scroll');
          WorkspaceStream.suspend();catalog=true;area.scrollTop=0;emit(area,'scroll');
          layout(1900);WorkspaceStream.changed();flushFrames();
          catalog=false;area.scrollTop=1200;WorkspaceStream.resume();flushFrames();
          assert.equal(area.scrollTop,1200);assert.equal(WorkspaceStream.isFollowing(),false);
        """)

    def test_home_and_empty_workspace_do_not_follow(self):
        self.run_case(r"""
          active=null;layout(1400);WorkspaceStream.changed();emit(area,'scroll');flushFrames();
          assert.equal(area.scrollTop,800);assert.equal($('latest-response').hidden,true);
          assert.equal(frames.size,0);assert.equal(timers.size,0);
        """)

    def test_reset_cancels_previous_workspace_frame_and_reuses_observer(self):
        self.run_case(r"""
          layout(1500);assert.equal(frames.size,1);
          WorkspaceStream.reset();active={id:'B',state:'idle'};
          flushFrames();assert.equal(area.scrollTop,800);
          WorkspaceStream.changed();flushFrames();assert.equal(area.scrollTop,1300);
          assert.equal(WorkspaceStream.isFollowing(),true);assert.equal(observers.length,1);
          assert.equal(timers.size,0);
        """)

    def test_sending_a_new_message_resumes_following_immediately(self):
        self.run_case(r"""(async()=>{
          emit(area,'wheel',{deltaY:-200});area.scrollTop=300;emit(area,'scroll');
          active.state='idle';sessions=[{...active}];$('prompt').value='이어서 분석해 주세요';
          api=async()=>({ok:true});refreshSessionMeta=async()=>{};
          await submit();flushFrames();assert.equal(WorkspaceStream.isFollowing(),true);
          assert.equal(area.scrollTop,800);
          layout(1450);flushFrames();assert.equal(area.scrollTop,1250);
        })()""")

    def test_restored_approval_reveal_does_not_detach_following(self):
        self.run_case(r"""
          active.state='approval';
          const card=el('section','restored approval');card.dataset.requestId='approval-1';$('requests').append(card);
          revealRequest(card);flushFrames();assert.equal(card.scrolledIntoView,true);
          // Native scrollIntoView may reveal the top of a tall approval card.
          area.scrollTop=500;emit(area,'scroll');
          assert.equal(WorkspaceStream.isFollowing(),true);
          handleEvent({type:'request_closed',data:{id:'approval-1',state:'running'}});
          area.scrollHeight=1500;
          handleEvent({type:'assistant',data:{text:'approval accepted; continuing work'}});
          flushFrames();assert.equal(area.scrollTop,1300);
          assert.equal(WorkspaceStream.isFollowing(),true);
        """)


if __name__ == "__main__":
    unittest.main()
