"""Exercise the shipped composer controls with delayed synthetic CLI responses.

No browser, installed Claude configuration, or actual AI requests are used.
"""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
for(const id of ['composer-model','composer-effort','composer-permission'])get(id).append(new Element('strong'));
get('composer-controls-panel').hidden=true;
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context,{filename:'composer.js'});
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'inline-controls.js'});
const scenario=process.argv[3];
""")
SETUP = r"""
const connection = () => ({connected:true,model:'original',modelOverride:null,
  availableModels:[{value:'original',displayName:'기존 모델'},{value:'company-next',displayName:'회사 모델',description:'회사에서 제공'}],
  permissionMode:'default',permissionModeOverride:null,permissionModeSupport:'confirmed',
  availablePermissionModes:[{value:'default',displayName:'직접 승인',description:'실행 전에 확인'},
    {value:'auto',displayName:'자동 판단',description:'CLI가 실행 위험을 판단'},
    {value:'bypassPermissions',displayName:'모든 승인 생략'}],
  effort:'medium',effortOverride:null,effortSupport:'confirmed',
  availableEfforts:[{value:'medium',displayName:'중간'},{value:'high',displayName:'높음'}],
  capabilities:{setModel:true,setPermissionMode:true,setEffort:true}});
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',trusted:true,state:'done',messages:[],modelOverride:null,connection:connection()};
sessions=[{...active}];$('prompt').value='작성 중 요청';attachments=['C:/fixture/A/report.csv'];
refreshFiles=async()=>{};refreshResults=async()=>{};refreshSessionMeta=async()=>{};
WorkspaceInlineControls.render();
const textFor = id => $(id).querySelector('strong').textContent;
const choose = label => [...$('runtime-panel-options').children].find(node=>flatText(node).includes(label));
const assertDraft = () => {assert.equal($('prompt').value,'작성 중 요청');assert.equal(attachments[0],'C:/fixture/A/report.csv');};
"""


@unittest.skipUnless(NODE, "Node.js is required for inline-control UI checks")
class WorkspaceInlineControlsFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + script,
             str(ROOT / 'local_app/web/inline-controls.js'), str(ROOT / 'local_app/web/composer.js')],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_chips_show_connection_values_and_open_nonmodal_without_api_or_settings(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};
          assert.equal(textFor('composer-model'),'기존 모델');assert.equal(textFor('composer-effort'),'medium');
          assert.equal(textFor('composer-permission'),'직접 승인');
          await $('composer-model').onclick();assert.equal($('composer-controls-panel').hidden,false);
          assert.equal($('runtime-panel-title').textContent,'모델 선택');assert.ok(choose('회사 모델'));
          assert.notEqual($('settings-dialog').open,true);assert.notEqual($('composer-controls-panel').open,true);
          assert.equal(calls,0);assertDraft();
        })()""")

    def test_effort_command_uses_session_control_before_send_and_preserves_files(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='/effort high';saveDraft();let reply,call;
          api=(path,body)=>{call={path,body};return new Promise(resolve=>reply=resolve);};
          const pending=submit();for(let i=0;i<6;i++)await Promise.resolve();
          assert.equal(call.path,'/api/effort');assert.equal(JSON.stringify(call.body),JSON.stringify({id:'A',effort:'high'}));
          assert.equal($('prompt').value,'/effort high');assert.equal(active.state,'done');assert.equal(active.messages.length,0);
          assert.equal($('conversation').children.filter(node=>node.classList.contains('user')).length,0);
          reply({effort:'medium',effortOverride:'high',effortSupport:'confirmed'});await pending;
          assert.equal($('prompt').value,'');assert.equal(attachments[0],'C:/fixture/A/report.csv');
          assert.equal(drafts.get('A').text,'');assert.equal(drafts.get('A').attachments.length,1);
          assert.equal(textFor('composer-effort'),'medium');assert.equal(active.state,'done');
          assert.match($('composer-control-status').textContent,/요청 high · 실제 적용 medium/);
        })()""")

    def test_effort_without_argument_opens_picker_and_only_selection_consumes_command(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='/effort';const calls=[];api=async(path,body)=>{calls.push({path,body});return {effort:'high',effortOverride:'high',effortSupport:'confirmed'};};
          await submit();assert.equal(calls.length,0);assert.equal($('composer-controls-panel').hidden,false);
          assert.equal($('prompt').value,'/effort');assert.ok(choose('high'));
          await choose('high').onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/effort');
          assert.equal($('prompt').value,'');assert.equal(attachments.length,1);assert.equal(active.messages.length,0);
        })()""")

    def test_invalid_and_unsupported_effort_commands_never_fall_through_to_ai(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;throw Error('No request expected');};
          for(const command of ['/effort ultracode','/effort default','/effort high extra','/effort high\n이어서 작업','/effort max']){
            $('prompt').value=command;await submit();assert.equal($('prompt').value,command);assert.equal(calls,0);
            assert.equal(active.messages.length,0);assert.equal(attachments.length,1);assert.equal(active.state,'done');
            assert.equal($('composer-control-status').dataset.error,'true');
          }
        })()""")

    def test_effort_rejection_and_newly_edited_draft_are_preserved(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='/effort high';api=async()=>{throw Error('변경 거절');};await submit();
          assert.equal($('prompt').value,'/effort high');assert.equal(active.connection.effort,'medium');
          assert.equal(active.state,'done');assert.match($('composer-control-status').textContent,/변경 거절/);
          let reply;api=()=>new Promise(resolve=>reply=resolve);const pending=submit();
          for(let i=0;i<6;i++)await Promise.resolve();$('prompt').value='새로 작성한 요청';saveDraft();
          reply({effort:'high',effortOverride:'high',effortSupport:'confirmed'});await pending;
          assert.equal($('prompt').value,'새로 작성한 요청');assert.equal(drafts.get('A').text,'새로 작성한 요청');
          assert.equal(attachments.length,1);assert.equal(active.state,'done');
        })()""")

    def test_late_effort_command_ack_does_not_clear_or_change_another_task(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='/effort high';saveDraft();let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=submit();for(let i=0;i<6;i++)await Promise.resolve();
          active={...active,id:'B',connection:connection()};selectionGeneration++;$('prompt').value='B 초안';attachments=['B.csv'];
          reply({effort:'high',effortOverride:'high',effortSupport:'confirmed'});await pending;
          assert.equal(active.id,'B');assert.equal(active.connection.effort,'medium');assert.equal($('prompt').value,'B 초안');
          assert.equal(attachments[0],'B.csv');assert.equal(drafts.get('A').text,'/effort high');
        })()""")

    def test_effort_command_prepares_connection_without_starting_a_task(self):
        self.run_case(r"""(async()=>{
          active.connection=null;$('prompt').value='/effort high';const paths=[];
          api=async(path)=>{paths.push(path);return path==='/api/connect'?{connection:connection()}:{effort:'high',effortOverride:'high',effortSupport:'confirmed'};};
          await submit();assert.equal(paths.join(','),'/api/connect,/api/effort');
          assert.equal(active.state,'done');assert.equal(active.messages.length,0);assert.equal($('prompt').value,'');
        })()""")

    def test_effort_command_home_and_untrusted_task_never_send_without_folder_confirmation(self):
        self.run_case(r"""(async()=>{
          const selected=active;active=null;$('prompt').value='/effort high';let calls=[];
          api=async(path)=>{calls.push(path);return path==='/api/connect'?{connection:connection()}:path==='/api/effort'?{effort:'high',effortOverride:'high',effortSupport:'confirmed'}:{};};
          await submit();assert.equal(calls.length,0);assert.equal($('prompt').value,'/effort high');
          assert.equal($('composer-controls-panel').hidden,false);assert.notEqual($('folder-dialog').open,true);
          WorkspaceInlineControls.close();active=selected;active.trusted=false;active.connection=null;await submit();
          assert.equal(calls.length,0);assert.equal($('folder-dialog').open,true);assert.equal($('folder-form').dataset.afterTrust,'controls');
          $('trust').checked=true;await $('folder-form').onsubmit({submitter:{value:'ok',disabled:false},preventDefault(){}});
          assert.equal(calls.join(','),'/api/trust,/api/connect,/api/effort');assert.equal(active.state,'done');
          assert.equal($('prompt').value,'');assert.equal(active.messages.length,0);
        })()""")

    def test_effort_auto_restores_only_effort_and_unavailable_restore_keeps_draft(self):
        self.run_case(r"""(async()=>{
          active.connection.effort='high';active.connection.effortOverride='high';active.connection.effortResetAvailable=false;
          $('prompt').value='/effort auto';let calls=0;api=async()=>{calls++;throw Error('Unavailable reset must not run');};
          await submit();assert.equal(calls,0);assert.equal($('prompt').value,'/effort auto');
          active.connection.effortResetAvailable=true;active.connection.model='chosen-model';active.connection.permissionMode='auto';
          api=async(path,body)=>{calls++;assert.equal(path,'/api/effort');assert.equal(body.effort,null);return {effort:'medium',effortOverride:null,effortSupport:'confirmed',reconnectRequired:false};};
          await submit();assert.equal(calls,1);assert.equal($('prompt').value,'');assert.equal(active.connection.model,'chosen-model');
          assert.equal(active.connection.permissionMode,'auto');assert.equal(active.connection.connected,true);assert.equal(active.state,'done');
          assert.doesNotMatch($('composer-control-status').textContent,/다음 요청/);
          $('prompt').value='/effort auto';await submit();assert.equal(calls,1);assert.equal($('prompt').value,'');
        })()""")

    def test_cli_mode_label_and_actual_effort_selection_are_not_request_aliases(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionModeLabel='auto mode on';active.connection.permissionMode='auto';active.connection.permissionModeOverride='default';
          active.connection.effort='medium';active.connection.effortOverride='high';WorkspaceInlineControls.render();
          assert.equal(textFor('composer-permission'),'auto mode on');assert.equal(textFor('composer-effort'),'medium');
          await $('composer-effort').onclick();assert.equal(choose('medium').attributes['aria-pressed'],'true');
          assert.equal(choose('high').attributes['aria-pressed'],'false');assert.doesNotMatch(flatText($('runtime-panel-options')),/높음|중간/);
          active.connection.effort=null;active.connection.effortOverride=null;WorkspaceInlineControls.render();
          assert.equal(textFor('composer-effort'),'미확인');
        })()""")

    def test_shift_tab_cycles_only_current_cli_choices_without_sending_or_work_state_change(self):
        self.run_case(r"""(async()=>{
          active.connection.availablePermissionModes=['default','acceptEdits','plan','auto','manual','bypassPermissions'].map(value=>({value,displayName:value}));
          active.connection.permissionModeCycle=['default','acceptEdits','plan','auto','bypassPermissions','manual'];
          const modes=[];api=async(path,body)=>{assert.equal(path,'/api/permission-mode');modes.push(body.mode);return {permissionMode:body.mode,permissionModeOverride:body.mode};};
          $('prompt').focus();
          for(const expected of ['acceptEdits','plan','auto','default']){
            const event={key:'Tab',shiftKey:true,preventDefault(){this.prevented=true;}};$('prompt').onkeydown(event);
            for(let i=0;i<12;i++)await Promise.resolve();assert.equal(event.prevented,true);assert.equal(modes.at(-1),expected);
            assert.equal(active.state,'done');assert.equal(active.messages.length,0);assertDraft();
          }
          assert.equal(modes.length,4);assert.equal($('prompt'),document.activeElement);
        })()""")

    def test_manual_default_aliases_select_actual_choice_and_cycle_using_supported_protocol_name(self):
        self.run_case(r"""(async()=>{
          for(const [offered,actual] of [['manual','default'],['default','manual']]){
            WorkspaceInlineControls.close();active.connection=connection();
            active.connection.availablePermissionModes=[{value:offered,displayName:'Manual'},
              {value:'acceptEdits',displayName:'Accept edits'},{value:'plan',displayName:'Plan'},{value:'auto',displayName:'Auto'}];
            active.connection.permissionModeCycle=[offered,'acceptEdits','plan','auto'];
            active.connection.permissionMode='auto';active.connection.permissionModeOverride='auto';
            const modes=[];api=async(path,body)=>{assert.equal(path,'/api/permission-mode');modes.push(body.mode);
              return {permissionMode:body.mode===offered?actual:body.mode,permissionModeOverride:body.mode,
                permissionModeLabel:body.mode===offered?'manual mode on':body.mode+' on'};};
            $('prompt').focus();await WorkspaceInlineControls.cyclePermission();
            assert.equal(modes[0],offered);assert.equal(active.connection.permissionMode,actual);
            assert.equal(textFor('composer-permission'),'manual mode on');await $('composer-permission').onclick();
            assert.equal(choose('Manual').attributes['aria-pressed'],'true');assert.match(flatText(choose('Manual')),/✓/);
            assert.equal(choose('Auto').attributes['aria-pressed'],'false');
            updatePermissionControls();assert.match($('permission-mode-message').textContent,/현재: Manual/);
            $('prompt').focus();await WorkspaceInlineControls.cyclePermission();assert.equal(modes[1],'acceptEdits');
            assert.equal(active.state,'done');assertDraft();
          }
        })()""")

    def test_open_permission_menu_tracks_control_events_without_rebuilding_or_losing_focus(self):
        self.run_case(r"""(async()=>{
          active.connection.availablePermissionModes=[{value:'manual',displayName:'Manual'},{value:'auto',displayName:'Auto'}];
          active.connection.permissionMode='auto';active.connection.permissionModeOverride='auto';
          await $('composer-permission').onclick();const manual=choose('Manual'),auto=choose('Auto');manual.focus();
          assert.equal(auto.attributes['aria-pressed'],'true');
          handleEvent({type:'permission_mode_changed',data:{permissionMode:'default',permissionModeOverride:'manual',permissionModeLabel:'manual mode on'}});
          assert.equal(choose('Manual'),manual);assert.equal(document.activeElement,manual);
          assert.equal(manual.attributes['aria-pressed'],'true');assert.match(flatText(manual),/✓/);
          assert.equal(auto.attributes['aria-pressed'],'false');assert.doesNotMatch(flatText(auto),/✓/);
          assert.equal(textFor('composer-permission'),'manual mode on');assert.equal(active.state,'done');assertDraft();
        })()""")

    def test_permission_event_before_http_ack_keeps_actual_manual_alias_selected_after_reopen(self):
        self.run_case(r"""(async()=>{
          active.connection.availablePermissionModes=[{value:'manual',displayName:'Manual'},{value:'auto',displayName:'Auto'}];
          active.connection.permissionMode='auto';active.connection.permissionModeOverride='auto';
          await $('composer-permission').onclick();let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=choose('Manual').onclick();
          const state={permissionMode:'default',permissionModeOverride:'manual',permissionModeLabel:'manual mode on'};
          handleEvent({type:'permission_mode_changed',data:state});assert.equal(choose('Manual').attributes['aria-pressed'],'true');
          reply({...state});await pending;await $('composer-permission').onclick();
          assert.equal(choose('Manual').attributes['aria-pressed'],'true');assert.equal(choose('Auto').attributes['aria-pressed'],'false');
          assert.equal(active.state,'done');assertDraft();
        })()""")

    def test_shift_tab_respects_ime_other_inputs_modifiers_and_auto_rejection(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionModeCycle=['default','auto'];let calls=0;api=async()=>{calls++;throw Error('회사 정책 거절');};
          const event=extra=>({key:'Tab',shiftKey:true,preventDefault(){this.prevented=true;},...extra});
          $('model-input').focus();assert.equal(WorkspaceInlineControls.keydown(event()),false);
          $('prompt').focus();
          for(const extra of [{isComposing:true},{keyCode:229},{ctrlKey:true},{altKey:true},{metaKey:true},{key:'Enter'}]){
            const e=event(extra);$('prompt').onkeydown(e);assert.notEqual(e.prevented,true);
          }
          $('prompt').oncompositionstart();const ime=event();$('prompt').onkeydown(ime);assert.notEqual(ime.prevented,true);$('prompt').oncompositionend();
          assert.equal(calls,0);const key=event();$('prompt').onkeydown(key);for(let i=0;i<12;i++)await Promise.resolve();
          assert.equal(calls,1);assert.equal(active.connection.permissionMode,'default');assert.equal(active.state,'done');
          assert.match($('composer-control-status').textContent,/회사 정책 거절/);assertDraft();
        })()""")

    def test_tab_completion_and_shift_enter_remain_available_with_mode_shortcut(self):
        self.run_case(r"""(async()=>{
          const paths=[];api=async(path)=>{paths.push(path);return {items:[{id:'s',invocation:'/company:skill',label:'/company:skill',supported:true}]};};
          $('prompt').focus();$('prompt').value='/ski';$('prompt').setSelectionRange(4,4);await WorkspaceComposer.refresh();
          const newline={key:'Enter',shiftKey:true,preventDefault(){this.prevented=true;}};$('prompt').onkeydown(newline);assert.notEqual(newline.prevented,true);
          const tab={key:'Tab',preventDefault(){this.prevented=true;}};$('prompt').onkeydown(tab);
          assert.equal(tab.prevented,true);assert.equal($('prompt').value,'/company:skill ');assert.equal(paths.join(','),'/api/completions');
        })()""")

    def test_model_selection_changes_only_after_ack_and_keeps_draft_and_attachment(self):
        self.run_case(r"""(async()=>{
          await $('composer-model').onclick();let reply,body;
          api=(path,data)=>{assert.equal(path,'/api/model');body=data;return new Promise(resolve=>reply=resolve);};
          const pending=choose('회사 모델').onclick();assert.equal(body.id,'A');assert.equal(body.model,'company-next');
          assert.equal(textFor('composer-model'),'기존 모델');assert.equal($('send').disabled,true);
          assert.equal([...$('runtime-panel-options').children].every(node=>node.disabled),true);
          reply({modelOverride:'company-next',session:{connection:{model:'company-next',modelOverride:'company-next'}}});
          await pending;assert.equal(textFor('composer-model'),'회사 모델');assert.equal($('send').disabled,false);
          assert.equal($('composer-controls-panel').hidden,true);assert.match($('composer-control-status').textContent,/변경을 확인/);
          assertDraft();
        })()""")

    def test_custom_model_uses_same_setter_and_empty_value_is_not_sent(self):
        self.run_case(r"""(async()=>{
          await $('composer-model').onclick();const box=$('runtime-panel-extra').children[0],input=box.querySelector('input'),button=box.querySelector('button');
          let calls=[];api=async(path,body)=>{calls.push({path,body});return {modelOverride:body.model,model:body.model};};
          await button.onclick();assert.equal(calls.length,0);assert.match($('runtime-panel-error').textContent,/모델 이름/);
          input.value='  company/custom  ';await button.onclick();assert.equal(calls.length,1);
          assert.equal(calls[0].path,'/api/model');assert.equal(calls[0].body.model,'company/custom');
          assert.equal(textFor('composer-model'),'company/custom');assertDraft();
        })()""")

    def test_permission_list_excludes_unsafe_mode_and_preserves_cli_values(self):
        self.run_case(r"""(async()=>{
          await $('composer-permission').onclick();assert.doesNotMatch(flatText($('runtime-panel-options')),/모든 승인 생략/);
          assert.equal($('runtime-panel-options').children.length,3);let call;
          api=async(path,body)=>{call={path,body};return {permissionMode:'auto',permissionModeOverride:'auto'};};
          await choose('자동 판단').onclick();assert.equal(call.path,'/api/permission-mode');
          assert.equal(call.body.mode,'auto');assert.equal(Object.keys(call.body).sort().join(','),'id,mode');
          assert.equal(textFor('composer-permission'),'자동 판단');assertDraft();
        })()""")

    def test_rejection_stays_inline_and_retains_current_connection_value(self):
        self.run_case(r"""(async()=>{
          await $('composer-permission').onclick();api=async()=>{throw new Error('회사 정책에서 변경을 거절했어요.');};
          await choose('자동 판단').onclick();assert.equal(textFor('composer-permission'),'직접 승인');
          assert.equal($('composer-controls-panel').hidden,false);assert.equal($('runtime-panel-error').hidden,false);
          assert.match($('runtime-panel-error').textContent,/회사 정책/);assert.equal($('composer-control-status').dataset.error,'true');
          assert.equal(choose('자동 판단').disabled,false);assert.notEqual($('settings-dialog').open,true);assertDraft();
        })()""")

    def test_effort_distinguishes_requested_from_actual_and_waits_for_response(self):
        self.run_case(r"""(async()=>{
          await $('composer-effort').onclick();let reply,body;
          api=(path,data)=>{assert.equal(path,'/api/effort');body=data;return new Promise(resolve=>reply=resolve);};
          const pending=choose('high').onclick();assert.equal(body.effort,'high');assert.equal(textFor('composer-effort'),'medium');
          assert.equal($('send').disabled,true);
          reply({effort:null,effortOverride:'high',effortSupport:'requested',reconnectRequired:true});await pending;
          assert.equal(textFor('composer-effort'),'요청 high');assert.match($('composer-control-status').textContent,/실제 적용 수준은 확인하지 못/);
          assert.doesNotMatch($('composer-control-status').textContent,/실제 high/);assert.equal(effortChanging,false);
          active.connection.effort='medium';active.connection.effortSupport='confirmed';WorkspaceInlineControls.render();
          assert.equal(textFor('composer-effort'),'medium');assertDraft();
        })()""")

    def test_effort_auto_picker_preserves_model_permission_and_live_connection(self):
        self.run_case(r"""(async()=>{
          await $('composer-effort').onclick();assert.equal(choose('auto').disabled,true);
          WorkspaceInlineControls.close();active.modelOverride='company-next';active.connection.modelOverride='company-next';
          active.connection.effortOverride='high';active.connection.permissionModeOverride='auto';
          await $('composer-effort').onclick();const reset=choose('auto');
          assert.equal(reset.disabled,false);assert.match(flatText(reset),/변경 전 확인한 Effort/);
          let body;api=async(path,data)=>{assert.equal(path,'/api/effort');body=data;return {
            effort:'medium',effortOverride:null,effortSupport:'confirmed',reconnectRequired:false};};
          await reset.onclick();assert.equal(body.effort,null);assert.equal(Object.keys(body).sort().join(','),'effort,id');
          assert.equal(active.modelOverride,'company-next');assert.equal(active.connection.modelOverride,'company-next');
          assert.equal(active.connection.permissionModeOverride,'auto');assert.equal(active.connection.effortOverride,null);
          assert.equal(active.connection.connected,true);assert.match($('composer-control-status').textContent,/변경 전 확인한 수준으로 복원/);
          assertDraft();
        })()""")

    def test_effort_clamp_displays_confirmed_actual_level_and_reports_requested_level(self):
        self.run_case(r"""(async()=>{
          await $('composer-effort').onclick();api=async(path,body)=>({effort:'medium',effortOverride:body.effort,
            effortSupport:'confirmed',availableEfforts:connection().availableEfforts});
          await choose('high').onclick();assert.equal(textFor('composer-effort'),'medium');
          assert.match($('composer-control-status').textContent,/요청 high · 실제 적용 medium/);
          assert.match($('composer-control-status').textContent,/모델·회사 정책 제한/);
          assert.equal($('composer-controls-panel').hidden,true);assertDraft();
        })()""")

    def test_effort_rejection_applies_capability_event_and_preserves_draft(self):
        self.run_case(r"""(async()=>{
          await $('composer-effort').onclick();let calls=0;
          api=async()=>{calls++;handleEvent({type:'effort_changed',data:{...connection(),effortSupport:'unavailable',
            capabilities:{setModel:true,setPermissionMode:true,setEffort:false}}});throw new Error('회사 정책으로 추론 수준 변경이 거절되었습니다.');};
          await choose('high').onclick();assert.equal(calls,1);assert.equal(textFor('composer-effort'),'medium');
          assert.equal(active.connection.effortSupport,'unavailable');assert.equal(choose('high').disabled,true);
          assert.equal($('composer-controls-panel').hidden,false);assert.match($('runtime-panel-error').textContent,/변경이 거절/);
          assert.equal($('composer-control-status').dataset.error,'true');assert.equal(effortChanging,false);assertDraft();
        })()""")

    def test_initial_connection_loads_without_user_message_and_survives_early_connected_event(self):
        self.run_case(r"""(async()=>{
          active.connection=null;let reply,calls=[];
          api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const pending=$('composer-model').onclick();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/connect');
          assert.equal(Object.keys(calls[0].body).join(','),'id');assert.equal(connectionPreparing,true);
          assert.equal($('send').disabled,true);assert.match($('composer-control-status').textContent,/불러오는 중/);
          handleEvent({type:'connected',data:connection()});assert.equal($('composer-controls-panel').hidden,false);
          reply({connection:connection()});await pending;assert.ok(choose('회사 모델'));
          assert.equal(connectionPreparing,false);assert.equal($('send').disabled,false);assert.equal(active.messages.length,0);
          assert.equal(calls.length,1);assertDraft();
        })()""")

    def test_connection_failure_offers_explicit_retry_without_automatic_resend(self):
        self.run_case(r"""(async()=>{
          active.connection=null;let calls=0;api=async()=>{calls++;throw new Error('초기화 연결 실패');};
          await $('composer-model').onclick();assert.equal(calls,1);assert.equal(connectionPreparing,false);
          assert.match($('runtime-panel-error').textContent,/초기화 연결 실패/);
          assert.equal($('runtime-panel-extra').children[0].textContent,'다시 불러오기');
          api=async path=>{calls++;assert.equal(path,'/api/connect');return {connection:connection()};};
          await $('runtime-panel-extra').children[0].onclick();assert.equal(calls,2);assert.ok(choose('회사 모델'));
          assert.equal($('runtime-panel-error').hidden,true);assertDraft();
        })()""")

    def test_home_explains_task_requirement_and_starts_only_when_clicked(self):
        self.run_case(r"""(async()=>{
          active=null;let calls=0,started=0;api=async()=>{calls++;};$('new-chat').onclick=()=>started++;
          WorkspaceInlineControls.render();await $('composer-effort').onclick();assert.equal(calls,0);assert.equal(started,0);
          assert.match($('runtime-panel-note').textContent,/업무를 선택/);
          assert.equal($('runtime-panel-extra').children[0].textContent,'새 업무 시작');
          $('runtime-panel-extra').children[0].onclick();assert.equal(started,1);assert.equal($('composer-controls-panel').hidden,true);
          assertDraft();
        })()""")

    def test_untrusted_task_confirms_folder_then_loads_controls_without_sending_draft(self):
        self.run_case(r"""(async()=>{
          active.trusted=false;active.connection=null;const calls=[];
          api=async(path,body)=>{calls.push({path,body});return path==='/api/connect'?{connection:connection()}:{ok:true};};
          await $('composer-permission').onclick();assert.equal(calls.length,0);assert.equal($('folder-dialog').open,true);
          assert.equal($('folder-form').dataset.afterTrust,'controls');assert.equal($('composer-controls-panel').hidden,true);
          $('trust').checked=true;await $('folder-form').onsubmit({submitter:{value:'ok'},preventDefault(){}});
          assert.equal(calls.map(call=>call.path).join(','),'/api/trust,/api/connect');
          assert.equal($('composer-controls-panel').hidden,false);assert.ok(choose('자동 판단'));assertDraft();
        })()""")

    def test_stale_model_response_after_task_roundtrip_never_changes_current_view(self):
        self.run_case(r"""(async()=>{
          await $('composer-model').onclick();let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=choose('회사 모델').onclick();selectionGeneration++;active={id:'B',state:'done'};
          selectionGeneration++;active={id:'A',state:'done',trusted:true,connection:connection(),modelOverride:null};
          active.connection.model='fresh-current';WorkspaceInlineControls.render();
          reply({model:'company-next',modelOverride:'company-next'});await pending;
          assert.equal(textFor('composer-model'),'fresh-current');assert.equal($('composer-controls-panel').hidden,true);
          assert.doesNotMatch($('composer-control-status').textContent,/변경을 확인/);assertDraft();
        })()""")

    def test_stale_connection_reply_never_replaces_another_tasks_options(self):
        self.run_case(r"""(async()=>{
          active.connection=null;let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=$('composer-model').onclick();selectionGeneration++;
          active={id:'B',title:'업무 B',state:'done',trusted:true,connection:connection(),modelOverride:null};active.connection.model='B-model';
          WorkspaceInlineControls.render();reply({connection:connection()});await pending;
          assert.equal(active.id,'B');assert.equal(active.connection.model,'B-model');assert.equal(textFor('composer-model'),'B-model');
          assert.equal($('composer-controls-panel').hidden,true);assert.equal(connectionPreparing,false);assertDraft();
        })()""")

    def test_busy_state_closes_picker_and_escape_restores_chip_focus(self):
        self.run_case(r"""(async()=>{
          await $('composer-model').onclick();const event={key:'Escape',preventDefault(){this.prevented=true;}};
          $('composer-controls-panel').onkeydown(event);assert.equal(event.prevented,true);
          assert.equal($('composer-controls-panel').hidden,true);assert.equal(document.activeElement,$('composer-model'));
          await $('composer-permission').onclick();setStatus('running');
          assert.equal($('composer-controls-panel').hidden,true);
          for(const id of ['composer-model','composer-effort','composer-permission'])assert.equal($(id).disabled,true);
          let calls=0;api=async()=>{calls++;};await $('composer-effort').onclick();assert.equal(calls,0);
          assert.match($('composer-control-status').textContent,/요청이 끝나면/);assertDraft();
        })()""")


if __name__ == '__main__':
    unittest.main()
