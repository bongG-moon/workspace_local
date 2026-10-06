"""Exercise shipped chat/editor key routing without Claude or personal state."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[2],scenario=process.argv[3];
const setup=`
const assert=globalThis.testAssert;
let now=10000,active={id:'A',state:'idle',trusted:true,messages:[]},selectionGeneration=1;
let sessions=[{id:'A'},{id:'B'}];
let attachments=[],sending=false,choiceSubmission=null,appClosed=false,uploading=false;
let modelChanging=false,permissionChanging=false,effortChanging=false,connectionPreparing=false;
let selectedText='',completionOpen=false,completionSelected=0,inlineOpen=false,modeCycles=0;
let submitted=0,queued=[],calls=[],toasts=[],draftSaves=0,attachmentsRendered=0,folderChoices=0;
let apiDeferred=null,resultRefreshes=0,editorOpened=[],modalStack=[];
const busyStates=new Set(['starting','running','question','approval']);
const drafts=new Map();
class Node {
 constructor(tag='div',text='',classes=''){this.tagName=tag.toUpperCase();this.textContent=text||'';this.className=classes||'';this.value='';this.selectionStart=0;this.selectionEnd=0;this.readOnly=false;this.disabled=false;this.open=false;this.hidden=false;this.children=[];this.attributes={};this.listeners=new Map();this.dataset={};this.isConnected=true;this.classList={toggle:()=>{},add:()=>{},remove:()=>{}};}
 setSelectionRange(start,end=start){this.selectionStart=start;this.selectionEnd=end;}
 setRangeText(value,start,end){this.value=this.value.slice(0,start)+value+this.value.slice(end);this.setSelectionRange(start+value.length);}
 setAttribute(name,value){this.attributes[name]=String(value);}
 removeAttribute(name){delete this.attributes[name];}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 addEventListener(type,handler){if(!this.listeners.has(type))this.listeners.set(type,new Set());this.listeners.get(type).add(handler);}
 removeEventListener(type,handler){this.listeners.get(type)?.delete(handler);}
 emit(type,event={}){this['on'+type]?.(event);for(const listener of this.listeners.get(type)||[])listener(event);}
 focus(){if(document.activeElement!==this){document.activeElement?.emit?.('blur');document.activeElement=this;}}
 close(){this.open=false;this.onclose?.();}
 remove(){this.isConnected=false;}
 showModal(){this.open=true;}
 scrollIntoView(){}
 querySelectorAll(selector){const wanted=selector.split(',').map(value=>value.trim().replace(/^\\./,''));return this.children.filter(row=>wanted.some(name=>row.className.split(' ').includes(name)));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const nodes=new Map();function $(id){if(!nodes.has(id))nodes.set(id,new Node(id.includes('dialog')?'dialog':'div'));return nodes.get(id);}
function el(tag,text,classes){return new Node(tag,text,classes);}
const document={activeElement:null,execCommand(){return false;},querySelectorAll(selector){return [...nodes.values()].filter(node=>node.tagName==='DIALOG'&&node.open);}};
function saveDraft(){draftSaves++;drafts.set(active?.id||'home',{text:input.value,attachments:[...attachments]});}
function renderAttachments(){attachmentsRendered++;WorkspaceComposer?.close();}
function showDialog(id){const node=$(id);node.showModal();modalStack.push(node);}
function toast(message){toasts.push(message);}
function submit(){submitted++;return Promise.resolve();}
function chooseFolder(){folderChoices++;}
function refreshResults(){resultRefreshes++;}
function controlRestoreState(){return false;}
function connectionLocked(){return !active||busyStates.has(active.state)||sending||!!choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||appClosed;}
function permissionConnectionLocked(){return !active||sending||!!choiceSubmission||modelChanging||permissionChanging||effortChanging||connectionPreparing||appClosed||active.connection?.permissionModeChangePending===true||(busyStates.has(active.state)&&(active.connection?.connected!==true||active.connection?.capabilities?.setPermissionModeWhileRunning!==true));}
function setStatus(state){if(active)active.state=state;input.readOnly=sending||!!choiceSubmission||appClosed;}
function error(message){}
function refreshSessionMeta(){return Promise.resolve();}
function renderMessage(message){const row=el('article');row.append(el('span','','message-label'));$('conversation').append(row);return row;}
function api(url,body){calls.push({url,body});return apiDeferred?.promise||Promise.resolve({});}
function getSelection(){return {toString:()=>selectedText};}
const WorkspaceAttachments={isUploading:()=>uploading};
const WorkspaceWorkflow={isSubmitting:()=>false,send(action){queued.push({action,text:$("prompt").value,files:[...attachments]});return Promise.resolve();}};
const input=$("prompt");input.tagName='TEXTAREA';input.focus();
function set(text,start=text.length,end=start){input.value=text;input.setSelectionRange(start,end);input.emit('input',{target:input});input.focus();}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function press(key,options={},target=input){const event={key,target,ctrlKey:false,altKey:false,metaKey:false,shiftKey:false,isComposing:false,keyCode:0,repeat:false,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){},...options};target.onkeydown?.(event);return event;}
function ctrl(key,options={}){return press(key,{ctrlKey:true,...options});}
function fire(key,options={}){return WorkspaceShortcuts.keydown({key,ctrlKey:false,altKey:false,metaKey:false,shiftKey:false,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...options});}
function tick(){return Promise.resolve().then(()=>Promise.resolve());}
globalThis.getSelection=getSelection;
globalThis.WorkspaceAttachments=WorkspaceAttachments;globalThis.WorkspaceWorkflow=WorkspaceWorkflow;
`;
const ctx={console,Intl,testAssert:assert,Date:{now:()=>vm.runInContext('now',ctx)}};vm.createContext(ctx);vm.runInContext(setup,ctx);
function read(name){return fs.readFileSync(path.join(root,'local_app','web',name),'utf8');}
// Use the real completion and inline-control key handlers, with the rest of
// their networking/rendering intentionally excluded from this input boundary.
const composer=read('composer.js');let start=composer.indexOf('  function keydown(event)');let end=composer.indexOf('  $("composer-connect").onmousedown',start);
assert.ok(start>=0&&end>start,'Cannot locate shipped completion key handler');
vm.runInContext(`globalThis.WorkspaceComposer=(()=>{let composing=false,dismissed=false,selected=0;const box={get hidden(){return !completionOpen;}};const rows=[{},{}];function close(){completionOpen=false;}function choose(){completionSelected++;close();input.value='/selected ';input.setSelectionRange(input.value.length);}function activeOption(){} input.addEventListener('compositionstart',()=>{composing=true;});input.addEventListener('compositionend',()=>{composing=false;});${composer.slice(start,end)}return {keydown,close,refresh(){},beforeSubmit(){return true;}};})();`,ctx);
const inline=read('inline-controls.js');start=inline.indexOf('  function keydown(event)');end=inline.indexOf('  for (const [kind,button]',start);
assert.ok(start>=0&&end>start,'Cannot locate shipped inline key handler');
const inlineStopGate=inline.match(/^  const stopBlocked = .*;$/m)?.[0];
assert.ok(inlineStopGate,'Cannot locate shipped inline stop gate');
const inlineControlGate=inline.match(/^  const blocked = .*;$/m)?.[0];
assert.ok(inlineControlGate,'Cannot locate shipped inline control gate');
vm.runInContext(`globalThis.WorkspaceInlineControls=(()=>{const panel={get hidden(){return !inlineOpen;}};const view={};function close(){inlineOpen=false;}function cyclePermission(){modeCycles++;}function open(kind){editorOpened.push(kind);}${inlineStopGate}${inlineControlGate}${inline.slice(start,end)}return {keydown,close,cyclePermission,open};})();`,ctx);
vm.runInContext(read('input-keys.js'),ctx);
vm.runInContext(read('chat-shortcuts.js'),ctx);
const app=read('app.js');start=app.indexOf('$("composer").onsubmit=');end=app.indexOf('$("folder-form").onsubmit=',start);
assert.ok(start>=0&&end>start,'Cannot locate shipped composer event routing');
vm.runInContext(app.slice(start,end),ctx);
const sendStart=app.indexOf('async function submit(){');
assert.ok(sendStart>=0&&sendStart<start,'Cannot locate shipped submission function');
vm.runInContext(app.slice(sendStart,start).replace('async function submit(){','async function submitActual(){'),ctx);
// Exercise the shipped queue submission and its real mutation/ACK boundary.
// Rendering is outside this check; draft ownership and recovery stay real.
const workflow=read('workflow.js');
const contextStart=workflow.indexOf('  let snapshot ='),contextEnd=workflow.indexOf('  const busy =',contextStart);
const mutateStart=workflow.indexOf('  function requestId(body)'),mutateEnd=workflow.indexOf('  async function requestResume(',mutateStart);
const workflowStart=workflow.indexOf('  function sameDraft('),workflowEnd=workflow.indexOf('  async function reorder(',workflowStart);
assert.ok(contextStart>=0&&contextEnd>contextStart&&mutateStart>=0&&mutateEnd>mutateStart&&workflowStart>=0&&workflowEnd>workflowStart,'Cannot locate shipped queue submission boundary');
vm.runInContext(`globalThis.workflowSendActual=(()=>{${workflow.slice(contextStart,contextEnd)} const locked=()=>mutations.has(active?.id);function render(){}function apply(){} ${workflow.slice(mutateStart,mutateEnd)} ${workflow.slice(workflowStart,workflowEnd)} return send;})();`,ctx);
vm.runInContext(`(async()=>{${scenario}})().catch(error=>{console.error(error.stack||error);globalThis.testFailed=true;});`,ctx);
process.on('beforeExit',()=>{if(ctx.testFailed)process.exitCode=1;});
"""


@unittest.skipUnless(NODE, "Node.js is required for chat-shortcut checks")
class WorkspaceChatShortcutTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT), javascript], input=HARNESS,
            text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_enter_submits_but_shift_enter_and_ctrl_j_keep_multiline_draft(self):
        self.run_case(r"""
          set('첫 줄');assert.equal(press('Enter').defaultPrevented,true);assert.equal(submitted,1);
          set('첫 줄');const shift=press('Enter',{shiftKey:true});
          assert.equal(submitted,1);
          // A non-prevented Shift+Enter uses the textarea's native newline.
          if(!shift.defaultPrevented)input.setRangeText('\n',input.selectionStart,input.selectionEnd);
          assert.equal(input.value,'첫 줄\n');
          set('앞뒤',1);ctrl('j');assert.equal(input.value,'앞\n뒤');assert.equal(submitted,1);
          assert.equal(draftSaves>0,true);
        """)

    def test_composition_repeat_and_prevented_events_never_submit(self):
        self.run_case(r"""
          set('한글');press('Enter',{isComposing:true});press('Enter',{keyCode:229});
          press('Enter',{repeat:true});press('Enter',{defaultPrevented:true});
          input.emit('compositionstart');press('Enter');input.emit('compositionend');
          assert.equal(submitted,0);assert.equal(queued.length,0);assert.equal(input.value,'한글');
          press('Enter');assert.equal(submitted,1);
        """)

    def test_completion_consumes_enter_tab_and_escape_before_send_or_stop(self):
        self.run_case(r"""
          active.state='running';set('/sk');completionOpen=true;
          press('Enter');assert.equal(completionSelected,1);assert.equal(submitted,0);assert.equal(calls.length,0);
          completionOpen=true;press('Tab');assert.equal(completionSelected,2);assert.equal(submitted,0);
          completionOpen=true;press('Escape');assert.equal(completionOpen,false);assert.equal(calls.length,0);
          press('Enter',{repeat:true});assert.equal(submitted,0);
        """)

    def test_inline_mode_shortcut_and_escape_take_priority(self):
        self.run_case(r"""
          set('유지할 입력');press('Tab',{shiftKey:true});assert.equal(modeCycles,1);assert.equal(submitted,0);
          active.state='running';inlineOpen=true;press('Escape');assert.equal(inlineOpen,false);assert.equal(calls.length,0);
          assert.equal(input.value,'유지할 입력');
        """)

    def test_permission_shortcuts_work_during_active_work_but_model_shortcut_waits(self):
        self.run_case(r"""
          active.state='running';set('작성한 후속 요청');
          press('m',{altKey:true});assert.equal(modeCycles,0);
          active.connection={connected:true,capabilities:{setPermissionModeWhileRunning:true}};
          press('m',{altKey:true});assert.equal(modeCycles,1);
          press('Tab',{shiftKey:true});assert.equal(modeCycles,2);
          press('p',{altKey:true});assert.equal(editorOpened.length,0);
          active.connection.permissionModeChangePending=true;
          press('m',{altKey:true});press('Tab',{shiftKey:true});assert.equal(modeCycles,2);
          assert.equal(submitted,0);assert.equal(queued.length,0);assert.equal(calls.length,0);
          assert.equal(input.value,'작성한 후속 요청');assert.equal(active.state,'running');
        """)

    def test_queue_chord_bypasses_completion_and_keeps_three_second_window(self):
        self.run_case(r"""
          active.state='running';set('/partial');completionOpen=true;
          ctrl('x');now+=2200;press('Enter');
          assert.equal(completionSelected,0);assert.equal(submitted,0);assert.equal(queued.length,1);
          assert.equal(queued[0].action,'enqueue');assert.equal(queued[0].text,'/partial');
          assert.equal(calls.length,0);
        """)

    def test_prefix_is_cancelled_on_blur_and_composition(self):
        self.run_case(r"""
          set('/s');ctrl('x');$('other').focus();input.focus();completionOpen=true;press('Enter');
          assert.equal(completionSelected,1);assert.equal(queued.length,0);
          set('/s');ctrl('x');input.emit('compositionstart');input.emit('compositionend');completionOpen=true;press('Enter');
          assert.equal(completionSelected,2);assert.equal(queued.length,0);
        """)

    def test_expired_queue_chord_returns_to_normal_completion_behavior(self):
        self.run_case(r"""
          set('/s');completionOpen=true;ctrl('x');now+=3100;press('Enter');
          assert.equal(completionSelected,1);assert.equal(queued.length,0);assert.equal(submitted,0);
        """)

    def test_ctrl_enter_busy_never_silently_queues_or_interrupts(self):
        self.run_case(r"""
          active.state='running';set('지금 이어서 할 일');attachments=['C:/report.xlsx'];
          press('Enter',{ctrlKey:true});ctrl('x');ctrl('s');
          assert.equal(input.value,'지금 이어서 할 일');assert.deepEqual(attachments,['C:/report.xlsx']);
          assert.equal(submitted,0);assert.equal(queued.length,0);assert.equal(calls.length,0);assert.equal(toasts.length,2);
          active.state='idle';press('Enter',{ctrlKey:true});assert.equal(submitted,1);
        """)

    def test_interrupt_captures_owner_and_never_interrupts_a_newly_selected_task(self):
        self.run_case(r"""
          active.state='running';set('초안');apiDeferred=deferred();press('Escape');
          assert.deepEqual(calls,[{url:'/api/stop',body:{id:'A'}}]);
          active={id:'B',state:'running',messages:[]};selectionGeneration++;press('Escape');
          assert.equal(calls.length,1);apiDeferred.resolve({});await tick();
          assert.equal(resultRefreshes,0);assert.equal(input.value,'초안');
        """)

    def test_keyboard_stop_uses_shared_lifecycle_and_never_queues_during_stop(self):
        self.run_case(r"""
          active.state='running';set('초안');let stopCalls=0;
          globalThis.WorkspaceStop={request:()=>{stopCalls++;},blocked:()=>true};
          press('Escape');ctrl('c');assert.equal(stopCalls,2);assert.equal(calls.length,0);
          ctrl('x');press('Enter');assert.equal(queued.length,0);assert.equal(input.value,'초안');
          assert.equal(submitted,0);assert.ok(draftSaves>0);
        """)

    def test_ctrl_c_keeps_selection_copy_and_interrupts_only_active_work(self):
        self.run_case(r"""
          active.state='running';set('선택한 글자',0,3);assert.equal(ctrl('c').defaultPrevented,false);assert.equal(calls.length,0);
          set('초안');selectedText='답변에서 선택한 글자';assert.equal(ctrl('c').defaultPrevented,false);assert.equal(calls.length,0);
          selectedText='';ctrl('c');assert.equal(calls.length,1);assert.equal(calls[0].body.id,'A');
          await tick();active.state='idle';set('지울 입력');ctrl('c');assert.equal(input.value,'');assert.equal(calls.length,1);
        """)

    def test_search_restores_current_task_prompt_and_attachments_without_sending(self):
        self.run_case(r"""
          active.messages=[{role:'assistant',text:'제외'},{role:'user',text:'첫 입력',files:['C:/one.png']},{role:'user',text:'둘째 입력',files:['C:/two.csv']}];
          set('보존 초안');ctrl('r');assert.equal($('input-history-dialog').open,true);
          assert.equal($('input-history-results').children.length,2);
          const search=$('input-history-search');search.value='첫';search.oninput();press('Enter',{},search);
          assert.equal(input.value,'첫 입력');assert.deepEqual(attachments,['C:/one.png']);assert.equal(submitted,0);assert.equal(queued.length,0);
          attachments.push('C:/extra.pdf');assert.deepEqual(active.messages[1].files,['C:/one.png']);
        """)

    def test_stale_search_does_not_replace_another_tasks_draft(self):
        self.run_case(r"""
          active.messages=[{role:'user',text:'업무 A 기록',files:['C:/a.png']}];set('A 초안');ctrl('r');
          active={id:'B',state:'idle',messages:[]};selectionGeneration++;input.value='B 초안';
          press('Enter',{},$('input-history-search'));assert.equal(input.value,'B 초안');assert.equal(submitted,0);
          $('input-history-dialog').close();$('settings-dialog').showModal();$('settings-search').focus();
          fire('c',{ctrlKey:true});fire('Escape');assert.equal(calls.length,0);assert.equal(input.value,'B 초안');
        """)

    def test_history_search_ime_enter_does_not_select_or_submit(self):
        self.run_case(r"""
          active.messages=[{role:'user',text:'한국어 요청',files:['C:/one.png']}];set('작성 중 초안');ctrl('r');
          const search=$('input-history-search');search.emit('compositionstart');press('Enter',{},search);
          assert.equal(input.value,'작성 중 초안');assert.equal($('input-history-dialog').open,true);
          search.emit('compositionend');press('Enter',{keyCode:229},search);assert.equal($('input-history-dialog').open,true);
          press('Enter',{},search);assert.equal(input.value,'한국어 요청');assert.equal($('input-history-dialog').open,false);
          assert.equal(submitted,0);assert.equal(queued.length,0);
        """)

    def test_shortcuts_are_inert_during_read_only_sending_and_uploading(self):
        self.run_case(r"""
          active.state='running';set('유지할 초안');
          for(const kind of ['readOnly','sending','uploading']){
            input.readOnly=kind==='readOnly';sending=kind==='sending';uploading=kind==='uploading';
            for(const name of ['c','s','r','o'])ctrl(name);press('Escape');
            assert.equal(input.value,'유지할 초안');assert.equal(calls.length,0);assert.equal(WorkspaceShortcuts.hasStashes(),false);
            assert.equal($('input-history-dialog').open,false);
          }
        """)

    def test_editor_history_retains_navigation_across_shared_restore_hooks(self):
        self.run_case(r"""
          active.messages=[{role:'user',text:'첫 요청',files:['C:/first.png']},{role:'user',text:'둘째 요청',files:['C:/second.csv']}];
          set('내 초안',0);attachments=['C:/draft.xlsx'];press('ArrowUp');assert.equal(input.value,'둘째 요청');
          press('ArrowUp');assert.equal(input.value,'첫 요청');input.setSelectionRange(input.value.length);press('ArrowDown');
          assert.equal(input.value,'둘째 요청');press('ArrowDown');assert.equal(input.value,'내 초안');
          assert.deepEqual(attachments,['C:/draft.xlsx']);assert.equal(input.selectionStart,0);assert.equal(submitted,0);
        """)

    def test_stash_replaces_the_single_slot_and_restores_selection_and_attachments(self):
        self.run_case(r"""
          set('첫 보관',1,3);attachments=['C:/one.png'];ctrl('s');assert.equal(input.value,'');
          set('새 보관',2,4);attachments=['C:/two.csv'];ctrl('s');assert.equal(input.value,'');
          ctrl('s');assert.equal(input.value,'새 보관');assert.deepEqual(attachments,['C:/two.csv']);
          assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);assert.equal(submitted,0);
          assert.equal(WorkspaceShortcuts.hasStashes(),false);
        """)

    def test_after_send_restores_stash_only_to_blank_draft_of_the_same_task(self):
        self.run_case(r"""
          set('원래 긴 입력',3);attachments=['C:/original.docx'];ctrl('s');
          set('임시 질문');WorkspaceShortcuts.afterSend('A');assert.equal(input.value,'임시 질문');
          input.value='';attachments=[];WorkspaceShortcuts.afterSend('B');assert.equal(input.value,'');
          WorkspaceShortcuts.afterSend('A');assert.equal(input.value,'원래 긴 입력');
          assert.deepEqual(attachments,['C:/original.docx']);assert.equal(input.selectionStart,3);assert.equal(submitted,0);
          assert.equal(WorkspaceShortcuts.hasStashes(),false);
        """)

    def test_stashes_are_separate_per_task_and_do_not_overwrite_new_attachments(self):
        self.run_case(r"""
          set('A 보관');ctrl('s');active={id:'B',state:'idle',messages:[]};selectionGeneration++;set('B 보관');ctrl('s');
          WorkspaceShortcuts.afterSend('A');assert.equal(input.value,'');assert.equal(drafts.get('A').text,'A 보관');
          attachments=['C:/new.png'];WorkspaceShortcuts.afterSend('B');assert.equal(input.value,'');assert.deepEqual(attachments,['C:/new.png']);
          attachments=[];WorkspaceShortcuts.afterSend('B');assert.equal(input.value,'B 보관');
          assert.equal(drafts.get('A').text,'A 보관');
        """)

    def test_accepted_send_records_history_and_restores_stash_only_after_ack(self):
        self.run_case(r"""
          set('원래 초안',2);attachments=['C:/saved.png'];ctrl('s');set('임시 요청');attachments=['C:/question.csv'];
          apiDeferred=deferred();const operation=submitActual();
          assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/send');assert.equal(active.messages.length,0);
          assert.equal(input.value,'임시 요청');assert.equal(WorkspaceShortcuts.hasStashes(),true);
          apiDeferred.resolve({});await operation;
          assert.equal(active.messages.length,1);assert.equal(active.messages[0].text,'임시 요청');
          assert.deepEqual(active.messages[0].files,['C:/question.csv']);
          assert.equal(input.value,'원래 초안');assert.deepEqual(attachments,['C:/saved.png']);assert.equal(input.selectionStart,2);
          ctrl('r');assert.equal($('input-history-results').children.length,1);
          assert.equal($('input-history-results').children[0].textContent,'임시 요청');
        """)

    def test_rejected_send_preserves_temporary_draft_stash_and_history(self):
        self.run_case(r"""
          set('보관 초안');attachments=['C:/saved.png'];ctrl('s');set('실패한 요청');attachments=['C:/request.csv'];
          apiDeferred=deferred();const operation=submitActual();apiDeferred.reject(new Error('Disconnected'));await operation;
          assert.equal(active.messages.length,0);assert.equal(input.value,'실패한 요청');assert.deepEqual(attachments,['C:/request.csv']);
          assert.equal(WorkspaceShortcuts.hasStashes(),true);assert.equal(submitted,0);
          set('');attachments=[];ctrl('s');assert.equal(input.value,'보관 초안');assert.deepEqual(attachments,['C:/saved.png']);
        """)

    def test_submission_ack_from_another_task_does_not_change_visible_draft(self):
        self.run_case(r"""
          set('A 보관');ctrl('s');set('A 요청');apiDeferred=deferred();const owner=active,operation=submitActual();
          active={id:'B',state:'idle',trusted:true,messages:[]};selectionGeneration++;input.value='B 새 초안';attachments=['C:/b.pdf'];
          apiDeferred.resolve({});await operation;
          assert.equal(input.value,'B 새 초안');assert.deepEqual(attachments,['C:/b.pdf']);assert.equal(active.messages.length,0);
          assert.equal(owner.messages[0].text,'A 요청');assert.equal(drafts.get('A').text,'A 보관');
        """)

    def test_recovery_keeps_existing_stashes_and_rejects_unknown_task_owners(self):
        self.run_case(r"""
          set('이미 보관한 내용',3);attachments=['C:/existing.png'];ctrl('s');
          const conflicts=WorkspaceShortcuts.restoreStashes([
            {id:'A',text:'덮어쓰면 안 되는 내용',attachments:[],selectionStart:0,selectionEnd:0},
            {id:'missing',text:'존재하지 않는 업무',attachments:[],selectionStart:0,selectionEnd:0},
            {id:'B',text:'업무 B 복원',attachments:['C:/b.csv'],selectionStart:2,selectionEnd:4},
          ]);
          assert.deepEqual([...conflicts],['A','missing']);
          const exported=WorkspaceShortcuts.exportStashes();assert.equal(exported.length,2);assert.equal(exported.find(row=>row.id==='A').text,'이미 보관한 내용');
          active={id:'B',state:'idle',messages:[]};selectionGeneration++;set('');attachments=[];ctrl('s');
          assert.equal(input.value,'업무 B 복원');assert.deepEqual(attachments,['C:/b.csv']);assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);
          assert.equal(submitted,0);assert.equal(calls.length,0);
        """)

    def test_queue_ack_restores_stash_only_after_accepted_unchanged_input(self):
        self.run_case(r"""
          active.state='running';set('보관한 본문',2);attachments=['C:/saved.png'];ctrl('s');
          set('  이어 할 요청  ');attachments=['C:/queued.csv'];apiDeferred=deferred();
          const restored=[],afterSend=WorkspaceShortcuts.afterSend;
          WorkspaceShortcuts.afterSend=id=>{restored.push(id);afterSend(id);};
          const operation=workflowSendActual('enqueue');
          assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/dispatch');
          assert.equal(calls[0].body.id,'A');assert.equal(calls[0].body.action,'enqueue');
          assert.equal(calls[0].body.text,'이어 할 요청');assert.deepEqual(calls[0].body.attachments,['C:/queued.csv']);
          assert.equal(input.value,'  이어 할 요청  ');assert.equal(restored.length,0);assert.equal(WorkspaceShortcuts.hasStashes(),true);
          apiDeferred.resolve({});await operation;
          assert.deepEqual(restored,['A']);assert.equal(input.value,'보관한 본문');assert.deepEqual(attachments,['C:/saved.png']);
          assert.equal(input.selectionStart,2);assert.equal(WorkspaceShortcuts.hasStashes(),false);assert.equal(submitted,0);
        """)

    def test_queue_ack_after_task_switch_restores_only_original_unchanged_draft(self):
        self.run_case(r"""
          active.state='running';set('A 보관',1);attachments=['C:/saved-a.png'];ctrl('s');
          set('A 대기 요청');attachments=['C:/queued-a.csv'];saveDraft();apiDeferred=deferred();
          const restored=[],afterSend=WorkspaceShortcuts.afterSend;
          WorkspaceShortcuts.afterSend=id=>{restored.push(id);afterSend(id);};
          const operation=workflowSendActual('enqueue');
          active={id:'B',state:'idle',trusted:true,messages:[]};selectionGeneration++;set('B 새 초안');attachments=['C:/b.pdf'];saveDraft();
          apiDeferred.resolve({});await operation;
          assert.deepEqual(restored,['A']);assert.equal(input.value,'B 새 초안');assert.deepEqual(attachments,['C:/b.pdf']);
          assert.equal(drafts.get('A').text,'A 보관');assert.deepEqual(drafts.get('A').attachments,['C:/saved-a.png']);
          assert.equal(drafts.get('A').selectionStart,1);assert.equal(drafts.get('B').text,'B 새 초안');
          assert.equal(WorkspaceShortcuts.hasStashes(),false);assert.equal(submitted,0);
        """)

    def test_queue_failure_keeps_pending_input_files_and_stash_without_restore(self):
        self.run_case(r"""
          active.state='running';set('보관할 원문');attachments=['C:/saved.png'];ctrl('s');
          set('전송 실패한 요청');attachments=['C:/request.csv'];saveDraft();apiDeferred=deferred();
          const restored=[],afterSend=WorkspaceShortcuts.afterSend;
          WorkspaceShortcuts.afterSend=id=>{restored.push(id);afterSend(id);};
          const operation=workflowSendActual('enqueue');apiDeferred.reject(new Error('연결이 끊겼습니다'));await operation;
          assert.deepEqual(restored,[]);assert.equal(input.value,'전송 실패한 요청');assert.deepEqual(attachments,['C:/request.csv']);
          assert.equal(drafts.get('A').text,'전송 실패한 요청');assert.equal(WorkspaceShortcuts.hasStashes(),true);
          assert.equal(toasts.at(-1),'연결이 끊겼습니다');assert.equal(submitted,0);
        """)

    def test_queue_ack_never_replaces_newer_text_or_attachments_in_current_task(self):
        self.run_case(r"""
          active.state='running';set('원래 보관');ctrl('s');
          const restored=[],afterSend=WorkspaceShortcuts.afterSend;
          WorkspaceShortcuts.afterSend=id=>{restored.push(id);afterSend(id);};
          for(const change of ['text','attachments']){
            set('대기 요청');attachments=['C:/request.csv'];apiDeferred=deferred();const operation=workflowSendActual('enqueue');
            if(change==='text')set('더 새로운 초안');else attachments.push('C:/newer.png');
            saveDraft();apiDeferred.resolve({});await operation;
            assert.equal(input.value,change==='text'?'더 새로운 초안':'대기 요청');
            assert.deepEqual(attachments,change==='text'?['C:/request.csv']:['C:/request.csv','C:/newer.png']);
            assert.deepEqual(restored,[]);assert.equal(WorkspaceShortcuts.hasStashes(),true);
          }
        """)

    def test_queue_ack_after_switch_preserves_newer_original_task_draft(self):
        self.run_case(r"""
          active.state='running';set('A 보관');ctrl('s');set('A 대기 요청');attachments=['C:/a.csv'];saveDraft();
          apiDeferred=deferred();const restored=[],afterSend=WorkspaceShortcuts.afterSend;
          WorkspaceShortcuts.afterSend=id=>{restored.push(id);afterSend(id);};const operation=workflowSendActual('enqueue');
          set('A 더 새로운 초안');attachments=['C:/new-a.png'];saveDraft();
          active={id:'B',state:'idle',trusted:true,messages:[]};selectionGeneration++;set('B 초안');attachments=['C:/b.pdf'];
          apiDeferred.resolve({});await operation;
          assert.deepEqual(restored,[]);assert.equal(input.value,'B 초안');assert.deepEqual(attachments,['C:/b.pdf']);
          assert.equal(drafts.get('A').text,'A 더 새로운 초안');assert.deepEqual(drafts.get('A').attachments,['C:/new-a.png']);
          assert.equal(WorkspaceShortcuts.hasStashes(),true);assert.equal(submitted,0);
        """)

    def test_ctrl_o_toggles_actual_tool_and_execution_detail_classes(self):
        self.run_case(r"""
          const activity=el('details','','tool-activity-detail'),execution=el('details','','execution-detail');let toggles=0;
          activity.ontoggle=execution.ontoggle=()=>toggles++;$('conversation').append(activity,execution);
          ctrl('o');assert.equal(activity.open,true);assert.equal(execution.open,true);assert.equal(toggles,2);
          ctrl('o');assert.equal(activity.open,false);assert.equal(execution.open,false);assert.equal(toggles,4);assert.equal(submitted,0);
        """)

    def test_unsupported_terminal_actions_keep_draft_and_current_work_untouched(self):
        self.run_case(r"""
          active.state='running';set('보존할 요청');attachments=['C:/file.txt'];
          for(const key of ['b','g','t'])ctrl(key);press('t',{altKey:true});press('o',{altKey:true});
          assert.equal(input.value,'보존할 요청');assert.deepEqual(attachments,['C:/file.txt']);
          assert.equal(submitted,0);assert.equal(queued.length,0);assert.equal(calls.length,0);assert.equal(toasts.length,5);
        """)


if __name__ == "__main__":
    unittest.main()
