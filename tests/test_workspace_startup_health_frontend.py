"""Readiness and explicit screen recovery with no model or approval replay."""
from pathlib import Path
import shutil
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as APP_HARNESS

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),storage=new Map(),events=new Map(),windowEvents=new Map(),intervals=new Map();
let now=1000000,nextTimer=1,reloads=0,storageFails=false;
function node(id){if(!nodes.has(id))nodes.set(id,{id,hidden:false,textContent:'',dataset:{},disabled:false,onclick:null,tagName:'BUTTON',closest(){return this;}});return nodes.get(id);}
const modules={'path-picker':'WorkspacePathPicker',stream:'WorkspaceStream',attachments:'WorkspaceAttachments',workflow:'WorkspaceWorkflow',composer:'WorkspaceComposer','inline-controls':'WorkspaceInlineControls', 'input-keys':'WorkspaceInputKeys','chat-shortcuts':'WorkspaceShortcuts',attention:'WorkspaceAttention',desktop:'WorkspaceDesktop','session-import':'WorkspaceSessionImport',capabilities:'WorkspaceCapabilities',productivity:'WorkspaceProductivityActions',palette:'WorkspacePalette',layout:'WorkspaceLayout','rich-content':'WorkspaceRichContent','execution-view':'WorkspaceExecutionView','tool-activity':'WorkspaceToolActivity','upgrade-handoff':'WorkspaceUpgrade','app-updates':'WorkspaceAppUpdates'};
const controls=['new-chat','settings-open','shortcuts-open','workflow-open','schedule-open','composer-model','composer-effort','composer-permission','attention-open','desktop-open','import-open','capabilities-open','changes-open','branch-open','palette-open','sidebar-toggle','materials-button','input-history-close','app-update-check','app-update-notes-open','app-update-install'];
const posted=[],restores=[],directCalls=[];
const context={assert,console,URL,URLSearchParams,Math,JSON,Object,Map,Set,Date:{now:()=>now},location:{href:'http://127.0.0.1:1234/',hash:'#token=TEST_AUTH_SECRET',reload:()=>reloads++},
 crypto:{randomUUID:()=> '12345678-abcd-1234-abcd-123456789abc'},
 document:{readyState:'loading',getElementById:node,addEventListener:(name,fn)=>{if(!events.has(name))events.set(name,[]);events.get(name).push(fn);}},
 addEventListener:(name,fn)=>{if(!windowEvents.has(name))windowEvents.set(name,[]);windowEvents.get(name).push(fn);},
 sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,val)=>{if(storageFails)throw Error('denied');storage.set(key,val);},removeItem:key=>storage.delete(key)},
 setInterval:fn=>{const id=nextTimer++;intervals.set(id,fn);return id;},clearInterval:id=>intervals.delete(id),setTimeout:fn=>{Promise.resolve().then(fn);return 1;},
 fetch:async(url,options)=>{directCalls.push({url,options});return {ok:true};},
 posted,restores,directCalls,storage,node,controls,readReloads:()=>reloads,failStorage:()=>storageFails=true,
 advance:ms=>{now+=ms;for(const fn of [...intervals.values()])fn();},
 fire:(name,event={})=>{for(const fn of events.get(name)||[])fn({type:name,...event});},
 fireWindow:(name,event={})=>{for(const fn of windowEvents.get(name)||[])fn(event);},
 installAll:()=>{for(const property of Object.values(modules))context[property]={};for(const id of controls)node(id).onclick=()=>{};},
 hooks:{report:async record=>posted.push(record),canReload:()=>true,capture:()=>({sessionId:'A',drafts:[{id:'A',text:'unsent 한국어',attachments:['C:/work/input.csv']}]}),restore:async value=>{restores.push(value);return {};}}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'startup-health.js'});
context.health=context.WorkspaceStartupHealth;
context.flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
(async()=>{try{await vm.runInContext(process.argv[3],context);}catch(e){console.error(e.stack||e);process.exitCode=1;}})();
"""


@unittest.skipUnless(NODE, "Node.js is required for UI readiness regression checks")
class WorkspaceStartupHealthFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/startup-health.js"), script],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_dom_loaded_does_not_claim_bootstrap_or_feature_ready(self):
        self.run_case(r"""(async()=>{
          health.attach(hooks);fire('DOMContentLoaded');await flush();
          assert.equal(health.snapshot().bootstrap,false);
          assert.equal(health.snapshot().modules.app.status,'pending');
          assert.equal(health.snapshot().modules.capabilities.status,'failed');
          assert.equal(node('startup-health').hidden,false);assert.equal(readReloads(),0);
        })()""")

    def test_delayed_scripts_are_pending_then_become_ready_without_reload(self):
        self.run_case(r"""(async()=>{
          health.attach(hooks);advance(2000);assert.equal(health.snapshot().modules.workflow.status,'pending');
          let blocked=0;fire('click',{target:node('workflow-open'),preventDefault(){blocked++;},stopImmediatePropagation(){blocked++;}});assert.equal(blocked,2);
          installAll();await health.bootstrapReady();fire('DOMContentLoaded');await flush();
          assert.equal(health.snapshot().missing.length,0);assert.equal(node('startup-health').hidden,true);assert.equal(readReloads(),0);
          assert.ok(posted.some(row=>row.event==='startup'&&row.status==='ready'));
        })()""")

    def test_global_without_handler_is_failed_and_click_does_not_silently_disappear(self):
        self.run_case(r"""(async()=>{
          installAll();node('desktop-open').onclick=null;health.attach(hooks);await health.bootstrapReady();fire('DOMContentLoaded');
          assert.equal(health.snapshot().modules.desktop.reason,'missing-handler');
          let blocked=false;fire('click',{target:node('desktop-open'),preventDefault(){blocked=true;},stopImmediatePropagation(){}});
          assert.equal(blocked,true);assert.match(node('startup-health-text').textContent,/다시 열어/);
          assert.equal(health.snapshot().modules.capabilities.status,'ready');
        })()""")

    def test_missing_keyboard_assets_are_reported_and_history_close_requires_handler(self):
        self.run_case(r"""(async()=>{
          installAll();delete WorkspaceInputKeys;node('input-history-close').onclick=null;
          health.attach(hooks);await health.bootstrapReady();fire('DOMContentLoaded');await flush();
          assert.equal(health.snapshot().modules['input-keys'].reason,'missing-global');
          assert.equal(health.snapshot().modules['chat-shortcuts'].reason,'missing-handler');
          globalThis.WorkspaceInputKeys={};node('input-history-close').onclick=()=>{};
          fire('load',{target:{tagName:'SCRIPT',src:'/input-keys.js'}});
          fire('load',{target:{tagName:'SCRIPT',src:'/chat-shortcuts.js'}});
          assert.equal(health.snapshot().modules['input-keys'].status,'ready');
          assert.equal(health.snapshot().modules['chat-shortcuts'].status,'ready');
          assert.equal(readReloads(),0);
        })()""")

    def test_missing_updater_script_is_reported_and_recovers_only_when_loaded(self):
        self.run_case(r"""(async()=>{
          installAll();delete WorkspaceAppUpdates;health.attach(hooks);
          fire('error',{target:{tagName:'SCRIPT',src:'/app-updates.js'}});
          fire('DOMContentLoaded');await health.bootstrapReady();await flush();
          assert.equal(health.snapshot().modules['app-updates'].reason,'resource-error');
          assert.ok(health.snapshot().missing.includes('app-updates'));
          assert.ok(posted.some(row=>row.module==='app-updates'&&row.reason==='resource-error'));
          let blocked=0;fire('click',{target:node('app-update-check'),preventDefault(){blocked++;},stopImmediatePropagation(){blocked++;}});
          assert.equal(blocked,2);assert.equal(readReloads(),0);
          globalThis.WorkspaceAppUpdates={};fire('load',{target:{tagName:'SCRIPT',src:'/app-updates.js'}});
          assert.equal(health.snapshot().modules['app-updates'].status,'ready');
          assert.equal(health.snapshot().missing.length,0);
        })()""")

    def test_updater_global_requires_check_notes_and_install_handlers(self):
        self.run_case(r"""(async()=>{
          installAll();health.attach(hooks);fire('DOMContentLoaded');await health.bootstrapReady();
          for(const id of ['app-update-check','app-update-notes-open','app-update-install']){
            node(id).onclick=null;health.check();
            assert.equal(health.snapshot().modules['app-updates'].reason,'missing-handler');
            let blocked=0;fire('click',{target:node(id),preventDefault(){blocked++;},stopImmediatePropagation(){blocked++;}});
            assert.equal(blocked,2);node(id).onclick=()=>{};health.check();
            assert.equal(health.snapshot().modules['app-updates'].status,'ready');
          }
          assert.equal(readReloads(),0);
        })()""")

    def test_resource_and_runtime_failure_records_are_sanitized_and_bounded(self):
        self.run_case(r"""(async()=>{
          health.attach(hooks);fire('error',{target:{tagName:'SCRIPT',src:'http://127.0.0.1:1234/workflow.js?token=SECRET'}});
          fireWindow('error',{filename:'http://127.0.0.1:1234/desktop.js?private=SECRET',lineno:17,colno:2,message:'private prompt SECRET',error:{stack:'SECRET'}});
          for(let i=0;i<200;i++)fireWindow('unhandledrejection',{reason:'SECRET'});await flush();
          assert.equal(health.snapshot().modules.workflow.reason,'resource-error');assert.ok(health.snapshot().events.length<=64);assert.ok(posted.length<=12);
          assert.ok(posted.some(row=>row.module==='workflow'&&row.reason==='resource-error'));
          assert.ok(posted.some(row=>row.module==='desktop'&&row.line===17));assert.ok(!JSON.stringify(posted).includes('SECRET'));
          assert.equal(readReloads(),0);
        })()""")

    def test_early_resource_failure_waits_for_settled_aggregate(self):
        self.run_case(r"""(async()=>{
          health.attach(hooks);fire('error',{target:{tagName:'SCRIPT',src:'/workflow.js'}});await flush();
          assert.ok(posted.some(row=>row.module==='workflow'&&row.reason==='resource-error'));
          assert.equal(posted.filter(row=>row.event==='startup'&&row.status==='failed').length,0);
          assert.equal(health.snapshot().modules.capabilities.status,'pending');
          installAll();delete WorkspaceWorkflow;fire('DOMContentLoaded');await flush();
          assert.equal(posted.filter(row=>row.event==='startup'&&row.status==='failed').length,0);
          await health.bootstrapReady();await flush();
          const failed=posted.filter(row=>row.event==='startup'&&row.status==='failed');assert.equal(failed.length,1);
          assert.equal(JSON.stringify(failed[0].missing),JSON.stringify(['workflow']));
          assert.equal(health.snapshot().modules.capabilities.status,'ready');
        })()""")

    def test_late_script_after_failure_can_recover_and_existing_feature_is_not_rebound(self):
        self.run_case(r"""(async()=>{
          installAll();delete WorkspaceCapabilities;health.attach(hooks);await health.bootstrapReady();fire('DOMContentLoaded');
          const prior=node('desktop-open').onclick;assert.equal(health.snapshot().modules.capabilities.status,'failed');
          globalThis.WorkspaceCapabilities={};fire('load',{target:{tagName:'SCRIPT',src:'/capabilities.js'}});
          assert.equal(health.snapshot().modules.capabilities.status,'ready');assert.equal(node('desktop-open').onclick,prior);assert.equal(readReloads(),0);
        })()""")

    def test_explicit_recovery_saves_tab_draft_once_and_does_not_report_contents(self):
        self.run_case(r"""(async()=>{
          installAll();health.attach(hooks);await health.bootstrapReady();fire('DOMContentLoaded');
          assert.equal(await health.requestRecovery(),true);assert.equal(await health.requestRecovery(),false);
          assert.equal(readReloads(),1);const value=JSON.parse(storage.get('workspace.uiRecovery.v1'));
          assert.equal(value.sessionId,'A');assert.equal(value.drafts[0].text,'unsent 한국어');assert.equal(value.drafts[0].attachments[0],'C:/work/input.csv');
          await flush();assert.ok(!JSON.stringify(posted).includes('unsent'));assert.ok(!JSON.stringify(posted).includes('input.csv'));
        })()""")

    def test_storage_failure_or_pending_mutation_never_reloads_or_loses_draft(self):
        self.run_case(r"""(async()=>{
          health.attach({...hooks,canReload:()=>false});assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);
          health.attach(hooks);failStorage();assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);
          assert.match(node('startup-health-text').textContent,/다시 열지 않았어요/);
        })()""")

    def test_restore_waits_for_bootstrap_and_scripts_then_consumes_once(self):
        self.run_case(r"""(async()=>{
          storage.set('workspace.uiRecovery.v1',JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[{id:'A',text:'keep',attachments:['C:/input.png']}]}));
          installAll();health.attach(hooks);await health.bootstrapReady();assert.equal(restores.length,0);
          fire('DOMContentLoaded');await flush();assert.equal(restores.length,1);assert.equal(storage.has('workspace.uiRecovery.v1'),false);
          await health.bootstrapReady();fire('DOMContentLoaded');await flush();assert.equal(restores.length,1);assert.equal(readReloads(),0);
        })()""")

    def test_write_started_during_report_wait_cancels_reload_and_allows_retry(self):
        self.run_case(r"""(async()=>{
          installAll();let busy=false;health.attach({...hooks,canReload:()=>!busy});
          await health.bootstrapReady();fire('DOMContentLoaded');await flush();
          const recovery=health.requestRecovery();busy=true;
          assert.equal(await recovery,false);assert.equal(readReloads(),0);
          assert.equal(storage.has('workspace.uiRecovery.v1'),false);
          busy=false;assert.equal(await health.requestRecovery(),true);assert.equal(readReloads(),1);
        })()""")

    def test_recovery_captures_edits_and_selection_after_report_wait(self):
        self.run_case(r"""(async()=>{
          installAll();let selected='A',draft='original';
          health.attach({...hooks,capture:()=>({sessionId:selected,drafts:[{id:selected,text:draft,attachments:[]}]})});
          await health.bootstrapReady();fire('DOMContentLoaded');await flush();
          const recovery=health.requestRecovery();selected='B';draft='latest edit';
          assert.equal(await recovery,true);
          const saved=JSON.parse(storage.get('workspace.uiRecovery.v1'));
          assert.equal(saved.sessionId,'B');assert.equal(saved.drafts[0].text,'latest edit');
          assert.equal(readReloads(),1);
        })()""")

    def test_failed_restore_keeps_handoff_for_manual_retry(self):
        self.run_case(r"""(async()=>{
          storage.set('workspace.uiRecovery.v1',JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[{id:'A',text:'keep',attachments:[]}]}));
          installAll();health.attach({...hooks,restore:async()=>{throw Error('offline');}});fire('DOMContentLoaded');await health.bootstrapReady();
          assert.equal(storage.has('workspace.uiRecovery.v1'),true);assert.equal(readReloads(),0);assert.match(node('startup-health-text').textContent,/복원하지 못했어요/);
        })()""")

    def test_normal_runtime_action_error_is_visible_without_uninitializing_module(self):
        self.run_case(r"""(async()=>{
          installAll();health.attach(hooks);await health.bootstrapReady();fire('DOMContentLoaded');
          fireWindow('error',{filename:'/capabilities.js',message:'private path'});
          assert.equal(health.snapshot().modules.capabilities.status,'ready');assert.equal(node('startup-health').hidden,false);
          assert.equal(readReloads(),0);
        })()""")

    def test_timeout_does_not_automatically_reload_or_change_authentication(self):
        self.run_case(r"""(()=>{
          storage.set('workspaceToken','existing-auth');health.attach(hooks);advance(16000);
          assert.equal(health.snapshot().modules.app.reason,'timeout');assert.equal(storage.get('workspaceToken'),'existing-auth');assert.equal(readReloads(),0);
        })()""")

    def test_app_script_failure_can_report_without_app_bootstrap(self):
        self.run_case(r"""(async()=>{
          fire('error',{target:{tagName:'SCRIPT',src:'/app.js'}});await flush();
          assert.ok(directCalls.length>0);assert.ok(directCalls.every(call=>call.url==='/api/ui-health'&&call.options.keepalive===true));
          assert.ok(directCalls.some(call=>JSON.parse(call.options.body).reason==='resource-error'));
          assert.ok(directCalls.every(call=>!call.options.body.includes('TEST_AUTH_SECRET')));assert.equal(readReloads(),0);
        })()""")

    def test_manual_retry_after_failed_restore_retains_original_selected_task(self):
        self.run_case(r"""(async()=>{
          storage.set('workspace.uiRecovery.v1',JSON.stringify({version:1,savedAt:1000000,sessionId:'OLD',drafts:[{id:'OLD',text:'original',attachments:[]}]}));
          health.attach({...hooks,capture:()=>({sessionId:null,drafts:[]}),restore:async()=>{throw Error('offline');}});
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();await health.requestRecovery();
          const preserved=JSON.parse(storage.get('workspace.uiRecovery.v1'));assert.equal(preserved.sessionId,'OLD');assert.equal(preserved.drafts[0].text,'original');
        })()""")

    def test_failed_restore_clear_then_retry_preserves_empty_intent_and_other_drafts(self):
        self.run_case(r"""(async()=>{
          storage.set('workspace.uiRecovery.v1',JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[
            {id:'home',text:'old home draft',attachments:['C:/old.csv']},
            {id:'A',text:'saved A draft',attachments:[]},
            {id:'B',text:'saved B draft',attachments:['C:/B.csv']}]}));
          let cleared=false;
          health.attach({...hooks,capture:()=>({sessionId:null,drafts:cleared?[{id:'home',text:'',attachments:[]}]:[]}),
            restore:async()=>{throw Error('offline');}});
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();
          assert.equal(readReloads(),0);assert.equal(storage.has('workspace.uiRecovery.v1'),true);
          cleared=true;assert.equal(await health.requestRecovery(),true);
          const saved=JSON.parse(storage.get('workspace.uiRecovery.v1')),byId=new Map(saved.drafts.map(row=>[row.id,row]));
          assert.equal(byId.get('home').text,'');assert.equal(byId.get('home').attachments.length,0);
          assert.equal(byId.get('A').text,'saved A draft');assert.equal(byId.get('B').text,'saved B draft');
          assert.equal(byId.get('B').attachments[0],'C:/B.csv');assert.equal(saved.sessionId,'A');assert.equal(readReloads(),1);
        })()""")

    def test_empty_draft_handoff_still_obeys_fifty_draft_limit(self):
        self.run_case(r"""(async()=>{
          health.attach({...hooks,capture:()=>({sessionId:null,drafts:Array.from({length:51},(_,i)=>({id:`draft-${i}`,text:'',attachments:[]}))})});
          assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);
          assert.equal(storage.has('workspace.uiRecovery.v1'),false);
        })()""")

    def test_stash_snapshot_roundtrip_preserves_utf16_selection_and_attachments(self):
        self.run_case(r"""(async()=>{
          const saved={sessionId:'A',drafts:[],stashes:[{id:'A',text:'A😀한글',attachments:['C:/원본.png'],selectionStart:1,selectionEnd:5}]};
          health.attach({...hooks,capture:()=>saved});assert.equal(await health.requestRecovery(),true);
          assert.deepEqual(JSON.parse(storage.get('workspace.uiRecovery.v1')).stashes,JSON.parse(JSON.stringify(saved.stashes)));
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();await flush();
          assert.equal(restores.length,1);assert.deepEqual(restores[0].stashes,JSON.parse(JSON.stringify(saved.stashes)));
          assert.equal(storage.has('workspace.uiRecovery.v1'),false);
        })()""")

    def test_stash_invalid_shapes_and_total_utf8_size_block_reload_without_dropping_data(self):
        self.run_case(r"""(async()=>{
          const row={id:'A',text:'😀',attachments:[],selectionStart:0,selectionEnd:2};
          const cases=[null,{},[{...row,selectionEnd:3}],[{...row,selectionStart:true}],[{...row,selectionStart:-1}],
            [{...row,selectionStart:2,selectionEnd:1}],[{...row,arbitrary:1}],[row,{...row}],
            [{...row,attachments:['']}],Array.from({length:51},(_,i)=>({...row,id:'row-'+i}))];
          for(const stashes of cases){health.attach({...hooks,capture:()=>({sessionId:'A',drafts:[],stashes})});assert.equal(await health.requestRecovery(),false);}
          health.attach({...hooks,capture:()=>({sessionId:'A',drafts:[{id:'A',text:'a'.repeat(90000),attachments:[]}],stashes:[{...row,text:'한'.repeat(90000),selectionEnd:90000}]})});
          assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);assert.equal(storage.has('workspace.uiRecovery.v1'),false);
        })()""")

    def test_failed_restore_retry_merges_absent_stashes_and_keeps_identical_owner_once(self):
        self.run_case(r"""(async()=>{
          const row={id:'A',text:'earlier stash',attachments:['C:/keep.png'],selectionStart:2,selectionEnd:4};
          storage.set('workspace.uiRecovery.v1',JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[],stashes:[row,{...row,id:'C',text:'not yet restored'}]}));
          health.attach({...hooks,restore:async()=>{throw Error('offline');},capture:()=>({sessionId:'B',drafts:[],stashes:[
            {selectionEnd:4,selectionStart:2,attachments:['C:/keep.png'],text:'earlier stash',id:'A'},
            {id:'B',text:'new stash',attachments:[],selectionStart:0,selectionEnd:0}]})});
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();
          assert.equal(await health.requestRecovery(),true);
          const saved=JSON.parse(storage.get('workspace.uiRecovery.v1'));assert.equal(saved.stashes.length,3);
          assert.deepEqual(saved.stashes.find(value=>value.id==='A'),JSON.parse(JSON.stringify(row)));assert.equal(saved.stashes.find(value=>value.id==='B').text,'new stash');
          assert.equal(saved.stashes.find(value=>value.id==='C').text,'not yet restored');
        })()""")

    def test_failed_restore_retry_stash_conflict_retains_old_record_and_current_input(self):
        self.run_case(r"""(async()=>{
          const row={id:'A',text:'earlier stash',attachments:[],selectionStart:0,selectionEnd:0};
          const previous=JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[],stashes:[row]});
          storage.set('workspace.uiRecovery.v1',previous);
          const current={sessionId:'A',drafts:[],stashes:[{...row,text:'new stash'}]};
          health.attach({...hooks,restore:async()=>{throw Error('offline');},capture:()=>current});
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();
          assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);
          assert.equal(storage.get('workspace.uiRecovery.v1'),previous);assert.equal(current.stashes[0].text,'new stash');
          assert.match(node('startup-health-text').textContent,/보관한 입력.*달라/);
        })()""")

    def test_invalid_stash_recovery_record_is_preserved_and_cannot_be_overwritten(self):
        self.run_case(r"""(async()=>{
          const previous=JSON.stringify({version:1,savedAt:1000000,sessionId:'A',drafts:[],stashes:[{id:'A',text:'keep privately',attachments:[],selectionStart:0,selectionEnd:999}]});
          storage.set('workspace.uiRecovery.v1',previous);health.attach(hooks);
          installAll();fire('DOMContentLoaded');await health.bootstrapReady();await flush();
          assert.equal(restores.length,0);assert.equal(storage.get('workspace.uiRecovery.v1'),previous);
          assert.equal(await health.requestRecovery(),false);assert.equal(readReloads(),0);
          assert.equal(storage.get('workspace.uiRecovery.v1'),previous);
        })()""")


@unittest.skipUnless(NODE, "Node.js is required for UI recovery integration checks")
class WorkspaceScreenRecoveryIntegrationTests(unittest.TestCase):
    def run_case(self, script):
        harness = APP_HARNESS.replace(
            "vm.createContext(context);",
            "context.WorkspaceStartupHealth={attach(value){context.recoveryHooks=value;},bootstrapReady:async()=>{},bootstrapFailed(){}};vm.createContext(context);",
        )
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), script],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_capture_and_restore_selected_task_and_drafts_without_send_or_approval(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'approval',workspace:'C:/work'};sessions=[{id:'A'}];
          $('prompt').value='still writing';attachments=['C:/input.png'];drafts.set('B',{text:'another draft',attachments:[]});
          const saved=recoveryHooks.capture();assert.equal(saved.sessionId,'A');assert.equal(saved.drafts.length,2);
          active=null;drafts.clear();$('prompt').value='';attachments=[];let apiCalls=0;api=async()=>{apiCalls++;};
          let selected;selectSession=async(id,options)=>{selected={id,options};active={id,state:'approval'};return true;};
          await recoveryHooks.restore(saved);
          assert.equal(selected.id,'A');assert.equal(selected.options.keepDraft,true);
          assert.equal($('prompt').value,'still writing');assert.equal(attachments[0],'C:/input.png');assert.equal(drafts.get('B').text,'another draft');
          assert.equal(apiCalls,0);assert.equal(active.state,'approval');
        })()""")

    def test_capture_keeps_cleared_draft_without_erasing_unshown_saved_drafts(self):
        self.run_case(r"""(()=>{
          active=null;$('prompt').value='';attachments=[];
          drafts.set('home',{text:'saved but not displayed',attachments:['C:/home.csv']});
          drafts.set('B',{text:'other saved draft',attachments:['C:/B.csv']});
          let captured=new Map(recoveryHooks.capture().drafts.map(row=>[row.id,row]));
          assert.equal(captured.get('home').text,'saved but not displayed');
          // An input edit explicitly saved as empty must survive the retry merge.
          saveDraft();captured=new Map(recoveryHooks.capture().drafts.map(row=>[row.id,row]));
          assert.equal(captured.get('home').text,'');assert.equal(captured.get('home').attachments.length,0);
          assert.equal(captured.get('B').text,'other saved draft');assert.equal(captured.get('B').attachments[0],'C:/B.csv');
        })()""")

    def test_inflight_writes_prevent_reload_but_active_cli_work_does_not(self):
        self.run_case(r"""(()=>{
          active={id:'A',state:'running'};assert.equal(recoveryHooks.canReload(),true);
          sending=true;assert.equal(recoveryHooks.canReload(),false);sending=false;
          choiceSubmission={};assert.equal(recoveryHooks.canReload(),false);choiceSubmission=null;
          modelChanging=true;assert.equal(recoveryHooks.canReload(),false);modelChanging=false;
          globalThis.WorkspaceAttachments={isUploading:()=>true};assert.equal(recoveryHooks.canReload(),false);
        })()""")

    def test_deleted_task_restores_unsent_text_to_home_without_creating_task(self):
        self.run_case(r"""(async()=>{
          sessions=[];let apiCalls=0;api=async()=>{apiCalls++;};
          const result=await recoveryHooks.restore({sessionId:'gone',drafts:[{id:'gone',text:'retain this',attachments:['C:/source.csv']}]});
          assert.equal(result.missingSession,true);assert.equal($('prompt').value,'retain this');assert.equal(attachments[0],'C:/source.csv');assert.equal(apiCalls,0);assert.equal(active,null);
        })()""")

    def test_recovery_preserves_drafts_edited_while_bootstrap_waits(self):
        self.run_case(r"""(async()=>{
          active=null;sessions=[{id:'A'}];
          $('prompt').value='new home draft';attachments=['C:/new-home.csv'];saveDraft();
          drafts.set('B',{text:'new B draft',attachments:['C:/new-B.csv']});
          selectSession=async id=>{selectionGeneration++;active={id,state:'idle'};return true;};
          await recoveryHooks.restore({sessionId:'A',drafts:[
            {id:'home',text:'old home draft',attachments:['C:/old-home.csv']},
            {id:'A',text:'saved A draft',attachments:['C:/A.csv']},
            {id:'B',text:'old B draft',attachments:['C:/old-B.csv']}]});
          assert.equal(drafts.get('home').text,'new home draft');assert.equal(drafts.get('home').attachments[0],'C:/new-home.csv');
          assert.equal(drafts.get('B').text,'new B draft');assert.equal(drafts.get('B').attachments[0],'C:/new-B.csv');
          assert.equal(active.id,'A');assert.equal($('prompt').value,'saved A draft');assert.equal(attachments[0],'C:/A.csv');
        })()""")

    def test_recovery_preserves_intentionally_cleared_selected_draft(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle'};sessions=[{id:'A'}];
          $('prompt').value='';attachments=[];saveDraft();
          selectSession=async id=>{selectionGeneration++;active={id,state:'idle'};return true;};
          await recoveryHooks.restore({sessionId:'A',drafts:[{id:'A',text:'old A draft',attachments:['C:/old.csv']}]});
          assert.equal($('prompt').value,'');assert.equal(attachments.length,0);
          assert.equal(drafts.get('A').text,'');assert.equal(drafts.get('A').attachments.length,0);
        })()""")

    def test_missing_recovery_task_does_not_replace_new_or_cleared_home_draft(self):
        self.run_case(r"""(async()=>{
          sessions=[];
          for(const text of ['new home draft','']){
            active=null;drafts.clear();$('prompt').value=text;attachments=[];saveDraft();
            const result=await recoveryHooks.restore({sessionId:'gone',drafts:[
              {id:'home',text:'old home draft',attachments:['C:/old.csv']},
              {id:'gone',text:'missing task draft',attachments:['C:/gone.csv']}]});
            assert.equal(result.missingSession,true);assert.equal($('prompt').value,text);assert.equal(attachments.length,0);
            assert.equal(drafts.get('home').text,text);assert.equal(drafts.get('gone').text,'missing task draft');
          }
        })()""")

    def test_recovery_captures_visible_input_before_composer_saves_it(self):
        self.run_case(r"""(async()=>{
          active=null;sessions=[];$('prompt').value='typed before composer loaded';attachments=['C:/new.csv'];
          assert.equal(drafts.has('home'),false);
          await recoveryHooks.restore({sessionId:null,drafts:[{id:'home',text:'old home draft',attachments:['C:/old.csv']}]});
          assert.equal($('prompt').value,'typed before composer loaded');assert.equal(attachments[0],'C:/new.csv');
        })()""")

    def test_late_recovery_selection_cannot_overwrite_another_tasks_draft(self):
        self.run_case(r"""(async()=>{
          sessions=[{id:'A'},{id:'B'}];drafts.set('B',{text:'B original',attachments:['C:/B.csv']});
          let reply;api=async path=>{assert.equal(path,'/api/session?id=A');return new Promise(resolve=>reply=resolve);};
          const recovery=recoveryHooks.restore({sessionId:'A',drafts:[{id:'A',text:'A restored',attachments:['C:/A.csv']}]});
          selectionGeneration++;active={id:'B',state:'idle'};restoreDraft('B');
          reply({id:'A'});const result=await recovery;
          assert.equal(result.selectionChanged,true);assert.equal(active.id,'B');
          assert.equal($('prompt').value,'B original');assert.equal(attachments[0],'C:/B.csv');
          assert.equal(drafts.get('A').text,'A restored');
        })()""")

    def test_recovery_generation_check_preserves_new_edit_after_task_roundtrip(self):
        self.run_case(r"""(async()=>{
          sessions=[{id:'A'}];let finish;
          selectSession=async id=>{selectionGeneration++;active={id,state:'idle'};return new Promise(resolve=>finish=resolve);};
          const recovery=recoveryHooks.restore({sessionId:'A',drafts:[{id:'A',text:'old restored',attachments:['C:/old.csv']}]});
          selectionGeneration+=2;$('prompt').value='new A edit';attachments=['C:/new.csv'];
          finish(true);const result=await recovery;
          assert.equal(result.selectionChanged,true);assert.equal($('prompt').value,'new A edit');
          assert.equal(attachments[0],'C:/new.csv');assert.equal(drafts.get('A').text,'old restored');
        })()""")


if __name__ == "__main__":
    unittest.main()
