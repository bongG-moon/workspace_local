"""Real draft, shortcut and handoff modules share durable recovery decisions."""
import json
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
LOAD = r'''
context.fixture=JSON.parse(process.argv[6]);context.requests=[];
context.fetch=async(route,options={})=>{
  const body=options.body?JSON.parse(options.body):null;context.requests.push({route,body});
  return {ok:true,json:async()=>route==='/api/bootstrap'?{sessions:[],demo:true}:
    route==='/api/drafts'?(body?{id:body.id,revision:body.revision+1}:context.fixture):{ok:true}};
};
for(const file of JSON.parse(process.argv[4]))vm.runInContext(fs.readFileSync(file,'utf8'),context,{filename:file});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});
for(const file of JSON.parse(process.argv[5]))vm.runInContext(fs.readFileSync(file,'utf8'),context,{filename:file});
'''


@unittest.skipUnless(NODE, 'Node.js is required')
class DraftRecoveryIntegrationTests(unittest.TestCase):
    def run_case(self, scenario, entries=()):
        harness = HARNESS.replace('Date,Map,Set,', 'Date,Map,Set,TextEncoder,').replace(
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});", LOAD)
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), '(async()=>{'+scenario+'})()',
            json.dumps([str(ROOT/'local_app/web/drafts.js'), str(ROOT/'local_app/web/upgrade-handoff.js')]),
            json.dumps([str(ROOT/'local_app/web/chat-shortcuts.js')]),
            json.dumps({'entries':list(entries), 'selectedId':'home'})], input=harness,
            text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_durable_stash_tombstone_rejects_stale_handoff(self):
        self.run_case(r'''
          assert.equal(WorkspaceDraftPersistence.isReady(),true);
          const conflicts=WorkspaceShortcuts.restoreStashes([{id:'home',text:'old stash',attachments:['old.zip'],selectionStart:0,selectionEnd:3}]);
          assert.deepEqual(conflicts,['home']);assert.equal(WorkspaceShortcuts.exportStashes().length,0);
          await WorkspaceDraftPersistence.flush();
          assert.equal(requests.filter(row=>row.route==='/api/drafts'&&row.body).length,0);
        ''', [{'id':'home','revision':2,'draft':None,'stash':None}])

    def test_stash_cleared_during_debounce_rejects_old_handoff(self):
        self.run_case(r'''
          assert.equal(WorkspaceShortcuts.exportStashes().length,1);
          $('prompt').focus();
          WorkspaceShortcuts.keydown({key:'s',ctrlKey:true,preventDefault(){}});
          assert.equal($('prompt').value,'saved stash');assert.equal(WorkspaceShortcuts.exportStashes().length,0);
          const conflicts=WorkspaceShortcuts.restoreStashes([{id:'home',text:'saved stash',attachments:['saved.zip'],selectionStart:0,selectionEnd:3}]);
          assert.deepEqual(conflicts,['home']);assert.equal(WorkspaceShortcuts.exportStashes().length,0);
          await WorkspaceDraftPersistence.flush();
          const writes=requests.filter(row=>row.route==='/api/drafts'&&row.body);
          assert.equal(writes.at(-1).body.stash,null);assert.equal(writes.at(-1).body.draft.text,'saved stash');
        ''', [{'id':'home','revision':2,'draft':None,
               'stash':{'text':'saved stash','attachments':['saved.zip'],'selectionStart':0,'selectionEnd':3}}])

    def test_archived_stash_is_loaded_and_survives_later_draft_write(self):
        owner='00000000-0000-4000-8000-000000000123'
        self.run_case(r'''
          const owner='00000000-0000-4000-8000-000000000123';
          assert.equal(sessions.length,0);assert.equal(WorkspaceShortcuts.exportStashes()[0].id,owner);
          drafts.set(owner,{text:'new draft',attachments:[]});await WorkspaceDraftPersistence.flush();
          const write=requests.filter(row=>row.route==='/api/drafts'&&row.body).at(-1).body;
          assert.equal(write.id,owner);assert.equal(write.stash.text,'archived stash');assert.equal(write.stash.attachments[0],'protected.zip');
        ''', [{'id':owner,'revision':2,'draft':None,
               'stash':{'text':'archived stash','attachments':['protected.zip'],'selectionStart':0,'selectionEnd':3}}])

    def test_thousands_of_durable_empty_rows_do_not_inflate_handoff(self):
        self.run_case(r'''
          fixture.entries=Array.from({length:4000},(_,i)=>({id:`00000000-0000-4000-8000-${String(i).padStart(12,'0')}`,revision:2,draft:null,stash:null}));
          await WorkspaceDraftPersistence.load();assert.equal(drafts.size,4000);
          let recovery=captureScreenRecovery();assert.equal(recovery.drafts.length,0);
          assert.ok(JSON.stringify(recovery).length<256*1024);
          drafts.set('home',{text:'',attachments:[]});recovery=captureScreenRecovery();
          assert.equal(recovery.drafts.length,1);assert.equal(recovery.drafts[0].id,'home');
          await WorkspaceDraftPersistence.flush();assert.equal(captureScreenRecovery().drafts.length,0);
        ''')

    def test_upgrade_ack_waits_for_durable_flush(self):
        self.run_case(r'''
          let release,started=false;
          WorkspaceDraftPersistence.flush=()=>{started=true;return new Promise(resolve=>release=resolve);};
          const restore={upgradeRestore:{requestId:'1234567890abcdef1234567890abcdef',snapshot:{sessionId:null,drafts:[{id:'home',text:'migrated draft',attachments:['copy.zip']}]}}};
          const pending=WorkspaceUpgrade.bootstrap(restore);
          for(let i=0;i<8;i++)await Promise.resolve();
          assert.equal(started,true);assert.equal(requests.filter(row=>row.body?.action==='restored').length,0);
          release(true);await pending;assert.equal(requests.filter(row=>row.body?.action==='restored').length,1);
          assert.equal(drafts.get('home').text,'migrated draft');
        ''')

    def test_failed_upgrade_flush_keeps_handoff_unacknowledged(self):
        self.run_case(r'''
          WorkspaceDraftPersistence.flush=async()=>{throw Error('disk full');};
          await WorkspaceUpgrade.bootstrap({upgradeRestore:{requestId:'1234567890abcdef1234567890abcdef',snapshot:{sessionId:null,drafts:[{id:'home',text:'keep migration',attachments:[]}]}}});
          assert.equal(requests.filter(row=>row.body?.action==='restored').length,0);
          assert.equal(drafts.get('home').text,'keep migration');
          assert.match($('upgrade-notice-text').textContent,/복원/);
        ''')


if __name__ == '__main__':
    unittest.main()
