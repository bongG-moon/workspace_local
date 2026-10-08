import subprocess
import unittest
from pathlib import Path
from tests.test_workspace_frontend_state import NODE, HARNESS

ROOT = Path(__file__).resolve().parents[1]
JS = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let timers=new Map(),n=0,requests=[],persisted=[],selected='home',stashes=[];
const node={textContent:'',hidden:true};
const ctx={console,Set,Map,JSON,Number,Error,
 document:{getElementById:()=>node,addEventListener(){}},
 setTimeout:(fn,ms)=>{timers.set(++n,{fn,ms});return n;},clearTimeout:id=>timers.delete(id)};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
const service=ctx.WorkspaceDraftPersistence,map=service.createMap();
let api=async(path,body)=>{if(!body)return {entries:persisted,selectedId:'home'};requests.push(JSON.parse(JSON.stringify(body)));return {revision:body.revision+1};};
service.attach({api:(...args)=>api(...args),notify(){},selected:()=>selected,stashes:()=>stashes,restoreStashes:rows=>stashes=rows});
(async()=>{await vm.runInNewContext('(async()=>{'+process.argv[3]+'})()',
 {assert,service,map,timers,requests,node,getRequests:()=>requests,getStashes:()=>stashes,
 setApi:fn=>api=fn,setRows:rows=>persisted=rows,setStashes:rows=>stashes=rows});})().catch(e=>{console.error(e);process.exitCode=1;});
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

    def test_restored_stash_and_empty_draft_do_not_auto_send(self):
        self.run_case("""setRows([{id:'home',revision:2,draft:null,stash:{text:'saved',attachments:['a'],selectionStart:0,selectionEnd:2}}]);
        await service.load();assert.equal(map.get('home').text,'');assert.equal(getStashes()[0].text,'saved');assert.equal(requests.length,0);""")

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
