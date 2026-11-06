"""Folder order through the shipped long-press arrow, keyboard and settings handlers."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_session_groups_frontend import HARNESS, GROUP_SETUP, DRAG_SETUP, NODE


ROOT = Path(__file__).resolve().parents[1]
SETUP = GROUP_SETUP + r"""
const groupOrder=()=>boot.sessionGroups.groups.map(group=>group.id);
const renderedOrder=()=>groupNodes().map(node=>node.dataset.groupId);
const groupApi=api;
api=async(path,body)=>{
  if(body?.action!=='reorder')return groupApi(path,body);
  calls.push({path,body});assert.equal(path,'/api/session-groups');
  assert.equal(body.revision,boot.sessionGroups.revision);
  const data=deepCopy(boot.sessionGroups),source=data.groups.find(group=>group.id===body.groupId);
  data.groups=data.groups.filter(group=>group.id!==body.groupId);
  data.groups.splice(data.groups.findIndex(group=>group.id===body.targetGroupId)+(body.position==='after'?1:0),0,source);
  data.revision++;return {ok:true,sessionGroups:data};
};
renderSessions();
const grip=id=>groupNodes().find(node=>node.dataset.groupId===id)?.querySelector('.task-group-arrow');
const orderKey=(name,extra={})=>({key:name,altKey:true,preventDefault(){this.prevented=true;},...extra});
"""
POINTER = DRAG_SETUP + r"""
Object.defineProperty(proto,'style',{get(){return this._style??={};},configurable:true});
const windowEvents=new Map();
globalThis.addEventListener=(name,fn)=>{if(!windowEvents.has(name))windowEvents.set(name,new Set());windowEvents.get(name).add(fn);};
globalThis.removeEventListener=(name,fn)=>windowEvents.get(name)?.delete(fn);
const fireWindow=(name,event={})=>{for(const fn of [...(windowEvents.get(name)||[])])fn(event);};
const after=(node,extra={})=>pointer(node,{clientY:node.getBoundingClientRect().bottom-5,...extra});
const dragPreview=()=>document.body.querySelector('.task-group-drag-preview');
const marker=()=>document.body.querySelector('.task-group-insertion');
let clock=0,nextTimer=0;const timers=new Map();
globalThis.setTimeout=(fn,delay=0)=>{const id=++nextTimer;timers.set(id,{fn,at:clock+delay});return id;};
globalThis.clearTimeout=id=>timers.delete(id);
const advance=ms=>{
  const end=clock+ms;let due;
  while((due=[...timers].filter(([,timer])=>timer.at<=end).sort((a,b)=>a[1].at-b[1].at)[0])){
    clock=due[1].at;timers.delete(due[0]);due[1].fn();
  }clock=end;
};
const hold=(handle,node)=>{handle.onpointerdown(pointer(node));advance(1000);};
"""


@unittest.skipUnless(NODE, 'Node.js required')
class FolderOrderFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + script,
             str(ROOT / 'local_app/web/session-groups.js')],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_keyboard_saves_array_order_and_preserves_every_task_property(self):
        self.run_case(r"""(async()=>{
          boot.sessionOrder={manual:true,ids:['C','B','A']};renderSessions();
          $('prompt').value='작성 중인 초안';attachments=['keep.xlsx'];
          const tasks=deepCopy(sessions),selected=deepCopy(active),groups=deepCopy(boot.sessionGroups),order=deepCopy(boot.sessionOrder);
          const handle=grip('group-a');handle.focus();
          assert.equal(handle.attributes['aria-keyshortcuts'],'Alt+ArrowUp Alt+ArrowDown');
          await handle.onkeydown(orderKey('ArrowDown'));
          assert.deepEqual(calls,[{path:'/api/session-groups',body:{action:'reorder',groupId:'group-a',targetGroupId:'group-b',position:'after',revision:3}}]);
          assert.deepEqual(groupOrder(),['group-b','group-a']);assert.deepEqual(renderedOrder(),groupOrder());
          assert.deepEqual(boot.sessionGroups.groups,[groups.groups[1],groups.groups[0]]);
          assert.deepEqual(boot.sessionGroups.assignments,groups.assignments);assert.deepEqual(boot.sessionOrder,order);
          assert.deepEqual(sessions,tasks);assert.deepEqual(active,selected);
          assert.equal($('prompt').value,'작성 중인 초안');assert.equal(attachments[0],'keep.xlsx');
          assert.equal(document.activeElement,grip('group-a'));
          await grip('group-a').onkeydown(orderKey('ArrowDown'));assert.equal(calls.length,1);
          await grip('group-a').onkeydown(orderKey('ArrowUp'));assert.equal(calls.length,2);
          assert.deepEqual(groupOrder(),['group-a','group-b']);assert.equal(calls[1].body.revision,4);
        })()""")

    def test_keyboard_modifiers_and_single_folder_do_not_mutate(self):
        self.run_case(r"""(async()=>{
          for(const extra of [{altKey:false},{ctrlKey:true},{shiftKey:true},{metaKey:true},{isComposing:true}])
            await grip('group-a').onkeydown(orderKey('ArrowDown',extra));
          await grip('group-a').onkeydown(orderKey('ArrowUp'));assert.equal(calls.length,0);
          boot.sessionGroups.groups.pop();renderSessions();
          await grip('group-a').onkeydown(orderKey('ArrowDown'));assert.equal(calls.length,0);
          WorkspaceSessionGroups.openEdit('group-a');
          assert.equal($('task-group-up').disabled,true);assert.equal($('task-group-down').disabled,true);
        })()""")

    def test_settings_controls_persist_immediately_without_losing_name_edit(self):
        self.run_case(r"""(async()=>{
          WorkspaceSessionGroups.openEdit();assert.equal($('task-group-order').hidden,true);
          assert.equal($('task-group-cancel').textContent,'취소');
          WorkspaceSessionGroups.openEdit('group-a');assert.equal($('task-group-title').textContent,'폴더 설정');
          assert.equal($('task-group-cancel').textContent,'닫기');
          assert.equal($('task-group-order').hidden,false);assert.equal($('task-group-up').disabled,true);assert.equal($('task-group-down').disabled,false);
          $('task-group-name').value='나중에 저장할 이름';
          $('task-group-down').focus();await $('task-group-down').onclick();
          assert.deepEqual(groupOrder(),['group-b','group-a']);assert.equal($('task-group-dialog').open,true);
          assert.equal($('task-group-name').value,'나중에 저장할 이름');assert.equal(boot.sessionGroups.groups[1].name,'보고서');
          assert.equal($('task-group-up').disabled,false);assert.equal($('task-group-down').disabled,true);
          assert.equal(document.activeElement,$('task-group-up'));assert.equal(notices.length,0);
          assert.equal($('task-group-order-note').textContent,'폴더 순서를 저장했어요.');
          await $('task-group-down').onclick();assert.equal(calls.length,1);
          await $('task-group-up').onclick();assert.deepEqual(groupOrder(),['group-a','group-b']);
          assert.equal(calls.length,2);
        })()""")

    def test_pending_response_is_authoritative_and_blocks_parallel_actions(self):
        self.run_case(r"""(async()=>{
          let reply;api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const original=deepCopy(boot.sessionGroups),handle=grip('group-a');handle.focus();
          const pending=handle.onkeydown(orderKey('ArrowDown'));
          assert.equal(WorkspaceSessionGroups.isSaving(),true);assert.deepEqual(boot.sessionGroups,original);
          assert.equal(grip('group-a').disabled,true);await handle.onkeydown(orderKey('ArrowDown'));
          assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          assert.equal(await moveSession('B','C','after'),false);
          await assert.rejects(updateSession('A',{pinned:false}),/변경을 마친/);assert.equal(calls.length,1);
          const fresh=deepCopy(original);fresh.groups.reverse();fresh.groups[0].name='서버의 최신 이름';fresh.revision=12;
          reply({ok:true,sessionGroups:fresh});await pending;
          assert.deepEqual(boot.sessionGroups,fresh);assert.deepEqual(renderedOrder(),['group-b','group-a']);
          assert.equal(WorkspaceSessionGroups.isSaving(),false);assert.equal(grip('group-a').disabled,false);
          assert.equal(document.activeElement,grip('group-a'));
        })()""")

    def test_save_failure_preserves_order_and_input_then_explicit_retry_works(self):
        self.run_case(r"""(async()=>{
          WorkspaceSessionGroups.openEdit('group-a');$('task-group-name').value='입력 유지';
          const original=deepCopy(boot.sessionGroups),save=api;
          api=async(path,body)=>{calls.push({path,body});throw Error('저장 실패');};
          await $('task-group-down').onclick();
          assert.deepEqual(boot.sessionGroups,original);assert.deepEqual(renderedOrder(),['group-a','group-b']);
          assert.equal($('task-group-error').textContent,'저장 실패');assert.equal($('task-group-name').value,'입력 유지');
          assert.equal($('task-group-dialog').open,true);assert.equal($('task-group-down').disabled,false);
          api=save;await $('task-group-down').onclick();assert.deepEqual(groupOrder(),['group-b','group-a']);
          assert.equal(calls.length,2);assert.equal($('task-group-error').hidden,true);
        })()""")

    def test_revision_conflict_refreshes_order_without_replaying(self):
        self.run_case(r"""(async()=>{
          const fresh=deepCopy(boot.sessionGroups);fresh.groups.reverse();fresh.revision=40;
          api=async(path,body)=>{calls.push({path,body});if(!body)return fresh;
            const err=Error('다른 창에서 폴더 순서를 바꿨어요');err.code='session_groups_changed';throw err;};
          await grip('group-a').onkeydown(orderKey('ArrowDown'));
          assert.equal(calls.length,2);assert.equal(calls.filter(call=>call.body).length,1);
          assert.deepEqual(boot.sessionGroups,fresh);assert.deepEqual(renderedOrder(),['group-b','group-a']);
          assert.match(notices.at(-1),/다른 창/);assert.equal(WorkspaceSessionGroups.isSaving(),false);
        })()""")

    def test_late_save_never_steals_focus_from_new_input_or_dialog(self):
        self.run_case(r"""(async()=>{
          let reply;api=(path,body)=>new Promise(resolve=>reply=resolve);
          grip('group-a').focus();const pending=grip('group-a').onkeydown(orderKey('ArrowDown'));
          $('prompt').focus();$('prompt').value='계속 작성';
          const fresh=deepCopy(boot.sessionGroups);fresh.groups.reverse();fresh.revision++;
          reply({ok:true,sessionGroups:fresh});await pending;
          assert.equal(document.activeElement,$('prompt'));assert.equal($('prompt').value,'계속 작성');
          grip('group-a').focus();const second=grip('group-a').onkeydown(orderKey('ArrowUp'));
          showDialog('settings-dialog');document.activeElement=document.body;
          reply({ok:true,sessionGroups:fresh});await second;
          assert.equal(document.activeElement,document.body);
        })()""")

    def test_search_and_all_lock_states_block_folder_reordering(self):
        self.run_case(POINTER+r"""(async()=>{
          WorkspaceSessionGroups.openEdit('group-a');$('session-search').value='보고';renderSessions();
          assert.equal(grip('group-a').disabled,false);assert.match(grip('group-a').title,/검색/);
          assert.equal($('task-group-down').disabled,true);assert.match($('task-group-down').title,/검색을 해제/);
          await grip('group-a').onkeydown(orderKey('ArrowDown'));await $('task-group-down').onclick();
          grip('group-a').onpointerdown(pointer(header('group-a')));assert.equal(WorkspaceSessionGroups.isDragging(),false);
          $('session-search').value='';renderSessions();
          for(const state of ['app','groups','order','saving','taskDrag']){
            appClosed=state==='app';boot.sessionGroups.warning=state==='groups'?'그룹 오류':null;
            boot.sessionOrder={warning:state==='order'?'순서 오류':null};sessionOrderSaving=state==='saving';sessionDragId=state==='taskDrag'?'A':null;
            await grip('group-a').onkeydown(orderKey('ArrowDown'));await $('task-group-down').onclick();
          }
          assert.equal(calls.length,0);assert.deepEqual(groupOrder(),['group-a','group-b']);
        })()""")

    def test_pointer_threshold_stable_source_and_release_before_mutation(self):
        self.run_case(POINTER+r"""(async()=>{
          const source=header('group-a'),handle=grip('group-a'),original=deepCopy(boot.sessionGroups);
          $('prompt').value='드래그 초안';const activeBefore=deepCopy(active),tasksBefore=deepCopy(sessions);
          handle.onpointerdown(pointer(source));assert.equal(handle.capture,7);assert.equal(WorkspaceSessionGroups.isDragging(),true);
          renderSessions();assert.equal(header('group-a'),source);
          handle.onpointermove(pointer(source,{clientX:25}));assert.equal(dragPreview(),null);
          advance(999);assert.equal(dragPreview(),null);advance(1);
          handle.onpointermove(after(header('group-b')));assert.ok(dragPreview());assert.equal(marker().hidden,false);
          assert.equal(source.classList.contains('task-group-dragging'),true);assert.deepEqual(boot.sessionGroups,original);
          const save=api;api=(path,body)=>{assert.equal(handle.capture,null);assert.equal(WorkspaceSessionGroups.isDragging(),false);return save(path,body);};
          await handle.onpointerup(after(header('group-b')));
          assert.equal(calls.length,1);assert.deepEqual(groupOrder(),['group-b','group-a']);
          assert.equal(calls[0].body.position,'after');assert.equal(dragPreview(),null);assert.equal(marker(),null);
          assert.equal(WorkspaceSessionGroups.isDragging(),false);assert.deepEqual(sessions,tasksBefore);assert.deepEqual(active,activeBefore);
          assert.equal($('prompt').value,'드래그 초안');assert.equal(document.activeElement,grip('group-a'));
        })()""")

    def test_whole_folder_block_edges_include_empty_and_overflow_rows(self):
        self.run_case(POINTER+r"""(async()=>{
          boot.sessionGroups.groups[1].collapsed=false;delete boot.sessionGroups.assignments.B;renderSessions();
          const tail=[...$('sessions').children].find(node=>node.classList.contains('task-group-tail')&&node.dataset.groupId==='group-b');
          const source=header('group-a'),handle=grip('group-a');hold(handle,source);handle.onpointermove(pointer(tail));
          assert.equal(marker().style.top,`${tail.getBoundingClientRect().bottom}px`);
          await handle.onpointerup(pointer(tail));assert.deepEqual(groupOrder(),['group-b','group-a']);
          for(const id of ['D','E','F','G','H','I']){sessions.push({id,title:id,state:'done',workspace:'C:/'+id,created:1});boot.sessionGroups.assignments[id]='group-a';}
          active=null;renderSessions();
          assert.ok([...$('sessions').children].find(node=>node.classList.contains('task-group-overflow')));
          const second=grip('group-b'),bottom=[...$('sessions').children].find(node=>node.classList.contains('task-group-tail')&&node.dataset.groupId==='group-a');
          hold(second,header('group-b'));second.onpointermove(pointer(bottom));
          assert.equal(marker().style.top,`${Math.min(bottom.getBoundingClientRect().bottom,597)}px`);
          await second.onpointerup(pointer(bottom));assert.deepEqual(groupOrder(),['group-a','group-b']);
        })()""")

    def test_cancel_paths_release_capture_remove_preview_and_never_save(self):
        self.run_case(POINTER+r"""(()=>{
          for(const reason of ['pointercancel','lostpointercapture','blur','Escape','gripEscape','contextmenu']){
            const handle=grip('group-a');hold(handle,header('group-a'));handle.onpointermove(after(header('group-b')));
            assert.ok(dragPreview());
            if(reason==='blur')fireWindow('blur');
            else if(reason==='Escape')fireWindow('keydown',orderKey('Escape'));
            else if(reason==='gripEscape')handle.onkeydown(orderKey('Escape'));
            else handle['on'+reason](pointer(header('group-b')));
            assert.equal(handle.capture,null);assert.equal(WorkspaceSessionGroups.isDragging(),false);assert.equal(dragPreview(),null);assert.equal(marker(),null);
            assert.equal(groupNodes().some(node=>node.classList.contains('task-group-dragging')),false);
            handle.onpointerup(after(header('group-b')));
          }
          assert.equal(calls.length,0);assert.deepEqual(groupOrder(),['group-a','group-b']);
        })()""")

    def test_task_drag_pin_and_keyboard_order_cannot_start_during_folder_pointerdown(self):
        self.run_case(POINTER+r"""(async()=>{
          const handle=grip('group-a');handle.onpointerdown(pointer(header('group-a')));
          const taskGrip=row('A').querySelector('.session-drag');taskGrip.onpointerdown(pointer(row('A')));
          assert.equal(sessionPointerDrag,null);assert.equal(sessionDragId,null);
          assert.equal(await moveSession('B','C','after'),false);assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          await assert.rejects(updateSession('A',{pinned:false}),/변경을 마친/);
          await grip('group-b').onkeydown(orderKey('ArrowUp'));await header('group-b').querySelector('.task-group-toggle').onclick();
          assert.equal(calls.length,0);handle.onpointercancel(pointer(header('group-a')));
        })()""")

    def test_click_wrong_pointer_and_noop_boundary_do_not_change_order(self):
        self.run_case(POINTER+r"""(async()=>{
          let handle=grip('group-a');hold(handle,header('group-a'));
          handle.onpointermove(after(header('group-b')));handle.onpointerup(after(header('group-b'),{pointerId:9}));
          assert.equal(WorkspaceSessionGroups.isDragging(),true);handle.onpointercancel(pointer(header('group-a')));
          handle=grip('group-a');handle.onpointerdown(pointer(header('group-a')));await handle.onpointerup(pointer(header('group-a')));
          handle=grip('group-a');hold(handle,header('group-a'));handle.onpointermove(pointer(header('group-b')));
          assert.equal(marker().hidden,true);await handle.onpointerup(pointer(header('group-b')));
          assert.equal(calls.length,0);assert.deepEqual(groupOrder(),['group-a','group-b']);
          assert.equal(boot.sessionGroups.groups[0].collapsed,false);
        })()""")

    def test_clipped_or_overlaid_drop_and_search_started_mid_drag_do_not_save(self):
        self.run_case(POINTER+r"""(async()=>{
          const realHit=document.elementFromPoint;
          for(const reason of ['clip','overlay','search']){
            const handle=grip('group-a');hold(handle,header('group-a'));handle.onpointermove(after(header('group-b')));
            if(reason==='clip')globalThis.innerHeight=50;
            if(reason==='overlay')document.elementFromPoint=()=>document.body;
            if(reason==='search')$('session-search').value='보고';
            await handle.onpointerup(after(header('group-b')));assert.equal(WorkspaceSessionGroups.isDragging(),false);
            globalThis.innerHeight=800;document.elementFromPoint=realHit;$('session-search').value='';renderSessions();
          }
          assert.equal(calls.length,0);assert.deepEqual(groupOrder(),['group-a','group-b']);
        })()""")

    def test_autoscroll_uses_visible_clipping_ancestor_and_stops_when_cancelled(self):
        self.run_case(POINTER+r"""(()=>{
          const scroll=el('div');scroll.append($('sessions'));scroll.scrollHeight=500;scroll.clientHeight=180;
          scroll.getBoundingClientRect=()=>({left:0,top:0,right:300,bottom:180,width:300,height:180});
          globalThis.getComputedStyle=node=>node===scroll?{overflowY:'auto'}:{};
          const handle=grip('group-a');hold(handle,header('group-a'));
          handle.onpointermove(pointer(header('group-b'),{clientY:175}));assert.ok(frames.length);
          frames.shift()();assert.equal(scroll.scrollTop,12);assert.ok(frames.length);
          handle.onpointercancel(pointer(header('group-a')));frames.shift()();assert.equal(scroll.scrollTop,12);
          assert.equal(calls.length,0);assert.equal(dragPreview(),null);
        })()""")

    def test_short_arrow_press_and_folder_name_keep_normal_collapse(self):
        self.run_case(POINTER+r"""(async()=>{
          const source=header('group-a'),handle=grip('group-a');document.elementFromPoint=()=>handle;
          assert.equal(source.querySelector('.task-group-drag'),null);
          assert.ok(handle.querySelector('.task-group-chevron'));
          assert.equal(source.querySelector('.task-group-toggle').querySelector('.task-group-chevron'),null);
          handle.onpointerdown(pointer(source));advance(999);await handle.onpointerup(pointer(source));
          assert.equal(header('group-a'),source);assert.equal(WorkspaceSessionGroups.isDragging(),false);
          assert.equal(timers.size,0);assert.equal(dragPreview(),null);
          await handle.onclick({detail:1});assert.equal(calls.length,1);assert.equal(calls[0].body.action,'collapse');
          assert.equal(boot.sessionGroups.groups[0].collapsed,true);assert.deepEqual(groupOrder(),['group-a','group-b']);
          await header('group-a').querySelector('.task-group-toggle').onclick();
          assert.equal(boot.sessionGroups.groups[0].collapsed,false);assert.equal(calls.length,2);
          advance(5000);assert.equal(dragPreview(),null);assert.equal(timers.size,0);
        })()""")

    def test_movement_before_one_second_cancels_without_moving_or_collapsing(self):
        self.run_case(POINTER+r"""(async()=>{
          const source=header('group-a'),handle=grip('group-a');handle.onpointerdown(pointer(source));advance(500);
          handle.onpointermove(after(header('group-b')));assert.equal(WorkspaceSessionGroups.isDragging(),false);
          assert.equal(handle.capture,null);assert.equal(timers.size,0);advance(1000);
          await handle.onpointerup(after(header('group-b')));await handle.onclick({detail:1});
          assert.equal(calls.length,0);assert.equal(dragPreview(),null);assert.deepEqual(groupOrder(),['group-a','group-b']);
          assert.equal(boot.sessionGroups.groups[0].collapsed,false);
        })()""")

    def test_long_hold_without_moving_does_not_collapse_and_next_click_works(self):
        self.run_case(POINTER+r"""(async()=>{
          const source=header('group-a'),handle=grip('group-a');hold(handle,source);
          assert.ok(dragPreview());assert.equal(marker().hidden,true);assert.equal(frames.length,0);
          await handle.onpointerup(pointer(source));await handle.onclick({detail:1});
          assert.equal(calls.length,0);assert.equal(timers.size,0);assert.equal(boot.sessionGroups.groups[0].collapsed,false);
          const next=grip('group-a');document.elementFromPoint=()=>next;
          next.onpointerdown(pointer(header('group-a')));await next.onpointerup(pointer(header('group-a')));
          await next.onclick({detail:1});assert.equal(calls.length,1);assert.equal(calls[0].body.action,'collapse');
        })()""")

    def test_pending_hold_cancel_paths_never_activate_late(self):
        self.run_case(POINTER+r"""(async()=>{
          for(const reason of ['pointercancel','lostpointercapture','blur','Escape','contextmenu']){
            const handle=grip('group-a');handle.onpointerdown(pointer(header('group-a')));advance(900);
            if(reason==='blur')fireWindow('blur');
            else if(reason==='Escape')fireWindow('keydown',orderKey('Escape'));
            else handle['on'+reason](pointer(header('group-a')));
            advance(1100);await handle.onclick({detail:1});
            assert.equal(WorkspaceSessionGroups.isDragging(),false);assert.equal(handle.capture,null);
            assert.equal(timers.size,0);assert.equal(dragPreview(),null);assert.equal(marker(),null);
            for(const handlers of windowEvents.values())assert.equal(handlers.size,0);
          }
          assert.equal(calls.length,0);assert.equal(boot.sessionGroups.groups[0].collapsed,false);
        })()""")

    def test_state_change_before_hold_completes_cancels_safely(self):
        self.run_case(POINTER+r"""(()=>{
          for(const reason of ['search','disabled','app','groupWarning','mode']){
            const handle=grip('group-a'),modeBefore=currentMode;handle.onpointerdown(pointer(header('group-a')));
            if(reason==='search')$('session-search').value='보고';
            if(reason==='disabled')handle.disabled=true;
            if(reason==='app')appClosed=true;
            if(reason==='groupWarning')boot.sessionGroups.warning='잠시 사용할 수 없어요';
            if(reason==='mode')currentMode='chat';
            advance(1000);assert.equal(WorkspaceSessionGroups.isDragging(),false);
            assert.equal(handle.capture,null);assert.equal(timers.size,0);assert.equal(dragPreview(),null);
            $('session-search').value='';appClosed=false;boot.sessionGroups.warning=null;currentMode=modeBefore;renderSessions();
          }
          assert.equal(calls.length,0);
        })()""")

    def test_nonprimary_mouse_buttons_and_capture_failure_do_not_start_hold(self):
        self.run_case(POINTER+r"""(()=>{
          const handle=grip('group-a');
          for(const extra of [{button:1},{button:2},{isPrimary:false}]){
            handle.onpointerdown(pointer(header('group-a'),extra));advance(1000);
            assert.equal(WorkspaceSessionGroups.isDragging(),false);assert.equal(timers.size,0);
          }
          handle.setPointerCapture=()=>{throw new Error('capture unavailable');};
          handle.onpointerdown(pointer(header('group-a')));advance(1000);
          assert.equal(WorkspaceSessionGroups.isDragging(),false);assert.equal(dragPreview(),null);assert.equal(timers.size,0);
          assert.equal(calls.length,0);
        })()""")

    def test_focus_change_modal_or_removed_arrow_cancels_pending_hold(self):
        self.run_case(POINTER+r"""(()=>{
          for(const reason of ['focus','modal','removed']){
            const handle=grip('group-a');handle.onpointerdown(pointer(header('group-a')));advance(500);
            if(reason==='focus'){$('prompt').focus();handle.onblur();}
            if(reason==='modal'){showDialog('settings-dialog');$('prompt').focus();}
            if(reason==='removed')handle.remove();
            advance(1000);assert.equal(WorkspaceSessionGroups.isDragging(),false);
            assert.equal(handle.capture,null);assert.equal(timers.size,0);assert.equal(dragPreview(),null);
            if(reason==='modal')$('settings-dialog').close();renderSessions();
          }
          assert.equal(calls.length,0);
        })()""")

    def test_keyboard_activation_is_not_swallowed_after_cancelled_pointer(self):
        self.run_case(POINTER+r"""(async()=>{
          const handle=grip('group-a');hold(handle,header('group-a'));fireWindow('keydown',orderKey('Escape'));
          await handle.onclick({detail:0});assert.equal(calls.length,1);assert.equal(calls[0].body.action,'collapse');
          assert.equal(boot.sessionGroups.groups[0].collapsed,true);
        })()""")

    def test_touch_scrolling_only_prevented_after_completed_hold(self):
        self.run_case(POINTER+r"""(async()=>{
          const handle=grip('group-a');handle.onpointerdown(pointer(header('group-a'),{pointerType:'touch'}));
          let prevented=false;await fire(handle,'touchmove',{preventDefault(){prevented=true;}});assert.equal(prevented,false);
          advance(1000);await fire(handle,'touchmove',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
          handle.onpointercancel(pointer(header('group-a')));prevented=false;
          await fire(handle,'touchmove',{preventDefault(){prevented=true;}});assert.equal(prevented,false);
          assert.equal(calls.length,0);assert.equal(timers.size,0);
        })()""")


if __name__ == '__main__':
    unittest.main()
