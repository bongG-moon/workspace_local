"""UI contracts for importing CLI history, explicit bypass, and desktop actions.

Runs shipped JavaScript in a deterministic DOM with delayed local API replies.
No personal Claude history, browser, model requests, or native windows are used.
"""
from pathlib import Path
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.addEventListener=function(name,fn){this.listeners??={};(this.listeners[name]??=[]).push(fn);};
Element.prototype.removeEventListener=function(name,fn){this.listeners??={};this.listeners[name]=(this.listeners[name]||[]).filter(row=>row!==fn);};
Element.prototype.contains=function(node){for(let n=node;n;n=n.parent)if(n===this)return true;return false;};
const originalClose=Element.prototype.close;
Element.prototype.close=function(value=''){originalClose.call(this,value);for(const fn of this.listeners?.close||[])fn({target:this});};
Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
context.window=context;context.addEventListener=()=>{};
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
for(const id of ['composer-model','composer-effort','composer-permission'])get(id).append(new Element('strong'));
get('composer-controls-panel').hidden=true;
for(const file of JSON.parse(process.argv[4]))vm.runInContext(fs.readFileSync(file,'utf8'),context,{filename:file});
const scenario=process.argv[3];
""")
SETUP = r"""
const connection=()=>({connected:true,model:'original',effort:'medium',permissionMode:'default',
  permissionModeOverride:null,permissionModeResetAvailable:true,
  bypassPermissions:{available:true,enabledForConnection:false,requiresReconnect:true,confirmationRequired:true,active:false},
  availableModels:[{value:'original',displayName:'기존 모델'}],availableEfforts:[{value:'medium',displayName:'medium'}],
  availablePermissionModes:[{value:'default',displayName:'Manual'},{value:'plan',displayName:'Plan'},
    {value:'auto',displayName:'Auto'},{value:'bypassPermissions',displayName:'Bypass permissions',risk:'high',requiresConfirmation:true}],
  permissionModeCycle:['default','plan','auto'],capabilities:{setModel:true,setEffort:true,setPermissionMode:true}});
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',trusted:true,state:'done',messages:[],connection:connection()};
sessions=[{...active}];$('prompt').value='작성 중 요청';attachments=['C:/fixture/A/report.csv'];
refreshFiles=async()=>{};refreshResults=async()=>{};refreshSessionMeta=async()=>{};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const assertDraft=()=>{assert.equal($('prompt').value,'작성 중 요청');assert.equal(attachments[0],'C:/fixture/A/report.csv');};
const imported={sessionId:'11111111-1111-1111-1111-111111111111',title:'이전 대화',workspace:'C:/fixture/imported',workspaceAvailable:true,
  messages:[{role:'user',text:'기존 질문'},{role:'assistant',text:'<script>아무 실행 없음</script>'}],warnings:[]};
"""


@unittest.skipUnless(NODE, "Node.js is required for continuity UI checks")
class WorkspaceContinuityFrontendTests(unittest.TestCase):
    def run_case(self, script, modules=()):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), SETUP + script,
             json.dumps([str(ROOT / f"local_app/web/{module}.js") for module in modules])],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_history_discovery_and_preview_are_reads_and_keep_current_task_and_draft(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return path.includes('?')?imported:{sessions:[imported]};};
          $('import-open').onclick();await settle();assert.equal($('import-sessions').children.length,1);
          $('import-sessions').children[0].onclick();await settle();
          assert.ok(calls.every(call=>call.body===undefined));assert.ok(calls.every(call=>call.path.startsWith('/api/claude-sessions')));
          assert.equal(active.id,'A');assertDraft();assert.equal($('import-apply').disabled,false);
          assert.match(flatText($('import-preview')),/기존 질문/);assert.match(flatText($('import-preview')),/<script>/);
          assert.equal($('import-preview').querySelector('script'),null);assert.match($('import-message').textContent,/실행하지 않습니다/);
        })()""", ("session-import",))

    def test_closed_preview_response_cannot_replace_reopened_dialog(self):
        self.run_case(r"""(async()=>{
          let reply;api=async(path)=>path.includes('?')?new Promise(resolve=>reply=resolve):{sessions:[imported]};
          $('import-open').onclick();await settle();$('import-sessions').children[0].onclick();await settle();
          $('import-dialog').close();$('import-open').onclick();await settle();reply(imported);await settle();
          assert.equal($('import-preview').children.length,0);assert.equal($('import-apply').disabled,true);
          assert.equal(active.id,'A');assertDraft();
        })()""", ("session-import",))

    def test_import_double_click_posts_once_and_only_selects_imported_task(self):
        self.run_case(r"""(async()=>{
          let reply,posts=0,opened=[];const calls=[];
          api=async(path,body)=>{calls.push({path,body});if(path==='/api/claude-sessions/import'){posts++;return new Promise(resolve=>reply=resolve);}if(path==='/api/bootstrap')return {sessions:[{id:'imported',title:imported.title,workspace:imported.workspace}]};return path.includes('?')?imported:{sessions:[imported]};};
          selectSession=async id=>{opened.push(id);};
          $('import-open').onclick();await settle();$('import-sessions').children[0].onclick();await settle();
          const first=$('import-apply').onclick();await $('import-apply').onclick();assert.equal(posts,1);
          reply({session:{id:'imported'},existing:true});await first;
          assert.equal(JSON.stringify(opened),'["imported"]');assert.match($('toast').textContent,/이미 불러온/);
          assert.ok(!calls.some(call=>['/api/connect','/api/send','/api/dispatch'].includes(call.path)));
          assert.deepEqual(Object.keys(calls.find(call=>call.body)?.body),['sessionId']);
        })()""", ("session-import",))

    def test_import_post_reply_after_close_does_not_select_any_task(self):
        self.run_case(r"""(async()=>{
          let reply,selected=0;api=async(path)=>path==='/api/claude-sessions/import'?new Promise(resolve=>reply=resolve):path.includes('?')?imported:{sessions:[imported]};
          selectSession=async()=>{selected++;};$('import-open').onclick();await settle();$('import-sessions').children[0].onclick();await settle();
          const pending=$('import-apply').onclick();$('import-dialog').close();active={id:'B',title:'업무 B'};selectionGeneration++;
          reply({session:{id:'imported'}});await pending;assert.equal(selected,0);assert.equal(active.id,'B');
        })()""", ("session-import",))

    def test_import_bootstrap_reply_after_close_preserves_new_task_and_sidebar(self):
        self.run_case(r"""(async()=>{
          let reply,selected=0;api=async(path)=>path==='/api/claude-sessions/import'?{session:{id:'imported'}}:path==='/api/bootstrap'?new Promise(resolve=>reply=resolve):path.includes('?')?imported:{sessions:[imported]};
          selectSession=async()=>{selected++;};$('import-open').onclick();await settle();$('import-sessions').children[0].onclick();await settle();
          const pending=$('import-apply').onclick();await settle();$('import-dialog').close();active={id:'B',title:'업무 B'};sessions=[active];selectionGeneration++;
          reply({sessions:[{id:'stale',title:'오래된 결과'}]});await pending;
          assert.equal(selected,0);assert.equal(active.id,'B');assert.equal(sessions[0].id,'B');
        })()""", ("session-import",))

    def test_missing_original_workspace_disables_import_and_shows_reason(self):
        self.run_case(r"""(async()=>{
          api=async(path)=>path.includes('?')?{...imported,workspaceAvailable:false,warnings:['원래 폴더가 없습니다.']}:{sessions:[imported]};
          $('import-open').onclick();await settle();$('import-sessions').children[0].onclick();await settle();
          assert.equal($('import-apply').disabled,true);assert.match($('import-message').textContent,/원래 폴더/);assertDraft();
        })()""", ("session-import",))

    def test_bypass_confirmation_cancel_never_calls_api_or_changes_mode(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};const pending=setPermissionMode('bypassPermissions');
          assert.equal($('action-dialog').open,true);assert.match($('action-title').textContent,/⚠.*Bypass/);
          assert.match($('action-message').textContent,/파일 수정/);assert.equal(calls,0);
          $('action-cancel').onclick();await pending;assert.equal(calls,0);assert.equal(active.connection.permissionMode,'default');assertDraft();
        })()""")

    def test_bypass_requires_confirmed_support_even_when_name_is_reported(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};
          for(const metadata of [undefined,{available:false},{available:'true'}]){
            active.connection.bypassPermissions=metadata;
            assert.ok(!permissionOptions().some(row=>row.value==='bypassPermissions'));
            const result=await setPermissionMode('bypassPermissions');assert.equal(result.ok,false);
            assert.notEqual($('action-dialog').open,true);
          }
          assert.equal(calls,0);assert.equal(active.connection.permissionMode,'default');assertDraft();
        })()""")

    def test_supported_bypass_option_has_warning_and_selection_always_confirms(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};await WorkspaceInlineControls.open('permission');
          const option=$('runtime-panel-options').children.find(row=>row.dataset.runtimeValue==='bypassPermissions');
          assert.ok(option);assert.match(flatText(option),/⚠.*Bypass permissions/);assert.equal(option.disabled,false);
          const pending=option.onclick();assert.equal($('action-dialog').open,true);assert.equal(calls,0);
          $('action-cancel').onclick();await pending;assert.equal(calls,0);assertDraft();
        })()""", ("inline-controls",))

    def test_explicit_bypass_confirmation_sends_true_once_and_never_a_chat_request(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {permissionMode:'bypassPermissions',permissionModeOverride:'bypassPermissions',permissionModeLabel:'bypass permissions on'};};
          const first=setPermissionMode('bypassPermissions'),second=setPermissionMode('bypassPermissions');
          await second;assert.equal(calls.length,0);$('action-confirm').onclick();await first;
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/permission-mode');
          assert.equal(JSON.stringify(calls[0].body),JSON.stringify({id:'A',mode:'bypassPermissions',bypassConfirmed:true}));
          assert.equal(active.connection.permissionMode,'bypassPermissions');assert.equal(active.messages.length,0);assertDraft();
        })()""")

    def test_bypass_confirmation_for_old_task_cannot_change_new_task(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;};const pending=setPermissionMode('bypassPermissions');
          active={id:'B',title:'업무 B',state:'done',connection:connection()};selectionGeneration++;
          $('action-confirm').onclick();await pending;assert.equal(calls,0);assert.equal(active.connection.permissionMode,'default');
        })()""")

    def test_bypass_risk_chip_tracks_actual_mode_and_regular_cycle_skips_bypass(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionMode='bypassPermissions';active.connection.permissionModeLabel='bypass permissions on';
          WorkspaceInlineControls.render();assert.equal($('composer-permission').classList.contains('bypass-active'),true);
          active.connection.permissionMode='plan';active.connection.permissionModeLabel='plan mode on';
          active.connection.permissionModeCycle=['default','plan','bypassPermissions','auto'];
          WorkspaceInlineControls.render();assert.equal($('composer-permission').classList.contains('bypass-active'),false);
          const calls=[];api=async(path,body)=>{calls.push(body);return {permissionMode:body.mode,permissionModeOverride:body.mode};};
          const pending=WorkspaceInlineControls.cyclePermission();await settle();assert.notEqual($('action-dialog').open,true);
          assert.equal(calls.length,1);await pending;assert.equal(calls[0].mode,'auto');assert.equal('bypassConfirmed' in calls[0],false);assertDraft();
        })()""", ("inline-controls",))

    def test_shift_tab_leaves_actual_bypass_for_manual_without_confirmation(self):
        self.run_case(r"""(async()=>{
          active.connection.permissionMode='bypassPermissions';active.connection.permissionModeOverride='bypassPermissions';
          active.connection.permissionModeLabel='bypass permissions on';WorkspaceInlineControls.render();
          assert.equal($('composer-permission').classList.contains('bypass-active'),true);
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {permissionMode:'default',permissionModeOverride:'default',permissionModeLabel:'manual mode on'};};
          $('prompt').focus();const event={key:'Tab',shiftKey:true,preventDefault(){this.prevented=true;}};
          assert.equal(WorkspaceInlineControls.keydown(event),true);await settle();
          assert.equal(event.prevented,true);assert.notEqual($('action-dialog').open,true);assert.equal(calls.length,1);
          assert.equal(calls[0].path,'/api/permission-mode');assert.equal(JSON.stringify(calls[0].body),JSON.stringify({id:'A',mode:'default'}));
          assert.equal(active.connection.permissionMode,'default');assert.equal($('composer-permission').classList.contains('bypass-active'),false);assertDraft();
        })()""", ("inline-controls",))

    def test_desktop_snapshot_displays_metadata_without_opening_or_running_anything(self):
        self.run_case(r"""
          let calls=0;api=async()=>{calls++;};let selections=0;selectSession=async()=>{selections++;};
          WorkspaceDesktop.apply({preferences:{enabled:true,completed:true,attention:true,errors:true},unreadCount:2,nativeAvailable:false,
            inbox:[{id:'n1',sessionId:'A',title:'월간 업무',kind:'completed',createdAt:1700000000,read:false}]});
          assert.equal($('desktop-count').textContent,'2');assert.equal($('desktop-count').hidden,false);
          assert.match(flatText($('desktop-items')),/월간 업무/);assert.match(flatText($('desktop-items')),/작업 완료/);
          assert.equal(calls,0);assert.equal(selections,0);assertDraft();
        """, ("desktop",))

    def test_desktop_explicit_preference_change_is_single_and_rolls_back_on_failure(self):
        self.run_case(r"""(async()=>{
          WorkspaceDesktop.apply({preferences:{enabled:true,completed:true,attention:true,errors:true},inbox:[]});
          let reject,calls=[];api=(path,body)=>{calls.push({path,body});return new Promise((resolve,no)=>reject=no);};
          $('desktop-completed').checked=false;const pending=$('desktop-completed').onchange();await $('desktop-completed').onchange();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/notifications');
          assert.equal(JSON.stringify(calls[0].body),JSON.stringify({action:'configure',preferences:{completed:false}}));
          reject(Error('설정 저장 실패'));await pending;assert.equal($('desktop-completed').checked,true);assert.equal($('desktop-completed').disabled,false);
          assertDraft();
        })()""", ("desktop",))

    def test_desktop_presence_has_only_current_task_and_actual_visibility(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {};};
          document.visibilityState='visible';document.hasFocus=()=>true;await WorkspaceDesktop.presence();
          assert.equal(JSON.stringify(calls[0].body),JSON.stringify({action:'view',id:'A',visible:true}));
          document.hasFocus=()=>false;await WorkspaceDesktop.presence();assert.equal(calls[1].body.visible,false);
          appClosed=true;await WorkspaceDesktop.presence();assert.equal(calls.length,2);assertDraft();
        })()""", ("desktop",))

    def test_native_notification_navigation_deduplicates_without_executing_task(self):
        self.run_case(r"""(async()=>{
          const selections=[],storage=new Map();let calls=0;api=async()=>{calls++;};selectSession=async id=>{selections.push(id);return true;};
          sessionStorage.getItem=key=>storage.get(key)||null;sessionStorage.setItem=(key,value)=>storage.set(key,value);
          await WorkspaceDesktop.follow({id:'nav1',sessionId:'A'});await WorkspaceDesktop.follow({id:'nav1',sessionId:'A'});
          await WorkspaceDesktop.follow({id:'nav2',sessionId:'B'});
          assert.equal(JSON.stringify(selections),'["A","B"]');assert.equal(calls,0);assertDraft();
        })()""", ("desktop",))

    def test_desktop_notice_marks_only_clicked_notice_after_successful_selection(self):
        self.run_case(r"""(async()=>{
          const state={preferences:{enabled:true,completed:true,attention:true,errors:true},inbox:[{id:'n1',sessionId:'B',title:'업무 B',kind:'attention',createdAt:1700000000,read:false}],unreadCount:1};
          WorkspaceDesktop.apply(state);const calls=[],selections=[];api=async(path,body)=>{calls.push({path,body});return {desktop:{...state,inbox:[],unreadCount:0}};};
          selectSession=async id=>{selections.push(id);return false;};await $('desktop-items').children[0].onclick();assert.equal(calls.length,0);
          selectSession=async id=>{selections.push(id);return true;};await $('desktop-items').children[0].onclick();
          assert.equal(JSON.stringify(calls[0].body),JSON.stringify({action:'read',notificationId:'n1'}));assert.equal($('desktop-count').hidden,true);
          assert.equal(JSON.stringify(selections),'["B","B"]');assertDraft();
        })()""", ("desktop",))
