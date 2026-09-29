"""Model, permission, structured choice and verification UI contract regressions.

Only synthetic responses and the shipped browser script are used.
"""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
const flatten = node => [node.textContent || '', ...(node.children || []).map(flatten)].join(' ');
const descendants = (node, tag) => [...(node.children || []).filter(child => child.tagName === tag),
  ...(node.children || []).flatMap(child => descendants(child, tag))];
const choice = (id='choice-a') => ({schemaVersion:1,kind:'html-report-style',id,
  question:'보고서 디자인을 선택하세요',options:[{id:'minimalism',label:'미니멀리즘',description:'간결한 보고서'},
    {id:'neumorphism',label:'뉴모피즘',description:'부드러운 그림자'}],allowCustom:true,responseMode:'next-user-message'});
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',trusted:true,state:'done',messages:[],choice:null,
  modelOverride:null,connection:{model:'original',modelOverride:null,availableModels:[{value:'provided',displayName:'제공 모델'}],
    permissionMode:'default',permissionModeOverride:null,permissionModeSupport:'unverified',
    availablePermissionModes:[{value:'default',displayName:'직접 승인',description:'실행 전에 확인'},
      {value:'auto',displayName:'자동 판단',description:'CLI가 실행 위험을 판단'}],
    capabilities:{setModel:true,setPermissionMode:true}}};
sessions=[{...active}];
refreshFiles=async()=>{};refreshResults=async()=>{};refreshSessionMeta=async()=>{};
"""


@unittest.skipUnless(NODE, "Node.js is required for UI contract checks")
class WorkspaceControlsFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + script],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_provided_models_and_manual_company_model_share_existing_api(self):
        self.run_case(r"""(async()=>{
          openSettings();assert.equal($('model-select').children.length,2);
          $('model-select').value='provided';$('model-select').onchange();assert.equal($('model-input').value,'provided');
          $('model-input').value='company-custom';$('model-input').oninput();assert.equal($('model-select').value,'');
          let body;api=async(path,data)=>{assert.equal(path,'/api/model');body=data;
            return {modelOverride:data.model,session:{connection:{model:data.model}}};};
          const result=await setModel('company-custom');assert.equal(body.model,'company-custom');
          assert.equal(result.ok,true);assert.equal(result.response.modelOverride,'company-custom');
          assert.equal(active.connection.model,'company-custom');assert.equal(active.modelOverride,'company-custom');
          assert.equal(active.state,'done');
        })()""")

    def test_unsupported_permission_modes_are_not_offered_and_changes_require_ack(self):
        self.run_case(r"""(async()=>{
          active.connection.availablePermissionModes.push({value:'bypassPermissions',displayName:'모든 승인 생략'});
          openSettings();assert.equal($('permission-mode-select').children.length,3);
          assert.doesNotMatch(flatten($('permission-mode-select')),/모든 승인 생략/);
          assert.match($('permission-mode-message').textContent,/CLI 적용 응답 확인/);
          let reply,body;api=(path,data)=>{assert.equal(path,'/api/permission-mode');body=data;return new Promise(resolve=>reply=resolve);};
          const pending=setPermissionMode('auto');assert.equal(body.mode,'auto');
          assert.equal(active.connection.permissionMode,'default');assert.equal($('send').disabled,true);
          assert.equal($('permission-mode-apply').disabled,true);
          active.state='running';active.messages=[{role:'assistant',text:'새 응답'}];
          reply({permissionMode:'auto',permissionModeOverride:'auto',permissionModeSupport:'confirmed'});const result=await pending;
          assert.equal(result.ok,true);assert.equal(result.response.permissionMode,'auto');
          assert.equal(active.connection.permissionMode,'auto');assert.equal(active.state,'running');
          assert.equal(active.messages[0].text,'새 응답');assert.equal($('permission-mode-apply').disabled,true);
        })()""")

    def test_permission_rejection_and_late_other_session_ack_never_claim_applied(self):
        self.run_case(r"""(async()=>{
          api=async()=>{throw new Error('모드 변경 거절');};const failure=await setPermissionMode('auto');
          assert.equal(failure.ok,false);assert.equal(failure.error,'모드 변경 거절');
          assert.equal(active.connection.permissionMode,'default');assert.equal(active.connection.permissionModeOverride,null);
          assert.match($('permission-mode-message').textContent,/변경 거절/);
          let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=setPermissionMode('auto');active={id:'B',state:'idle',connection:{permissionMode:'plan',capabilities:{}}};
          reply({permissionMode:'auto',permissionModeOverride:'auto'});assert.equal(await pending,null);
          assert.equal(active.connection.permissionMode,'plan');assert.equal(active.id,'B');
        })()""")

    def test_model_and_permission_ack_ignore_leave_and_return_to_same_session(self):
        self.run_case(r"""(async()=>{
          for(const kind of ['model','permission']){
            active.state='done';let reply;api=()=>new Promise(resolve=>reply=resolve);
            const before={...active,connection:{...active.connection}},pending=kind==='model'?setModel('late-model'):setPermissionMode('auto');
            selectionGeneration++;active={id:'B',state:'done'};
            selectionGeneration++;active=before;active.connection.model='current-model';active.connection.permissionMode='plan';
            $('toast').textContent='current notice';
            reply({model:'late-model',modelOverride:'late-model',permissionMode:'auto',permissionModeOverride:'auto'});
            assert.equal(await pending,null);assert.equal(active.connection.model,'current-model');
            assert.equal(active.connection.permissionMode,'plan');assert.equal($('toast').textContent,'current notice');
            assert.equal(modelChanging,false);assert.equal(permissionChanging,false);
          }
        })()""")

    def test_control_failure_and_blocked_attempts_have_explicit_result_without_mutation(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;throw new Error('모델 변경 거절');};
          const failure=await setModel('rejected');assert.equal(failure.ok,false);assert.equal(failure.error,'모델 변경 거절');
          assert.equal(active.connection.model,'original');assert.match($('model-message').textContent,/모델 변경 거절/);
          active.state='running';assert.equal(await setModel('blocked'),null);assert.equal(await setPermissionMode('auto'),null);
          assert.equal(calls,1);active.state='done';
          const invalid=await setPermissionMode('bypassPermissions');assert.equal(invalid.ok,false);assert.match(invalid.error,/제공하는 승인 모드/);
          assert.equal(calls,1);assert.equal(active.connection.permissionMode,'default');
        })()""")

    def test_stale_control_failure_does_not_replace_current_notice(self):
        self.run_case(r"""(async()=>{
          for(const kind of ['model','permission']){
            let reject;api=()=>new Promise((resolve,fail)=>reject=fail);
            const pending=kind==='model'?setModel('late'):setPermissionMode('auto');
            selectionGeneration+=2;$('toast').textContent='현재 업무 알림';
            reject(new Error('이전 요청 오류'));assert.equal(await pending,null);
            assert.doesNotMatch($('model-message').textContent,/이전 요청 오류/);
            assert.doesNotMatch($('permission-mode-message').textContent,/이전 요청 오류/);
            assert.equal($('toast').textContent,'현재 업무 알림');
          }
        })()""")

    def test_inline_controls_refresh_on_status_and_connection_without_opening_settings(self):
        self.run_case(r"""(()=>{
          let calls=0;globalThis.WorkspaceInlineControls={render(){calls++;}};
          setStatus('running');assert.equal(calls,1);renderConnection(active.connection);assert.equal(calls,2);
          assert.notEqual($('settings-dialog').open,true);
        })()""")

    def test_effort_change_and_connection_preparation_lock_shared_actions(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;return {ok:true};};
          for(const pending of ['effort','connection']){
            effortChanging=pending==='effort';connectionPreparing=pending==='connection';setStatus('done');
            assert.equal(connectionLocked(),true);assert.equal($('send').disabled,true);
            assert.equal($('model-apply').disabled,true);assert.equal($('permission-mode-apply').disabled,true);
            assert.equal(await setModel('provided'),null);assert.equal(await setPermissionMode('auto'),null);
            await $('native').onclick();assert.equal(calls,0);
          }
          effortChanging=false;connectionPreparing=false;setStatus('done');assert.equal(connectionLocked(),false);
          assert.equal($('send').disabled,false);
        })()""")

    def test_effort_fields_apply_without_overwriting_current_task_or_draft(self):
        self.run_case(r"""(()=>{
          $('prompt').value='보존할 초안';attachments=['draft.csv'];
          applyConnectionState({session:{state:'idle',messages:[],connection:{effort:'high',effortOverride:'high',
            availableEfforts:[{value:'high',displayName:'높음'}],effortSupport:'confirmed',effortChangeRequiresReconnect:false}}});
          assert.equal(active.connection.effort,'high');assert.equal(active.connection.effortOverride,'high');
          assert.equal(active.connection.availableEfforts[0].value,'high');assert.equal(active.connection.effortSupport,'confirmed');
          assert.equal(active.connection.effortChangeRequiresReconnect,false);assert.equal(active.state,'done');
          assert.equal($('prompt').value,'보존할 초안');assert.equal(attachments[0],'draft.csv');
        })()""")

    def test_inline_controls_trust_resume_preserves_draft_and_never_sends_it(self):
        self.run_case(r"""(async()=>{
          active.trusted=false;$('prompt').value='보존할 초안';attachments=['report.csv'];
          chooseFolder(true);$('folder-form').dataset.afterTrust='controls';$('trust').checked=true;
          const calls=[];let resumed=0;api=async(path,body)=>{calls.push(path);return {ok:true};};
          globalThis.WorkspaceInlineControls={render(){},resumeAfterTrust:async()=>{resumed++;}};
          await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          assert.equal(calls.join(','),'/api/trust');assert.equal(resumed,1);assert.equal(active.trusted,true);
          assert.equal($('prompt').value,'보존할 초안');assert.equal(attachments[0],'report.csv');
        })()""")

    def test_new_connection_replaces_old_overrides_with_inherited_state(self):
        self.run_case(r"""(()=>{
          active.modelOverride='previous-model';active.connection.permissionModeOverride='auto';
          handleEvent({type:'connected',data:{model:'inherited-model',modelOverride:null,sessionId:'new-connection',
            permissionMode:'manual',permissionModeOverride:null,availablePermissionModes:[{value:'manual',displayName:'직접 승인'}],
            capabilities:{setModel:true,setPermissionMode:true}}});
          assert.equal(active.modelOverride,null);assert.equal(active.connection.permissionModeOverride,null);
          assert.equal($('model-reset').disabled,true);assert.equal($('permission-mode-reset').disabled,true);
          assert.match($('permission-mode-message').textContent,/기존 설정 상속/);
        })()""")

    def test_manual_mode_from_actual_help_uses_original_name_and_null_restore(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionMode='manual';active.connection.availablePermissionModes=[
            {value:'manual',displayName:'직접 승인'},{value:'auto',displayName:'자동 판단'}];
          openSettings();assert.equal($('permission-mode-select').children[1].value,'manual');
          const calls=[];api=async(path,body)=>{calls.push(body);return {permissionMode:body.mode||'manual',permissionModeOverride:body.mode};};
          await setPermissionMode('manual');assert.equal(calls[0].mode,'manual');
          await setPermissionMode(null);assert.equal(calls[1].mode,null);assert.equal(active.connection.permissionMode,'manual');
        })()""")

    def test_restore_is_available_after_control_rejection_and_sends_null_only(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionMode='auto';active.connection.permissionModeOverride='auto';
          active.connection.permissionModeResetRequiresReconnect=true;active.connection.capabilities.setPermissionMode=false;
          openSettings();assert.equal($('permission-mode-apply').disabled,true);assert.equal($('permission-mode-reset').disabled,false);
          assert.match($('permission-mode-reset-note').textContent,/다음 요청/);
          let body;api=async(path,data)=>{body=data;return {reconnectRequired:true,session:{connection:{permissionMode:null,
            permissionModeOverride:null,connected:false,capabilities:{setPermissionMode:false}}}};};
          await setPermissionMode(null);assert.equal(body.mode,null);assert.equal(Object.keys(body).length,2);
          assert.equal(active.connection.permissionModeOverride,null);assert.equal($('permission-mode-reset').disabled,true);
          assert.match($('toast').textContent,/다음 요청에서 기존 승인 설정/);
        })()""")

    def test_permission_scope_is_explicit_and_transmits_only_server_choice_id(self):
        self.run_case(r"""(async()=>{
          const request={id:'req-a',tool:'Read',input:{file_path:'C:/fixture/report.txt'},permissionChoices:[
            {id:'scope-a',label:'같은 범위 허용',destination:'session',rules:[{toolName:'Read'}]}]};
          renderRequest(request);const card=$('requests').children[0];assert.match(flatten(card),/Read · 이 도구 전체/);
          const inputs=descendants(card,'INPUT');assert.equal(inputs[0].checked,true);assert.equal(inputs[1].checked,false);
          for(const input of inputs)input.checked=input.value==='scope-a';
          let reply,calls=[];api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const approve=card.querySelector('.request-actions').children[1],pending=approve.onclick();approve.onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].body.permissionChoiceId,'scope-a');
          assert.equal('rules' in calls[0].body,false);assert.equal('destination' in calls[0].body,false);
          reply({ok:true});await pending;assert.equal(approve.disabled,true);
        })()""")

    def test_denial_and_question_never_send_permission_choice_and_closed_card_is_inert(self):
        self.run_case(r"""(async()=>{
          let calls=[];api=async(path,body)=>{calls.push(body);return {ok:true};};
          const permissionChoices=[{id:'scope-a',label:'범위 허용',destination:'session',rules:[{toolName:'Read'}]}];
          renderRequest({id:'req-a',tool:'Read',input:{},permissionChoices});const card=$('requests').children[0];
          for(const input of descendants(card,'INPUT'))input.checked=input.value==='scope-a';
          await card.querySelector('.request-actions').children[0].onclick();
          assert.equal(calls[0].allow,false);assert.equal('permissionChoiceId' in calls[0],false);
          handleEvent({type:'request_closed',data:{id:'req-a',state:'running'}});
          await card.querySelector('.request-actions').children[1].onclick();assert.equal(calls.length,1);
          renderRequest({id:'question-a',tool:'AskUserQuestion',permissionChoices,input:{questions:[{question:'어떤 자료?',options:[{label:'첫 자료'}]}]}});
          const question=$('requests').children[0];assert.equal(question.querySelector('.permission-scope-options'),null);
          descendants(question,'INPUT').find(input=>input.type==='radio').checked=true;
          await question.querySelector('.request-actions').children[1].onclick();
          assert.equal(calls[1].answers['어떤 자료?'],'첫 자료');assert.equal('permissionChoiceId' in calls[1],false);
        })()""")

    def test_cli_persistent_choices_show_full_scope_and_only_explicit_approval_sends_id(self):
        self.run_case(r"""(async()=>{
          const permissionChoices=[
            {id:'local',destination:'localSettings',label:'이 프로젝트에서 항상 허용 · 나만',
             description:'현재 프로젝트의 개인 설정에 저장합니다. 다음 연결에도 적용돼요.'},
            {id:'project',destination:'projectSettings',label:'이 프로젝트에서 항상 허용 · 공유 설정',
             description:'공유 설정에 저장합니다. 파일을 공유하면 다른 사용자에게도 적용될 수 있어요.'},
            {id:'user',destination:'userSettings',label:'모든 프로젝트에서 항상 허용 · 내 설정',
             description:'현재 Claude 사용자 설정에 저장합니다. 다른 프로젝트와 다음 연결에도 적용돼요.'}
          ].map(choice=>({...choice,rules:[{toolName:'Bash',ruleContent:'git status'},
                                          {toolName:'Bash',ruleContent:null}]}));
          let calls=[];api=async(path,body)=>{calls.push({path,body});return {ok:true};};
          renderRequest({id:'persistent',tool:'Bash',input:{command:'git status'},permissionChoices});
          const card=$('requests').children[0],inputs=descendants(card,'INPUT'),text=flatten(card);
          assert.equal(inputs.length,4);assert.equal(inputs[0].checked,true);
          assert.equal(inputs.slice(1).some(input=>input.checked),false);
          assert.match(text,/이번만 허용/);assert.match(text,/개인 설정/);assert.match(text,/공유 설정/);
          assert.match(text,/다른 사용자/);assert.match(text,/모든 프로젝트/);assert.match(text,/다음 연결/);
          assert.match(text,/Bash · git status/);assert.match(text,/Bash · 이 도구 전체/);
          for(const input of inputs)input.checked=input.value==='user';
          assert.equal(calls.length,0);
          await card.querySelector('.request-actions').children[1].onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].body.permissionChoiceId,'user');
          assert.equal(Object.keys(calls[0].body).sort().join(','),'allow,answers,id,permissionChoiceId,requestId');
          handleEvent({type:'request_closed',data:{id:'persistent',state:'running'}});
          renderRequest({id:'once',tool:'Bash',input:{},permissionChoices});
          await $('requests').children[0].querySelector('.request-actions').children[1].onclick();
          assert.equal(calls.length,2);assert.equal('permissionChoiceId' in calls[1].body,false);
        })()""")

    def test_unexplained_or_unknown_scope_is_not_offered_and_old_session_card_cannot_approve(self):
        self.run_case(r"""(async()=>{
          const rules=[{toolName:'Read'}],permissionChoices=[
            {id:'unknown',destination:'managedSettings',label:'unknown',description:'unknown',rules},
            {id:'missing-description',destination:'userSettings',label:'global',rules},
            {id:'user',destination:'userSettings',label:'내 모든 프로젝트에서 항상 허용',description:'사용자 설정에 저장',rules}];
          let calls=0;api=async()=>{calls++;return {ok:true};};
          renderRequest({id:'old',tool:'Read',input:{},permissionChoices});
          const card=$('requests').children[0],inputs=descendants(card,'INPUT');
          assert.equal(inputs.length,2);assert.equal(inputs[1].value,'user');
          for(const input of inputs)input.checked=input.value==='user';
          active={id:'B',state:'approval'};
          await card.querySelector('.request-actions').children[1].onclick();assert.equal(calls,0);
        })()""")

    def test_repeated_choice_event_preserves_custom_text_until_result_and_click_is_once(self):
        self.run_case(r"""(async()=>{
          active.state='running';handleEvent({type:'choice',data:choice()});const first=choiceView.card;
          assert.equal(choiceView.buttons[0].disabled,true);choiceView.input.value='다른 스타일';
          handleEvent({type:'choice',data:choice()});assert.equal(choiceView.card,first);assert.equal(choiceView.input.value,'다른 스타일');
          let calls=[];api=async(path,body)=>{calls.push({path,body});return {session:{messages:[{role:'user',text:'서버가 받은 답변'}]}};};
          await choiceView.buttons[0].onclick();assert.equal(calls.length,0);
          handleEvent({type:'result',data:{verification:{state:'unverified',message:'검증 미확인'}}});
          assert.equal(choiceView.buttons[0].disabled,false);const button=choiceView.buttons[0];await button.onclick();await button.onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/choice');assert.equal(calls[0].body.optionId,'minimalism');
          assert.equal(active.choice,null);assert.equal($('workspace-choice').hidden,true);
          handleEvent({type:'choice',data:choice()});assert.equal($('workspace-choice').hidden,true);
        })()""")

    def test_choice_ack_preserves_early_stream_draft_and_confirmed_message_order(self):
        self.run_case(r"""(async()=>{
          active.choice=choice();renderWorkspaceChoice();$('prompt').value='나중에 보낼 초안';attachments=['draft.csv'];
          let reply,body;api=(path,data)=>{body=data;return new Promise(resolve=>reply=resolve);};
          choiceView.input.value='회사 색상으로';const pending=choiceView.buttons.at(-1).onclick();
          assert.equal(body.text,'회사 색상으로');assert.equal('optionId' in body,false);assert.equal($('prompt').readOnly,true);
          const anchor=$('conversation').children[0];assert.equal(anchor.hidden,true);
          renderDelta({messageId:'answer',index:0,text:'먼저 도착한 응답'});const bubble=streaming.get('answer:0');
          reply({session:{messages:[{role:'user',text:'회사 색상으로'}]}});await pending;
          assert.equal($('conversation').children[0],anchor);assert.equal(anchor.hidden,false);
          assert.match(flatten(anchor),/회사 색상으로/);assert.equal(streaming.get('answer:0'),bubble);
          assert.equal(bubble.querySelector('.message-body').textContent,'먼저 도착한 응답');
          assert.equal($('prompt').value,'나중에 보낼 초안');assert.equal(attachments[0],'draft.csv');
        })()""")

    def test_choice_failure_no_automatic_retry_and_untrusted_context_requires_recheck(self):
        self.run_case(r"""(async()=>{
          active.choice=choice();active.trusted=false;renderWorkspaceChoice();let calls=[];
          api=async(path,body)=>{calls.push({path,body});return {ok:true};};$('prompt').value='보존할 초안';
          await choiceView.buttons[0].onclick();assert.equal(calls.length,0);assert.equal($('folder-form').dataset.afterTrust,'choice');
          $('trust').checked=true;await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/trust');assert.equal($('prompt').value,'보존할 초안');
          api=async(path,body)=>{calls.push({path,body});throw new Error('전송 확인 실패');};
          await choiceView.buttons[0].onclick();assert.equal(calls.length,2);assert.equal(choiceView.buttons[0].disabled,false);
          assert.equal(active.choice.id,'choice-a');assert.match($('error-banner').textContent,/전송 확인 실패/);
        })()""")

    def test_stopped_choice_and_old_session_ack_cannot_mutate_new_session(self):
        self.run_case(r"""(async()=>{
          active.choice=choice();renderWorkspaceChoice();const oldButton=choiceView.buttons[0];
          handleEvent({type:'status',data:{state:'stopped'}});assert.equal(active.choice,null);assert.equal(choiceView,null);
          let calls=0;api=async()=>{calls++;};await oldButton.onclick();assert.equal(calls,0);
          active.state='done';active.choice=choice('choice-b');renderWorkspaceChoice();let reply;api=()=>new Promise(resolve=>reply=resolve);
          await oldButton.onclick();assert.equal(reply,undefined);
          const pending=choiceView.buttons[0].onclick();selectionGeneration++;active={id:'B',state:'done',messages:[],choice:choice('choice-c'),trusted:true};
          $('conversation').replaceChildren(el('article','다른 업무'));$('prompt').value='B 업무 초안';setStatus(active.state);
          assert.equal($('send').disabled,true);assert.equal(choiceView.buttons[0].disabled,true);
          reply({session:{messages:[{role:'user',text:'이전 업무 선택'}]}});await pending;
          assert.equal(active.choice.id,'choice-c');assert.doesNotMatch(flatten($('conversation')),/이전 업무 선택/);
          assert.equal($('send').disabled,false);assert.equal(choiceView.buttons[0].disabled,false);
          assert.equal($('prompt').value,'B 업무 초안');
        })()""")

    def test_verification_never_becomes_success_from_result_only(self):
        self.run_case(r"""(()=>{
          handleEvent({type:'verification',data:{state:'checking',message:'후크가 결과를 확인 중입니다.'}});
          assert.equal($('verification-status').dataset.state,'checking');
          handleEvent({type:'result',data:{}});assert.equal(active.state,'done');
          assert.equal($('verification-status').dataset.state,'unverified');assert.doesNotMatch(flatten($('verification-status')),/검증 성공/);
          handleEvent({type:'verification',data:{state:'needs-review',message:'확인 단계 오류'}});
          handleEvent({type:'result',data:{}});assert.match(flatten($('verification-status')),/확인 단계 오류/);
          handleEvent({type:'status',data:{state:'starting'}});assert.equal($('verification-status').hidden,true);
        })()""")


if __name__ == '__main__':
    unittest.main()
