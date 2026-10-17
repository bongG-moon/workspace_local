from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE.replace('const scenario=process.argv[3];', '''
context.flush = () => new Promise(resolve => setImmediate(resolve));
context.flatText = function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
const originalClose=Element.prototype.close;
Element.prototype.close=function(value=''){
  originalClose.call(this,value);
  for(const listener of this.listeners?.close||[])listener.call(this,{type:'close',target:this});
};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'customizations.js'});
const scenario=process.argv[3];
''')
SETUP = r'''
active={id:'A',title:'A',workspace:'C:/fixture/A',state:'running',trusted:true};sessions=[active];
const catalog=()=>({scope:'local',settingsPath:'C:/fixture/A/.claude/settings.local.json',revision:'revision-A',
  notice:'설정 상태',skills:[{name:'report',description:'Report',source:'user',visibility:'on',override:null,locked:false}],
  plugins:[{name:'format@test',enabled:false,locked:false}]});
let calls=[];api=async(path,body)=>{calls.push({path,body});if(path.startsWith('/api/session'))return active;
  if(body)return {ok:true,notice:'저장 완료 · 재시작 후 적용',catalog:catalog()};return catalog();};
'''
APPLY_SETUP = r'''
boot={demo:false};active.state='done';active.requests=[];active.messages=[];active.seq=7;
active.connection={connected:true,restarting:false,sessionId:'original-cli',capabilities:{}};
const targetSessions=new Map([['A',active]]);
const snapshot=id=>({...targetSessions.get(id),state:'done',seq:20,requests:[],choice:null,
  connection:{connected:true,restarting:false,sessionId:'same-cli-'+id,capabilities:{}}});
poll=async()=>{};refreshInspector=async()=>{};
let saves=0,restarts=0;
api=async(path,body)=>{
  calls.push({path,body});
  if(path.startsWith('/api/session?'))return targetSessions.get(new URLSearchParams(path.split('?')[1]).get('id'));
  if(path==='/api/restart-connection'){restarts++;return {ok:true,session:snapshot(body.id)};}
  if(path==='/api/customizations'&&body){saves++;return {ok:true,reconnectRequired:true,notice:'설정 저장됨',catalog:catalog()};}
  return catalog();
};
const editSetting=async(kind='plugins',id='A')=>{
  await WorkspaceCustomizations.open(kind,id);
  const selector=$('customizations-list').children[0].children[1];
  selector.value=kind==='plugins'?'off':'off';
  const pending=selector.onchange();await flush();return {pending,selector};
};
'''


@unittest.skipUnless(NODE, 'Node.js required')
class CustomizationsFrontendTests(unittest.TestCase):
    def run_case(self, case):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),SETUP+case,
                               str(ROOT/'local_app/web/customizations.js')],input=HARNESS,
                              text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_skills_command_opens_without_enqueue_connect_or_send(self):
        self.run_case(r'''(async()=>{
          let enqueued=0;globalThis.WorkspaceWorkflow={isSubmitting:()=>false,send:()=>enqueued++};
          $('prompt').value='/skills';attachments=['C:/fixture/A/input.csv'];await submit();
          assert.equal(enqueued,0);assert.equal(calls.length,1);assert.ok(calls[0].path.startsWith('/api/customizations?'));
          assert.equal($('customizations-dialog').open,true);assert.equal($('prompt').value,'');assert.equal(attachments.length,1);
          assert.ok(flatText($('customizations-list')).includes('report'));
        })()''')

    def test_change_is_scoped_to_displayed_task_and_preserves_running_request(self):
        self.run_case(r'''(async()=>{
          await WorkspaceCustomizations.open('skills');
          const selector=$('customizations-list').children[0].children[1];selector.value='off';await selector.onchange();
          const writes=calls.filter(call=>call.body);assert.equal(writes.length,1);
          assert.equal(writes[0].path,'/api/customizations');assert.equal(writes[0].body.id,'A');
          assert.equal(writes[0].body.scope,'local');assert.equal(writes[0].body.value,'off');
          assert.equal(active.state,'running');assert.match($('customizations-status').textContent,/저장 완료/);
        })()''')

    def test_scope_response_cannot_replace_newer_scope(self):
        self.run_case(r'''(async()=>{
          let resolveOld;api=async(path)=>path.includes('scope=user')?{...catalog(),settingsPath:'user settings'}:new Promise(resolve=>resolveOld=resolve);
          const old=WorkspaceCustomizations.open();$('customizations-scope').value='user';$('customizations-scope').onchange();await flush();
          resolveOld(catalog());await old;assert.equal($('customizations-path').textContent,'user settings');
        })()''')

    def test_large_catalog_has_bounded_initial_dom_and_locked_rows(self):
        self.run_case(r'''(async()=>{
          api=async()=>({...catalog(),skills:Array.from({length:350},(_,i)=>({name:'skill-'+i,visibility:'off',locked:i===0,reason:'Managed'}))});
          await WorkspaceCustomizations.open();assert.equal($('customizations-list').children.length,101);
          assert.equal($('customizations-list').children[0].children[1].disabled,true);
          $('customizations-list').children[100].onclick();assert.equal($('customizations-list').children.length,201);
        })()''')

    def test_plugin_failure_is_visible_without_success_or_retry_loop(self):
        self.run_case(r'''(async()=>{
          await WorkspaceCustomizations.open('plugins');
          api=async(path,body)=>{calls.push({path,body});if(body)throw Error('CLI 거절');return active;};
          const selector=$('customizations-list').children[0].children[1];selector.value='on';await selector.onchange();
          assert.match($('customizations-status').textContent,/CLI 거절/);
          assert.equal(calls.filter(call=>call.body).length,1);assert.equal($('customizations-refresh').disabled,false);
        })()''')

    def test_saved_settings_offer_immediate_apply_and_later_preserves_running_connection(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          active.state='running';$('prompt').value='작성 중인 내용';attachments=['keep.zip'];
          const {pending}=await editSetting();
          assert.equal(saves,1);assert.equal(restarts,0);assert.equal($('action-dialog').open,true);
          assert.match($('action-title').textContent,/바로 적용/);
          assert.match($('action-message').textContent,/Claude Code/);
          assert.equal($('action-cancel').textContent,'나중에');
          $('action-cancel').onclick();await pending;
          assert.equal(restarts,0);assert.equal(active.state,'running');
          assert.equal($('customizations-dialog').open,true);
          assert.match($('customizations-status').textContent,/저장/);
          assert.equal($('prompt').value,'작성 중인 내용');assert.deepEqual(attachments,['keep.zip']);
          assert.equal(restartingConnections.size,0);
        })()''')

    def test_confirm_applies_each_supported_kind_without_replaying_chat(self):
        for kind in ('skills', 'plugins'):
            with self.subTest(kind=kind):
                self.run_case(APPLY_SETUP+"const testedKind="+repr(kind)+r''';(async()=>{
                  $('prompt').value='보관할 초안';attachments=['keep.csv'];
                  const {pending}=await editSetting(testedKind);
                  assert.equal($('action-confirm').textContent,'지금 적용');
                  assert.equal($('customizations-dialog').open,true);
                  $('action-confirm').onclick();await pending;
                  const requests=calls.filter(call=>call.path==='/api/restart-connection');
                  assert.equal(requests.length,1);assert.deepEqual(requests[0].body,{id:'A'});
                  assert.equal($('customizations-dialog').open,false);assert.equal(active.id,'A');
                  assert.equal(active.connection.sessionId,'same-cli-A');
                  assert.equal($('prompt').value,'보관할 초안');assert.deepEqual(attachments,['keep.csv']);
                  assert.equal(calls.some(call=>['/api/send','/api/workflow'].includes(call.path)),false);
                  assert.equal(restartingConnections.size,0);
                })()''')

    def test_busy_apply_uses_one_explicit_stop_confirmation(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          active.state='approval';active.requests=[{id:'permission',tool:'Bash',input:{command:'fixture'}}];
          const {pending,selector}=await editSetting();
          assert.equal($('action-confirm').textContent,'중지하고 적용');
          assert.match($('action-message').textContent,/중지/);
          assert.match($('action-message').textContent,/이어 할 일/);
          assert.match($('action-message').textContent,/예약/);
          assert.match($('action-message').textContent,/일시.?정지/);
          await selector.onchange();assert.equal(saves,1);
          $('action-confirm').onclick();await pending;
          const requests=calls.filter(call=>call.path==='/api/restart-connection');
          assert.equal(requests.length,1);assert.deepEqual(requests[0].body,{id:'A',stopRunning:true});
          assert.equal($('action-dialog').open,false);assert.equal(restartingConnections.size,0);
        })()''')

    def test_settings_for_capability_task_restart_that_task_without_switching_active_chat(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const other={...active,id:'B',title:'조회 기준 업무 B',workspace:'C:/fixture/B'};
          targetSessions.set('B',other);sessions.push(other);
          $('prompt').value='A의 입력';attachments=['A.txt'];
          const {pending}=await editSetting('plugins','B');
          assert.match($('action-message').textContent,/조회 기준 업무 B/);
          $('action-confirm').onclick();await pending;
          const request=calls.find(call=>call.path==='/api/restart-connection');
          assert.equal(request.body.id,'B');assert.equal(active.id,'A');
          assert.equal(active.connection.sessionId,'original-cli');
          assert.equal($('prompt').value,'A의 입력');assert.deepEqual(attachments,['A.txt']);
          assert.equal(restartingConnections.size,0);
        })()''')

    def test_failed_save_never_prompts_or_restarts(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const original=api;api=async(path,body)=>{
            if(path==='/api/customizations'&&body)throw Error('저장 거절');
            return original(path,body);
          };
          const {pending}=await editSetting();await pending;
          assert.notEqual($('action-dialog').open,true);assert.equal(restarts,0);
          assert.match($('customizations-status').textContent,/저장 거절/);
          assert.equal($('customizations-refresh').disabled,false);
        })()''')

    def test_close_during_save_keeps_late_reply_from_opening_apply_confirmation(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          let acknowledge;const original=api;api=(path,body)=>path==='/api/customizations'&&body
            ?new Promise(resolve=>acknowledge=resolve):original(path,body);
          const {pending}=await editSetting();$('customizations-close').onclick();
          acknowledge({ok:true,reconnectRequired:true,notice:'설정 저장됨',catalog:catalog()});await pending;
          assert.notEqual($('action-dialog').open,true);assert.equal(restarts,0);
          assert.equal($('customizations-dialog').open,false);
        })()''')

    def test_apply_later_survives_manager_reopen_and_success_clears_pending_state(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const {pending}=await editSetting();$('action-cancel').onclick();await pending;
          $('customizations-close').onclick();await WorkspaceCustomizations.open('plugins','A');
          assert.equal($('customizations-apply').hidden,false);
          assert.equal($('customizations-apply').disabled,false);assert.equal(restarts,0);
          const apply=$('customizations-apply').onclick();await flush();
          $('action-confirm').onclick();await apply;
          assert.equal(restarts,1);assert.equal(saves,1);
          await WorkspaceCustomizations.open('plugins','A');
          assert.equal($('customizations-apply').hidden,true);
        })()''')

    def test_save_acknowledged_after_manager_closed_preserves_pending_state_on_reopen(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          let acknowledge;const original=api;api=(path,body)=>path==='/api/customizations'&&body
            ?new Promise(resolve=>acknowledge=resolve):original(path,body);
          const {pending}=await editSetting();$('customizations-close').onclick();
          acknowledge({ok:true,reconnectRequired:true,notice:'설정 저장됨',catalog:catalog()});await pending;
          assert.notEqual($('action-dialog').open,true);assert.equal(restarts,0);
          await WorkspaceCustomizations.open('plugins','A');
          assert.equal($('customizations-apply').hidden,false);
          assert.equal($('customizations-apply').disabled,false);
        })()''')

    def test_close_during_confirmation_prevents_late_restart(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const {pending}=await editSetting();$('customizations-close').onclick();
          $('action-confirm').onclick();await pending;
          assert.equal(restarts,0);assert.equal(restartingConnections.size,0);
        })()''')

    def test_switching_active_task_during_apply_confirmation_never_restarts_new_task(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const {pending}=await editSetting();
          active={...active,id:'B',title:'새 업무 B'};sessions.push(active);selectionGeneration++;
          $('prompt').value='새 화면 입력';attachments=['B.zip'];
          $('action-confirm').onclick();await pending;
          assert.equal(restarts,0);assert.equal(active.id,'B');
          assert.equal($('prompt').value,'새 화면 입력');assert.deepEqual(attachments,['B.zip']);
          assert.equal(restartingConnections.size,0);
        })()''')

    def test_reconnect_failure_keeps_saved_settings_and_retry_does_not_save_twice(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          let attempts=0;const original=api;api=async(path,body)=>{
            if(path==='/api/restart-connection'&&++attempts===1){
              calls.push({path,body});throw Error('연결 실행 실패');
            }
            return original(path,body);
          };
          const {pending}=await editSetting();$('action-confirm').onclick();await pending;
          assert.equal(saves,1);assert.equal($('customizations-dialog').open,true);
          assert.match($('customizations-status').textContent,/설정은 저장/);
          assert.match($('customizations-status').textContent,/연결 실행 실패/);
          assert.equal($('customizations-apply').hidden,false);assert.equal($('customizations-apply').disabled,false);
          const retry=$('customizations-apply').onclick();await flush();
          assert.equal($('action-dialog').open,true);$('action-confirm').onclick();await retry;
          assert.equal(attempts,2);assert.equal(saves,1);assert.equal($('customizations-dialog').open,false);
          assert.equal(restartingConnections.size,0);
        })()''')

    def test_common_scope_save_requires_scope_consent_before_distinct_apply_consent(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          await WorkspaceCustomizations.open('plugins');$('customizations-scope').value='user';
          $('customizations-scope').onchange();await flush();
          const selector=$('customizations-list').children[0].children[1];selector.value='off';
          const pending=selector.onchange();await flush();
          assert.match($('action-title').textContent,/공통 설정/);assert.equal(saves,0);
          $('action-confirm').onclick();await flush();
          assert.equal(saves,1);assert.match($('action-title').textContent,/바로 적용/);
          assert.match($('action-message').textContent,/다른 업무와 터미널/);
          assert.equal(calls.find(call=>call.path==='/api/customizations'&&call.body).body.scope,'user');
          $('action-cancel').onclick();await pending;assert.equal(restarts,0);
        })()''')

    def test_close_before_late_busy_race_cannot_prompt_or_stop_closed_target(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          let rejectStart;const original=api;api=(path,body)=>{
            if(path==='/api/restart-connection'){
              calls.push({path,body});return new Promise((resolve,reject)=>rejectStart=reject);
            }
            return original(path,body);
          };
          const {pending}=await editSetting();$('action-confirm').onclick();await flush();
          $('customizations-close').onclick();
          const error=new Error('새 작업 시작됨');error.code='restart_requires_stop';rejectStart(error);
          await flush();assert.equal($('action-dialog').open,false);await pending;
          assert.equal(calls.filter(call=>call.path==='/api/restart-connection').length,1);
          assert.equal(restartingConnections.size,0);
        })()''')

    def test_new_work_started_after_apply_consent_needs_new_stop_consent(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                self.run_case(APPLY_SETUP+f'const acceptStop={str(accepted).lower()};'+r'''(async()=>{
                  let attempts=0;const original=api;api=async(path,body)=>{
                    if(path==='/api/restart-connection'&&++attempts===1){
                      calls.push({path,body});const err=new Error('중지 동의 필요');
                      err.code='restart_requires_stop';throw err;
                    }
                    return original(path,body);
                  };
                  const {pending}=await editSetting();$('action-confirm').onclick();await flush();
                  assert.equal(attempts,1);assert.equal($('action-dialog').open,true);
                  assert.match($('action-message').textContent,/진행 중인 요청/);
                  $(acceptStop?'action-confirm':'action-cancel').onclick();await pending;
                  const requests=calls.filter(call=>call.path==='/api/restart-connection');
                  assert.equal(requests.length,acceptStop?2:1);assert.deepEqual(requests[0].body,{id:'A'});
                  if(acceptStop)assert.deepEqual(requests[1].body,{id:'A',stopRunning:true});
                  else {assert.equal($('customizations-dialog').open,true);assert.equal($('customizations-apply').hidden,false);}
                  assert.equal(saves,1);assert.equal(restartingConnections.size,0);
                })()''')

    def test_cancel_common_scope_save_restores_selection_and_status_without_writes(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          await WorkspaceCustomizations.open('skills');$('customizations-scope').value='user';
          $('customizations-scope').onchange();await flush();
          const selector=$('customizations-list').children[0].children[1];selector.value='off';
          const pending=selector.onchange();await flush();
          assert.match($('action-title').textContent,/공통 설정/);$('action-cancel').onclick();await pending;
          assert.equal(saves,0);assert.equal(restarts,0);
          assert.equal(calls.some(row=>row.body),false);
          assert.match($('customizations-status').textContent,/설정을 변경하지 않았/);
          assert.doesNotMatch($('customizations-status').textContent,/저장하고/);
          assert.equal($('customizations-list').children[0].children[1].value,'inherit');
          assert.equal($('customizations-scope').disabled,false);assert.equal($('customizations-refresh').disabled,false);
        })()''')

    def test_skill_modes_and_plugin_boolean_are_saved_exactly_without_changing_permissions(self):
        cases=[('skills','on','on'),('skills','off','off'),('skills','name-only','name-only'),
               ('skills','user-invocable-only','user-invocable-only'),('skills','inherit',None),
               ('plugins','on',True),('plugins','off',False)]
        import json
        for kind,selected,expected in cases:
            with self.subTest(kind=kind,selected=selected):
                self.run_case('const changeCase='+json.dumps([kind,selected,expected])+r''';(async()=>{
                  active.permissionModeOverride='plan';const original=active;
                  await WorkspaceCustomizations.open(changeCase[0]);
                  const selector=$('customizations-list').children[0].children[1];selector.value=changeCase[1];await selector.onchange();
                  const writes=calls.filter(row=>row.body);assert.equal(writes.length,1);
                  assert.equal(writes[0].path,'/api/customizations');assert.equal(writes[0].body.kind,changeCase[0]);
                  assert.equal(writes[0].body.value,changeCase[2]);assert.equal(writes[0].body.revision,'revision-A');
                  assert.equal(writes[0].body.scope,'local');assert.equal(active,original);
                  assert.equal(active.permissionModeOverride,'plan');assert.equal(active.state,'running');
                  assert.equal(calls.some(row=>['/api/send','/api/connect','/api/restart-connection'].includes(row.path)),false);
                })()''')

    def test_scope_inheritance_help_tracks_selected_scope_and_shows_saved_state(self):
        self.run_case(r'''(async()=>{
          await WorkspaceCustomizations.open();
          assert.equal($('customizations-inherit-title').textContent,'공통·기존 설정 따르기');
          assert.match($('customizations-inherit-description').textContent,/기존 프로젝트 설정/);
          assert.match(flatText($('customizations-list')),/저장된 스킬 설정: 사용/);
          assert.doesNotMatch(flatText($('customizations-list')),/현재 설정:/);
          $('customizations-scope').value='user';$('customizations-scope').onchange();await flush();
          assert.equal($('customizations-inherit-title').textContent,'스킬 기본 설정 따르기');
          assert.match($('customizations-inherit-description').textContent,/프로젝트별 설정이나 회사 정책/);
          const selector=$('customizations-list').children[0].children[1];
          assert.equal(selector.children[0].textContent,'스킬 기본 설정 따르기');
          assert.match(flatText($('customizations-list')),/스킬은 삭제하지 않습니다/);
        })()''')

    def test_pending_apply_remains_explicit_after_reopen_refresh_and_scope_change(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const {pending}=await editSetting();$('action-cancel').onclick();await pending;
          $('customizations-close').onclick();await WorkspaceCustomizations.open('plugins');
          assert.match($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          $('customizations-scope').value='user';$('customizations-scope').onchange();await flush();
          assert.match($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          await $('customizations-refresh').onclick();
          assert.match($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          assert.equal($('customizations-apply').hidden,false);assert.equal(restarts,0);assert.equal(saves,1);
          const count=calls.length;WorkspaceCustomizations.connectionRestarted('A');
          assert.equal($('customizations-apply').hidden,true);
          assert.doesNotMatch($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          assert.equal(calls.length,count);
        })()''')

    def test_other_task_pending_apply_does_not_mark_current_task_or_clear_it_on_wrong_restart(self):
        self.run_case(APPLY_SETUP+r'''(async()=>{
          const other={...active,id:'B',title:'B'};sessions.push(other);targetSessions.set('B',other);
          const {pending}=await editSetting('plugins','B');$('action-cancel').onclick();await pending;
          $('customizations-close').onclick();await WorkspaceCustomizations.open('plugins','A');
          assert.equal($('customizations-apply').hidden,true);
          assert.doesNotMatch($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          $('customizations-close').onclick();await WorkspaceCustomizations.open('plugins','B');
          WorkspaceCustomizations.connectionRestarted('A');assert.equal($('customizations-apply').hidden,false);
          assert.match($('customizations-status').textContent,/이 연결에 아직 적용하지 않았/);
          assert.equal(restarts,0);
        })()''')

    def test_project_override_lock_reason_is_visible_in_common_skill_scope(self):
        self.run_case(r'''(async()=>{
          api=async(path)=>({...catalog(),skills:[{name:'report',visibility:'off',source:'project',override:'on',
            locked:true,reason:'이 프로젝트에 별도 스킬 설정이 있어요. 저장 범위를 이 프로젝트로 바꿔 주세요.'}]});
          await WorkspaceCustomizations.open('skills');$('customizations-scope').value='user';
          $('customizations-scope').onchange();await flush();
          const card=$('customizations-list').children[0],selector=card.children[1];
          assert.equal(selector.disabled,true);assert.match(flatText(card),/별도 스킬 설정/);
          assert.match(flatText(card),/저장된 스킬 설정: 사용 안 함/);
          assert.equal(selector.value,'on');
        })()''')
