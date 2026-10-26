"""Concurrent HTTP operations must lock only their own task's composer."""
import unittest

from tests import test_workspace_frontend_state as frontend
from tests import test_workspace_inline_controls_frontend as controls


SETUP = r'''
const a={id:'A',kind:'work',title:'A',workspace:'C:/A',state:'idle',trusted:true,messages:[],requests:[],seq:0,connection:{connected:true}};
const b={id:'B',kind:'work',title:'B',workspace:'C:/B',state:'idle',trusted:true,messages:[],requests:[],seq:0,connection:{connected:true}};
sessions=[a,b];poll=()=>{};refreshInspector=()=>{};refreshSessionMeta=async()=>{};
const calls=[],replies=new Map();
api=(path,body)=>{calls.push({path,body});if(path.startsWith('/api/session?')){const row=sessions.find(row=>row.id===decodeURIComponent(path.split('id=')[1]));return Promise.resolve({...row,messages:row.messages.map(message=>({...message})),requests:[],seq:row.seq||0});}return new Promise(resolve=>replies.set(body.id,resolve));};
'''


@unittest.skipUnless(frontend.NODE, 'Node.js is required')
class MultiworkFrontendTests(unittest.TestCase):
    def run_case(self, code):
        frontend.WorkspaceFrontendStateTests.run_case(self, '(async()=>{' + SETUP + code + '})()')

    def run_controls(self, code):
        controls.WorkspaceInlineControlsFrontendTests.run_case(self, '(async()=>{' + SETUP + code + '})()')

    def run_guards(self, code):
        harness = frontend.HARNESS.replace('vm.createContext(context);', r'''
          context.WorkspaceStartupHealth={attach:config=>context.healthConfig=config,snapshot:()=>({missing:[]}),bootstrapReady(){},bootstrapFailed(){}};
          context.WorkspaceUpgrade={attach:config=>context.upgradeConfig=config,isLocked:()=>false,begin(){},bootstrap(){}};
          context.WorkspaceAppUpdates={attach:config=>context.updateConfig=config,contextChanged(){},start(){}};
          vm.createContext(context);
        ''')
        result = frontend.subprocess.run(
            [frontend.NODE, '-', str(frontend.ROOT / 'local_app/web/app.js'), '(async()=>{' + SETUP + code + '})()'],
            input=harness, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_two_tasks_can_send_before_either_http_response_returns(self):
        self.run_case(r'''
          await selectSession('A');$('prompt').value='A request';const first=submit();
          assert.equal($('prompt').readOnly,true);
          await selectSession('B');assert.equal($('send').disabled,false);assert.equal($('prompt').readOnly,false);
          $('prompt').value='B request';const second=submit();
          assert.equal(calls.filter(call=>call.path==='/api/send').length,2);
          assert.equal($('prompt').readOnly,true);
          replies.get('A')({ok:true});await first;
          assert.equal(active.id,'B');assert.equal($('prompt').value,'B request');assert.equal($('send').disabled,true);
          assert.equal($('prompt').readOnly,true);assert.equal(sending,true);
          replies.get('B')({ok:true});await second;
          assert.equal($('prompt').value,'');assert.equal(sending,false);assert.equal($('send').disabled,false);
        ''')

    def test_delayed_ack_preserves_a_new_draft_after_returning_to_task(self):
        self.run_case(r'''
          await selectSession('A');$('prompt').value='sent A';attachments=['C:/A/input.txt'];const first=submit();
          await selectSession('B');$('prompt').value='B kept';
          await selectSession('A');assert.equal($('prompt').readOnly,true);
          // Draft restore/recovery can bring newer content into the same task.
          $('prompt').value='new A draft';attachments=['C:/A/new.txt'];saveDraft();
          replies.get('A')({ok:true});await first;
          assert.equal($('prompt').value,'new A draft');assert.deepEqual(attachments,['C:/A/new.txt']);
          assert.equal(drafts.get('A').text,'new A draft');
          await selectSession('B');assert.equal($('prompt').value,'B kept');
        ''')

    def test_model_update_only_locks_its_owner_and_does_not_clear_other_update(self):
        self.run_case(r'''
          for(const row of sessions)row.connection={connected:true,model:'old',capabilities:{setModel:true}};
          await selectSession('A');const first=setModel('new-A');
          assert.equal(modelChanging,true);await selectSession('B');assert.equal(modelChanging,false);
          assert.equal($('send').disabled,false);const second=setModel('new-B');assert.equal(modelChanging,true);
          replies.get('A')({connection:{connected:true,model:'new-A'}});await first;
          assert.equal(active.id,'B');assert.equal(modelChanging,true);assert.equal($('send').disabled,true);
          replies.get('B')({connection:{connected:true,model:'new-B'}});await second;
          assert.equal(modelChanging,false);assert.equal($('send').disabled,false);
        ''')

    def test_design_choice_in_one_task_does_not_lock_another_task(self):
        self.run_case(r'''
          a.choice={id:'choice-A',schemaVersion:1,kind:'html-report-style',responseMode:'next-user-message',prompt:'Pick',options:[{id:'first',label:'First'}]};
          await selectSession('A');const first=submitWorkspaceChoice({optionId:'first'},choiceView.key);
          assert.ok(choiceSubmission);await selectSession('B');assert.equal(choiceSubmission,null);
          $('prompt').value='B request';const second=submit();
          replies.get('A')({session:{messages:[{role:'user',text:'First'}]}});await first;
          assert.equal(sending,true);assert.equal($('prompt').value,'B request');
          replies.get('B')({ok:true});await second;assert.equal(hasTaskOperations(),false);
        ''')

    def test_inline_connection_preparation_and_model_selection_are_task_scoped(self):
        self.run_controls(r'''
          a.connection={connected:false};b.connection={connected:false};
          await selectSession('A');const first=WorkspaceInlineControls.open('model');
          assert.equal(connectionPreparing,true);assert.ok(WorkspaceInlineControls.waitForPreparation());
          await selectSession('B');assert.equal(connectionPreparing,false);assert.equal($('send').disabled,false);
          const second=WorkspaceInlineControls.open('model');assert.equal(connectionPreparing,true);
          assert.equal(calls.filter(call=>call.path==='/api/connect').length,2);
          replies.get('A')({connection:connection()});await first;
          assert.equal(connectionPreparing,true);assert.ok(WorkspaceInlineControls.waitForPreparation());
          replies.get('B')({connection:connection()});await second;
          assert.equal(connectionPreparing,false);assert.equal(hasTaskOperations(),false);
          const third=choose('회사 모델').onclick();assert.equal(modelChanging,true);
          await selectSession('A');active.connection=connection();renderConnection(active.connection);
          assert.equal($('composer-model').disabled,false);await WorkspaceInlineControls.open('model');
          const fourth=choose('회사 모델').onclick();assert.equal(modelChanging,true);
          replies.get('B')({connection:connection()});await third;
          assert.equal(modelChanging,true);assert.equal($('composer-model').disabled,true);
          replies.get('A')({connection:connection()});await fourth;assert.equal(hasTaskOperations(),false);
        ''')

    def test_completion_preparations_can_overlap_without_losing_current_request(self):
        self.run_controls(r'''
          await selectSession('A');const first=WorkspaceComposer.prepareConnection();
          assert.ok(WorkspaceComposer.waitForPreparation());await selectSession('B');
          assert.equal(WorkspaceComposer.waitForPreparation(),null);const second=WorkspaceComposer.prepareConnection();
          assert.equal(calls.filter(call=>call.path==='/api/connect').length,2);
          replies.get('A')({connection:connection()});await first;
          assert.ok(WorkspaceComposer.waitForPreparation());assert.equal($('composer-connect').disabled,true);
          assert.equal(hasTaskOperations(),true);
          replies.get('B')({connection:connection()});await second;
          assert.equal(WorkspaceComposer.waitForPreparation(),null);assert.equal($('composer-connect').disabled,false);
          assert.equal(hasTaskOperations(),false);
        ''')

    def test_background_send_ack_leaves_current_completion_and_attachment_focus_untouched(self):
        self.run_controls(r'''
          await selectSession('A');$('prompt').value='A request';const first=submit();
          await selectSession('B');$('prompt').value='@inp';attachments=['C:/B/kept.txt'];renderAttachments();
          const chip=$('attachments').children[0];chip.focus();$('composer-suggestions').hidden=false;
          replies.get('A')({ok:true});await first;
          assert.equal(document.activeElement,chip);assert.equal($('attachments').children[0],chip);
          assert.equal($('composer-suggestions').hidden,false);assert.equal($('prompt').value,'@inp');
        ''')

    def test_update_capture_and_reload_wait_for_background_operations(self):
        self.run_guards(r'''
          await selectSession('A');$('prompt').value='A request';const first=submit();
          await selectSession('B');assert.equal(sending,false);assert.equal($('send').disabled,false);
          assert.equal(healthConfig.canReload(),false);assert.equal(upgradeConfig.canCapture(),false);
          assert.equal(upgradeConfig.canStopCapture(),false);assert.equal(updateConfig.canOfferUpdate(),false);
          replies.get('A')({ok:true});await first;
          assert.equal(healthConfig.canReload(),true);assert.equal(upgradeConfig.canCapture(),true);
          assert.equal(upgradeConfig.canStopCapture(),true);assert.equal(updateConfig.canOfferUpdate(),true);
        ''')

    def test_pending_send_bubble_survives_return_before_server_has_accepted_message(self):
        self.run_case(r'''
          await selectSession('A');$('prompt').value='A sent';const first=submit();
          const bubble=$('conversation').querySelector('.pending');
          await selectSession('B');await selectSession('A');
          assert.equal($('conversation').querySelector('.pending'),bubble);
          assert.equal($('conversation').querySelector('.conversation-empty'),null);
          replies.get('A')({ok:true});await first;
          assert.equal($('conversation').children.filter(node=>node.classList.contains('message')).length,1);
          assert.equal(bubble.classList.contains('pending'),false);
          assert.deepEqual(active.messages.map(message=>message.text),['A sent']);
        ''')

    def test_return_to_accepted_send_reuses_owned_bubble_without_duplicate_message(self):
        self.run_case(r'''
          a.lastRunId='old-run';a.messages=[{role:'user',text:'same request',runId:'old-run'}];
          await selectSession('A');$('prompt').value='same request';const first=submit();
          const bubble=$('conversation').querySelector('.pending');await selectSession('B');
          a.lastRunId='accepted-run';a.messages.push({role:'user',text:'same request',runId:'accepted-run'});
          await selectSession('A');
          assert.equal($('conversation').querySelector('.pending'),bubble);
          assert.equal(bubble.dataset.recoveredRunId,'accepted-run');
          replies.get('A')({ok:true});await first;
          assert.equal(active.messages.length,2);
          assert.equal($('conversation').children.filter(node=>node.classList.contains('message')).length,2);
        ''')

    def test_delayed_send_ack_matches_original_run_after_followup_and_history_trim(self):
        for followup_text in ['followup request', 'A sent']:
            with self.subTest(followup_text=followup_text):
                self.run_case(r'''
                  a.messages=Array.from({length:150},(_,index)=>({role:'user',text:'old '+index,runId:'old-'+index}));
                  a.lastRunId='old-149';await selectSession('A');$('prompt').value='A sent';const first=submit();
                  const bubble=$('conversation').querySelector('.pending');await selectSession('B');
                  a.messages=[...a.messages,{role:'user',text:'A sent',runId:'accepted-run'},
                    {role:'assistant',text:'finished',runId:'accepted-run'},
                    {role:'user',text:FOLLOWUP_TEXT,runId:'followup-run'}].slice(-150);
                  a.lastRunId='followup-run';await selectSession('A');
                  assert.equal(bubble.dataset.recoveredRunId,'accepted-run');
                  replies.get('A')({ok:true});await first;
                  assert.equal(active.messages.length,150);
                  assert.equal(active.messages.filter(message=>message.role==='user'&&message.text==='A sent').length,FOLLOWUP_TEXT==='A sent'?2:1);
                  assert.equal($('conversation').children.filter(node=>node.classList.contains('message')).length,150);
                '''.replace('FOLLOWUP_TEXT', repr(followup_text)))


if __name__ == '__main__':
    unittest.main()
