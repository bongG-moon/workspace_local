"""Unified inbox: receipts are not approvals, and closed views allocate no rows."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
context.flush=()=>new Promise(resolve=>setImmediate(resolve));
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'desktop.js'});
const scenario=process.argv[3];
""")
SETUP = r"""
active={id:'A',title:'업무 A',state:'idle'};sessions=[{...active},{id:'B',title:'업무 B',state:'approval'}];
const receipt=(id,sessionId='B',kind='attention',read=false)=>({id,sessionId,kind,read,title:'업무 '+sessionId,createdAt:1700000000,message:'알림'});
const pending=(id='request-one',sessionId='B',kind='approval')=>({id,sessionId,kind,title:'업무 '+sessionId});
const snapshot=inbox=>({inbox,preferences:{enabled:true,completed:true,attention:true,errors:true},nativeAvailable:true,unreadCount:inbox.filter(row=>!row.read).length});
const filter=scope=>$('desktop-filters').children.find(node=>node.id==='inbox-filter-'+scope);
const cards=()=>$('desktop-items').children.filter(node=>node.classList.contains('desktop-item'));
let calls=[];api=async(...args)=>{calls.push(args);throw Error('Unexpected API');};
const visibleView=()=>{
  active.seq=5;active.messages=[];document.visibilityState='visible';document.hasFocus=()=>true;
  $('task-view').hidden=false;$('conversation').hidden=false;
  const query=document.querySelector;document.querySelector=selector=>selector==='dialog[open]'?($('test-modal').open?$('test-modal'):null):query(selector);
};
const observed=(seq=5)=>({items:[],sessions:[{id:'A',seq},{id:'B',seq}]});
const networkError=()=>Object.assign(Error('temporary'),{transportFailure:true});
"""


@unittest.skipUnless(NODE, "Node.js is required for inbox UI checks")
class WorkspaceInboxFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script,
             str(ROOT/'local_app/web/desktop.js')], input=HARNESS, text=True,
            encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_direct_visible_conversation_reads_only_its_displayed_receipts(self):
        self.run_case(r"""(async()=>{
          visibleView();const old=snapshot([receipt('done','A','completed'),receipt('other','B','completed')]);
          api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('done','A','completed',true),receipt('other','B','completed')])};};
          WorkspaceDesktop.apply(old,observed());await flush();
          assert.equal(calls.length,1);assert.equal(calls[0].body.notificationId,'done');
          assert.equal(calls[0].body.action,'read');assert.equal($('desktop-count').textContent,'1');
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);
          assert.equal(active.state,'idle');assert.equal(sessions[1].state,'approval');
        })()""")

    def test_loading_hidden_modal_and_secondary_views_do_not_auto_read(self):
        self.run_case(r"""(async()=>{
          visibleView();const data=snapshot([receipt('done','A','completed')]);
          document.visibilityState='hidden';WorkspaceDesktop.apply(data,observed());await flush();assert.equal(calls.length,0);
          document.visibilityState='visible';document.hasFocus=()=>false;WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,0);
          document.hasFocus=()=>true;$('test-modal').open=true;WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,0);
          $('test-modal').open=false;pendingSelection={id:'B'};WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,0);pendingSelection=null;
          globalThis.WorkspaceCapabilities={isOpen:()=>true};WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,0);
          globalThis.WorkspaceCapabilities={isOpen:()=>false};
          api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('done','A','completed',true)])};};
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);
        })()""")

    def test_new_metadata_waits_for_rendered_sequence_and_read_reply_cannot_extend_candidates(self):
        self.run_case(r"""(async()=>{
          visibleView();active.seq=4;
          const before={...snapshot([receipt('old','A','completed')]),revision:1};
          api=async(path,body)=>{calls.push({path,body});return {desktop:{...snapshot([receipt('fresh','A','completed'),receipt('old','A','completed',true)]),revision:2}};};
          WorkspaceDesktop.apply(before,observed());await flush();assert.equal(calls.length,0);
          active.seq=5;WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);assert.equal(calls[0].body.notificationId,'old');
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);
          WorkspaceDesktop.apply({...snapshot([receipt('fresh','A','completed'),receipt('old','A','completed',true)]),revision:2},observed(6));
          await flush();assert.equal(calls.length,1);
          active.seq=6;api=async(path,body)=>{calls.push({path,body});return {desktop:{...snapshot([receipt('fresh','A','completed',true),receipt('old','A','completed',true)]),revision:3}};};
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,2);assert.equal(calls[1].body.notificationId,'fresh');
        })()""")

    def test_all_read_captures_snapshot_and_stale_reply_never_erases_new_notice(self):
        self.run_case(r"""(async()=>{
          WorkspaceDesktop.apply({...snapshot([receipt('old','A','completed')]),revision:1},observed());WorkspaceDesktop.open();
          let reply;api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>reply=resolve);};
          const reading=$('desktop-read').onclick();await flush();assert.equal(calls[0].body.notificationId,'old');
          WorkspaceDesktop.apply({...snapshot([receipt('new','B','completed'),receipt('old','A','completed',true)]),revision:3},observed());
          reply({desktop:{...snapshot([receipt('old','A','completed',true)]),revision:2}});await reading;
          assert.equal(cards().length,2);assert.equal($('desktop-count').textContent,'1');assert.match(flatText(cards()[0]),/읽지 않음/);
        })()""")

    def test_read_in_flight_queues_another_visible_conversation_without_duplicate_requests(self):
        self.run_case(r"""(async()=>{
          visibleView();const rows=[receipt('one','A','completed'),receipt('two','B','completed')],replies=[];
          api=(path,body)=>{calls.push({path,body});return new Promise(resolve=>replies.push(resolve));};
          WorkspaceDesktop.apply(snapshot(rows),observed());await flush();assert.equal(calls.length,1);
          WorkspaceDesktop.viewed();active={id:'B',seq:5,state:'idle'};WorkspaceDesktop.viewed();WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);
          replies[0]({desktop:snapshot([{...rows[0],read:true},rows[1]])});await flush();assert.equal(calls.length,2);assert.equal(calls[1].body.notificationId,'two');
          replies[1]({desktop:snapshot(rows.map(row=>({...row,read:true})))});await flush();assert.equal(calls.length,2);assert.equal($('desktop-count').hidden,true);
        })()""")

    def test_failed_auto_read_stays_unread_and_retries_only_after_backoff(self):
        self.run_case(r"""(async()=>{
          visibleView();let now=100000;Date.now=()=>now;let messages=0;toast=()=>messages++;
          api=async(path,body)=>{calls.push({path,body});throw Error('temporary');};
          WorkspaceDesktop.apply(snapshot([receipt('one','A','completed')]),observed());await flush();assert.equal(calls.length,1);assert.equal(messages,0);assert.equal($('desktop-count').hidden,false);
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,1);now+=5001;
          api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('one','A','completed',true)])};};
          WorkspaceDesktop.viewed();await flush();assert.equal(calls.length,2);assert.equal($('desktop-count').hidden,true);
        })()""")

    def test_native_lookup_failure_retries_but_user_supersession_consumes_navigation(self):
        self.run_case(r"""(async()=>{
          const storage=new Map();sessionStorage.getItem=key=>storage.get(key);sessionStorage.setItem=(key,value)=>storage.set(key,value);
          let now=100000,selected=0;Date.now=()=>now;
          selectSession=async()=>{selected++;throw networkError();};await WorkspaceDesktop.follow({id:'retry',sessionId:'B'});
          assert.equal(storage.has('workspaceNavigation'),false);await WorkspaceDesktop.follow({id:'retry',sessionId:'B'});assert.equal(selected,1);
          now+=5001;selectSession=async()=>{selected++;return true;};await WorkspaceDesktop.follow({id:'retry',sessionId:'B'});assert.equal(selected,2);
          await WorkspaceDesktop.follow({id:'retry',sessionId:'B'});assert.equal(selected,2);
          selectSession=async()=>{selected++;selectionGeneration++;return false;};await WorkspaceDesktop.follow({id:'cancel',sessionId:'A'});
          now+=10000;await WorkspaceDesktop.follow({id:'cancel',sessionId:'A'});assert.equal(selected,3);
        })()""")

    def test_old_native_navigation_cannot_overwrite_newer_click_or_reclaim_after_manual_switch(self):
        self.run_case(r"""(async()=>{
          const storage=new Map();sessionStorage.getItem=key=>storage.get(key);sessionStorage.setItem=(key,value)=>storage.set(key,value);
          let reply;selectSession=id=>id==='A'?new Promise(resolve=>reply=resolve):Promise.resolve(true);
          const old=WorkspaceDesktop.follow({id:'old',sessionId:'A'});await WorkspaceDesktop.follow({id:'new',sessionId:'B'});reply(false);await old;
          assert.equal(storage.get('workspaceNavigation'),'new');
          let now=100000,selected=0;Date.now=()=>now;selectSession=async()=>{selected++;throw networkError();};
          await WorkspaceDesktop.follow({id:'failed',sessionId:'A'});selectionGeneration++;active={id:'B'};now+=5001;
          await WorkspaceDesktop.follow({id:'failed',sessionId:'A'});assert.equal(selected,1);assert.equal(storage.get('workspaceNavigation'),'failed');
        })()""")

    def test_new_native_click_ignores_other_click_backoff_and_missing_task_does_not_loop(self):
        self.run_case(r"""(async()=>{
          const storage=new Map();sessionStorage.getItem=key=>storage.get(key);sessionStorage.setItem=(key,value)=>storage.set(key,value);
          let now=100000;Date.now=()=>now;const selected=[];
          selectSession=async id=>{selected.push(id);if(id==='A')throw networkError();return true;};
          await WorkspaceDesktop.follow({id:'first',sessionId:'A'});await WorkspaceDesktop.follow({id:'next',sessionId:'B'});
          assert.deepEqual(selected,['A','B']);assert.equal(storage.get('workspaceNavigation'),'next');
          let messages=0;toast=()=>messages++;selectSession=async id=>{selected.push(id);const error=Error('보관한 업무입니다.');error.httpStatus=400;throw error;};
          await WorkspaceDesktop.follow({id:'missing',sessionId:'C'});now+=10000;await WorkspaceDesktop.follow({id:'missing',sessionId:'C'});
          assert.equal(selected.filter(id=>id==='C').length,1);assert.equal(messages,1);
        })()""")

    def test_live_requests_merge_with_receipts_and_old_attention_is_superseded(self):
        self.run_case(r"""(()=>{
          WorkspaceDesktop.apply(snapshot([receipt('new'),receipt('old'),receipt('done','A','completed')]),{items:[pending(),pending(),pending('question','B','question')]});
          WorkspaceDesktop.open();assert.equal(cards().length,2);assert.match(flatText(cards()[0]),/승인·답변 대기 2건/);
          assert.equal($('desktop-count').textContent,'2');assert.match(filter('attention').textContent,/1/);
          assert.equal(active.state,'idle');assert.equal(sessions[1].state,'approval');assert.equal(calls.length,0);
        })()""")

    def test_current_request_summary_appears_under_title_and_never_uses_stale_receipt(self):
        self.run_case(r"""(()=>{
          WorkspaceDesktop.apply(snapshot([{...receipt('old'),summary:'이전 요청'}]),
            {items:[{...pending(),notificationId:'new',summary:'보고서 형식을 골라 주세요'}]});
          WorkspaceDesktop.open('attention');const button=cards()[0].children[0];
          assert.equal(button.children[0].textContent,'업무 B');
          assert.equal(button.children[1].textContent,'보고서 형식을 골라 주세요');
          assert.equal(button.children[1].classList.contains('inbox-request-summary'),true);
          assert.doesNotMatch(flatText(cards()[0]),/이전 요청/);
          assert.match(button.attributes['aria-label'],/보고서 형식을 골라 주세요/);
          assert.equal(calls.length,0);
        })()""")

    def test_marking_read_preserves_pending_request_and_only_calls_receipt_api(self):
        self.run_case(r"""(async()=>{
          WorkspaceDesktop.apply(snapshot([receipt('new')]),{items:[pending()]});WorkspaceDesktop.open('attention');
          api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('new','B','attention',true)])};};
          await cards()[0].children[1].onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/notifications');assert.equal(calls[0].body.action,'read');
          assert.equal(calls[0].body.notificationId,'new');assert.equal(cards().length,1);
          assert.match(flatText(cards()[0]),/승인 대기/);assert.match(flatText(cards()[0]),/읽음/);assert.equal($('desktop-count').hidden,true);
          assert.equal(sessions[1].state,'approval');
        })()""")

    def test_resolved_attention_remains_a_record_not_an_actionable_request(self):
        self.run_case(r"""(()=>{
          const notices=snapshot([receipt('old')]);WorkspaceDesktop.apply(notices,{items:[pending()]});WorkspaceDesktop.open('attention');
          const stale=cards()[0].children[0];let selected=0;selectSession=async()=>{selected++;return true;};let message;toast=value=>message=value;
          WorkspaceDesktop.apply(notices,{items:[]});assert.equal(cards().length,0);assert.equal($('desktop-empty').hidden,false);
          stale.onclick();assert.equal(selected,0);assert.match(message,/더 이상/);
          filter('all').onclick();assert.match(flatText(cards()[0]),/현재 대기 없음/);assert.match(flatText(cards()[0]),/읽지 않음/);
        })()""")

    def test_closed_and_unchanged_polls_do_not_rebuild_dom_and_close_releases_rows(self):
        self.run_case(r"""(()=>{
          const data=snapshot([receipt('done','A','completed')]);WorkspaceDesktop.apply(data,{items:[]});
          assert.equal($('desktop-items').children.length,0);WorkspaceDesktop.open();const original=cards()[0];
          WorkspaceDesktop.apply(data,{items:[]});assert.equal(cards()[0],original);
          WorkspaceDesktop.close();assert.equal($('desktop-items').children.length,0);
          WorkspaceDesktop.apply(snapshot([receipt('later','B','error')]),{items:[]});assert.equal($('desktop-items').children.length,0);
        })()""")

    def test_filter_counts_and_pagination_bound_visible_rows(self):
        self.run_case(r"""(()=>{
          const notices=Array.from({length:50},(_,i)=>receipt('notice-'+i,'A',i%2?'completed':'error',i%3===0));
          WorkspaceDesktop.apply(snapshot(notices),{items:[]});WorkspaceDesktop.open();assert.equal(cards().length,30);
          $('desktop-items').children.at(-1).children[2].onclick();assert.equal(cards().length,20);
          filter('completed').onclick();assert.equal(cards().length,25);assert.match(filter('completed').textContent,/25/);
          filter('unread').onclick();assert.equal(cards().length,30);assert.match(filter('unread').textContent,/33/);
        })()""")

    def test_native_preferences_survive_reads_and_read_failure_does_not_hide_pending(self):
        self.run_case(r"""(async()=>{
          WorkspaceDesktop.apply({...snapshot([receipt('new')]),preferences:{enabled:false,completed:true,attention:false,errors:true}},{items:[pending()]});
          WorkspaceDesktop.open();await cards()[0].children[1].onclick();assert.match(flatText(cards()[0]),/읽지 않음/);
          assert.equal($('desktop-attention').checked,false);assert.equal($('desktop-toggle').textContent,'PC 알림 켜기');
          assert.equal(calls.length,1);assert.equal(cards().length,1);assert.match(flatText(cards()[0]),/승인 대기/);
        })()""")

    def test_task_navigation_marks_receipt_only_after_success(self):
        self.run_case(r"""(async()=>{
          WorkspaceDesktop.apply(snapshot([receipt('new')]),{items:[pending()]});WorkspaceDesktop.open();
          selectSession=async()=>false;await cards()[0].children[0].onclick();assert.equal(calls.length,0);assert.equal($('desktop-dialog').open,true);
          let selected;selectSession=async id=>{selected=id;return true;};api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('new','B','attention',true)])};};
          await cards()[0].children[0].onclick();assert.equal(selected,'B');assert.equal($('desktop-dialog').open,false);
          assert.equal(calls.length,1);assert.equal(calls[0].body.action,'read');
        })()""")

    def test_filter_keyboard_access_and_escape_restore_focus(self):
        self.run_case(r"""(()=>{
          WorkspaceDesktop.apply(snapshot([]),{items:[pending()]});$('prompt').focus();WorkspaceDesktop.open();
          const event={key:'ArrowRight',preventDefault(){this.defaultPrevented=true;}};filter('all').onkeydown(event);
          assert.equal(event.defaultPrevented,true);assert.equal(document.activeElement,filter('unread'));
          assert.equal(filter('unread').attributes['aria-selected'],'true');
          $('desktop-dialog').oncancel({preventDefault(){}});assert.equal(document.activeElement,$('prompt'));
          assert.equal($('desktop-items').children.length,0);
        })()""")

    def test_exact_receipt_identity_never_reuses_old_read_state_for_new_request(self):
        self.run_case(r"""(()=>{
          const data=snapshot([receipt('old','B','attention',true)]);
          WorkspaceDesktop.apply(data,{items:[{...pending('new'),notificationId:'new-receipt'}]});WorkspaceDesktop.open('attention');
          assert.equal(cards().length,1);assert.match(flatText(cards()[0]),/읽지 않음/);
          assert.equal($('desktop-count').textContent,'1');assert.equal(cards()[0].children.length,1);
          assert.equal(calls.length,0);
        })()""")

    def test_group_read_marks_only_exact_active_receipts_in_one_batch(self):
        self.run_case(r"""(async()=>{
          const data=snapshot([receipt('new-two'),receipt('new-one'),receipt('old')]);
          WorkspaceDesktop.apply(data,{items:[{...pending('one'),notificationId:'new-one'},{...pending('two'),notificationId:'new-two'}]});
          WorkspaceDesktop.open('attention');
          api=async(path,body)=>{calls.push({path,body});return {desktop:snapshot([receipt('new-two','B','attention',true),receipt('new-one','B','attention',true),receipt('old')])};};
          await cards()[0].children[1].onclick();assert.equal(calls.length,1);
          assert.deepEqual([...calls[0].body.notificationIds].sort(),['new-one','new-two']);
          assert.equal(calls[0].body.action,'read');assert.equal('notificationId' in calls[0].body,false);
          assert.match(flatText(cards()[0]),/읽음/);assert.match(flatText(cards()[0]),/승인 대기 2건/);
          assert.equal($('desktop-count').hidden,true);
        })()""")

    def test_older_poll_cannot_reverse_acknowledged_read_state(self):
        self.run_case(r"""(async()=>{
          const old=snapshot([receipt('current')]),attention={items:[{...pending(),notificationId:'current'}]};
          WorkspaceDesktop.apply(old,attention);WorkspaceDesktop.open('attention');
          api=async()=>({desktop:snapshot([receipt('current','B','attention',true)])});await cards()[0].children[1].onclick();
          WorkspaceDesktop.apply(old,attention);assert.match(flatText(cards()[0]),/읽음/);
          assert.doesNotMatch(flatText(cards()[0]),/읽지 않음/);assert.equal($('desktop-count').hidden,true);
          assert.match(flatText(cards()[0]),/승인 대기/);
        })()""")


if __name__ == '__main__':
    unittest.main()
