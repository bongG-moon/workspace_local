import subprocess
import unittest
from pathlib import Path
from tests.test_workspace_frontend_state import NODE, HARNESS

ROOT = Path(__file__).resolve().parents[1]
JS = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let timers=new Map(),n=0,requests=[],persisted=[],archivedIds=[],selected='home',stashes=[];
const node={textContent:'',hidden:true};
const ctx={console,Set,Map,JSON,Number,Error,
 document:{getElementById:()=>node,addEventListener(){}},
 setTimeout:(fn,ms)=>{timers.set(++n,{fn,ms});return n;},clearTimeout:id=>timers.delete(id)};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
const service=ctx.WorkspaceDraftPersistence,map=service.createMap();
let api=async(path,body)=>{if(!body)return {entries:persisted,archivedIds,selectedId:'home'};requests.push(JSON.parse(JSON.stringify(body)));return {revision:body.revision+1};};
service.attach({api:(...args)=>api(...args),notify(){},selected:()=>selected,stashes:()=>stashes,restoreStashes:rows=>{const merged=new Map(stashes.map(row=>[row.id,row]));for(const row of rows)merged.set(row.id,row);stashes=[...merged.values()];}});
(async()=>{await vm.runInNewContext('(async()=>{'+process.argv[3]+'})()',
 {assert,service,map,timers,requests,node,getRequests:()=>requests,getStashes:()=>stashes,
 setApi:fn=>api=fn,setRows:rows=>persisted=rows,setArchivedIds:rows=>archivedIds=rows,setStashes:rows=>stashes=rows});})().catch(e=>{console.error(e);process.exitCode=1;});
'''


@unittest.skipUnless(NODE, 'Node.js required')
class DraftFrontendTests(unittest.TestCase):
    def run_case(self, scenario):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/drafts.js'),scenario],input=JS,
                              capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_many_keystrokes_one_save_and_no_idle_timer(self):
        self.run_case("""await service.load();for(let i=0;i<500;i++)map.set('home',{text:'x'.repeat(i),attachments:[]});
        assert.equal(requests.length,0);assert.equal(timers.size,2);await service.flush();
        assert.equal(requests.length,1);assert.equal(requests[0].draft.text.length,499);assert.equal(timers.size,0);
        map.set('home',{text:'x'.repeat(499),attachments:[]});await service.flush();assert.equal(requests.length,1);""")

    def test_boot_does_not_overwrite_edit_made_during_read(self):
        self.run_case("""let resolve;setApi(()=>new Promise(r=>resolve=r));const boot=service.load();
        map.set('home',{text:'new edit',attachments:[]});resolve({entries:[{id:'home',revision:5,draft:{text:'old',attachments:[]},stash:null}],selectedId:'home'});
        await boot;assert.equal(map.get('home').text,'new edit');""")

    def test_deleting_after_send_saves_tombstone(self):
        self.run_case("""await service.load();map.set('home',{text:'send',attachments:['copy']});await service.flush();
        map.delete('home');await service.flush();assert.equal(requests[1].draft,null);assert.equal(requests[1].revision,1);""")

    def test_failed_flush_preserves_pending_and_can_retry(self):
        self.run_case("""await service.load();map.set('home',{text:'keep',attachments:[]});
        setApi(async()=>{throw Error('disk full')});await assert.rejects(service.flush());
        assert.equal(map.get('home').text,'keep');assert.equal(service.hasPending(),true);assert.equal(node.hidden,false);
        setApi(async()=>({revision:1}));await service.flush();assert.equal(service.hasPending(),false);assert.equal(node.hidden,true);""")

    def test_write_in_flight_is_serialized_and_new_text_is_not_lost(self):
        self.run_case("""await service.load();let reply,count=0;setApi(async(p,b)=>{count++;if(count===1)return new Promise(r=>reply=r);return {revision:b.revision+1};});
        map.set('home',{text:'one',attachments:[]});const first=service.flush();map.set('home',{text:'two',attachments:[]});
        const second=service.flush();assert.equal(count,1);reply({revision:1});await Promise.all([first,second]);assert.equal(count,2);assert.equal(service.hasPending(),false);""")

    def test_deleted_draft_ignores_late_success_or_failure_without_blocking_other_drafts(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                self.run_case("""await service.load();let resolve,reject;const sent=[];
                setApi((path,body)=>{sent.push(body.id);return body.id==='gone'?new Promise((r,j)=>{resolve=r;reject=j}):Promise.resolve({revision:1});});
                map.set('gone',{text:'delete this',attachments:[]});map.set('keep',{text:'preserve this',attachments:[]});
                const flight=service.flush();service.forget('gone');
                """ + ("reject(Error('unknown session'));" if fails else "resolve({revision:9});") + """
                await flight;assert.equal(map.has('gone'),false);assert.equal(map.get('keep').text,'preserve this');
                assert.equal(sent.join(','),'gone,keep');assert.equal(service.hasPending(),false);assert.equal(node.hidden,true);
                assert.equal(service.allowStashRestore('gone'),true);map.set('gone',{text:'late edit',attachments:[]});
                await service.flush();assert.equal(map.has('gone'),false);assert.equal(sent.length,2);
                """)

    def test_restored_stash_and_empty_draft_do_not_auto_send(self):
        self.run_case("""setRows([{id:'home',revision:2,draft:null,stash:{text:'saved',attachments:['a'],selectionStart:0,selectionEnd:2}}]);
        await service.load();assert.equal(map.get('home').text,'');assert.equal(getStashes()[0].text,'saved');assert.equal(requests.length,0);""")

    def test_archived_dirty_draft_is_excluded_while_current_draft_still_saves(self):
        self.run_case("""await service.load();
        map.set('archive',{text:'no longer needed',attachments:['old']});map.set('current',{text:'keep',attachments:[]});
        service.archive('archive');map.set('archive',{text:'late edit',attachments:[]});
        await service.flush();assert.equal(requests.length,1);assert.equal(requests[0].id,'current');
        assert.equal(map.has('archive'),false);assert.equal(service.keepForRecovery('archive',{text:'late'}),false);
        assert.equal(service.allowStashRestore('archive'),false);assert.equal(service.hasPending(),false);""")

    def test_backend_skip_drops_archived_row_and_continues_current_save(self):
        self.run_case("""await service.load();const sent=[];
        setApi(async(path,body)=>{sent.push(body.id);return body.id==='archive'
          ?{id:body.id,revision:0,skipped:true,reason:'archived_work'}:{revision:1};});
        map.set('archive',{text:'discard late draft',attachments:[]});map.set('current',{text:'keep',attachments:[]});
        await service.flush();assert.equal(sent.join(','),'archive,current');assert.equal(map.has('archive'),false);
        assert.equal(service.isArchived('archive'),true);assert.equal(service.hasPending(),false);assert.equal(node.hidden,true);
        map.set('archive',{text:'ignored',attachments:[]});await service.flush();assert.equal(sent.length,2);""")

    def test_archived_request_late_failure_does_not_block_other_drafts(self):
        self.run_case("""await service.load();let reject;const sent=[];
        setApi((path,body)=>{sent.push(body.id);return body.id==='archive'?new Promise((r,j)=>reject=j):Promise.resolve({revision:1});});
        map.set('archive',{text:'old',attachments:[]});map.set('current',{text:'keep',attachments:[]});
        const saving=service.flush();service.archive('archive');reject(Error('old archived error'));
        await saving;assert.equal(sent.join(','),'archive,current');assert.equal(service.hasPending(),false);assert.equal(node.hidden,true);""")

    def test_restore_resumes_last_saved_draft_and_preserves_other_stashes(self):
        self.run_case("""setRows([{id:'archive',revision:4,draft:{text:'previously saved',attachments:['saved-file']},
          stash:{text:'saved stash',attachments:[]}}]);setArchivedIds(['archive']);
        await service.load();assert.equal(map.has('archive'),false);assert.equal(service.hasPending(),false);
        setStashes([{id:'other',text:'keep this stash',attachments:[]}]);service.resume('archive');
        assert.equal(map.get('archive').text,'previously saved');assert.equal(service.isArchived('archive'),false);
        assert.equal(getStashes().find(row=>row.id==='other').text,'keep this stash');
        assert.equal(getStashes().find(row=>row.id==='archive').text,'saved stash');
        map.set('archive',{text:'new edit after restore',attachments:[]});await service.flush();
        assert.equal(requests.length,1);assert.equal(requests[0].revision,4);assert.equal(requests[0].draft.text,'new edit after restore');""")

    def test_archive_restore_cycle_ignores_old_success_skip_or_failure(self):
        for outcome in ('success', 'skipped', 'failure'):
            with self.subTest(outcome=outcome):
                response = { 'success': "resolve({revision:9});",
                    'skipped': "resolve({revision:0,skipped:true,reason:'archived_work'});",
                    'failure': "reject(Error('old archived failure'));" }[outcome]
                self.run_case("""await service.load();let resolve,reject;const sent=[];
                setApi((path,body)=>{sent.push(body);return sent.length===1?new Promise((r,j)=>{resolve=r;reject=j}):Promise.resolve({revision:1});});
                map.set('archive',{text:'before archive',attachments:[]});const saving=service.flush();
                service.archive('archive');service.resume('archive');map.set('archive',{text:'new edit after restore',attachments:[]});
                """ + response + """
                await saving;assert.equal(sent.length,2);assert.equal(sent[1].draft.text,'new edit after restore');
                assert.equal(service.isArchived('archive'),false);assert.equal(service.hasPending(),false);assert.equal(node.hidden,true);""")

    def test_late_initial_read_cannot_archive_a_newly_restored_edit(self):
        self.run_case("""let resolve;setApi(()=>new Promise(r=>resolve=r));const loading=service.load();
        map.set('restored',{text:'new visible edit',attachments:[]});
        resolve({entries:[{id:'restored',revision:3,draft:{text:'old saved',attachments:[]},stash:null}],archivedIds:['restored'],selectedId:'home'});
        await loading;assert.equal(map.get('restored').text,'new visible edit');assert.equal(service.isArchived('restored'),false);
        setApi(async()=>({revision:4}));await service.flush();assert.equal(service.hasPending(),false);""")

    def test_committed_save_before_archive_keeps_revision_for_later_restore(self):
        self.run_case("""setRows([{id:'archive',revision:1,draft:{text:'old saved',attachments:[]},stash:null}]);
        await service.load();let reply;const sent=[];
        setApi((path,body)=>{sent.push(body);if(sent.length===1)return new Promise(r=>reply=r);
          assert.equal(body.revision,2);return Promise.resolve({revision:3});});
        map.set('archive',{text:'committed before archive',attachments:[]});const saving=service.flush();
        service.archive('archive');reply({revision:2});await saving;
        assert.equal(map.has('archive'),false);assert.equal(service.hasPending(),false);
        service.resume('archive');assert.equal(map.get('archive').text,'committed before archive');
        map.set('archive',{text:'new restored edit',attachments:[]});await service.flush();
        assert.equal(sent.length,2);assert.equal(sent[1].revision,2);assert.equal(sent[1].draft.text,'new restored edit');""")

    def test_real_quit_waits_for_flush_and_failure_keeps_app_open(self):
        scenario="""(async()=>{let release;const calls=[];globalThis.WorkspaceDraftPersistence={flush:()=>new Promise(r=>release=r)};
        api=async path=>{calls.push(path);return {closed:true,shutdownState:'closed'}};
        $('prompt').value='last edit';const closing=requestShutdown({confirmed:true});assert.equal(calls.length,0);assert.equal(appClosed,false);
        release();await closing;assert.deepEqual(calls,['/api/quit']);
        })()"""
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),scenario],input=HARNESS,
                              capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(0,result.returncode,result.stderr)


if __name__=='__main__':unittest.main()
