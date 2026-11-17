"""A removed task must not become the target of another conversation's send."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import threading
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE
from tests.test_workspace_session_visibility_frontend import SETUP
from local_app.server import LocalApp, Server

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(NODE, 'Node.js is needed for shipped frontend checks')
class ArchiveSendFrontendTests(unittest.TestCase):
    def run_case(self, kind):
        code = SETUP + r"""
        (async()=>{
          const targetKind=KIND;
          const selected={...sessions[1],kind:targetKind,trusted:true,requests:[],artifacts:[],messages:[]};
          Object.assign(sessions[1],selected);
          const calls=[];
          poll=()=>{};refreshInspector=()=>{};refreshSessionMeta=async()=>{};
          api=async(path,body)=>{
            calls.push({path,body});
            if(path==='/api/session/hide')return {ok:true,hiddenId:'A',historyPreserved:true};
            if(path==='/api/session?id=B')return {...selected,messages:[]};
            if(path==='/api/send')return {ok:true};
            throw Error('unexpected endpoint '+path);
          };
          const removing=remove('A').onclick();$('action-confirm').onclick();
          assert.equal(await removing,true);assert.equal(active,null);
          assert.equal(await selectSession('B'),true);
          $('prompt').value='새로운 요청';
          await submit();
          assert.equal(active.id,'B');
          const sends=calls.filter(call=>call.path==='/api/send');
          assert.equal(sends.length,1);assert.equal(sends[0].body.id,'B');
          assert.equal(sends[0].body.text,'새로운 요청');
          assert.equal(calls.filter(call=>call.path==='/api/session?id=A').length,0);
          assert.equal(notices.some(text=>text.includes('보관한 업무')),false);
        })()
        """.replace('KIND', repr(kind))
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), code],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_sending_another_work_after_removal_keeps_the_new_id(self):
        self.run_case('work')

    def test_sending_a_general_chat_after_removal_keeps_the_new_id(self):
        self.run_case('chat')

    def run_persisted_case(self, kind):
        with tempfile.TemporaryDirectory() as temporary:
            app = LocalApp(Path(temporary) / 'state', demo=True)
            (Path(temporary) / 'work').mkdir()
            (Path(temporary) / 'other').mkdir()
            archived_id = app.create(str(Path(temporary) / 'work'), True)['id']
            selected_id = (app.create(kind='chat') if kind == 'chat'
                           else app.create(str(Path(temporary) / 'other'), True))['id']
            server = Server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                harness = HARNESS.replace(
                    "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
                    """context.backendApi=async(path,body)=>{
                      const reply=await fetch(process.env.ARCHIVE_TEST_ORIGIN+path,{method:body?'POST':'GET',
                        headers:{Authorization:'Bearer '+process.env.ARCHIVE_TEST_TOKEN,'Content-Type':'application/json'},
                        body:body?JSON.stringify(body):undefined});
                      const value=await reply.json();if(!reply.ok)throw Error(value.error);return value;
                    };
                    vm.runInContext(fs.readFileSync(process.argv[2].replace('app.js','drafts.js'),'utf8'),context,{filename:'drafts.js'});
                    vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});""")
                setup = SETUP.replace("sessions=['A','B']", 'sessions=' + json.dumps([archived_id, selected_id]))
                code = setup + r"""
                (async()=>{
                  const target={...sessions[1],kind:KIND,trusted:true,requests:[],artifacts:[],messages:[]};
                  Object.assign(sessions[1],target);const sends=[],posts=[];
                  poll=()=>{};refreshInspector=()=>{};refreshSessionMeta=async()=>{};
                  api=async(path,body)=>{
                    if(path==='/api/drafts'||path==='/api/session/hide'){
                      if(body&&path==='/api/drafts')posts.push(body.id);
                      return backendApi(path,body);
                    }
                    if(path==='/api/session?id='+target.id)return {...target,messages:[]};
                    if(path==='/api/send'){sends.push(body.id);return {ok:true};}
                    throw Error('unexpected endpoint '+path);
                  };
                  WorkspaceDraftPersistence.attach({api:(...args)=>api(...args),notify:text=>notices.push(text),
                    selected:()=>active?.id||homeDraftKey(),stashes:()=>[],restoreStashes(){}});
                  await WorkspaceDraftPersistence.load();
                  $('prompt').value='복원할 보관 업무 초안';
                  const removing=remove(ARCHIVED_ID).onclick();$('action-confirm').onclick();
                  assert.equal(await removing,true);
                  // Flush a row dirtied by showHome after the task was hidden.
                  await WorkspaceDraftPersistence.flush();
                  assert.equal(WorkspaceDraftPersistence.hasPending(),false);
                  await selectSession(target.id);
                  for(let i=0;i<3;i++){
                    $('prompt').value='다른 대화의 요청 '+i;saveDraft();await submit();
                    await WorkspaceDraftPersistence.flush();
                    assert.equal(WorkspaceDraftPersistence.hasPending(),false);
                  }
                  assert.deepEqual(sends,[target.id,target.id,target.id]);
                  assert.equal(posts.filter(id=>id===ARCHIVED_ID).length,1);
                  assert.equal(notices.some(text=>text.includes('보관한 업무')),false);
                  assert.equal(drafts.get(ARCHIVED_ID).text,'복원할 보관 업무 초안');
                  const persisted=await backendApi('/api/drafts');
                  const archived=persisted.entries.find(row=>row.id===ARCHIVED_ID);
                  assert.equal(archived.draft.text,'복원할 보관 업무 초안');
                })()
                """.replace('ARCHIVED_ID', json.dumps(archived_id)).replace('KIND', json.dumps(kind))
                environment = dict(os.environ, ARCHIVE_TEST_ORIGIN=server.origin, ARCHIVE_TEST_TOKEN=app.token)
                result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), code],
                    input=harness, text=True, encoding='utf-8', capture_output=True, timeout=20, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                self.assertTrue(app.session_visibility.contains(archived_id))
                self.assertIsNone(app.sessions[archived_id].get('bridge'))
            finally:
                app.close()
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_archived_draft_flushes_before_and_after_other_work_sends(self):
        self.run_persisted_case('work')

    def test_archived_draft_flushes_before_and_after_general_chat_sends(self):
        self.run_persisted_case('chat')


if __name__ == '__main__':
    unittest.main()
