"""Run the shipped UI recovery against delayed reads and unchanged local drafts."""
import unittest
from tests import test_workspace_frontend_state as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for event recovery UI checks')
class EventRecoveryFrontendTests(unittest.TestCase):
    run_case = frontend.WorkspaceFrontendStateTests.run_case

    def test_recovers_missing_completed_answer_and_stream_prefix_without_touching_draft(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'running',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='draft\nkeep';attachments=['keep.csv'];saveDraft();$('prompt').focus();
          refreshInspector=async()=>{};let workflow=0,posts=0;globalThis.WorkspaceWorkflow={refresh(){workflow++},render(){},isSubmitting:()=>false};api=async()=>{posts++;throw Error('unexpected request')};
          const owner=active,stored=drafts.get('A');
          restoreEventSnapshot({...active,seq:321,messages:[{role:'assistant',text:'completed earlier',runId:'one'}],streams:[{messageId:'second',index:0,text:'entire prefix ',runId:'two',afterMessage:0}]},'A');
          assert.equal(active,owner);assert.equal(active.messages[0].text,'completed earlier');
          assert.equal(streaming.get('second:0').dataset.streamText,'entire prefix ');
          handleEvent({type:'assistant_delta',data:{messageId:'second',index:0,text:'suffix',runId:'two'}});
          assert.equal(streaming.get('second:0').dataset.streamText,'entire prefix suffix');
          handleEvent({type:'assistant',data:{messageId:'second',index:0,text:'entire prefix suffix',runId:'two'}});
          assert.equal(streaming.size,0);assert.equal($('conversation').children.filter(node=>node.classList.contains('message')).length,2);
          assert.equal($('prompt').value,'draft\nkeep');assert.deepEqual(attachments,['keep.csv']);assert.equal(drafts.get('A'),stored);
          assert.equal(document.activeElement,$('prompt'));assert.equal(posts,0);assert.equal(workflow,1);
        })()''')

    def test_same_approval_form_preserves_typed_answer_focus_and_pending_submission(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'question',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};
          const request={id:'question',tool:'AskUserQuestion',input:{questions:[{question:'Which?',options:[]}]}};
          const card=renderRequest(request),input=card.querySelector('input');input.value='typed answer';input.focus();
          let acknowledge,calls=[];api=(path,data)=>{calls.push({path,data});return new Promise(resolve=>acknowledge=resolve)};
          const buttons=card.querySelector('.request-actions').children,operation=buttons[1].onclick();
          restoreEventSnapshot({...active,seq:401,requests:[request],messages:[{role:'assistant',text:'restored'}],streams:[]},'A');
          assert.equal($('requests').children[0],card);assert.equal(card.querySelector('input'),input);assert.equal(input.value,'typed answer');
          assert.equal(document.activeElement,input);assert.equal(buttons[1].disabled,true);assert.equal(calls.length,1);
          assert.equal(calls[0].path,'/api/respond');assert.equal(calls[0].data.answers['Which?'],'typed answer');
          acknowledge({ok:true});await operation;assert.equal(calls.length,1);
          restoreEventSnapshot({...active,seq:402,state:'running',requests:[],streams:[]},'A');assert.equal($('requests').children.length,0);
        })()''')

    def test_choice_answer_and_queue_editor_survive_recovery(self):
        self.run_case(r'''(async()=>{
          const choice={id:'choice',schemaVersion:1,kind:'html-report-style',responseMode:'next-user-message',question:'Style?',options:[{id:'one',label:'One'}],allowCustom:true};
          active={id:'A',title:'A',state:'done',messages:[],requests:[],choice,connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};
          renderWorkspaceChoice();const input=choiceView.input;input.value='custom style';input.focus();
          let touched=0;globalThis.WorkspaceWorkflow={refresh(){},render(){},isSubmitting:()=>false,contextChanged(){touched++}};
          $('workflow-edit-text').value='unsaved queue edit';
          restoreEventSnapshot({...active,seq:499,messages:[{role:'assistant',text:'restored'}],streams:[]},'A');
          assert.equal(choiceView.input,input);assert.equal(input.value,'custom style');assert.equal(document.activeElement,input);
          assert.equal($('workflow-edit-text').value,'unsaved queue edit');assert.equal(touched,0);
        })()''')

    def test_poll_recovers_atomically_then_reads_from_snapshot_cursor_only(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'running',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};
          let calls=[],controller=new AbortController();api=async(path,data)=>{assert.equal(data,undefined);calls.push(path);
            if(calls.length===1)return {seq:321,events:[],recovery:{session:{...active,seq:321,messages:[{role:'assistant',text:'missing'}],streams:[]}}};
            controller.abort();return {events:[]};};
          await poll('A',0,controller.signal);assert.equal(calls.length,2);assert.match(calls[1],/after=321$/);assert.equal(active.seq,321);
          assert.equal(active.messages[0].text,'missing');
        })()''')

    def test_stale_same_id_generation_cannot_replace_new_selection(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'old',state:'running',messages:[],requests:[]};sessions=[{...active}];refreshInspector=async()=>{};
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve)};
          const polling=poll('A',0,new AbortController().signal);
          active={id:'B',messages:[]};selectionGeneration++;active={id:'A',title:'new selection',messages:[{role:'user',text:'keep new'}]};selectionGeneration++;
          reply({seq:500,events:[],recovery:{session:{id:'A',seq:500,messages:[{role:'assistant',text:'stale'}],requests:[]}}});await polling;
          assert.equal(active.title,'new selection');assert.equal(active.messages[0].text,'keep new');assert.equal(calls,1);
        })()''')

    def test_inflight_send_keeps_original_bubble_and_has_one_user_message_after_ack(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'idle',trusted:true,messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='new request';attachments=['file.csv'];refreshInspector=refreshSessionMeta=async()=>{};
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve)};
          const posting=submit(),bubble=$('conversation').children.find(node=>node.classList.contains('pending'));
          restoreEventSnapshot({...active,seq:400,state:'running',lastRunId:'new-run',messages:[{role:'user',text:'new request',files:['file.csv'],runId:'new-run'}],streams:[{messageId:'answer',index:0,text:'started',afterMessage:0}]},'A');
          assert.equal($('conversation').children[0],bubble);assert.equal($('prompt').value,'new request');
          reply({ok:true});await posting;
          assert.equal(active.messages.filter(message=>message.role==='user').length,1);
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,1);assert.equal(calls,1);
        })()''')

    def test_identical_previous_user_is_not_mistaken_for_unadmitted_send(self):
        self.run_case(r'''(async()=>{
          const prior={role:'user',text:'same request',files:['file.csv'],runId:'previous'};
          active={id:'A',title:'A',state:'done',trusted:true,lastRunId:'previous',messages:[prior],requests:[],connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='same request';attachments=['file.csv'];refreshInspector=refreshSessionMeta=async()=>{};
          let reply;api=()=>new Promise(resolve=>reply=resolve);const posting=submit();
          restoreEventSnapshot({...active,seq:400,messages:[{...prior}],streams:[]},'A');
          const pending=$('conversation').children.find(node=>node.classList.contains('pending'));assert.notEqual(pending.dataset.recoveredConfirmed,'true');
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,2);
          reply({ok:true});await posting;assert.equal(active.messages.filter(message=>message.role==='user').length,2);
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,2);
        })()''')

    def test_second_gap_keeps_confirmed_pending_bubble_at_its_own_run(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'done',trusted:true,lastRunId:'previous',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];
          $('prompt').value='current request';attachments=[];refreshInspector=refreshSessionMeta=async()=>{};
          let reply;api=()=>new Promise(resolve=>reply=resolve);const posting=submit();
          const message={role:'user',text:'current request',files:[],runId:'current'};
          restoreEventSnapshot({...active,seq:400,state:'running',lastRunId:'current',messages:[message],streams:[]},'A');
          restoreEventSnapshot({...active,seq:800,lastRunId:'later',messages:[message,{role:'assistant',text:'done',runId:'current'},{role:'user',text:'queued next',runId:'later'}],streams:[]},'A');
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,2);
          reply({ok:true});await posting;assert.equal(active.messages.filter(message=>message.role==='user').length,2);
        })()''')

    def test_accepted_choice_recovery_does_not_duplicate_message_after_post_ack(self):
        self.run_case(r'''(async()=>{
          const choice={id:'choice',schemaVersion:1,kind:'html-report-style',responseMode:'next-user-message',question:'Style?',options:[{id:'one',label:'One'}],allowCustom:true};
          active={id:'A',title:'A',state:'done',trusted:true,lastRunId:'previous',messages:[],requests:[],choice,connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};renderWorkspaceChoice();
          let reply,calls=0;api=()=>{calls++;return new Promise(resolve=>reply=resolve)};
          const posting=submitWorkspaceChoice({text:'custom choice'},choiceView.key);
          const next={...active,seq:500,state:'running',lastRunId:'new-choice-run',choice:null,messages:[{role:'user',text:'custom choice',runId:'new-choice-run'}],streams:[]};
          restoreEventSnapshot(next,'A');reply({ok:true,session:next});await posting;
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,1);assert.equal(calls,1);
        })()''')

    def test_terminal_recovery_restores_error_verification_and_interrupted_fragment(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'running',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};
          restoreEventSnapshot({...active,seq:400,state:'error',displayError:'provider failed',verification:{state:'needs-review',message:'check result'},streams:[{messageId:'part',index:0,text:'received before failure',afterMessage:-1,interrupted:true}]},'A');
          assert.equal(active.state,'error');assert.equal($('error-message').textContent,'provider failed');assert.equal($('verification-status').hidden,false);
          assert.equal(streaming.size,0);assert.equal($('conversation').children[0].classList.contains('streaming'),false);
          assert.equal($('conversation').children[0].querySelector('.message-body').textContent,'received before failure');
          restoreEventSnapshot({...active,seq:401,state:'running',displayError:'',streams:[]},'A');assert.equal($('error-banner').hidden,true);
        })()''')

    def test_reading_position_is_preserved_without_forcing_latest_response(self):
        self.run_case(r'''(async()=>{
          active={id:'A',title:'A',state:'running',messages:[],requests:[],connection:{connected:true}};sessions=[{...active}];refreshInspector=async()=>{};
          $('work-area').scrollTop=225;$('work-area').scrollHeight=1000;$('work-area').clientHeight=300;
          let resets=0,pauses=0,jumps=0;globalThis.WorkspaceStream={isFollowing:()=>false,reset(){resets++},pause(){pauses++},changed(){},finish(){},jump(){jumps++}};
          restoreEventSnapshot({...active,seq:450,messages:[{role:'assistant',text:'restored'}],streams:[]},'A');
          assert.equal($('work-area').scrollTop,225);assert.equal(resets,1);assert.equal(pauses,1);assert.equal(jumps,0);
        })()''')


if __name__ == '__main__':
    unittest.main()
