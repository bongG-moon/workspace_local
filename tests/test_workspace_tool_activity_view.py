"""Tool evidence stays visible without changing the work or executing code."""
import subprocess
import unittest

from test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, "Node required")
class ToolActivityViewTests(unittest.TestCase):
    def run_case(self, body):
        harness = HARNESS.replace(
            "const scenario=process.argv[3];",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context);\nconst scenario=process.argv[3];",
        )
        setup = r"""
          active={id:'A',state:'running',workspace:'C:/task',messages:[],lastRunId:'run-a'};
          const conversation=$('conversation');
          conversation.insertBefore=function(node,next){node.remove();const index=this.children.indexOf(next);node.parent=this;this.children.splice(index,0,node);};
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
          const detail=conversation.children[0].querySelector('details');
          assert.ok(!detail.open);assert.equal(detail.querySelector('ol').children.length,0);
          WorkspaceToolActivity.render({...record,state:'running'});
          assert.equal($('status-text').textContent,'자료 읽기 중 · report.csv');
          WorkspaceToolActivity.render({...record,id:'skill-a',tool:'Skill',target:'plugin:report',action:'스킬 사용'});
          assert.ok($('status-text').textContent.includes('다른 도구 1개'));
          assert.equal(calls,0);assert.equal(conversation.children.length,1);
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
          $('tool-activity-open').onclick();
          assert.ok(conversation.children[0].querySelector('details').open);
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

    def test_record_and_dom_limits_literal_text_and_lazy_history(self):
        self.run_case(r"""
          const danger='<img src=x onerror=alert(1)>';
          for(let n=0;n<100;n++)WorkspaceToolActivity.render({...record,id:'tool-'+n,state:'completed',target:danger,parentToolUseId:'parent'});
          assert.equal(active.toolActivity.length,80);assert.equal(conversation.children.length,1);
          const detail=conversation.children[0].querySelector('details');
          assert.equal(detail.querySelector('ol').children.length,0);
          detail.open=true;detail.ontoggle();assert.equal(detail.querySelector('ol').children.length,80);
          assert.equal(detail.querySelector('.tool-activity-target').textContent,danger);
          assert.equal(detail.querySelector('img'),null);
          assert.equal(detail.querySelector('.tool-activity-child').textContent,'추가 작업자의 활동');
          detail.open=false;detail.ontoggle();assert.equal(detail.querySelector('ol').children.length,0);
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
