"""Task naming and sidebar organization through the shipped UI event handlers.

The DOM double is local to these tests; no real Claude profile or task is used.
"""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE, NODE


ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE.replace("const scenario=process.argv[3];", r"""
context.flush = () => new Promise(resolve => setImmediate(resolve));
context.flatText = function flatText(node) {return [node.textContent||'', ...(node.children||[]).map(flatText)].join(' ');};
context.fire = async (node, name, event={}) => {
  const payload={type:name,target:node,...event};
  let result;
  if(typeof node['on'+name]==='function')result=await node['on'+name](payload);
  for(const listener of node.listeners?.[name]||[])await listener.call(node,payload);
  return result;
};
Element.prototype.setCustomValidity=function(message){this.validationMessage=message;};
Object.defineProperty(Element.prototype,'parentElement',{get(){return this.parent;},configurable:true});
Element.prototype.closest=function(selector){
  for(let node=this;node;node=node.parentElement)
    if(selector[0]==='.'?node.classList.contains(selector.slice(1)):node.tagName===selector.toUpperCase())return node;
  return null;
};
Element.prototype.reportValidity=function(){this.validityReported=true;return !this.validationMessage;};
Element.prototype.select=function(){this.selectionStart=0;this.selectionEnd=this.value.length;};
const originalClose=Element.prototype.close;
Element.prototype.close=function(value=''){
  originalClose.call(this,value);
  for(const listener of this.listeners?.close||[])listener.call(this,{type:'close',target:this});
};
if(process.argv[4])vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'session-groups.js'});
const scenario=process.argv[3];
""")
SETUP = r"""
boot={demo:false};
active={id:'A',title:'기존 업무',state:'done',trusted:true,pinned:false,workspace:'C:/fixture/A',messages:[]};
sessions=[active,{id:'B',title:'다른 업무',state:'idle',workspace:'C:/fixture/B'}];
const saved=(id,title)=>({session:{id,title,pinned:false,updated:10}});
let calls=[];api=async(path,body)=>{calls.push({path,body});return saved(body.id,body.title);};
"""
GROUP_SETUP = r"""
boot={demo:false,sessionGroups:{groups:[
  {id:'group-a',name:'보고서',collapsed:false},
  {id:'group-b',name:'기획',collapsed:true}],
  assignments:{A:'group-a',B:'group-b'},warning:null,revision:3}};
sessions=[
  {id:'A',title:'실적 보고서',state:'done',pinned:true,workspace:'C:/fixture/A',created:1,updated:3},
  {id:'B',title:'다음 분기 계획',state:'running',pinned:false,workspace:'C:/fixture/B',created:2,updated:2},
  {id:'C',title:'회의 준비',state:'idle',pinned:false,workspace:'C:/fixture/C',created:3,updated:1}];
active={...sessions[0],messages:[{role:'assistant',text:'기존 대화'}]};
let calls=[],notices=[];toast=message=>notices.push(message);
const deepCopy=value=>JSON.parse(JSON.stringify(value));
const rows=()=>[...$('sessions').children].filter(node=>node.dataset.sessionId);
const visibleIds=()=>rows().map(row=>row.dataset.sessionId);
const groupNodes=()=>[...$('sessions').children].filter(node=>node.classList.contains('task-group-header'));
api=async(path,body)=>{
  calls.push({path,body});
  const data=deepCopy(boot.sessionGroups);
  if(!body)return data;
  assert.equal(path,'/api/session-groups');
  assert.equal(body.revision,data.revision);
  let groupId;
  if(body.action==='move'){
    if(body.groupId)data.assignments[body.id]=body.groupId;
    else delete data.assignments[body.id];
  }else if(body.action==='create'){
    groupId='group-c';data.groups.push({id:groupId,name:body.name,collapsed:false});
  }else if(body.action==='rename')data.groups.find(group=>group.id===body.groupId).name=body.name;
  else if(body.action==='collapse')data.groups.find(group=>group.id===body.groupId).collapsed=body.collapsed;
  else if(body.action==='delete'){
    data.groups=data.groups.filter(group=>group.id!==body.groupId);
    data.assignments=Object.fromEntries(Object.entries(data.assignments).filter(([id,value])=>value!==body.groupId));
  }
  data.revision++;return {ok:true,sessionGroups:data,...(groupId?{groupId}:{})};
};
"""
LONG_GROUP_SETUP = r"""
for(const id of ['D','E','F','G','H','I']){
  sessions.push({id,title:`보고서 ${id}`,workspace:`C:/fixture/${id}`,state:'done',pinned:false,created:1,updated:1});
  boot.sessionGroups.assignments[id]='group-a';
}
boot.sessionOrder={manual:true,ids:['A','D','E','F','G','H','I','B','C']};
const groupIds=()=>rows().filter(node=>node.dataset.groupId==='group-a').map(node=>node.dataset.sessionId);
const more=()=>[...$('sessions').children].find(node=>node.classList.contains('task-group-overflow')&&node.dataset.groupId==='group-a')?.querySelector('.task-group-more');
"""
DRAG_SETUP = r"""
const proto=Object.getPrototypeOf($('sessions'));
Object.defineProperty(proto,'parentElement',{get(){return this.parent;},configurable:true});
proto.contains=function(other){for(let node=other;node;node=node.parentElement)if(node===this)return true;return false;};
proto.closest=function(selector){for(let node=this;node;node=node.parentElement)if(selector[0]==='.'?node.classList.contains(selector.slice(1)):node.tagName===selector.toUpperCase())return node;return null;};
proto.setPointerCapture=function(id){this.capture=id;};proto.hasPointerCapture=function(id){return this.capture===id;};proto.releasePointerCapture=function(id){if(this.capture===id)this.capture=null;};
proto.getBoundingClientRect=function(){
  if(this.parent===$('sessions')){const top=this.parent.children.indexOf(this)*40;return {left:0,top,right:300,bottom:top+40,width:300,height:40};}
  return {left:0,top:0,right:300,bottom:600,width:300,height:600};
};
globalThis.getComputedStyle=()=>({});globalThis.innerWidth=1000;globalThis.innerHeight=800;
let frames=[];globalThis.requestAnimationFrame=fn=>{frames.push(fn);return frames.length;};globalThis.cancelAnimationFrame=()=>{};
document.elementFromPoint=(x,y)=>{
  const node=[...$('sessions').children].find(node=>{const box=node.getBoundingClientRect();return y>=box.top&&y<box.bottom;});
  return node?.querySelector('.task-group-toggle')||node?.querySelector('.session')||node||$('sessions');
};
const row=id=>rows().find(node=>node.dataset.sessionId===id);
const header=id=>[...$('sessions').children].find(node=>node.classList.contains('task-group-header')&&node.dataset.groupId===(id||''));
const pointer=(node,extra={})=>({pointerId:7,button:0,isPrimary:true,clientX:20,
  clientY:node.getBoundingClientRect().top+10,preventDefault(){this.prevented=true;},...extra});
renderSessions();
"""


@unittest.skipUnless(NODE, 'Node.js required')
class TaskRenameFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),SETUP+script],
                              input=HARNESS,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_enter_without_submitter_saves_trimmed_title_and_closes(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='  새 업무 이름  ';
          let prevented=false;
          await fire($('rename-form'),'submit',{preventDefault(){prevented=true;}});
          assert.equal(prevented,true);assert.equal(calls.length,1);
          assert.deepEqual(calls[0],{path:'/api/session/update',body:{id:'A',title:'새 업무 이름'}});
          assert.equal(active.title,'새 업무 이름');assert.equal($('rename-dialog').open,false);
        })()""")

    def test_cancel_closes_without_saving(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='저장하지 않을 값';
          await fire($('rename-cancel'),'click');
          assert.equal(calls.length,0);assert.equal(active.title,'기존 업무');
          assert.equal($('rename-dialog').open,false);
        })()""")

    def test_repeated_enter_while_saving_sends_only_once(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='업무';
          let reply;api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const first=fire($('rename-form'),'submit',{preventDefault(){}});
          await fire($('rename-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,1);assert.equal($('rename-confirm').disabled,true);
          reply(saved('A','업무'));await first;
          assert.equal($('rename-dialog').open,false);assert.equal($('rename-confirm').disabled,false);
        })()""")

    def test_dialog_keeps_original_target_if_selected_task_changes(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='처음 연 업무 이름';
          selectionGeneration++;active={...sessions[1],messages:[{role:'assistant',text:'B 내용'}]};
          await fire($('rename-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,1);assert.equal(calls[0].body.id,'A');
          assert.equal(sessions[0].title,'처음 연 업무 이름');assert.equal(active.id,'B');
          assert.equal(active.title,'다른 업무');assert.equal(active.messages[0].text,'B 내용');
        })()""")

    def test_blank_name_stays_open_without_request(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='   ';
          await fire($('rename-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,0);assert.equal($('rename-dialog').open,true);
          assert.equal($('rename-input').value,'   ');
        })()""")

    def test_save_failure_keeps_value_and_allows_explicit_retry(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='남겨둘 업무 이름';
          api=async(path,body)=>{calls.push({path,body});throw Error('일시적인 저장 오류');};
          await fire($('rename-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,1);assert.equal($('rename-dialog').open,true);
          assert.equal($('rename-input').value,'남겨둘 업무 이름');assert.equal($('rename-confirm').disabled,false);
          api=async(path,body)=>{calls.push({path,body});return saved(body.id,body.title);};
          await fire($('rename-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,2);assert.equal(active.title,'남겨둘 업무 이름');
          assert.equal($('rename-dialog').open,false);
        })()""")

    def test_korean_composition_enter_is_not_a_submit_key(self):
        self.run_case(r"""(async()=>{
          $('task-title').onclick();$('rename-input').value='새 업무';
          let prevented=false;
          await fire($('rename-input'),'keydown',{key:'Enter',isComposing:true,keyCode:229,
            preventDefault(){prevented=true;}});
          assert.equal(prevented,true);assert.equal(calls.length,0);
          assert.equal($('rename-dialog').open,true);assert.equal($('rename-input').value,'새 업무');
        })()""")


@unittest.skipUnless(NODE, 'Node.js required')
class SessionGroupsFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),GROUP_SETUP+script,
                               str(ROOT/'local_app/web/session-groups.js')],
                              input=HARNESS,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_collapsed_groups_keep_counts_and_do_not_hide_ungrouped_tasks(self):
        self.run_case(r"""(()=>{
          renderSessions();assert.deepEqual(visibleIds(),['A','C']);
          const collapsed=groupNodes().find(node=>node.dataset.groupId==='group-b');
          assert.equal(collapsed.querySelector('.task-group-toggle').attributes['aria-expanded'],'false');
          assert.equal(collapsed.querySelector('.task-group-count').textContent,'1');
          assert.equal(rows()[0].querySelector('.session').attributes['aria-current'],'page');
          assert.equal(rows()[0].querySelector('.session-pin').attributes['aria-pressed'],'true');
          assert.equal(calls.length,0);
        })()""")

    def test_unassigned_tasks_are_flat_without_a_separate_root_heading(self):
        self.run_case(r"""(()=>{
          renderSessions();assert.equal(groupNodes().length,2);
          assert.equal(groupNodes().some(node=>!node.dataset.groupId),false);
          const unassigned=rows().find(row=>row.dataset.sessionId==='C');
          assert.equal(unassigned.dataset.groupId,'');
          assert.equal(unassigned.classList.contains('grouped-session'),false);
          assert.equal(rows()[0].classList.contains('grouped-session'),true);
          assert.equal(WorkspaceSessionGroups.label('C'),'일반 목록');
          assert.equal(flatText($('sessions')).includes('폴더 밖 업무'),false);
          assert.ok($('sessions').children.indexOf(unassigned)>$('sessions').children.indexOf(groupNodes()[1]));
          assert.equal(calls.length,0);
        })()""")

    def test_search_reveals_collapsed_matches_without_changing_saved_preference(self):
        self.run_case(r"""(()=>{
          $('session-search').value='다음 분기';renderSessions();
          assert.deepEqual(visibleIds(),['B']);
          assert.equal(boot.sessionGroups.groups[1].collapsed,true);assert.equal(calls.length,0);
          const group=groupNodes().find(node=>node.dataset.groupId==='group-b');
          assert.equal(group.querySelector('.task-group-toggle').attributes['aria-expanded'],'true');
          $('session-search').value='';renderSessions();assert.deepEqual(visibleIds(),['A','C']);
          $('session-search').value='기획';renderSessions();assert.deepEqual(visibleIds(),['B']);
          assert.equal(calls.length,0);assert.equal(boot.sessionGroups.groups[1].collapsed,true);
        })()""")

    def test_large_folder_previews_five_and_toggle_is_local_with_focus_retained(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          const original=deepCopy(boot.sessionGroups),order=deepCopy(boot.sessionOrder);renderSessions();
          assert.deepEqual(groupIds(),['A','D','E','F','G']);assert.equal(more().attributes['aria-expanded'],'false');
          assert.match(more().attributes['aria-label'],/나머지 2개/);assert.match(flatText(more()),/더 보기/);
          more().focus();more().onclick();assert.deepEqual(groupIds(),['A','D','E','F','G','H','I']);
          assert.equal(more().attributes['aria-expanded'],'true');assert.equal(document.activeElement,more());
          assert.match(flatText(more()),/간단히 보기/);more().onclick();assert.equal(document.activeElement,more());
          assert.deepEqual(groupIds(),['A','D','E','F','G']);
          assert.deepEqual(boot.sessionGroups,original);assert.deepEqual(boot.sessionOrder,order);assert.equal(calls.length,0);
        })()""")

    def test_exactly_five_members_need_no_more_button_and_total_count_stays_correct(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          sessions=sessions.filter(item=>!['H','I'].includes(item.id));renderSessions();
          assert.equal(groupIds().length,5);assert.equal(more(),undefined);
          assert.equal(groupNodes()[0].querySelector('.task-group-count').textContent,'5');
          assert.equal(calls.length,0);
        })()""")

    def test_search_finds_hidden_members_and_folder_name_displays_every_match(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          renderSessions();assert.equal(groupIds().includes('I'),false);
          $('session-search').value='보고서 I';renderSessions();assert.deepEqual(groupIds(),['I']);assert.equal(more(),undefined);
          $('session-search').value='보고서';renderSessions();assert.equal(groupIds().length,7);assert.equal(more(),undefined);
          $('session-search').value='';renderSessions();assert.deepEqual(groupIds(),['A','D','E','F','G']);
          assert.equal(more().attributes['aria-expanded'],'false');assert.equal(calls.length,0);
        })()""")

    def test_selecting_an_unlisted_task_opens_its_preview_without_changing_sort_or_folder(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          renderSessions();const original=deepCopy(boot.sessionGroups),order=deepCopy(boot.sessionOrder);
          active={...sessions.find(item=>item.id==='I'),messages:[]};renderSessions();
          assert.equal(groupIds().length,7);assert.equal(more().attributes['aria-expanded'],'true');
          assert.equal(rows().find(row=>row.dataset.sessionId==='I').querySelector('.session').attributes['aria-current'],'page');
          more().onclick();renderSessions();assert.equal(groupIds().length,5);
          active={...sessions[0],messages:[]};renderSessions();
          active={...sessions.find(item=>item.id==='H'),messages:[]};renderSessions();assert.equal(groupIds().length,7);
          assert.deepEqual(boot.sessionGroups,original);assert.deepEqual(boot.sessionOrder,order);assert.equal(calls.length,0);
        })()""")

    def test_selecting_search_result_remains_visible_after_search_is_cleared(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          renderSessions();$('session-search').value='보고서 I';renderSessions();
          active={...sessions.find(item=>item.id==='I'),messages:[]};renderSessions();
          $('session-search').value='';renderSessions();assert.equal(groupIds().includes('I'),true);
          assert.equal(more().attributes['aria-expanded'],'true');assert.equal(calls.length,0);
        })()""")

    def test_pin_or_order_changes_cannot_silently_hide_current_task(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          for(const item of sessions)if(['D','E','F','G','H'].includes(item.id))item.pinned=true;
          renderSessions();assert.equal(groupIds().includes('A'),true);assert.equal(groupIds().length,5);
          sessions[0].pinned=false;renderSessions();
          assert.equal(groupIds().includes('A'),true);assert.equal(groupIds().length,7);
          assert.equal(more().attributes['aria-expanded'],'true');assert.equal(active.id,'A');
          more().onclick();assert.equal(groupIds().includes('A'),false);
          for(let i=0;i<3;i++)renderSessions();assert.equal(groupIds().length,5);
          // A new selection resets only the explicit suppression, without changing the order.
          active={...sessions.find(item=>item.id==='D'),messages:[]};renderSessions();
          active={...sessions[0],messages:[]};renderSessions();assert.equal(groupIds().length,7);
          assert.equal(groupIds().includes('A'),true);assert.equal(calls.length,0);
        })()""")

    def test_loading_selected_sixth_task_expands_its_preview_immediately(self):
        self.run_case(LONG_GROUP_SETUP+r"""(()=>{
          active={...sessions.find(item=>item.id==='H'),messages:[]};renderSessions();
          assert.equal(groupIds().length,7);assert.equal(more().attributes['aria-expanded'],'true');assert.equal(calls.length,0);
        })()""")

    def test_group_collapse_preserves_local_more_preference_and_aria_icon_state(self):
        self.run_case(LONG_GROUP_SETUP+r"""(async()=>{
          renderSessions();more().onclick();assert.equal(groupIds().length,7);
          await groupNodes()[0].querySelector('.task-group-toggle').onclick();
          assert.equal(groupIds().length,0);assert.equal(more(),undefined);
          assert.equal(groupNodes()[0].querySelector('.task-group-toggle').attributes['aria-expanded'],'false');
          await groupNodes()[0].querySelector('.task-group-toggle').onclick();
          assert.equal(groupIds().length,7);assert.equal(more().attributes['aria-expanded'],'true');
          assert.equal(groupNodes()[0].querySelector('.task-group-toggle').attributes['aria-expanded'],'true');assert.equal(calls.length,2);
        })()""")

    def test_deleted_folder_does_not_keep_stale_more_state(self):
        self.run_case(LONG_GROUP_SETUP+r"""(async()=>{
          renderSessions();more().onclick();const original=deepCopy(boot.sessionGroups);
          WorkspaceSessionGroups.openEdit('group-a');const pending=$('task-group-delete').onclick();
          $('action-confirm').onclick();await pending;assert.equal(more(),undefined);
          // Reusing an ID emulates a fresh catalog: only existing groups may retain UI state.
          boot.sessionGroups=original;renderSessions();assert.equal(groupIds().length,5);
          assert.equal(more().attributes['aria-expanded'],'false');assert.equal(calls.length,1);
        })()""")

    def test_more_row_accepts_drop_and_preserves_unrendered_members(self):
        self.run_case(LONG_GROUP_SETUP+DRAG_SETUP+r"""(async()=>{
          const target=more().parentElement,source=row('C'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(target));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-a');
          assert.equal(target.classList.contains('drop-folder'),true);await handle.onpointerup(pointer(target));
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('C'),'group-a');
          assert.equal(WorkspaceSessionGroups.groupFor('H'),'group-a');assert.equal(WorkspaceSessionGroups.groupFor('I'),'group-a');
          assert.equal(groupNodes()[0].querySelector('.task-group-count').textContent,'8');
          assert.equal(groupIds().length,5);more().onclick();assert.equal(groupIds().length,8);
        })()""")

    def test_group_create_and_rename_use_enter_and_preserve_original_task_metadata(self):
        self.run_case(r"""(async()=>{
          const original=deepCopy(sessions);$('task-group-create').onclick();
          $('task-group-name').value='  새 프로젝트  ';
          await fire($('task-group-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,1);
          assert.deepEqual(calls[0].body,{action:'create',name:'새 프로젝트',revision:3});
          assert.equal(boot.sessionGroups.groups.at(-1).name,'새 프로젝트');
          assert.equal($('task-group-dialog').open,false);
          WorkspaceSessionGroups.openEdit('group-c');$('task-group-name').value='프로젝트 수정';
          await fire($('task-group-form'),'submit',{preventDefault(){}});
          assert.deepEqual(calls[1].body,{action:'rename',groupId:'group-c',name:'프로젝트 수정',revision:4});
          assert.equal(boot.sessionGroups.groups.at(-1).name,'프로젝트 수정');
          assert.deepEqual(sessions,original);assert.equal(active.messages[0].text,'기존 대화');
        })()""")

    def test_blank_folder_name_keeps_dialog_and_sends_no_mutation(self):
        self.run_case(r"""(async()=>{
          WorkspaceSessionGroups.openEdit();$('task-group-name').value='   ';
          await fire($('task-group-form'),'submit',{preventDefault(){}});
          assert.equal(calls.length,0);assert.equal($('task-group-dialog').open,true);
          assert.match($('task-group-error').textContent,/이름/);
        })()""")

    def test_move_changes_only_app_organization_and_keeps_draft_and_conversation(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='작성 중 요청';attachments=['keep.zip'];
          const original=deepCopy(sessions);renderSessions();
          const success=await WorkspaceSessionGroups.move('A','group-b');
          assert.equal(success,true);assert.equal(calls.length,1);
          assert.deepEqual(calls[0],{path:'/api/session-groups',body:{action:'move',id:'A',groupId:'group-b',revision:3}});
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
          assert.deepEqual(sessions,original);assert.equal(active.id,'A');assert.equal(active.messages[0].text,'기존 대화');
          assert.equal($('prompt').value,'작성 중 요청');assert.deepEqual(attachments,['keep.zip']);
          assert.equal(active.pinned,true);assert.equal(active.workspace,'C:/fixture/A');
        })()""")

    def test_folder_drag_retains_source_when_current_task_changes(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          const source=row('A'),title=source.querySelector('.session-drag');
          title.onpointerdown(pointer(source,{target:title}));
          title.onpointermove(pointer(header('group-b'),{target:title}));
          active={...sessions[1],messages:[{text:'B 대화'}]};selectionGeneration++;
          renderSessions();assert.equal(row('A'),source);
          await title.onpointerup(pointer(header('group-b'),{target:title}));
          assert.equal(calls.length,1);assert.equal(calls[0].body.id,'A');
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');assert.equal(active.id,'B');
          assert.equal(active.messages[0].text,'B 대화');
        })()""")

    def test_move_failure_keeps_membership_metadata_and_draft_for_retry(self):
        self.run_case(r"""(async()=>{
          const original=deepCopy(sessions),save=api;renderSessions();
          $('prompt').value='유지할 요청';attachments=['original.zip'];
          api=async(path,body)=>{calls.push({path,body});throw Error('폴더 저장 실패');};
          assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');
          assert.match(notices.at(-1),/폴더 저장 실패/);assert.equal(WorkspaceSessionGroups.isSaving(),false);
          assert.deepEqual(sessions,original);assert.equal(active.messages[0].text,'기존 대화');
          assert.equal($('prompt').value,'유지할 요청');assert.deepEqual(attachments,['original.zip']);
          api=save;assert.equal(await WorkspaceSessionGroups.move('A','group-b'),true);
          assert.equal(calls.length,2);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
          assert.deepEqual(sessions,original);assert.equal($('prompt').value,'유지할 요청');
        })()""")

    def test_empty_folder_delete_requires_confirmation_and_never_deletes_tasks(self):
        self.run_case(r"""(async()=>{
          delete boot.sessionGroups.assignments.A;
          const original=deepCopy(sessions);WorkspaceSessionGroups.openEdit('group-a');
          assert.equal($('task-group-delete').disabled,false);
          let pending=$('task-group-delete').onclick();assert.equal(calls.length,0);
          assert.match($('action-message').textContent,/대화와 실제 작업 파일/);
          $('action-cancel').onclick();await pending;assert.equal(calls.length,0);
          pending=$('task-group-delete').onclick();$('action-confirm').onclick();await pending;
          assert.equal(calls.length,1);assert.deepEqual(calls[0].body,{action:'delete',groupId:'group-a',revision:3});
          assert.equal(boot.sessionGroups.groups.some(group=>group.id==='group-a'),false);
          assert.equal(WorkspaceSessionGroups.groupFor('A'),null);assert.deepEqual(sessions,original);
          assert.equal(active.messages[0].text,'기존 대화');assert.equal($('task-group-dialog').open,false);
        })()""")

    def test_populated_folder_delete_releases_members_even_when_collapsed_or_filtered(self):
        self.run_case(r"""(async()=>{
          boot.sessionGroups.groups[0].collapsed=true;$('session-search').value='다른 검색';
          const original=deepCopy(sessions);$('prompt').value='계속 쓸 요청';attachments=['keep.zip'];
          renderSessions();WorkspaceSessionGroups.openEdit('group-a');
          assert.equal($('task-group-delete').disabled,false);
          const pending=$('task-group-delete').onclick();assert.equal(calls.length,0);
          assert.match($('action-message').textContent,/업무/);
          $('action-confirm').onclick();await pending;
          assert.equal(calls.length,1);assert.equal(calls[0].body.action,'delete');
          assert.equal(boot.sessionGroups.groups.some(group=>group.id==='group-a'),false);
          assert.equal(WorkspaceSessionGroups.groupFor('A'),null);
          assert.equal(WorkspaceSessionGroups.groupFor('B'),'group-b');
          assert.deepEqual(sessions,original);assert.equal(active.messages[0].text,'기존 대화');
          assert.equal($('prompt').value,'계속 쓸 요청');assert.deepEqual(attachments,['keep.zip']);
          assert.equal($('task-group-dialog').open,false);
          $('session-search').value='';renderSessions();assert.deepEqual(visibleIds(),['A','C']);
        })()""")

    def test_removing_folder_assignment_preserves_task_and_has_no_general_list_heading(self):
        self.run_case(r"""(async()=>{
          const original=deepCopy(sessions);renderSessions();
          assert.equal(await WorkspaceSessionGroups.move('A',null),true);
          assert.equal(WorkspaceSessionGroups.groupFor('A'),null);assert.equal(calls.length,1);
          assert.deepEqual(calls[0].body,{action:'move',id:'A',groupId:null,revision:3});
          assert.deepEqual(sessions,original);assert.equal(groupNodes().length,2);
          assert.ok(groupNodes().every(node=>node.dataset.groupId));
          const moved=rows().find(node=>node.dataset.sessionId==='A');
          assert.equal(moved.classList.contains('grouped-session'),false);assert.equal(moved.dataset.groupId,'');
        })()""")

    def test_cancel_populated_folder_delete_preserves_all_memberships(self):
        self.run_case(r"""(async()=>{
          WorkspaceSessionGroups.openEdit('group-a');const original=deepCopy(boot.sessionGroups);
          const pending=$('task-group-delete').onclick();
          $('action-cancel').onclick();await pending;
          assert.equal(calls.length,0);assert.equal($('task-group-delete').disabled,false);
          assert.equal($('task-group-dialog').open,true);assert.deepEqual(boot.sessionGroups,original);
        })()""")

    def test_pending_move_blocks_duplicate_and_conflicting_group_mutations(self):
        self.run_case(r"""(async()=>{
          let reply;api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const pending=WorkspaceSessionGroups.move('A','group-b');
          assert.equal(WorkspaceSessionGroups.isSaving(),true);assert.equal($('task-group-create').disabled,true);
          assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          assert.equal(await WorkspaceSessionGroups.move('B',null),false);assert.equal(calls.length,1);
          const changed=deepCopy(boot.sessionGroups);changed.assignments.A='group-b';changed.revision++;
          reply({ok:true,sessionGroups:changed});assert.equal(await pending,true);
          assert.equal(WorkspaceSessionGroups.isSaving(),false);assert.equal($('task-group-create').disabled,false);
          assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);assert.equal(calls.length,1);
        })()""")

    def test_revision_conflict_refreshes_without_replaying_the_move(self):
        self.run_case(r"""(async()=>{
          const fresh=deepCopy(boot.sessionGroups);fresh.revision=9;fresh.groups[0].name='다른 창에서 수정';
          api=async(path,body)=>{calls.push({path,body});if(!body)return fresh;
            const err=Error('목록이 변경됐어요');err.code='session_groups_changed';throw err;};
          assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          assert.equal(calls.length,2);assert.equal(calls.filter(call=>call.body).length,1);
          assert.equal(calls[1].path,'/api/session-groups');assert.equal(boot.sessionGroups.revision,9);
          assert.equal(boot.sessionGroups.groups[0].name,'다른 창에서 수정');
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');
          assert.match(notices.at(-1),/목록이 변경/);assert.equal(WorkspaceSessionGroups.isSaving(),false);
        })()""")

    def test_collapse_persists_once_and_ordinary_rerenders_do_not_fetch(self):
        self.run_case(r"""(async()=>{
          renderSessions();const group=groupNodes().find(node=>node.dataset.groupId==='group-a');
          await group.querySelector('.task-group-toggle').onclick();
          assert.equal(calls.length,1);assert.deepEqual(calls[0].body,{action:'collapse',groupId:'group-a',collapsed:true,revision:3});
          assert.deepEqual(visibleIds(),['C']);
          for(let index=0;index<10;index++){sessions[1].state=index%2?'running':'done';renderSessions();}
          assert.equal(calls.length,1);assert.equal(boot.sessionGroups.groups[0].collapsed,true);
          await groupNodes().find(node=>node.dataset.groupId==='group-a').querySelector('.task-group-toggle').onclick();
          assert.equal(calls.length,2);assert.equal(calls[1].body.collapsed,false);
          assert.deepEqual(visibleIds(),['A','C']);
          assert.equal(groupNodes().find(node=>node.dataset.groupId==='group-a').querySelector('.task-group-toggle').attributes['aria-expanded'],'true');
        })()""")

    def test_keyboard_folder_focus_returns_after_disabled_saving_render(self):
        self.run_case(r"""(async()=>{
          Object.getPrototypeOf($('sessions')).focus=function(){if(!this.disabled)document.activeElement=this;};
          renderSessions();const toggle=groupNodes().find(node=>node.dataset.groupId==='group-a').querySelector('.task-group-toggle');
          toggle.focus();await toggle.onclick();
          const replacement=groupNodes().find(node=>node.dataset.groupId==='group-a').querySelector('.task-group-toggle');
          assert.equal(replacement.disabled,false);assert.equal(replacement.attributes['aria-expanded'],'false');
          assert.equal(document.activeElement,replacement);
        })()""")

    def test_delayed_collapse_never_steals_focus_from_new_chat_input(self):
        self.run_case(r"""(async()=>{
          Object.getPrototypeOf($('sessions')).focus=function(){if(!this.disabled)document.activeElement=this;};
          renderSessions();const toggle=groupNodes().find(node=>node.dataset.groupId==='group-a').querySelector('.task-group-toggle');
          let reply;api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          toggle.focus();const pending=toggle.onclick();$('prompt').focus();$('prompt').value='계속 작성';
          const changed=deepCopy(boot.sessionGroups);changed.groups[0].collapsed=true;changed.revision++;
          reply({ok:true,sessionGroups:changed});await pending;
          assert.equal(document.activeElement,$('prompt'));assert.equal($('prompt').value,'계속 작성');
        })()""")

    def test_drag_to_collapsed_folder_moves_membership_without_selecting_or_reordering(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          selectSession=()=>{throw Error('Drag must not select a task');};$('prompt').value='드래그 중 초안';
          const original=row('A'),handle=original.querySelector('.session-drag');
          handle.onpointerdown(pointer(original));handle.onpointermove(pointer(header('group-b')));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          assert.equal(header('group-b').classList.contains('drop-folder'),true);
          sessions[0].state='running';renderSessions();assert.equal(row('A'),original);
          await handle.onpointerup(pointer(header('group-b')));
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/session-groups');
          assert.deepEqual(calls[0].body,{action:'move',id:'A',groupId:'group-b',revision:3});
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');assert.equal(sessions[0].pinned,true);
          assert.equal($('prompt').value,'드래그 중 초안');assert.equal(sessionPointerDrag,null);assert.equal(sessionDragId,null);
        })()""")

    def test_drag_to_unassigned_task_removes_only_folder_assignment(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(row('C')));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,null);
          await handle.onpointerup(pointer(row('C')));
          assert.equal(WorkspaceSessionGroups.groupFor('A'),null);assert.equal(calls.length,1);
          assert.deepEqual(calls[0].body,{action:'move',id:'A',groupId:null,revision:3});
          assert.equal(sessions[0].workspace,'C:/fixture/A');assert.equal(sessions[0].pinned,true);
        })()""")

    def test_foreign_folder_member_accepts_drop_across_pin_boundary(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.groups[1].collapsed=false;renderSessions();
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(row('B')));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          await handle.onpointerup(pointer(row('B')));
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/session-groups');
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');assert.equal(sessions[0].pinned,true);
        })()""")

    def test_task_grip_drop_on_folder_member_moves_without_opening_task(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.groups[1].collapsed=false;renderSessions();
          const selected=[];selectSession=async id=>{selected.push(id);return true;};
          $('prompt').value='손잡이로 옮겨도 초안 유지';attachments=['report.xlsx'];
          const source=row('A'),title=source.querySelector('.session-drag'),target=row('B');
          title.onpointerdown(pointer(source,{target:title}));title.onpointermove(pointer(target,{target:title}));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          assert.equal(target.classList.contains('drop-folder'),true);
          await title.onpointerup(pointer(target,{target:title}));

          assert.deepEqual(selected,[]);assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/session-groups');
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');assert.equal(sessions[0].pinned,true);
          assert.equal(active.id,'A');assert.equal(active.messages[0].text,'기존 대화');
          assert.equal($('prompt').value,'손잡이로 옮겨도 초안 유지');assert.equal(attachments[0],'report.xlsx');
        })()""")

    def test_task_grip_drop_on_empty_folder_area_keeps_pin_and_remove_without_move_icon(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          delete boot.sessionGroups.assignments.B;boot.sessionGroups.groups[1].collapsed=false;renderSessions();
          selectSession=()=>{throw Error('Folder move must not open task');};
          const source=row('A'),title=source.querySelector('.session-drag');
          const target=[...$('sessions').children].find(node=>node.classList.contains('task-group-empty')&&node.dataset.groupId==='group-b');
          title.onpointerdown(pointer(source,{target:title}));title.onpointermove(pointer(target,{target:title}));
          await title.onpointerup(pointer(target,{target:title}));
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
          const moved=row('A');assert.equal(moved.querySelector('.session-group-move'),null);
          const pin=moved.querySelector('.session-pin'),remove=moved.querySelector('.session-remove'),actions=[];
          updateSession=async(id,patch)=>actions.push({id,patch});hideSession=async id=>actions.push({id,hide:true});
          pin.onpointerdown?.(pointer(moved,{target:pin}));assert.equal(sessionPointerDrag,null);await pin.onclick();
          remove.onpointerdown?.(pointer(moved,{target:remove}));assert.equal(sessionPointerDrag,null);await remove.onclick();
          assert.deepEqual(actions,[{id:'A',patch:{pinned:false}},{id:'A',hide:true}]);assert.equal(calls.length,1);
        })()""")

    def test_task_lists_keep_title_click_grip_pin_and_remove_without_folder_move_button(self):
        self.run_case(DRAG_SETUP+r"""(()=>{
          showDialog('tasks-dialog');
          for(const id of ['sessions','home-recents','all-sessions']){
            const listed=[...$(id).children].filter(node=>node.dataset.sessionId);assert.ok(listed.length);
            for(const item of listed){
              assert.equal(item.querySelector('.session-group-move'),null);
              assert.ok(item.querySelector('.session-pin'));assert.ok(item.querySelector('.session-remove'));
              const title=item.querySelector(id==='home-recents'?'.recent-card':'.session');
              assert.equal(typeof title.onpointerdown,'function');assert.equal(typeof title.onclick,'function');
            }
          }
        })()""")

    def test_same_folder_task_row_cannot_reorder_across_pin_boundary(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.assignments.B='group-a';renderSessions();
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(row('B')));
          assert.equal(sessionPointerDrag.target,null);await handle.onpointerup(pointer(row('B')));
          assert.equal(calls.length,0);assert.equal(sessions[0].pinned,true);
        })()""")

    def test_empty_folder_placeholder_accepts_a_drop(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          delete boot.sessionGroups.assignments.B;boot.sessionGroups.groups[1].collapsed=false;renderSessions();
          const target=[...$('sessions').children].find(node=>node.classList.contains('task-group-empty')&&node.dataset.groupId==='group-b');
          assert.ok(target);const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(target));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          await handle.onpointerup(pointer(target));
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
        })()""")

    def test_trailing_folder_body_space_accepts_a_drop(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.groups[1].collapsed=false;renderSessions();
          const target=[...$('sessions').children].find(node=>node.classList.contains('task-group-tail')&&node.dataset.groupId==='group-b');
          assert.ok(target);const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(target));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          await handle.onpointerup(pointer(target));
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
        })()""")

    def test_blank_gap_between_folder_members_accepts_a_drop(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.groups[1].collapsed=false;
          sessions.push({id:'D',title:'두 번째 기획',workspace:'C:/fixture/D',state:'idle',pinned:false,created:4,updated:1});
          boot.sessionGroups.assignments.D='group-b';renderSessions();
          proto.getBoundingClientRect=function(){
            if(this.parent===$('sessions')){const top=this.parent.children.indexOf(this)*40;return {left:0,top,right:300,bottom:top+30,width:300,height:30};}
            return {left:0,top:0,right:300,bottom:600,width:300,height:600};
          };
          const members=rows().filter(node=>node.dataset.groupId==='group-b');assert.equal(members.length,2);
          const point=pointer(members[0],{clientY:members[0].getBoundingClientRect().bottom+5});
          assert.equal(document.elementFromPoint(point.clientX,point.clientY),$('sessions'));
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(point);
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,'group-b');
          await handle.onpointerup(point);
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-b');
        })()""")

    def test_general_list_blank_target_removes_membership_when_no_unassigned_task_exists(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          boot.sessionGroups.assignments.C='group-b';renderSessions();
          const target=[...$('sessions').children].find(node=>node.classList.contains('session-root-drop'));
          assert.ok(target);assert.equal(target.dataset.groupId,'');
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(target));
          assert.equal(sessionPointerDrag.target.kind,'folder');assert.equal(sessionPointerDrag.target.groupId,null);
          await handle.onpointerup(pointer(target));
          assert.equal(calls.length,1);assert.equal(WorkspaceSessionGroups.groupFor('A'),null);
          assert.equal(sessions[0].pinned,true);assert.equal(sessions[0].workspace,'C:/fixture/A');
        })()""")

    def test_drop_in_own_folder_space_is_a_noop(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          const target=[...$('sessions').children].find(node=>node.classList.contains('task-group-tail')&&node.dataset.groupId==='group-a');
          assert.ok(target);const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(target));
          assert.equal(sessionPointerDrag.target,null);await handle.onpointerup(pointer(target));
          assert.equal(calls.length,0);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');
        })()""")

    def test_boundary_gap_between_different_folders_does_not_move_a_task(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          proto.getBoundingClientRect=function(){
            if(this.parent===$('sessions')){const top=this.parent.children.indexOf(this)*40;return {left:0,top,right:300,bottom:top+30,width:300,height:30};}
            return {left:0,top:0,right:300,bottom:600,width:300,height:600};
          };
          const target=header('group-b');
          const point=pointer(target,{clientY:target.getBoundingClientRect().top-5});
          assert.equal(document.elementFromPoint(point.clientX,point.clientY),$('sessions'));
          const source=row('C'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(point);
          assert.equal(sessionPointerDrag.target,null);await handle.onpointerup(point);
          assert.equal(calls.length,0);assert.equal(WorkspaceSessionGroups.groupFor('C'),null);
        })()""")

    def test_same_folder_reorder_keeps_existing_order_endpoint_and_memberships(self):
        self.run_case(DRAG_SETUP+r"""(async()=>{
          sessions[0].pinned=false;
          sessions.push({id:'D',title:'추가 보고서',workspace:'C:/fixture/D',state:'done',pinned:false,created:4,updated:4});
          boot.sessionGroups.assignments.D='group-a';renderSessions();
          api=async(path,body)=>{calls.push({path,body});assert.equal(path,'/api/session/reorder');return {sessionOrder:boot.sessionOrder};};
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(row('D')));
          assert.equal(sessionPointerDrag.target.id,'D');
          await handle.onpointerup(pointer(row('D')));
          assert.equal(calls.length,1);assert.deepEqual(calls[0].body,{id:'A',targetId:'D',position:'before'});
          assert.deepEqual(visibleIds(),['A','D','C']);
          assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');assert.equal(WorkspaceSessionGroups.groupFor('D'),'group-a');
        })()""")

    def test_escape_cancels_highlighted_folder_drop_without_mutation(self):
        self.run_case(DRAG_SETUP+r"""(()=>{
          const source=row('A'),handle=source.querySelector('.session-drag');
          handle.onpointerdown(pointer(source));handle.onpointermove(pointer(header('group-b')));
          assert.equal(header('group-b').classList.contains('drop-folder'),true);
          handle.onkeydown({key:'Escape',preventDefault(){}});
          assert.equal(calls.length,0);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');
          assert.equal(sessionPointerDrag,null);assert.equal(sessionDragId,null);
          assert.equal(header('group-b').classList.contains('drop-folder'),false);
        })()""")

    def test_group_changes_are_blocked_while_existing_order_is_saving_or_app_closed(self):
        self.run_case(r"""(async()=>{
          sessionOrderSaving=true;assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          sessionOrderSaving=false;sessionDragId='A';assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          sessionDragId=null;appClosed=true;assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          appClosed=false;boot.sessionGroups.warning='목록 파일을 확인해 주세요';renderSessions();
          assert.equal($('task-group-create').disabled,true);assert.equal(await WorkspaceSessionGroups.move('A','group-b'),false);
          assert.equal(calls.length,0);assert.equal(WorkspaceSessionGroups.groupFor('A'),'group-a');
        })()""")


if __name__ == '__main__':
    unittest.main()
