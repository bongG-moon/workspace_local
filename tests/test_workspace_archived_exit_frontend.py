"""Archived autosave recovery must unblock native quit and update handoff.

Run the shipped draft, quit and upgrade JavaScript against an isolated real
HTTP server. One legacy archived-save rejection leaves the draft writer dirty;
the following exit/update skips that obsolete write and saves the current draft.
No installed app, real tray, model or installer is started.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_app.server import LocalApp, Server
from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, 'Node.js is needed for shipped exit/update checks')
class ArchivedExitFrontendTests(unittest.TestCase):
    def run_case(self, action):
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='archived-exit-', dir=ROOT / 'build') as raw:
            root = Path(raw)
            app = LocalApp(root / 'state', demo=True)
            archived = app.create(str(root), True)['id']
            target = app.create(str(root), True)['id']
            app.save_draft({'id': archived, 'revision': 0, 'selectedId': target,
                'draft': {'text': '기존에 저장된 보관 업무 초안', 'attachments': []}, 'stash': None})
            confirmation = app.begin_quit_confirmation()
            save = app.save_draft
            rejected = []

            def reject_legacy_save_once(body):
                if body.get('id') == archived and not rejected:
                    rejected.append(archived)
                    raise ValueError('보관한 업무입니다. 설정의 보관한 업무에서 목록으로 복원할 수 있어요.')
                return save(body)

            server = Server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                harness = HARNESS.replace(
                    'const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,',
                    'const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,TextEncoder,'
                    'nextTurn:()=>new Promise(setImmediate),',
                ).replace(
                    'querySelector:selector=>get(selector),querySelectorAll:()=>[],addEventListener(){}},',
                    "querySelector:selector=>selector.startsWith('dialog[open]')?"
                    "[...nodes.values()].find(node=>node.open)||null:get(selector),"
                    'querySelectorAll:()=>[],addEventListener(){}},',
                ).replace(
                    "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
                    """context.backendApi=async(path,body)=>{
                      const reply=await fetch(process.env.ARCHIVED_EXIT_ORIGIN+path,{method:body?'POST':'GET',
                        headers:{Authorization:'Bearer '+process.env.ARCHIVED_EXIT_TOKEN,'Content-Type':'application/json'},
                        body:body?JSON.stringify(body):undefined});
                      const value=await reply.json();if(!reply.ok)throw Object.assign(Error(value.error),value);return value;
                    };
                    vm.runInContext(fs.readFileSync(process.argv[2].replace('app.js','drafts.js'),'utf8'),context,{filename:'drafts.js'});
                    vm.runInContext(fs.readFileSync(process.argv[2].replace('app.js','upgrade-handoff.js'),'utf8'),context,{filename:'upgrade-handoff.js'});
                    vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});""",
                )
                scenario = r"""
                (async()=>{
                  const archived=ARCHIVED,target=TARGET,notices=[],calls=[];
                  toast=value=>notices.push(value);
                  api=async(path,body)=>{calls.push({path,body});return backendApi(path,body);};
                  WorkspaceDraftPersistence.attach({api:(...args)=>api(...args),notify:toast,
                    selected:()=>active?.id||homeDraftKey(),stashes:()=>[],restoreStashes(){}});
                  active={id:target,kind:'work',title:'현재 업무',workspace:'C:/fixture',state:'idle',messages:[]};
                  sessions=[active];boot={};
                  await WorkspaceDraftPersistence.load();
                  assert.equal(WorkspaceDraftPersistence.isReady(),true,JSON.stringify(notices));
                  // Another screen can archive a task after this screen loads.
                  await backendApi('/api/session/hide',{id:archived,confirmed:true});
                  drafts.set(archived,{text:'보관한 업무의 미전송 초안',attachments:[]});
                  await assert.rejects(WorkspaceDraftPersistence.flush(),/보관한 업무/);
                  assert.equal(WorkspaceDraftPersistence.hasPending(),true);
                  assert.equal(appClosed,false);
                  $('prompt').value='현재 업무의 미전송 초안';
                  if(ACTION==='quit'){
                    assert.equal(confirmShutdownChallenge(CONFIRMATION),true);
                    $('action-confirm').onclick();
                    const deadline=Date.now()+5000;
                    while(Date.now()<deadline&&(nativeQuitConfirmation!==null||quitting))await nextTurn();
                    assert.equal(nativeQuitConfirmation,null);
                    assert.equal(quitting,false);
                    assert.equal(shutdownState,'closed');
                    assert.equal(appClosed,true);
                    assert.equal($('action-dialog').open,false);
                    const quit=calls.filter(row=>row.path==='/api/quit');
                    assert.equal(quit.length,1);
                    assert.deepEqual(quit[0].body,{confirmed:true,confirmationId:CONFIRMATION});
                    // Shutdown renders save the same composer again. That
                    // identical dirty marker needs no request to the closed
                    // server and must not leave unsaved content behind.
                    const draftsBefore=calls.filter(row=>row.path==='/api/drafts').length;
                    await WorkspaceDraftPersistence.flush();
                    assert.equal(calls.filter(row=>row.path==='/api/drafts').length,draftsBefore);
                  }else{
                    await WorkspaceUpgrade.bootstrap({});
                    const requestId='a'.repeat(32);
                    const prepared=await backendApi('/api/upgrade',{action:'prepare',requestId,targetVersion:'9.0.0'});
                    assert.equal(prepared.upgrade.stage,'capture');
                    await WorkspaceUpgrade.observe(prepared.upgrade,{items:[],total:0});
                    const captured=await backendApi('/api/upgrade?requestId='+requestId);
                    assert.equal(captured.upgrade.stage,'captured');
                    const capture=calls.filter(row=>row.path==='/api/upgrade'&&row.body?.action==='capture');
                    assert.equal(capture.length,1);
                    assert.equal(capture[0].body.snapshot.drafts.some(row=>row.id===archived),false);
                    assert.equal(capture[0].body.snapshot.drafts.find(row=>row.id===target).text,'현재 업무의 미전송 초안');
                    const committed=await backendApi('/api/upgrade',{action:'commit',requestId,targetVersion:'9.0.0'});
                    assert.equal(committed.closed,true);
                    assert.equal(committed.upgrade.stage,'closed');
                  }
                  assert.equal(WorkspaceDraftPersistence.hasPending(),false);
                  assert.equal(drafts.has(archived),false);
                  assert.equal($('prompt').value,'현재 업무의 미전송 초안');
                  assert.equal(calls.filter(row=>row.path==='/api/drafts'&&row.body?.id===archived).length,2);
                  assert.equal(calls.some(row=>row.path==='/api/send'),false);
                })()
                """.replace('ARCHIVED', json.dumps(archived)).replace('TARGET', json.dumps(target))
                scenario = scenario.replace('CONFIRMATION', json.dumps(confirmation)).replace('ACTION', json.dumps(action))
                environment = dict(os.environ, ARCHIVED_EXIT_ORIGIN=server.origin, ARCHIVED_EXIT_TOKEN=app.token)
                with patch.object(app, 'save_draft', side_effect=reject_legacy_save_once):
                    result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), scenario],
                        input=harness, text=True, encoding='utf-8', capture_output=True, timeout=20, env=environment)
                self.assertEqual(0, result.returncode, result.stderr or result.stdout)
                self.assertEqual([archived], rejected)
                self.assertEqual('closed', app.shutdown_status()['shutdownState'])
                self.assertTrue(app.session_visibility.contains(archived))
                saved = {row['id']: row for row in app.drafts.snapshot()['entries']}
                self.assertEqual('기존에 저장된 보관 업무 초안', saved[archived]['draft']['text'])
                self.assertEqual('현재 업무의 미전송 초안', saved[target]['draft']['text'])
                self.assertIsNone(app.sessions[archived].get('bridge'))
                self.assertEqual([], app.sessions[target]['messages'])
                thread.join(3)
                self.assertFalse(thread.is_alive())
            finally:
                app.close()
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_native_tray_quit_skips_previously_rejected_archived_autosave(self):
        self.run_case('quit')

    def test_update_capture_and_commit_skip_previously_rejected_archived_autosave(self):
        self.run_case('upgrade')


if __name__ == '__main__':
    unittest.main()
