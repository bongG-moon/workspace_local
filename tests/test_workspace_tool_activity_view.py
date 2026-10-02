"""Tool evidence stays visible without changing the work or executing code."""
import subprocess
import unittest

from test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, "Node required")
class ToolActivityViewTests(unittest.TestCase):
    def run_case(self, body):
        harness = HARNESS.replace(
            "const nodes=new Map();",
            "Element.prototype.insertBefore=function(node,next){node.remove();const index=next?this.children.indexOf(next):this.children.length;node.parent=this;node.isConnected=true;this.children.splice(index,0,node);};\nconst nodes=new Map();",
        ).replace(
            "const scenario=process.argv[3];",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context);\nconst scenario=process.argv[3];",
        )
        setup = r"""
          active={id:'A',state:'running',workspace:'C:/task',messages:[],lastRunId:'run-a'};
          const conversation=$('conversation');
          WorkspaceToolActivity.reset('A');
          WorkspaceToolActivity.restore([]);setStatus('running');
          const record={id:'read-a',runId:'run-a',tool:'Read',action:'자료 읽기',target:'report.csv',state:'requested',startedAt:100};
        """
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), setup + body,
             str(ROOT / "local_app/web/tool-activity.js")],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_requested_and_running_are_distinct_and_do_not_start_work(self):
        self.run_case(r"""
          let calls=0;api=async()=>{calls++;};
          WorkspaceToolActivity.render(record);
          assert.equal($('status-text').textContent,'자료 읽기 요청 · report.csv');
          assert.equal($('tool-activity-badge').textContent,'Read');
          assert.equal(conversation.children.length,1);
          const list=conversation.children[0].querySelector('ol');
          assert.equal(list.children.length,1);assert.equal(conversation.children[0].querySelector('details'),null);
          const first=list.children[0];
          WorkspaceToolActivity.render({...record,state:'running'});
          assert.equal($('status-text').textContent,'자료 읽기 중 · report.csv');
          assert.equal(list.children[0],first);assert.equal(first.querySelector('.tool-activity-state').textContent,'실행 중');
          WorkspaceToolActivity.render({...record,id:'skill-a',tool:'Skill',target:'plugin:report',action:'스킬 사용'});
          assert.ok($('status-text').textContent.includes('다른 도구 1개'));
          assert.equal(calls,0);assert.equal(conversation.children.length,1);
          assert.equal(list.children.length,2);
          assert.ok(!conversation.children[0].scrolledIntoView);
        """)

    def test_approval_question_and_choice_override_activity_without_losing_history(self):
        self.run_case(r"""
          WorkspaceToolActivity.render({...record,state:'running'});setStatus('approval');
          WorkspaceToolActivity.render({...record,target:'new.csv',state:'running'});
          assert.equal($('status-text').textContent,statusLabels.approval);
          assert.equal($('tool-activity-badge').hidden,true);assert.equal(active.state,'approval');
          setStatus('question');assert.equal($('status-text').textContent,statusLabels.question);
          setStatus('running');assert.ok($('status-text').textContent.includes('new.csv'));
          active.choice={id:'choice-a'};WorkspaceToolActivity.sync();
          assert.equal($('status-text').textContent,'다음 단계 선택을 기다려요');
          assert.equal($('tool-activity-badge').hidden,true);
          active.choice=null;WorkspaceToolActivity.sync();
          let opened;globalThis.WorkspaceProgressView={open:run=>{opened=run;return true;}};
          $('tool-activity-open').onclick();
          assert.equal(opened,'run-a');
          assert.equal(conversation.children[0].querySelector('ol').children.length,1);
        """)

    def test_terminal_results_never_claim_whole_task_success_and_do_not_regress(self):
        self.run_case(r"""
          WorkspaceToolActivity.render({...record,tool:'Agent',target:'자료 확인'});
          WorkspaceToolActivity.render({...record,tool:'Agent',target:'자료 확인',state:'completed',finishedAt:101});
          WorkspaceToolActivity.render({...record,state:'requested'});
          assert.equal(active.toolActivity[0].state,'completed');
          assert.equal($('status-text').textContent,'최근 응답: 자료 읽기 · 자료 확인');
          assert.equal(conversation.children[0].querySelector('.tool-activity-group-state').textContent,'결과 수신');
          WorkspaceToolActivity.render({...record,id:'unresolved'});setStatus('stopped');
          assert.equal(active.toolActivity[1].state,'interrupted');
          assert.equal($('status-text').textContent,statusLabels.stopped);
          assert.ok($('tool-activity-badge').hidden);
          assert.equal(conversation.children[0].querySelector('.tool-activity-group-state').textContent,'일부 결과 미확인');
        """)

    def test_explicit_cli_status_survives_ui_refresh_until_next_tool_evidence(self):
        self.run_case(r"""
          WorkspaceToolActivity.render({...record,state:'running'});
          setStatus('running','대화 내용을 정리하고 있어요');
          assert.equal($('status-text').textContent,'대화 내용을 정리하고 있어요');
          setStatus('running');assert.equal($('status-text').textContent,'대화 내용을 정리하고 있어요');
          assert.ok($('tool-activity-badge').hidden);
          WorkspaceToolActivity.render({...record,state:'completed'});
          assert.equal($('status-text').textContent,'최근 응답: 자료 읽기 · report.csv');
        """)

    def test_new_request_cannot_reuse_old_running_or_replay_old_history(self):
        self.run_case(r"""
          WorkspaceToolActivity.render(record);setStatus('done');
          setStatus('running');assert.equal($('status-text').textContent,statusLabels.running);
          assert.ok($('tool-activity-open').hidden);
          WorkspaceToolActivity.render({...record,id:'old-late',state:'running'});
          assert.equal($('status-text').textContent,statusLabels.running);
          handleEvent({type:'status',data:{state:'running',runId:'run-b'}});
          assert.equal(active.lastRunId,'run-b');
          handleEvent({type:'tool_activity',data:{...record,id:'new',runId:'run-b',target:'new.csv'}});
          assert.equal($('status-text').textContent,'자료 읽기 요청 · new.csv');
          assert.equal(conversation.children.length,2);
          setStatus('idle');modelChanging=true;setStatus('idle');
          assert.equal($('status-text').textContent,statusLabels.idle);assert.ok($('tool-activity-badge').hidden);
        """)

    def test_terminal_event_snapshot_applies_results_before_marking_unresolved(self):
        self.run_case(r"""
          WorkspaceToolActivity.render(record);
          handleEvent({type:'status',data:{state:'stopped',toolActivity:[{...record,state:'completed',finishedAt:101}]}});
          assert.equal(active.toolActivity[0].state,'completed');
          assert.equal($('status-text').textContent,statusLabels.stopped);
        """)

    def test_history_restores_by_run_and_session_switch_clears_live_view(self):
        self.run_case(r"""
          active.state='done';WorkspaceToolActivity.reset('A');
          for(const run of ['run-a','run-b']){
            renderMessage({role:'user',text:'question',runId:run});
            renderMessage({role:'assistant',text:'answer',runId:run});
          }
          WorkspaceToolActivity.restore(['run-a','run-b'].map(run=>({...record,id:run,runId:run,state:'completed'})));
          const order=conversation.children.map(node=>`${node.dataset.runId}:${node.classList.contains('tool-activity-card')?'activity':node.classList.contains('user')?'user':'assistant'}`);
          assert.equal(order.join('|'),'run-a:user|run-a:activity|run-a:assistant|run-b:user|run-b:activity|run-b:assistant');
          active={id:'B',state:'running'};WorkspaceToolActivity.render({...record,id:'wrong'});
          assert.equal(conversation.children.length,6);
          WorkspaceToolActivity.reset('B');setStatus('running');
          assert.equal($('status-text').textContent,statusLabels.running);assert.ok($('tool-activity-open').hidden);
        """)

    def test_record_and_visible_preview_limits_keep_literal_text_and_name_older_history(self):
        self.run_case(r"""
          const danger='<img src=x onerror=alert(1)>';
          for(let n=0;n<100;n++)WorkspaceToolActivity.render({...record,id:'tool-'+n,state:'completed',target:danger,parentToolUseId:'parent'});
          assert.equal(active.toolActivity.length,80);assert.equal(conversation.children.length,1);
          const detail=conversation.children[0].querySelector('.tool-activity-detail');
          assert.equal(detail.querySelector('ol').children.length,6);
          assert.equal(detail.querySelector('.tool-activity-target').textContent,danger);
          assert.equal(detail.querySelector('img'),null);
          assert.equal(detail.querySelector('.tool-activity-child').textContent,'추가 작업자');
          assert.equal(detail.querySelector('.tool-activity-note').hidden,false);
          assert.match(detail.querySelector('.tool-activity-note').textContent,/이전 활동/);
          assert.equal(detail.querySelector('.tool-activity-count').textContent,'80+');
          WorkspaceToolActivity.reset();assert.ok($('tool-activity-badge').hidden);assert.ok($('tool-activity-open').hidden);
        """)

    def test_stopped_request_without_reply_keeps_tools_before_the_next_request(self):
        self.run_case(r"""
          active.state='done';WorkspaceToolActivity.reset('A');
          renderMessage({role:'user',text:'stopped question',runId:'run-a'});
          renderMessage({role:'user',text:'next question',runId:'run-b'});
          renderMessage({role:'assistant',text:'answer',runId:'run-b'});
          WorkspaceToolActivity.restore([{...record,state:'interrupted'},{...record,id:'b',runId:'run-b',state:'completed'}]);
          const order=conversation.children.map(node=>`${node.dataset.runId}:${node.classList.contains('tool-activity-card')?'activity':node.classList.contains('user')?'user':'assistant'}`);
          assert.equal(order.join('|'),'run-a:user|run-a:activity|run-b:user|run-b:activity|run-b:assistant');
        """)

    def test_tool_event_preserves_scroll_when_user_reads_previous_messages(self):
        self.run_case(r"""
          const area=$('work-area');area.scrollTop=3;area.scrollHeight=200;area.clientHeight=100;
          handleEvent({type:'tool_activity',data:record});
          assert.equal(area.scrollTop,3);
        """)

    def test_pending_rows_survive_completed_eviction_and_preview_reuses_nodes(self):
        self.run_case(r"""
          WorkspaceToolActivity.render({...record,state:'running'});
          const list=conversation.children[0].querySelector('ol'),first=list.children[0];
          for(let n=0;n<100;n++)WorkspaceToolActivity.render({...record,id:'done-'+n,state:'completed'});
          assert.equal(active.toolActivity.length,80);assert.equal(list.children.length,6);
          assert.equal(list.children[0],first);assert.equal(first.dataset.activityId,'read-a');
          assert.equal(active.toolActivity[0].id,'read-a');
          assert.equal(list.children.at(-1).dataset.activityId,'done-99');
          for(let n=0;n<8;n++)WorkspaceToolActivity.render({...record,id:'pending-'+n});
          assert.equal(list.children.length,6);assert.ok(list.children.every(node=>node.dataset.state==='requested'));
          assert.match(conversation.children[0].querySelector('.tool-activity-note').textContent,/진행·대기 9개 중 6개/);
        """)

    def test_actual_status_updates_without_rerendering_cards_focus_or_scrolling(self):
        self.run_case(r"""
          WorkspaceToolActivity.render(record);const row=conversation.children[0].querySelector('ol').children[0];
          const area=$('work-area');area.scrollTop=3;area.scrollHeight=200;area.clientHeight=100;$('prompt').focus();
          const activity={runId:'run-a',phase:'tool_preparing',label:'자료 읽기 준비 중',tool:'Read',updatedAt:1};
          handleEvent({type:'run_activity',data:activity});
          assert.equal($('status-text').textContent,'자료 읽기 준비 중');assert.equal(active.state,'running');
          assert.equal(row.dataset.state,'requested');assert.equal(area.scrollTop,3);assert.equal(document.activeElement,$('prompt'));
          const status=$('status-text');let writes=0,label=status.textContent;
          Object.defineProperty(status,'textContent',{get:()=>label,set:value=>{writes++;label=value;}});
          for(let n=2;n<102;n++)handleEvent({type:'run_activity',data:{...activity,updatedAt:n}});
          assert.equal(writes,0);assert.equal(conversation.children[0].querySelector('ol').children[0],row);
          handleEvent({type:'run_activity',data:{...activity,phase:'answering',label:'답변 작성 중',tool:'',updatedAt:102}});
          assert.equal(label,'답변 작성 중');assert.ok($('tool-activity-badge').hidden);
          handleEvent({type:'assistant',data:{runId:'run-a',text:'자료 확인을 마쳤고 다음 항목을 확인합니다.'}});
          assert.equal(conversation.children[1].querySelector('.message-body').querySelector('p').firstChild.textContent,'자료 확인을 마쳤고 다음 항목을 확인합니다.');
        """)

    def test_actual_status_respects_human_requests_and_rejects_stale_or_terminal_runs(self):
        self.run_case(r"""
          const activity={runId:'run-a',phase:'tool_running',label:'자료 읽기 진행 중',tool:'Read',updatedAt:10};
          assert.equal(WorkspaceToolActivity.runActivity(activity),true);
          setStatus('approval');WorkspaceToolActivity.runActivity({...activity,updatedAt:11});
          assert.equal($('status-text').textContent,statusLabels.approval);
          setStatus('question');assert.equal($('status-text').textContent,statusLabels.question);
          setStatus('running');assert.equal($('status-text').textContent,activity.label);
          active.choice={id:'choose'};WorkspaceToolActivity.sync();
          WorkspaceToolActivity.runActivity({...activity,updatedAt:12});assert.equal($('status-text').textContent,'다음 단계 선택을 기다려요');
          active.choice=null;WorkspaceToolActivity.sync();
          assert.equal(WorkspaceToolActivity.runActivity({...activity,updatedAt:9,label:'old'}),false);
          assert.equal(WorkspaceToolActivity.runActivity({...activity,runId:'run-old',updatedAt:99}),false);
          assert.equal(WorkspaceToolActivity.runActivity({...activity,phase:'imagined'}),false);
          setStatus('done');assert.equal(active.runActivity,null);
          assert.equal(WorkspaceToolActivity.runActivity({...activity,updatedAt:99}),false);
          setStatus('running');assert.equal(WorkspaceToolActivity.runActivity({...activity,updatedAt:100}),false);
          handleEvent({type:'status',data:{state:'running',runId:'run-b'}});
          assert.equal(WorkspaceToolActivity.runActivity({...activity,runId:'run-b',updatedAt:101}),true);
          assert.equal(WorkspaceToolActivity.runActivity({...activity,updatedAt:102}),false);
        """)

    def test_restored_actual_status_is_only_live_for_the_running_request(self):
        self.run_case(r"""
          const activity={runId:'run-a',phase:'answering',label:'답변 작성 중',updatedAt:10};
          WorkspaceToolActivity.reset('A');WorkspaceToolActivity.restore([{...record,state:'completed'}],activity);setStatus('running');
          assert.equal($('status-text').textContent,activity.label);
          active.state='done';WorkspaceToolActivity.reset('A');WorkspaceToolActivity.restore([record],activity);setStatus('done');
          assert.equal($('status-text').textContent,statusLabels.done);assert.equal(active.runActivity,null);
          assert.equal(active.toolActivity[0].state,'interrupted');
        """)
