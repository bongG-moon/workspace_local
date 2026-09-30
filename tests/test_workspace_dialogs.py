"""Exercise app confirmations, modal feedback, and complete task navigation.

All session data and HTTP replies are synthetic; no local profile is touched.
"""
from pathlib import Path
import subprocess
import unittest

from test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
Object.defineProperty(Element.prototype, 'innerHTML', {
  set() { throw new Error('Dialog text must never become HTML'); }
});
context.walk = function walk(node) {
  return [node, ...(node.children || []).flatMap(walk)];
};
context.flush = () => new Promise(resolve => setImmediate(resolve));
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), context, {filename:'companion.js'});
const scenario=process.argv[3];
"""
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", SETUP)


@unittest.skipUnless(NODE, "Node.js is required for dialog regression checks")
class WorkspaceDialogTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), javascript,
             str(ROOT / "local_app/web/companion.js")],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_confirmation_esc_cancels_and_keeps_underlying_dialog_and_focus(self):
        self.run_case(r"""(async()=>{
          showDialog('settings-dialog');$('quit').focus();
          let calls=0;api=async()=>{calls++;return {closed:true};};
          const pending=$('quit').onclick();
          assert.equal(calls,0);assert.equal(appClosed,false);
          assert.equal($('settings-dialog').open,true);assert.equal($('action-dialog').open,true);
          assert.equal(document.activeElement,$('action-cancel'));
          let prevented=false;$('action-dialog').oncancel({preventDefault(){prevented=true;}});
          await pending;
          assert.equal(prevented,true);assert.equal(calls,0);assert.equal(appClosed,false);
          assert.equal($('settings-dialog').open,true);assert.equal($('action-dialog').open,false);
          assert.equal(document.activeElement,$('quit'));
        })()""")

    def test_confirmation_safe_text_and_repeated_open_cannot_share_approval(self):
        self.run_case(r"""(async()=>{
          const pending=confirmAction({title:'<img src=x>',message:'<script>unsafe()</script>',confirmLabel:'실행'});
          assert.equal($('action-title').textContent,'<img src=x>');
          assert.equal($('action-message').textContent,'<script>unsafe()</script>');
          assert.equal(await confirmAction({title:'another',message:'another'}),false);
          assert.equal($('action-title').textContent,'<img src=x>');
          $('action-confirm').onclick();assert.equal(await pending,true);
          const next=confirmAction({title:'new',message:'new'});$('action-close').onclick();
          assert.equal(await next,false);
        })()""")

    def test_feedback_uses_topmost_open_dialog_then_returns_to_body(self):
        self.run_case(r"""(async()=>{
          showDialog('folder-dialog');toast('폴더 확인 오류');
          assert.equal($('toast').parent,$('folder-dialog'));
          assert.equal($('toast').classList.contains('in-dialog'),true);
          assert.equal($('toast').scrolledIntoView,true);
          const pending=confirmAction({title:'확인',message:'내용'});toast('현재 확인 오류');
          assert.equal($('toast').parent,$('action-dialog'));
          $('action-cancel').onclick();await pending;toast('폴더 다시 확인');
          assert.equal($('toast').parent,$('folder-dialog'));
          $('folder-dialog').close();toast('화면 안내');
          assert.equal($('toast').parent,document.body);
          assert.equal($('toast').classList.contains('in-dialog'),false);
        })()""")

    def test_older_tasks_are_searchable_and_selectable_beyond_four_recents(self):
        self.run_case(r"""(async()=>{
          sessions=Array.from({length:6},(_,i)=>({id:String(i),title:'업무 '+i,
            workspace:'C:\\fixture\\folder'+i,state:'done',updated:100-i}));
          renderSessions();assert.equal($('home-recents').children.length,4);
          assert.equal($('tasks-open').hidden,false);$('tasks-open').onclick();
          assert.equal($('tasks-dialog').open,true);assert.equal($('all-sessions').children.length,6);
          $('task-search').value='folder5';$('task-search').oninput();
          assert.equal($('all-sessions').children.length,1);
          assert.equal($('all-sessions').children[0].querySelector('.session-title').textContent,'업무 5');
          let selected;selectSession=async id=>{selected=id;return true;};
          await $('all-sessions').children[0].querySelector('.all-session').onclick();
          assert.equal(selected,'5');assert.equal($('tasks-dialog').open,false);
        })()""")

    def test_stale_task_selection_cannot_close_list_before_latest_selection_fails(self):
        self.run_case(r"""(async()=>{
          sessions=['A','B'].map(id=>({id,title:'업무 '+id,workspace:'C:\\fixture\\'+id,
            state:'done',messages:[],updated:1}));
          renderSessions();$('tasks-open').onclick();
          const pending=[];api=path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
          const first=$('all-sessions').children[0].querySelector('.all-session').onclick();
          const second=$('all-sessions').children[1].querySelector('.all-session').onclick();
          pending[0].resolve(sessions[0]);await first;
          assert.equal(active,null);assert.equal($('tasks-dialog').open,true);
          pending[1].reject(new Error('최근 선택 연결 실패'));await second;
          assert.equal(active,null);assert.equal($('tasks-dialog').open,true);
          assert.equal($('toast').parent,$('tasks-dialog'));
          assert.equal($('toast').textContent,'최근 선택 연결 실패');
        })()""")

    def test_latest_success_closes_task_list_and_stale_error_cannot_replace_feedback(self):
        self.run_case(r"""(async()=>{
          sessions=['A','B'].map(id=>({id,title:'업무 '+id,workspace:'C:\\fixture\\'+id,
            state:'done',messages:[],updated:1}));
          renderSessions();$('tasks-open').onclick();
          refreshFiles=async()=>{};refreshResults=async()=>{};poll=async()=>{};
          const pending=[];api=path=>new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
          const first=$('all-sessions').children[0].querySelector('.all-session').onclick();
          const second=$('all-sessions').children[1].querySelector('.all-session').onclick();
          pending[1].resolve(sessions[1]);await second;
          assert.equal(active.id,'B');assert.equal($('tasks-dialog').open,false);
          toast('현재 업무 B');pending[0].reject(new Error('오래된 A 실패'));await first;
          assert.equal(active.id,'B');assert.equal($('toast').textContent,'현재 업무 B');
          assert.equal($('tasks-dialog').open,false);
        })()""")

    def test_example_cancel_preserves_companion_and_draft_then_accept_replaces_only_text(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle',trusted:true};$('prompt').value='작성 중';attachments=['keep.csv'];
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {course:{steps:[
            {id:'read',title:'연습',concept:'가상 내용',prompt:'새 예문',check:'결과 확인',recovery:'다시 확인'}]}};};
          await $('learn-open').onclick();
          const compose=walk($('companion-content')).find(node=>node.textContent==='입력창에 넣기');
          const cancelled=compose.onclick();
          assert.equal($('companion-dialog').open,true);assert.equal($('prompt').value,'작성 중');
          $('action-cancel').onclick();await cancelled;
          assert.equal($('companion-dialog').open,true);assert.equal($('prompt').value,'작성 중');
          assert.equal(compose.disabled,false);
          const approved=compose.onclick();$('action-confirm').onclick();await approved;
          assert.equal($('companion-dialog').open,false);assert.equal($('prompt').value,'새 예문');
          assert.equal(attachments[0],'keep.csv');assert.ok(calls.every(call=>call.body===undefined));
        })()""")

    def test_companion_clear_requires_confirmation_and_rechecks_selected_task(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle',trusted:true};const mutations=[];
          api=async(path,body)=>{if(body){mutations.push(body);return {notice:'완료'};}return {};};
          await $('learn-open').onclick();
          await walk($('companion-tabs')).find(node=>node.textContent==='준비·결과 확인').onclick();
          const clear=walk($('companion-content')).find(node=>node.textContent==='이 폴더의 확인 기록만 비우기');
          const cancelled=clear.onclick();assert.equal(mutations.length,0);
          $('action-cancel').onclick();await cancelled;assert.equal(mutations.length,0);
          const changed=clear.onclick();active={id:'B',state:'idle',trusted:true};
          $('action-confirm').onclick();await changed;assert.equal(mutations.length,0);
          active={id:'A',state:'idle',trusted:true};const accepted=clear.onclick();
          assert.equal(mutations.length,0);$('action-confirm').onclick();await accepted;
          assert.equal(mutations.length,1);assert.equal(mutations[0].action,'records-clear');
          assert.equal(mutations[0].id,'A');assert.equal(mutations[0].confirmed,true);
        })()""")


if __name__ == '__main__':
    unittest.main()
