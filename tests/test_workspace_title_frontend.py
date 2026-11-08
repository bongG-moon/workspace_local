"""The most recent title revision wins without touching the open rename input."""
import subprocess
import unittest

from tests.test_workspace_attention_frontend import HARNESS, NODE, ROOT, SETUP


@unittest.skipUnless(NODE, "Node.js is required for title synchronization checks")
class SessionTitleFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script,
                                 str(ROOT/'local_app/web/attention.js')], input=HARNESS,
                                text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_delayed_metadata_cannot_revert_a_user_rename(self):
        self.run_case(r"""(async()=>{
          active.titleRevision=0;sessions[0].titleRevision=0;
          let release;api=async(path)=>path.startsWith('/api/session?')?
            new Promise(resolve=>release=resolve):{session:{id:'A',title:'직접 정한 이름',titleRevision:2}};
          const reading=refreshSessionMeta();
          await updateSession('A',{title:'직접 정한 이름'});
          release({...active,title:'예전 자동명',titleRevision:1,messages:[]});await reading;
          assert.equal(active.title,'직접 정한 이름');assert.equal(sessions[0].title,active.title);
          assert.equal($('chat-title').textContent,active.title);
        })()""")

    def test_inactive_task_title_refresh_does_not_change_active_or_editor(self):
        self.run_case(r"""(async()=>{
          openRename(active);$('rename-input').value='작성 중';
          let snapshot=[{id:'B',state:'running',title:'B 결과 검토',titleRevision:1}];
          api=async()=>({...attention(),sessions:snapshot});
          WorkspaceAttention.start();await flush();
          assert.equal(sessions[1].title,'B 결과 검토');assert.equal(active.title,'업무 A');
          snapshot=[{id:'A',state:'idle',title:'A 결과 검토',titleRevision:1}];
          await WorkspaceAttention.refresh();assert.equal(active.title,'A 결과 검토');
          assert.equal($('rename-input').value,'작성 중');
          handleEvent({type:'title_updated',data:{id:'A',title:'A 최신 제목',titleRevision:2}});
          handleEvent({type:'title_updated',data:{id:'A',title:'묵은 제목',titleRevision:1}});
          assert.equal($('chat-title').textContent,'A 최신 제목');
        })()""")

    def test_editor_untouched_or_explicitly_retyped_original(self):
        self.run_case(r"""(async()=>{
          let writes=[];api=async(path,data)=>{writes.push(data);return {session:{id:'A',title:data.title,titleRevision:3}}};
          const submit=()=>$('rename-form').onsubmit({preventDefault(){},submitter:{value:'ok'}});
          openRename(active);
          handleEvent({type:'title_updated',data:{id:'A',title:'자동 업무명',titleRevision:1}});
          await submit();assert.equal(writes.length,0);assert.equal(active.title,'자동 업무명');
          openRename(active);$('rename-input').value='편집 중';$('rename-input').oninput();
          $('rename-input').value='자동 업무명';$('rename-input').oninput();
          handleEvent({type:'title_updated',data:{id:'A',title:'또 다른 이름',titleRevision:2}});
          await submit();assert.equal(writes.length,1);assert.equal(writes[0].title,'자동 업무명');
        })()""")
