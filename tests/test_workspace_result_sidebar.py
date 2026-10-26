"""Unread results stay distinct from execution, attention and navigation state."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import NODE
from tests.test_workspace_session_groups_frontend import HARNESS


ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
boot={demo:false};active=null;
const unread=(revision=2,seenRevision=1)=>({revision,seenRevision,runId:'run-1'});
WorkspaceResultReceipts={unread:item=>!!item?.resultReceipt&&item.resultReceipt.revision>item.resultReceipt.seenRevision};
const task=(id,state,receipt)=>({id,title:`업무 ${id}`,state,resultReceipt:receipt,workspace:`C:/fixture/${id}`,created:1,updated:1});
const sidebar=id=>$('sessions').children.find(row=>row.dataset.sessionId===id)?.querySelector('.session');
const dot=id=>sidebar(id).querySelector('.session-state-dot');
const recent=id=>$('home-recents').children.find(row=>row.dataset.sessionId===id)?.querySelector('.recent-card');
"""


@unittest.skipUnless(NODE, 'Node.js is required for sidebar state regression checks')
class WorkspaceResultSidebarTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script,
             str(ROOT/'local_app/web/session-groups.js')],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_completed_unread_is_visible_and_read_or_legacy_rows_are_quiet(self):
        self.run_case(r"""
          sessions=[task('A','done',unread()),task('B','done',unread(2,2)),task('C','done')];
          renderSessions();
          assert.equal(dot('A').dataset.state,'unread');
          assert.equal(sidebar('A').classList.contains('session-unread'),true);
          assert.match(sidebar('A').getAttribute('aria-label'),/새 결과 있음/);
          assert.equal(dot('B'),null);assert.equal(dot('C'),null);
          assert.equal(sidebar('B').classList.contains('session-unread'),false);
          assert.equal(sidebar('C').classList.contains('session-unread'),false);
          for(const id of ['A','B','C'])assert.ok(sidebar(id).querySelector('.session-state-mark'));
          assert.equal(sessions[0].resultReceipt.seenRevision,1);
        """)

    def test_execution_dot_keeps_priority_without_erasing_unread_emphasis(self):
        self.run_case(r"""
          sessions=[task('A','running',unread()),task('B','starting',unread())];
          renderSessions();
          assert.equal(dot('A').dataset.state,'running');assert.equal(dot('B').dataset.state,'starting');
          assert.equal(sidebar('A').classList.contains('session-unread'),true);
          assert.match(sidebar('A').getAttribute('aria-label'),/진행 중.*새 결과 있음/);
          assert.equal(sessions[0].state,'running');assert.equal(sessions[0].resultReceipt.seenRevision,1);
        """)

    def test_attention_and_error_priority_preserve_unread_result(self):
        self.run_case(r"""
          sessions=[task('A','approval',unread()),task('B','error',unread())];
          WorkspaceAttention={countFor:id=>id==='A'?2:0};renderSessions();
          assert.equal(sidebar('A').querySelector('.session-attention').textContent,'2');
          assert.equal(dot('A'),null);assert.equal(dot('B').dataset.state,'error');
          assert.match(sidebar('A').getAttribute('aria-label'),/응답 대기 2.*새 결과 있음/);
          assert.equal(sidebar('A').classList.contains('session-unread'),true);
          assert.equal(sidebar('B').classList.contains('session-unread'),true);
        """)

    def test_recent_and_all_task_views_expose_same_result_without_changing_selection(self):
        self.run_case(r"""
          sessions=[task('A','done',unread()),task('B','done',unread(2,2))];active=sessions[1];
          renderSessions();renderAllSessions();
          assert.equal(recent('A').classList.contains('session-unread'),true);
          assert.match(recent('A').getAttribute('aria-label'),/새 결과 있음/);
          const all=$('all-sessions').children.find(row=>row.dataset.sessionId==='A').querySelector('.all-session');
          assert.match(flatText(all.querySelector('.session-meta')),/새 결과 있음/);
          assert.equal(all.classList.contains('session-unread'),true);
          assert.equal(active.id,'B');assert.equal(sessions[0].resultReceipt.seenRevision,1);
          sessions[0].resultReceipt.seenRevision=2;renderSessions();renderAllSessions();
          assert.equal(dot('A'),null);assert.doesNotMatch(recent('A').getAttribute('aria-label'),/새 결과/);
        """)

    def test_collapsed_folder_exposes_unread_without_changing_member_count_or_opening(self):
        self.run_case(r"""
          sessions=[task('A','done',unread()),task('B','done',unread(2,2))];
          boot.sessionGroups={groups:[{id:'folder',name:'보고서',collapsed:true}],assignments:{A:'folder',B:'folder'},revision:1};
          renderSessions();
          let group=$('sessions').children.find(row=>row.dataset.groupId==='folder');
          assert.equal(group.querySelector('.task-group-count').textContent,'2');
          assert.equal(group.querySelector('.task-group-unread').dataset.state,'unread');
          assert.equal(group.querySelector('.task-group-toggle').getAttribute('aria-expanded'),'false');
          assert.match(group.querySelector('.task-group-toggle').getAttribute('aria-label'),/새 결과 있음/);
          assert.equal(sidebar('A'),undefined);assert.equal(boot.sessionGroups.revision,1);
          sessions[0].resultReceipt.seenRevision=2;renderSessions();
          group=$('sessions').children.find(row=>row.dataset.groupId==='folder');
          assert.equal(group.querySelector('.task-group-unread'),null);
          assert.equal(group.querySelector('.task-group-count').textContent,'2');
        """)

    def test_chat_and_work_keep_separate_results_and_existing_running_counts(self):
        self.run_case(r"""
          sessions=[task('A','done',unread()),{...task('B','running',unread()),kind:'chat'}];
          currentMode='work';renderSessions();assert.ok(dot('A'));assert.equal(sidebar('B'),undefined);
          currentMode='chat';renderSessions();assert.equal(sidebar('A'),undefined);assert.equal(dot('B').dataset.state,'running');
          assert.equal(sidebar('B').classList.contains('session-unread'),true);
        """)


if __name__ == '__main__':
    unittest.main()
