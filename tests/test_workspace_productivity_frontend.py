"""Deterministic UI regressions for streaming, copied drops, and queued work."""
from pathlib import Path
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.addEventListener=function(name,fn){this.listeners??={};(this.listeners[name]??=[]).push(fn);};
Element.prototype.contains=function(node){for(let n=node;n;n=n.parent)if(n===this)return true;return false;};
context.frames=new Map();context.timers=new Map();let serial=0;
context.requestAnimationFrame=fn=>{context.frames.set(++serial,fn);return serial;};
context.cancelAnimationFrame=id=>context.frames.delete(id);
context.setTimeout=fn=>{context.timers.set(++serial,fn);return serial;};
context.clearTimeout=id=>context.timers.delete(id);
context.crypto={randomUUID:()=>`request-${++serial}`};
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
for(const file of JSON.parse(process.argv[4]))vm.runInContext(fs.readFileSync(file,'utf8'),context,{filename:file});
const scenario=process.argv[3];
""")
SETUP = r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'running',trusted:true,messages:[]};sessions=[{...active}];
const emit=(node,name,event={})=>{for(const fn of node.listeners?.[name]||[])fn(event);};
const flushTimers=()=>{const batch=[...timers.values()];timers.clear();for(const fn of batch)fn();};
const flushFrames=()=>{const batch=[...frames.values()];frames.clear();for(const fn of batch)fn();};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
"""


@unittest.skipUnless(NODE, "Node.js is required for productivity UI checks")
class WorkspaceProductivityFrontendTests(unittest.TestCase):
    def run_case(self, script, modules=()):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), SETUP + script,
             json.dumps([str(ROOT / f"local_app/web/{module}.js") for module in modules])],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_fragments_are_batched_and_final_message_supersedes_pending_fragments(self):
        self.run_case(r"""
          renderDelta({messageId:'one',text:'첫 '});renderDelta({messageId:'one',text:'응답'});
          assert.equal(streaming.size,0);assert.equal(timers.size,1);
          flushTimers();flushFrames();
          assert.equal(streaming.get('one:0').querySelector('.message-body').textContent,'첫 응답');
          renderDelta({messageId:'one',text:' 오래된 부분'});
          renderMessage({role:'assistant',messageId:'one',text:'최종 응답'});
          flushTimers();flushFrames();assert.equal(streaming.size,0);
          assert.match(flatText($('conversation')),/최종 응답/);assert.doesNotMatch(flatText($('conversation')),/오래된 부분/);
        """, ("rendering",))

    def test_reading_upward_cancels_pending_scroll_until_explicit_latest(self):
        self.run_case(r"""
          const area=$('work-area');area.scrollHeight=1000;area.clientHeight=100;area.scrollTop=900;
          WorkspaceStream.changed();emit(area,'wheel',{deltaY:-100});area.scrollTop=500;
          flushFrames();assert.equal(area.scrollTop,500);assert.equal($('latest-response').hidden,false);
          renderDelta({messageId:'one',text:'추가'});flushTimers();flushFrames();flushFrames();
          assert.equal(area.scrollTop,500);$('latest-response').onclick();assert.equal(area.scrollTop,1000);
          assert.equal($('latest-response').hidden,true);
        """, ("rendering",))

    def test_pending_stream_cannot_leak_into_another_session(self):
        self.run_case(r"""
          renderDelta({messageId:'one',text:'A 답변'});WorkspaceStream.reset();
          active={id:'B',state:'idle'};flushTimers();flushFrames();
          assert.equal(streaming.size,0);assert.doesNotMatch(flatText($('conversation')),/A 답변/);
        """, ("rendering",))

    def test_uploaded_copy_stays_with_original_task_after_switch(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='A 초안';attachments=['C:/A/existing.txt'];saveDraft();
          let reply,call;fetch=(path,options)=>{call={path,options};return new Promise(resolve=>reply=resolve);};
          const file={name:'한글 자료.txt',size:4};
          const event={preventDefault(){},dataTransfer:{types:['Files'],files:[file],getData(){return '';}}};
          const pending=WorkspaceAttachments.drop(event);assert.equal(WorkspaceAttachments.isUploading(),true);
          assert.match(call.path,/id=A/);assert.equal(call.options.headers['X-File-Name'],encodeURIComponent(file.name));
          assert.equal(call.options.body,file);
          active={id:'B',title:'B',state:'idle'};selectionGeneration++;attachments=[];$('prompt').value='B 초안';
          reply({ok:true,json:async()=>({path:'C:/managed/copy.txt',name:file.name,size:4,copied:true})});await pending;
          assert.equal($('prompt').value,'B 초안');assert.equal(attachments.length,0);
          assert.equal(drafts.get('A').attachments[1],'C:/managed/copy.txt');assert.equal(WorkspaceAttachments.isCopy('C:/managed/copy.txt'),true);
          assert.equal(WorkspaceAttachments.isUploading(),false);assert.equal($('send').disabled,false);
        })()""", ("attachments",))

    def test_internal_file_drag_uses_memory_path_and_rejects_forged_or_stale_drop(self):
        self.run_case(r"""(async()=>{
          let count=0;fetch=async()=>{count++;};const node=el('button');
          const transfer={types:['application/x-company-workspace-file'],value:'',setData(type,value){this.value=value;},getData(){return this.value;}};
          WorkspaceAttachments.makeDraggable(node,'C:/A/original.txt','A');node.ondragstart({dataTransfer:transfer});
          await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:transfer});assert.equal(attachments[0],'C:/A/original.txt');assert.equal(count,0);
          transfer.value='forged';await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:transfer});assert.equal(attachments.length,1);
          node.ondragstart({dataTransfer:transfer});selectionGeneration++;
          await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:transfer});assert.equal(attachments.length,1);
        })()""", ("attachments",))

    def test_drop_limits_reject_without_upload_or_losing_existing_draft(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='그대로';attachments=['C:/keep'];let calls=0;fetch=async()=>{calls++;};
          await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:{types:['Files'],getData(){return '';},files:[{name:'large.bin',size:50*1024*1024+1}]}});
          assert.equal(calls,0);assert.equal($('prompt').value,'그대로');assert.equal(attachments.length,1);
        })()""", ("attachments",))

    def test_followup_idempotency_retry_preserves_draft_and_action_semantics(self):
        self.run_case(r"""(async()=>{
          const state={revision:1,queue:[],schedules:[],steer:{supported:true,mode:'interrupt_resume',reason:'현재 요청을 중지한 뒤 같은 대화에서 이어갑니다.'}};
          api=async()=>state;await WorkspaceWorkflow.refresh();
          assert.match($('followup-note').textContent,/중지/);assert.equal($('followup-now').disabled,false);
          $('prompt').value='후속 질문';attachments=['C:/A/data.csv'];let calls=[];
          api=async(path,body)=>{calls.push(body);throw Error('응답 확인 실패');};
          await WorkspaceWorkflow.send('enqueue');assert.equal($('prompt').value,'후속 질문');assert.equal(attachments.length,1);
          api=async(path,body)=>{calls.push(body);return {...state,revision:2,queue:[{id:'q1',text:body.text,attachments:body.attachments,state:'queued'}]};};
          await WorkspaceWorkflow.send('enqueue');assert.equal(calls[0].clientRequestId,calls[1].clientRequestId);
          assert.equal(calls[1].action,'enqueue');assert.equal($('prompt').value,'');assert.equal(attachments.length,0);
        })()""", ("workflow",))

    def test_duplicate_click_and_old_session_ack_cannot_clear_new_task(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='A 질문';attachments=['A.csv'];saveDraft();let reply,calls=0;
          api=()=>{calls++;return new Promise(resolve=>reply=resolve);};
          const first=WorkspaceWorkflow.send('enqueue');await WorkspaceWorkflow.send('enqueue');assert.equal(calls,1);
          active={id:'B',title:'B',state:'idle',trusted:true};selectionGeneration++;$('prompt').value='B 질문';attachments=['B.csv'];
          reply({queue:[],schedules:[],revision:2});await first;
          assert.equal($('prompt').value,'B 질문');assert.equal(attachments[0],'B.csv');assert.equal($('send').disabled,false);
        })()""", ("workflow",))

    def test_weekly_editor_sends_local_time_and_exact_weekdays(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='월간 보고';attachments=['data.csv'];WorkspaceWorkflow.openEditor('schedule');
          $('schedule-kind').value='weekly';$('schedule-time').value='09:30';
          const days=$('schedule-weekday-options').children;days[0].children[0].checked=false;days[2].children[0].checked=true;days[4].children[0].checked=true;
          let call;api=async(path,body)=>{call=body;return {queue:[],schedules:[],revision:1};};
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(call.action,'schedule');assert.equal(call.schedule.kind,'weekly');assert.equal(call.schedule.time,'09:30');
          assert.equal(JSON.stringify(call.schedule.weekdays),'[2,4]');assert.equal(call.attachments[0],'data.csv');assert.ok(call.clientRequestId);
          assert.equal($('request-editor-dialog').open,false);assert.equal($('prompt').value,'월간 보고');
        })()""", ("workflow",))

    def test_stale_editor_or_invalid_once_cannot_schedule(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='예약';WorkspaceWorkflow.openEditor('schedule');$('schedule-at').value='2000-01-01T09:00';let calls=0;api=async()=>{calls++;};
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});assert.equal(calls,0);assert.match($('request-editor-error').textContent,/앞으로/);
          active={id:'B',title:'B',state:'idle'};selectionGeneration++;$('schedule-at').value='2099-01-01T09:00';
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});assert.equal(calls,0);
        })()""", ("workflow",))

    def test_busy_ctrl_enter_queues_but_effort_keeps_its_control_semantics(self):
        self.run_case(r"""(async()=>{
          let calls=[];api=async(path,body)=>{calls.push({path,body});return {queue:[],schedules:[],revision:1};};
          $('prompt').value='/effort high';await submit();assert.equal(calls.length,0);assert.equal($('prompt').value,'/effort high');
          $('prompt').value='후속 질문';await submit();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/dispatch');assert.equal(calls[0].body.action,'enqueue');
        })()""", ("workflow",))

    def test_queued_user_event_is_added_only_once(self):
        self.run_case(r"""
          const event={type:'queued_user',data:{requestId:'q1',text:'후속 질문',files:['data.csv']}};
          handleEvent(event);handleEvent(event);assert.equal(active.messages.length,1);
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,1);
        """)

    def test_reorder_only_sends_queued_rows_and_uncertain_delivery_requires_review(self):
        self.run_case(r"""(async()=>{
          active.state='done';const state={revision:1,paused:true,queue:[
            {id:'uncertain',text:'확인 필요',attachments:[],state:'needs_review'},
            {id:'sending',text:'전송 중',attachments:[],state:'dispatching'},
            {id:'q1',text:'첫 대기',attachments:[],state:'queued'},
            {id:'q2',text:'둘째 대기',attachments:[],state:'queued'}],schedules:[],steer:{supported:false}};
          api=async()=>state;await WorkspaceWorkflow.refresh();
          assert.equal($('workflow-resume').disabled,true);assert.match($('workflow-paused').textContent,/전송 여부/);
          const uncertain=$('queue-list').children[0].querySelector('.workflow-row-actions');
          assert.equal(uncertain.children[2].disabled,true);assert.equal(uncertain.children[3].textContent,'대기에서 제거');
          let call;api=async(path,body)=>{call=body;return {...state,revision:2};};
          $('queue-list').children[2].querySelector('.workflow-row-actions').children[1].onclick();await settle();
          assert.equal(call.action,'reorder');assert.equal(JSON.stringify(call.order),'["q2","q1"]');
        })()""", ("workflow",))

    def test_unsupported_now_preserves_text_and_ime_never_enqueues(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;return {revision:1,queue:[],schedules:[],steer:{supported:false,reason:'체험 모드'}};};
          await WorkspaceWorkflow.refresh();calls=0;$('prompt').value='유지할 질문';
          await WorkspaceWorkflow.send('steer');assert.equal(calls,0);assert.equal($('prompt').value,'유지할 질문');
          $('prompt').oncompositionstart();$('prompt').onkeydown({key:'Enter',ctrlKey:true,preventDefault(){}});await settle();
          assert.equal(calls,0);assert.equal($('prompt').value,'유지할 질문');
        })()""", ("workflow","composer"))

    def test_outdated_dispatch_response_does_not_relabel_new_task(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const pending=WorkspaceWorkflow.refresh();
          WorkspaceWorkflow.contextChanged();active={id:'B',title:'업무 B',workspace:'D:/B',state:'done',trusted:true};selectionGeneration++;
          WorkspaceWorkflow.render();reply({queue:[{id:'A-only',text:'A 요청',state:'queued'}],schedules:[],revision:50});await pending;
          assert.match($('workflow-context').textContent,/업무 B/);assert.equal($('queue-list').children.length,0);
        })()""", ("workflow",))

    def test_dropping_copies_reports_failures_without_losing_draft_or_original(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='입력 유지';attachments=['C:/original.csv'];fetch=async()=>({ok:false,json:async()=>({error:'저장 한도'})});
          await WorkspaceAttachments.drop({preventDefault(){},dataTransfer:{types:['Files'],getData(){return '';},files:[{name:'copy.txt',size:2}]}});
          assert.equal($('prompt').value,'입력 유지');assert.equal(JSON.stringify(attachments),'["C:/original.csv"]');
          assert.equal(WorkspaceAttachments.isUploading(),false);assert.match($('toast').textContent,/저장 한도/);
        })()""", ("attachments",))

    def test_tray_hide_is_explicit_and_never_calls_full_quit(self):
        self.run_case(r"""(async()=>{
          boot={window:{hideSupported:true}};$('prompt').value='보존할 초안';openSettings();assert.equal($('hide-window').hidden,false);
          const calls=[];api=async(path,body)=>{calls.push(path);return {hidden:true};};await $('hide-window').onclick();
          assert.equal(JSON.stringify(calls),'["/api/window/hide"]');assert.equal(drafts.get('A').text,'보존할 초안');assert.equal(appClosed,false);
          boot.window.hideSupported=false;openSettings();assert.equal($('hide-window').hidden,true);await $('hide-window').onclick();assert.equal(calls.length,1);
        })()""")
