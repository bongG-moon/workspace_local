"""Exercise actual drag, keyboard and pin handlers without Claude or a browser."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
sessions=['A','B','C'].map((id,index)=>({id,title:'업무 '+id,workspace:'C:/'+id,pinned:false,created:index+1,updated:index+1,state:'idle'}));
boot={};active=null;let notices=[];toast=message=>notices.push(message);
const ids=()=>orderedSessions().map(row=>row.id).join(',');
const row=(id,container='sessions')=>[...$(container).children].find(row=>row.dataset.sessionId===id);
const handle=id=>row(id).querySelector('.session-drag');
const pin=id=>row(id).querySelector('.session-pin');
const proto=Object.getPrototypeOf($('sessions'));Object.defineProperty(proto,'parentElement',{get(){return this.parent;},configurable:true});
proto.contains=function(other){for(let node=other;node;node=node.parentElement)if(node===this)return true;return false;};
proto.closest=function(selector){for(let node=this;node;node=node.parentElement)if(selector[0]==='.'?node.classList.contains(selector.slice(1)):node.tagName===selector.toUpperCase())return node;return null;};
proto.setPointerCapture=function(id){this.capture=id;};proto.hasPointerCapture=function(id){return this.capture===id;};proto.releasePointerCapture=function(id){if(this.capture===id)this.capture=null;};
proto.getBoundingClientRect=function(){if(this.bounds)return this.bounds;if(this.classList.contains('session-row')){const top=this.parent.children.indexOf(this)*100-this.parent.scrollTop;return {left:0,top,right:300,bottom:top+100,width:300,height:100};}return {left:0,top:0,right:300,bottom:300,width:300,height:300};};
globalThis.getComputedStyle=node=>node.testStyle||{};globalThis.innerWidth=1000;globalThis.innerHeight=800;
let hitContainer='sessions',blockedHit=false,frames=[],cancelledFrames=[];
document.elementFromPoint=(x,y)=>{if(blockedHit)return document.body;const box=$(hitContainer);return [...box.children].find(node=>{const bounds=node.getBoundingClientRect();return x>=bounds.left&&x<=bounds.right&&y>=bounds.top&&y<bounds.bottom;})?.querySelector(hitContainer==='home-recents'?'.recent-card':'.session')||box;};
globalThis.requestAnimationFrame=callback=>{frames.push(callback);return frames.length;};globalThis.cancelAnimationFrame=id=>cancelledFrames.push(id);
const event=(x=10,y=10,extra={})=>({pointerId:7,button:0,isPrimary:true,clientX:x,clientY:y,preventDefault(){this.prevented=true;},...extra});
renderSessions();
"""


@unittest.skipUnless(NODE, 'Node.js is required for task order UI checks')
class SessionOrderFrontendTests(unittest.TestCase):
    def run_case(self, code):
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + code],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_manual_order_ignores_metadata_poll_updates_and_keeps_pin_group(self):
        self.run_case(r"""(()=>{
          assert.equal(ids(),'C,B,A');boot.sessionOrder={manual:true,ids:['A','C','B']};
          sessions[1].updated=1000;sessions=sessions.map(row=>({...row}));renderSessions();assert.equal(ids(),'A,C,B');
          sessions.push({id:'D',title:'D',created:4,updated:4,pinned:false});assert.equal(ids(),'D,A,C,B');
          sessions[1].pinned=true;renderSessions();assert.equal(ids(),'B,D,A,C');
          assert.equal(pin('B').attributes['aria-pressed'],'true');assert.ok(pin('B').querySelector('.session-pin-icon'));
          assert.match(handle('A').attributes['aria-label'],/Alt/);
        })()""")

    def test_drag_preserves_source_during_poll_and_uses_before_after_without_selecting_task(self):
        self.run_case(r"""(async()=>{
          let calls=[];api=async(path,body)=>{calls.push({path,body});return {sessionOrder:boot.sessionOrder};};
          selectSession=()=>{throw Error('Drag selected a task');};$('prompt').value='keep draft';attachments=['keep.csv'];
          const original=row('A'),source=handle('A');source.onpointerdown(event(10,250));sessions[1].state='running';renderSessions();assert.equal(row('A'),original);
          const target=row('C'),over=event(30,15);source.onpointermove(over);
          assert.ok(over.prevented);assert.ok(target.classList.contains('drop-before'));assert.ok(original.classList.contains('dragging'));await source.onpointerup(event(30,15));
          assert.equal(ids(),'A,C,B');assert.equal(calls[0].path,'/api/session/reorder');assert.equal(calls[0].body.position,'before');
          const next=handle('A');next.onpointerdown(event(10,50));next.onpointermove(event(30,280));await next.onpointerup(event(30,280));
          assert.equal(ids(),'C,B,A');assert.equal(calls[1].body.position,'after');
          assert.equal($('prompt').value,'keep draft');assert.equal(attachments[0],'keep.csv');
        })()""")

    def test_title_short_click_and_small_pointer_motion_still_open_task_once(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('A short click must not save task order');};
          for(const [dx,dy] of [[0,0],[3,2],[6,0],[12,8],[24,12]]){
            const original=row('A'),title=original.querySelector('.session');
            title.onpointerdown(event(80,250,{target:title}));
            title.onpointermove(event(80+dx,250+dy,{target:title}));
            assert.equal(sessionPointerDrag.active,false);
            await title.onpointerup(event(80+dx,250+dy,{target:title}));
            // Native click follows pointerup on this same connected button.
            assert.equal(row('A'),original);assert.equal(sessionPointerDrag,null);
            await title.onclick(event(80+dx,250+dy,{detail:1,target:title}));
          }
          assert.deepEqual(selected,['A','A','A','A','A']);assert.equal(ids(),'C,B,A');
        })()""")

    def test_grip_reorders_without_opening_and_title_click_still_works(self):
        self.run_case(r"""(async()=>{
          const selected=[],calls=[];selectSession=async id=>{selected.push(id);return true;};
          api=async(path,body)=>{calls.push({path,body});return {sessionOrder:boot.sessionOrder};};
          $('prompt').value='keep title-drag draft';attachments=['keep.zip'];
          const original=row('A'),title=original.querySelector('.session-drag');
          title.onpointerdown(event(80,250,{target:title}));
          title.onpointermove(event(80,15,{target:title}));
          sessions[0].state='running';renderSessions();assert.equal(row('A'),original);
          assert.equal(sessionPointerDrag.active,true);assert.ok(row('C').classList.contains('drop-before'));
          await title.onpointerup(event(80,15,{target:title}));
          assert.deepEqual(selected,[]);assert.equal(ids(),'A,C,B');assert.equal(calls.length,1);
          assert.deepEqual(calls[0],{path:'/api/session/reorder',body:{id:'A',targetId:'C',position:'before'}});
          const next=row('A').querySelector('.session');
          next.onpointerdown(event(80,50,{target:next}));await next.onpointerup(event(80,50,{target:next}));
          await next.onclick(event(80,50,{detail:1,target:next}));
          assert.deepEqual(selected,['A']);assert.equal($('prompt').value,'keep title-drag draft');assert.equal(attachments[0],'keep.zip');
        })()""")

    def test_recent_and_all_tasks_use_grips_for_drag_and_titles_for_click(self):
        self.run_case(r"""(async()=>{
          const selected=[],calls=[];selectSession=async id=>{selected.push(id);return true;};
          api=async(path,body)=>{calls.push({path,body});return {sessionOrder:boot.sessionOrder};};
          showDialog('tasks-dialog');
          for(const container of ['home-recents','all-sessions']){
            boot.sessionOrder={};renderSessions();hitContainer=container;
            const title=row('A',container).querySelector('.session-drag');
            title.onpointerdown(event(80,250,{target:title}));title.onpointermove(event(80,15,{target:title}));
            await title.onpointerup(event(80,15,{target:title}));
            assert.equal(ids(),'A,C,B');assert.equal(selected.length,0);
            const click=row('B',container).querySelector(container==='home-recents'?'.recent-card':'.session');
            click.onpointerdown(event(80,250,{target:click}));await click.onpointerup(event(80,250,{target:click}));
            await click.onclick(event(80,250,{detail:1,target:click}));assert.equal(selected.pop(),'B');
          }
          assert.equal(calls.length,2);assert.ok(calls.every(call=>call.path==='/api/session/reorder'));
        })()""")

    def test_title_cancel_capture_loss_and_escape_cannot_open_or_reorder(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('Cancelled title drag must not save');};
          for(const ending of ['onpointercancel','onlostpointercapture','Escape']){
            const title=row('A').querySelector('.session');
            title.onpointerdown(event(80,250,{target:title}));title.onpointermove(event(80,15,{target:title}));
            if(ending==='Escape')title.onkeydown({key:'Escape',preventDefault(){}});
            else title[ending](event(80,15,{target:title}));
            assert.equal(sessionPointerDrag,null);assert.equal(title.capture,null);
            await title.onclick(event(80,15,{detail:1,target:title}));
            assert.deepEqual(selected,[]);assert.equal(ids(),'C,B,A');
          }
        })()""")

    def test_previous_drag_threshold_and_keyboard_both_open_task(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('Same-row drag must not save');};
          const title=row('A').querySelector('.session');
          title.onpointerdown(event(80,250,{target:title}));title.onpointermove(event(86,250,{target:title}));
          assert.equal(sessionPointerDrag.active,false);await title.onpointerup(event(86,250,{target:title}));
          await title.onclick(event(86,250,{detail:1,target:title}));assert.deepEqual(selected,['A']);
          const keyboard=row('A').querySelector('.session');let prevented=false;
          keyboard.onkeydown?.({key:'Enter',preventDefault(){prevented=true;}});
          assert.equal(prevented,false);await keyboard.onclick({detail:0,preventDefault(){},target:keyboard});
          assert.deepEqual(selected,['A','A']);
          await row('B').querySelector('.session').onclick(event(80,150,{detail:1}));
          assert.deepEqual(selected,['A','A','B']);
        })()""")

    def test_title_release_on_another_row_does_not_open_or_reorder(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('Title movement must not save order');};
          const title=row('A').querySelector('.session');
          title.onpointerdown(event(80,250,{target:title}));title.onpointermove(event(80,15,{target:title}));
          sessions[1].state='running';renderSessions();assert.equal(row('A').querySelector('.session'),title);
          await title.onpointerup(event(80,15,{target:title}));await title.onclick(event(80,15,{detail:1,target:title}));
          assert.deepEqual(selected,[]);assert.equal(sessionPointerDrag,null);assert.equal(ids(),'C,B,A');
          title.onpointerdown(event(80,250,{target:title}));await title.onpointerup(event(85,250,{target:title}));
          await title.onclick(event(85,250,{detail:1,target:title}));assert.deepEqual(selected,['A']);
        })()""")

    def test_title_drag_handlers_do_not_turn_pin_or_remove_into_drag_handles(self):
        self.run_case(r"""(async()=>{
          const mutations=[];selectSession=()=>{throw Error('Action buttons must not open task');};
          updateSession=async(id,patch)=>mutations.push({id,patch});
          hideSession=async id=>mutations.push({id,hide:true});
          const pinButton=pin('A'),remove=row('A').querySelector('.session-remove');
          pinButton.onpointerdown?.(event(10,250,{target:pinButton}));assert.equal(sessionPointerDrag,null);
          await pinButton.onclick();remove.onpointerdown?.(event(10,250,{target:remove}));assert.equal(sessionPointerDrag,null);
          await remove.onclick();assert.deepEqual(mutations,[{id:'A',patch:{pinned:true}},{id:'A',hide:true}]);
          assert.equal(ids(),'C,B,A');
        })()""")

    def test_cancel_before_title_drag_threshold_does_not_open_task(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('Cancelled click must not reorder');};
          const title=row('A').querySelector('.session');
          title.onpointerdown(event(80,250,{target:title}));title.onpointermove(event(82,251,{target:title}));
          title.onpointercancel(event(82,251,{target:title}));
          await title.onclick(event(82,251,{detail:1,target:title}));
          assert.deepEqual(selected,[]);assert.equal(sessionPointerDrag,null);assert.equal(title.capture,null);
          title.onpointerdown(event(80,250,{target:title}));await title.onpointerup(event(80,250,{target:title}));
          await title.onclick(event(80,250,{detail:1,target:title}));assert.deepEqual(selected,['A']);
        })()""")

    def test_touch_title_keeps_native_scrolling_and_unrelated_pointers_cannot_drag(self):
        self.run_case(r"""(async()=>{
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          api=()=>{throw Error('Touch scroll or secondary pointer must not reorder');};
          const title=row('A').querySelector('.session');
          for(const extra of [{pointerType:'touch'},{isPrimary:false},{button:2}]){
            const start=event(80,250,{target:title,...extra});title.onpointerdown(start);
            title.onpointermove(event(80,15,{target:title,...extra}));
            assert.equal(sessionPointerDrag,null);assert.equal(start.prevented,undefined);
          }
          title.onpointerdown(event(80,250,{target:title}));
          title.onpointermove(event(80,15,{target:title,pointerId:8}));
          assert.equal(sessionPointerDrag.active,false);
          title.onpointerup(event(80,15,{target:title,pointerId:8}));assert.ok(sessionPointerDrag);
          await title.onpointerup(event(80,250,{target:title}));
          await title.onclick(event(80,250,{detail:1,target:title}));
          assert.deepEqual(selected,['A']);assert.equal(ids(),'C,B,A');
        })()""")

    def test_cross_pin_drag_and_plain_arrow_keys_do_not_change_order(self):
        self.run_case(r"""(()=>{
          sessions[0].pinned=true;renderSessions();api=()=>{throw Error('No mutation expected');};
          const source=handle('A');source.onpointerdown(event(10,50));source.onpointermove(event(30,250));assert.equal(sessionPointerDrag.target,null);source.onpointerup(event(30,250));
          const arrow={key:'ArrowDown',preventDefault(){throw Error('Plain arrow consumed');}};handle('B').onkeydown(arrow);
          assert.equal(ids(),'A,C,B');
        })()""")

    def test_alt_arrow_saves_moves_focus_and_excludes_other_group(self):
        self.run_case(r"""(async()=>{
          let body;api=async(path,data)=>{body=data;return {sessionOrder:boot.sessionOrder};};
          const key={key:'ArrowUp',altKey:true,preventDefault(){this.prevented=true;}};await handle('A').onkeydown(key);
          assert.ok(key.prevented);assert.equal(body.id,'A');assert.equal(body.targetId,'B');assert.equal(body.position,'before');
          assert.equal(ids(),'C,A,B');assert.equal(document.activeElement,handle('A'));
          sessions[2].pinned=true;renderSessions();body=null;await handle('A').onkeydown(key);assert.equal(body,null);
        })()""")

    def test_reorder_failure_rolls_back_and_blocks_pin_or_second_move_until_response(self):
        self.run_case(r"""(async()=>{
          let reject,calls=0;api=()=>{calls++;return new Promise((_,failure)=>reject=failure);};
          const pending=moveSession('A','C','before');assert.equal(ids(),'A,C,B');
          assert.equal(await moveSession('B','C','before'),false);
          await assert.rejects(()=>updateSession('B',{pinned:true}),/순서/);assert.equal(calls,1);
          reject(Error('disk unavailable'));assert.equal(await pending,false);assert.equal(ids(),'C,B,A');assert.equal(sessionOrderSaving,false);
          assert.equal(pin('A').attributes['aria-pressed'],'false');assert.equal(notices.at(-1),'disk unavailable');
        })()""")

    def test_delayed_keyboard_reorder_preserves_new_input_focus(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);handle('A').focus();
          const pending=handle('A').onkeydown({key:'ArrowUp',altKey:true,preventDefault(){}});
          $('prompt').focus();$('prompt').value='이동을 기다리며 작성';reply({sessionOrder:boot.sessionOrder});
          assert.equal(await pending,true);assert.equal(document.activeElement,$('prompt'));
          assert.equal($('prompt').value,'이동을 기다리며 작성');assert.equal(ids(),'C,A,B');
        })()""")

    def test_delayed_reorder_failure_does_not_take_focus_from_open_dialog(self):
        self.run_case(r"""(async()=>{
          let reject;api=()=>new Promise((_,fail)=>reject=fail);handle('A').focus();
          const pending=moveSession('A','C','before');showDialog('folder-dialog');$('task-name').focus();
          reject(Error('다시 시도해 주세요'));assert.equal(await pending,false);
          assert.equal(document.activeElement,$('task-name'));assert.equal($('folder-dialog').open,true);
          assert.equal(ids(),'C,B,A');
        })()""")

    def test_pin_button_updates_only_target_and_failed_pin_keeps_original_ui(self):
        self.run_case(r"""(async()=>{
          active={id:'C',title:'업무 C',state:'idle',workspace:'C:/C',messages:[{text:'keep conversation'}]};$('prompt').value='keep draft';
          api=async(path,body)=>{assert.equal(path,'/api/session/update');assert.equal(body.id,'A');return {...sessions[0],pinned:body.pinned,updated:10};};
          await pin('A').onclick();assert.equal(pin('A').attributes['aria-pressed'],'true');assert.equal(ids(),'A,C,B');
          api=async()=>{throw Error('pin failure');};await pin('A').onclick();assert.equal(pin('A').attributes['aria-pressed'],'true');
          assert.equal(active.id,'C');assert.equal(active.messages[0].text,'keep conversation');assert.equal($('prompt').value,'keep draft');
          assert.equal(sessionOrderSaving,false);
        })()""")

    def test_poll_keeps_keyboard_focus_and_home_keyboard_stays_in_visible_rows(self):
        self.run_case(r"""(async()=>{
          sessions.push({id:'D',title:'D',created:4,updated:4,pinned:false},{id:'E',title:'E',created:5,updated:5,pinned:false});renderSessions();
          handle('C').focus();sessions[2].state='running';renderSessions();assert.equal(document.activeElement,handle('C'));
          api=()=>{throw Error('No hidden-row move expected');};
          const last=row('B','home-recents').querySelector('.session-drag');
          await last.onkeydown({key:'ArrowDown',altKey:true,preventDefault(){}});assert.equal(ids(),'E,D,C,B,A');
        })()""")

    def test_pending_pin_blocks_reorder_until_its_actual_reply(self):
        self.run_case(r"""(async()=>{
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve);};
          const pending=updateSession('A',{pinned:true});assert.equal(await moveSession('B','C','before'),false);
          assert.equal(calls,1);assert.equal(pin('A').attributes['aria-pressed'],'false');
          reply({...sessions[0],pinned:true,updated:10});await pending;assert.equal(ids(),'A,C,B');assert.equal(sessionOrderSaving,false);
        })()""")

    def test_delayed_pin_does_not_take_focus_from_a_new_input(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);pin('A').focus();
          const pending=pin('A').onclick();$('prompt').focus();$('prompt').value='새 질문 작성 중';
          reply({...sessions[0],pinned:true,updated:10});await pending;
          assert.equal(document.activeElement,$('prompt'));assert.equal($('prompt').value,'새 질문 작성 중');
          assert.equal(pin('A').attributes['aria-pressed'],'true');
        })()""")

    def test_pointer_threshold_cancel_capture_loss_and_escape_never_save(self):
        self.run_case(r"""(()=>{
          api=()=>{throw Error('Cancelled pointer interaction must not save');};
          let source=handle('A');source.onpointerdown(event(10,250));source.onpointermove(event(13,252));assert.equal(sessionPointerDrag.active,false);source.onpointerup(event(13,252));assert.equal(sessionPointerDrag,null);
          for(const end of ['onpointercancel','onlostpointercapture']){source=handle('A');source.onpointerdown(event(10,250));source.onpointermove(event(30,15));source[end](event());assert.equal(sessionDragId,null);assert.equal(sessionPointerDrag,null);assert.equal(ids(),'C,B,A');}
          source=handle('A');source.onpointerdown(event(10,250));source.onpointermove(event(30,15));source.onkeydown({key:'Escape',preventDefault(){}});assert.equal(sessionPointerDrag,null);
          assert.ok(cancelledFrames.length);assert.equal(source.capture,null);
        })()""")

    def test_pointer_drop_outside_clip_or_under_overlay_is_cancelled(self):
        self.run_case(r"""(()=>{
          api=()=>{throw Error('Clipped/covered row must not save');};
          const parent=el('div');parent.testStyle={overflowY:'auto'};parent.bounds={left:0,top:80,right:300,bottom:300,width:300,height:220};parent.append($('sessions'));
          let source=handle('A');source.onpointerdown(event(10,250));source.onpointermove(event(30,15));assert.equal(sessionPointerDrag.target,null);source.onpointerup(event(30,15));
          source=handle('A');source.onpointerdown(event(10,250));blockedHit=true;source.onpointermove(event(30,150));assert.equal(sessionPointerDrag.target,null);source.onpointerup(event(30,150));assert.equal(ids(),'C,B,A');
        })()""")

    def test_touch_pointer_autoscroll_and_cleanup_do_not_affect_pin_or_task_click(self):
        self.run_case(r"""(()=>{
          let selected=0;selectSession=async()=>{selected++;};api=()=>{throw Error('Cancel must not save');};
          $('sessions').testStyle={overflowY:'auto'};$('sessions').scrollHeight=600;$('sessions').clientHeight=300;
          const source=handle('A');source.onpointerdown(event(10,250,{pointerType:'touch'}));source.onpointermove(event(30,292,{pointerType:'touch'}));
          assert.ok(frames.length);blockedHit=true;frames.shift()();assert.equal($('sessions').scrollTop,0);
          blockedHit=false;source.onpointermove(event(30,292,{pointerType:'touch'}));frames.shift()();assert.ok($('sessions').scrollTop>0);assert.equal(selected,0);
          source.onpointercancel(event());const stopped=$('sessions').scrollTop;for(const tick of frames)tick();assert.equal($('sessions').scrollTop,stopped);assert.equal(sessionPointerDrag,null);
          assert.equal(pin('A').attributes['aria-pressed'],'false');assert.equal(source.capture,null);
        })()""")


if __name__ == '__main__':
    unittest.main()
