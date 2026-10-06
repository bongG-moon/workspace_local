"""The shipped upgrade UI preserves drafts before a coordinated app restart."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


PRELUDE = """
const upgradeId='1234567890abcdef1234567890abcdef';
const captureState={requestId:upgradeId,targetVersion:'0.21.6',stage:'capture',revision:1};
const capturedState={...captureState,stage:'captured',revision:2};
const calls=[];
api=async(path,data)=>{calls.push({path,data});return data.action==='capture'
  ?{ok:true,upgrade:capturedState}:{ok:true,upgrade:null};};
"""


@unittest.skipUnless(NODE, "Node.js is required for upgrade handoff UI checks")
class WorkspaceUpgradeFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        harness = HARNESS.replace(
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,",
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,TextEncoder,",
        ).replace(
            "querySelector:selector=>get(selector)",
            "querySelector:selector=>selector==='dialog[open]'?[...nodes.values()].find(n=>n.open)||null:get(selector)",
        ).replace(
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'upgrade-handoff.js'});\n"
            "vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context,{filename:'execution-mode.js'});\n"
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
        )
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), javascript,
             str(ROOT / "local_app/web/upgrade-handoff.js"), str(ROOT / "local_app/web/execution-mode.js")],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_captures_home_and_other_task_drafts_with_exact_attachments(self):
        self.run_case("(async()=>{" + PRELUDE + """
          drafts.set('other-task',{text:'다른 업무 초안',attachments:['D:/자료/표.xlsx']});
          $('prompt').value='보내지 않은 홈 요청';attachments=['D:/보고서 초안.docx'];
          await WorkspaceUpgrade.observe(captureState,{items:[],total:0});
          assert.equal(WorkspaceUpgrade.isLocked(),true);assert.equal($('upgrade-dialog').open,true);
          assert.equal(calls.length,1);assert.equal(calls[0].data.revision,1);
          const snapshot=calls[0].data.snapshot;
          assert.equal(snapshot.sessionId,null);assert.equal(snapshot.drafts.length,2);
          assert.deepEqual(snapshot.drafts.find(row=>row.id==='home'),{id:'home',text:'보내지 않은 홈 요청',attachments:['D:/보고서 초안.docx']});
          assert.deepEqual(snapshot.drafts.find(row=>row.id==='other-task').attachments,['D:/자료/표.xlsx']);
          assert.equal($('prompt').value,'보내지 않은 홈 요청');assert.equal(appClosed,false);
        })()""")

    def test_settings_keeps_its_draft_until_user_closes_it_for_version_capture(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='보내지 않은 요청';attachments=['C:/work/data.csv'];
          $('settings-dialog').open=true;
          WorkspaceExecutionMode.start('administrator');
          await WorkspaceUpgrade.observe(captureState,{});
          assert.equal(calls.length,0);assert.equal(WorkspaceUpgrade.isLocked(),false);
          assert.equal($('settings-dialog').open,true);
          $('settings-dialog').close();
          await WorkspaceUpgrade.observe(captureState,{});
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/upgrade');
          assert.equal(calls[0].data.action,'capture');
          const draft=calls[0].data.snapshot.drafts.find(row=>row.id==='home');
          assert.equal(draft.text,'보내지 않은 요청');assert.deepEqual(draft.attachments,['C:/work/data.csv']);
          assert.equal(WorkspaceUpgrade.isLocked(),true);assert.equal($('prompt').value,'보내지 않은 요청');
        })()""")

    def test_human_answers_take_priority_and_waiting_is_nonmodal(self):
        self.run_case("(async()=>{" + PRELUDE + """
          active={id:'A',state:'question'};
          await WorkspaceUpgrade.observe({...captureState,stage:'waiting'},{items:[{id:'Q'}],total:1});
          assert.match($('upgrade-notice-text').textContent,/현재 작업과 응답/);
          assert.equal(WorkspaceUpgrade.isLocked(),false);assert.equal(calls.length,0);
          await WorkspaceUpgrade.observe(captureState,{items:[],total:0});
          assert.equal(calls.length,0);assert.equal(WorkspaceUpgrade.isLocked(),false);
          active.state='idle';
          await WorkspaceUpgrade.observe(captureState,{items:[{id:'another-task-question'}],total:1});
          assert.equal(calls.length,0);assert.equal(WorkspaceUpgrade.isLocked(),false);
        })()""")

    def test_file_dialog_submission_and_inflight_request_finish_before_capture(self):
        self.run_case("(async()=>{" + PRELUDE + """
          attachmentPicking=true;await WorkspaceUpgrade.observe(captureState,{});assert.equal(calls.length,0);
          attachmentPicking=false;showDialog('folder-dialog');
          await WorkspaceUpgrade.observe(captureState,{});assert.equal(calls.length,0);$('folder-dialog').close();
          const done=WorkspaceUpgrade.begin('/api/session?id=A',false);
          await WorkspaceUpgrade.observe(captureState,{});assert.equal(calls.length,0);done();
          sending=true;await WorkspaceUpgrade.observe(captureState,{});assert.equal(calls.length,0);sending=false;
          await WorkspaceUpgrade.observe(captureState,{});assert.equal(calls.length,1);
        })()""")

    def test_cancel_unfreezes_and_ignores_a_late_captured_snapshot(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='그대로 유지';attachments=['D:/keep.csv'];
          await WorkspaceUpgrade.observe(captureState,{});
          api=async(path,data)=>{calls.push({path,data});return {ok:true,upgrade:{...captureState,stage:'cancelled',revision:3}};};
          assert.equal(await WorkspaceUpgrade.cancel(),true);
          await WorkspaceUpgrade.observe(capturedState,{});
          assert.equal(WorkspaceUpgrade.isLocked(),false);assert.equal($('upgrade-dialog').open,false);
          assert.equal($('prompt').value,'그대로 유지');assert.deepEqual(attachments,['D:/keep.csv']);
          assert.equal(appClosed,false);assert.equal(calls[1].data.action,'cancel');
        })()""")

    def test_failed_capture_cancels_without_quit_or_losing_draft(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='저장 실패 시 보존';let message='';toast=value=>message=value;
          api=async(path,data)=>{calls.push({path,data});if(data.action==='capture')throw new Error('저장 실패');return {ok:true,upgrade:null};};
          await WorkspaceUpgrade.observe(captureState,{});
          assert.deepEqual(calls.map(row=>row.data.action),['capture','cancel']);
          assert.equal(WorkspaceUpgrade.isLocked(),false);assert.equal($('prompt').value,'저장 실패 시 보존');
          assert.equal(message,'저장 실패');assert.equal(appClosed,false);
        })()""")

    def test_ambiguous_capture_and_cancel_keep_editing_paused_until_failed_state(self):
        self.run_case("(async()=>{" + PRELUDE + """
          api=async()=>{throw new Error('연결 확인 필요');};
          await WorkspaceUpgrade.observe(captureState,{});
          assert.equal(WorkspaceUpgrade.isLocked(),true);
          await WorkspaceUpgrade.observe({...captureState,stage:'failed',revision:3},{});
          assert.equal(WorkspaceUpgrade.isLocked(),false);assert.equal(appClosed,false);
        })()""")

    def test_utf8_limit_cancels_without_sending_a_truncated_snapshot(self):
        self.run_case("(async()=>{" + PRELUDE + """
          const text='한'.repeat(100000);$('prompt').value=text;
          await WorkspaceUpgrade.observe(captureState,{});
          assert.deepEqual(calls.map(row=>row.data.action),['cancel']);
          assert.equal($('prompt').value,text);assert.equal(WorkspaceUpgrade.isLocked(),false);
        })()""")

    def test_newer_work_revision_wins_over_a_delayed_capture_ack(self):
        self.run_case("(async()=>{" + PRELUDE + """
          let reply;api=()=>new Promise(resolve=>reply=resolve);
          const capturing=WorkspaceUpgrade.observe(captureState,{});
          assert.equal(WorkspaceUpgrade.isLocked(),true);
          await WorkspaceUpgrade.observe({...captureState,stage:'waiting',revision:3},{});
          assert.equal(WorkspaceUpgrade.isLocked(),false);
          reply({ok:true,upgrade:capturedState});await capturing;
          assert.equal(WorkspaceUpgrade.isLocked(),false);
          await WorkspaceUpgrade.observe(capturedState,{});assert.equal(WorkspaceUpgrade.isLocked(),false);
        })()""")

    def test_capture_revision_invalidation_recaptures_latest_draft(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='first';await WorkspaceUpgrade.observe(captureState,{});
          await WorkspaceUpgrade.observe({...captureState,stage:'waiting',revision:3},{});
          $('prompt').value='latest';
          api=async(path,data)=>{calls.push({path,data});return {ok:true,upgrade:{...capturedState,revision:5}};};
          await WorkspaceUpgrade.observe({...captureState,revision:4},{});
          assert.equal(calls.length,2);assert.equal(calls[1].data.revision,4);
          assert.equal(calls[1].data.snapshot.drafts[0].text,'latest');assert.equal(WorkspaceUpgrade.isLocked(),true);
        })()""")

    def test_http_busy_capture_reply_waits_without_cancelling_handoff(self):
        self.run_case("(async()=>{" + PRELUDE + """
          api=async(path,data)=>{calls.push({path,data});const error=new Error('busy');
            error.upgrade={...captureState,stage:'waiting',revision:3};throw error;};
          await WorkspaceUpgrade.observe(captureState,{});
          assert.equal(calls.length,1);assert.equal(calls[0].data.action,'capture');
          assert.equal(WorkspaceUpgrade.isLocked(),false);assert.match($('upgrade-notice-text').textContent,/현재 작업과 응답/);
        })()""")

    def test_restore_preserves_newly_edited_home_draft_and_never_sends_it(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='새 창에서 이미 작성한 내용';attachments=['new.csv'];
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:null,drafts:[
            {id:'home',text:'old draft',attachments:['old.csv']},{id:'A',text:'saved task',attachments:['A.csv']}]}}});
          assert.equal($('prompt').value,'새 창에서 이미 작성한 내용');assert.deepEqual(attachments,['new.csv']);
          assert.equal(drafts.get('A').text,'saved task');
          assert.equal(calls.length,0);assert.match($('upgrade-notice-text').textContent,/별도로 보관/);
          $('upgrade-recovery-open').onclick();
          assert.equal($('upgrade-recovery-text').value,'old draft');
          assert.equal($('upgrade-recovery-files').textContent,'old.csv');
          assert.equal($('prompt').value,'새 창에서 이미 작성한 내용');
          assert.equal(appClosed,false);
        })()""")

    def test_missing_task_restores_to_home_and_acknowledges_only_after_display(self):
        self.run_case("(async()=>{" + PRELUDE + """
          api=async(path,data)=>{assert.equal($('prompt').value,'다시 이어갈 요청');calls.push({path,data});return {ok:true};};
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:'missing',drafts:[
            {id:'missing',text:'다시 이어갈 요청',attachments:['D:/계획.xlsx']}]}}});
          assert.deepEqual(attachments,['D:/계획.xlsx']);assert.equal(calls[0].data.action,'restored');
        })()""")

    def test_conflicting_stashes_are_available_separately_from_drafts_in_recovery(self):
        self.run_case("(async()=>{" + PRELUDE + """
          globalThis.WorkspaceShortcuts={restoreStashes:()=>['home']};
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:null,
            drafts:[{id:'home',text:'ordinary draft',attachments:['draft.csv']}],
            stashes:[{id:'home',text:'stashed request',attachments:['stash.csv'],selectionStart:1,selectionEnd:3}]}}});
          assert.equal(calls.length,0);assert.equal($('prompt').value,'ordinary draft');
          $('upgrade-recovery-open').onclick();
          const options=$('upgrade-recovery-task').children;assert.equal(options.length,2);
          assert.equal(options[0].textContent,'업무 홈');assert.equal(options[1].textContent,'업무 홈 · 임시 보관');
          assert.equal($('upgrade-recovery-text').value,'ordinary draft');
          $('upgrade-recovery-task').value='1';$('upgrade-recovery-task').onchange();
          assert.equal($('upgrade-recovery-text').value,'stashed request');assert.equal($('upgrade-recovery-files').textContent,'stash.csv');
          assert.equal($('prompt').value,'ordinary draft');assert.equal(calls.length,0);
        })()""")

    def test_missing_stash_module_keeps_record_and_exposes_stash_only_snapshot_for_copy(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='newly typed';attachments=['new.csv'];
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:null,drafts:[],
            stashes:[{id:'home',text:'saved only in stash',attachments:['keep.csv'],selectionStart:0,selectionEnd:0}]}}});
          assert.equal(calls.length,0);assert.equal($('prompt').value,'newly typed');assert.deepEqual(attachments,['new.csv']);
          assert.match($('upgrade-notice-text').textContent,/복원/);$('upgrade-recovery-open').onclick();
          assert.equal($('upgrade-recovery-task').children.length,1);
          assert.equal($('upgrade-recovery-task').children[0].textContent,'업무 홈 · 임시 보관');
          assert.equal($('upgrade-recovery-text').value,'saved only in stash');
          assert.equal($('upgrade-recovery-files').textContent,'keep.csv');assert.equal(calls.length,0);
        })()""")

    def test_task_restore_displays_original_task_without_sending_or_losing_home_edit(self):
        self.run_case("(async()=>{" + PRELUDE + """
          sessions=[{id:'task-A',title:'이전 업무',state:'idle',workspace:'D:/work'}];
          $('prompt').value='새 창 홈 초안';attachments=['new.csv'];
          poll=async()=>{};refreshFiles=async()=>{};refreshResults=async()=>{};
          api=async(path,data)=>{calls.push({path,data});if(path.startsWith('/api/session?'))return {...sessions[0],messages:[],requests:[]};
            assert.equal($('prompt').value,'이전 업무 초안');return {ok:true};};
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:'task-A',drafts:[
            {id:'task-A',text:'이전 업무 초안',attachments:['D:/source.pdf']}]}}});
          assert.equal(active.id,'task-A');assert.deepEqual(attachments,['D:/source.pdf']);
          assert.equal(drafts.get('home').text,'새 창 홈 초안');assert.deepEqual(drafts.get('home').attachments,['new.csv']);
          assert.deepEqual(calls.map(row=>row.path),['/api/session?id=task-A','/api/upgrade']);
        })()""")

    def test_lock_blocks_new_mutations_but_attention_and_upgrade_remain_available(self):
        self.run_case("(async()=>{" + PRELUDE + """
          await WorkspaceUpgrade.observe(captureState,{});
          assert.throws(()=>WorkspaceUpgrade.begin('/api/send',true),/전환/);
          assert.throws(()=>WorkspaceUpgrade.begin('/api/create',true),/전환/);
          assert.doesNotThrow(()=>WorkspaceUpgrade.begin('/api/upgrade',true)());
          assert.doesNotThrow(()=>WorkspaceUpgrade.begin('/api/attention/bind',true)());
          await WorkspaceUpgrade.observe({...captureState,stage:'expired',revision:3},{});
          assert.doesNotThrow(()=>WorkspaceUpgrade.begin('/api/send',true)());
        })()""")

    def test_restore_failure_does_not_consume_saved_snapshot(self):
        self.run_case("(async()=>{" + PRELUDE + """
          WorkspaceUpgrade.attach({api:async(path,data)=>calls.push({path,data}),capture:captureScreenRecovery,
            restore:async()=>{throw new Error('display failed');},canCapture:()=>true});
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:null,drafts:[]}}});
          assert.equal(calls.length,0);assert.match($('upgrade-notice-text').textContent,/복원/);
        })()""")

    def test_bootstrap_warning_is_persistent_without_blocking_work(self):
        self.run_case("(async()=>{" + PRELUDE + """
          await WorkspaceUpgrade.bootstrap({upgradeWarning:'이전 창의 작성 내용을 확인하지 못했습니다. 저장 기록은 보존했습니다.'});
          assert.equal($('upgrade-notice').hidden,false);assert.match($('upgrade-notice-text').textContent,/저장 기록은 보존/);
          await WorkspaceUpgrade.observe(null,{});
          assert.equal($('upgrade-notice').hidden,false);assert.equal(WorkspaceUpgrade.isLocked(),false);
          assert.equal(calls.length,0);
        })()""")

    def test_saved_conflicting_draft_released_only_after_explicit_confirmation(self):
        self.run_case("(async()=>{" + PRELUDE + """
          $('prompt').value='new edit';
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:upgradeId,snapshot:{sessionId:null,drafts:[
            {id:'home',text:'old edit',attachments:['old.csv']}]}}});
          $('upgrade-recovery-open').onclick();confirmAction=async()=>false;
          await $('upgrade-recovery-release').onclick();assert.equal(calls.length,0);
          assert.equal($('upgrade-recovery-dialog').open,true);assert.equal($('prompt').value,'new edit');
          confirmAction=async()=>true;await $('upgrade-recovery-release').onclick();
          assert.deepEqual(calls.map(row=>row.data.action),['restored']);
          assert.equal($('upgrade-recovery-dialog').open,false);assert.equal($('prompt').value,'new edit');
          assert.equal($('upgrade-notice').hidden,true);
        })()""")

    def test_shipped_page_loads_handoff_before_app_and_reuses_attention_loop(self):
        page=(ROOT / 'local_app/web/index.html').read_text(encoding='utf-8')
        self.assertLess(page.index('/upgrade-handoff.js'),page.index('/app.js'))
        self.assertIn('upgrade-handoff.css',page)
        attention=(ROOT / 'local_app/web/attention.js').read_text(encoding='utf-8')
        self.assertIn('WorkspaceUpgrade?.observe(snapshot.upgrade,snapshot)',attention)
        script=(ROOT / 'local_app/web/upgrade-handoff.js').read_text(encoding='utf-8')
        self.assertNotIn('setInterval(',script)
        self.assertNotIn('setTimeout(',script)


if __name__ == '__main__':
    unittest.main()
