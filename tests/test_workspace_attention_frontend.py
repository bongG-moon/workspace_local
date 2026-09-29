"""Global attention badges and explicitly opted-in browser notifications."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT=Path(__file__).resolve().parents[1]
HARNESS=BASE_HARNESS.replace("const scenario=process.argv[3];",r"""
context.flush=()=>new Promise(resolve=>setImmediate(resolve));
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
const stored=new Map();context.sessionStorage={getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,String(value))};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'attention.js'});
const scenario=process.argv[3];
""")
SETUP=r"""
WorkspaceAttention.stop();
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'idle'};
sessions=[{...active},{id:'B',title:'업무 B',workspace:'C:/fixture/B',state:'running'}];
document.visibilityState='hidden';document.hasFocus=()=>false;
const attention=(items=[],native={supported:false,bound:false})=>({revision:1,total:items.length,items,native,windowTitle:'Company Workspace [instance-test]'});
const pending={id:'request-one',sessionId:'B',title:'업무 B',kind:'approval'};
"""


@unittest.skipUnless(NODE,"Node.js is required for attention UI checks")
class WorkspaceAttentionFrontendTests(unittest.TestCase):
    def run_case(self,script):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),SETUP+script,
                               str(ROOT/'local_app/web/attention.js')],input=HARNESS,text=True,
                              encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_all_task_badge_does_not_change_active_or_other_task_execution_state(self):
        self.run_case(r"""(async()=>{
          api=async()=>attention([pending]);WorkspaceAttention.start();await flush();
          assert.equal($('attention-count').textContent,'1');assert.equal($('attention-open').hidden,false);
          assert.equal(active.id,'A');assert.equal(sessions[1].state,'running');
          assert.match(flatText($('sessions')),/응답 대기 1/);assert.equal(document.title,'Company Workspace [instance-test]');
          $('attention-open').onclick();assert.equal($('attention-dialog').open,true);
          let selected;selectSession=async id=>{selected=id;return true;};await $('attention-items').children[0].onclick();
          assert.equal(selected,'B');assert.equal($('attention-dialog').open,false);
          api=async()=>attention([]);await WorkspaceAttention.refresh();assert.equal($('attention-open').hidden,true);
          assert.equal(document.title,'Company Workspace [instance-test]');assert.equal(active.id,'A');
        })()""")

    def test_notification_permission_is_requested_only_by_explicit_button(self):
        self.run_case(r"""(async()=>{
          let requests=0;globalThis.Notification=class {static permission='default';static async requestPermission(){requests++;return this.permission='granted';}close(){}};
          api=async()=>attention([pending]);WorkspaceAttention.start();await flush();assert.equal(requests,0);
          await $('notifications-toggle').onclick();assert.equal(requests,1);assert.match($('notifications-toggle').textContent,/끄기/);
          await WorkspaceAttention.refresh();assert.equal(requests,1);
          await $('notifications-toggle').onclick();assert.equal(requests,1);assert.match($('notifications-toggle').textContent,/켜기/);
        })()""")

    def test_notifications_are_deduplicated_and_closed_when_request_resolves(self):
        self.run_case(r"""(async()=>{
          const notices=[];let focusCalls=0;globalThis.focus=()=>focusCalls++;
          globalThis.Notification=class {static permission='granted';constructor(title,options){this.title=title;this.options=options;notices.push(this);}close(){this.closed=true;}};
          await $('notifications-toggle').onclick();api=async()=>attention([pending]);WorkspaceAttention.start();await flush();
          await WorkspaceAttention.refresh();assert.equal(notices.length,1);assert.equal(focusCalls,0);
          assert.equal(notices[0].options.body,'업무 B · 승인 대기');assert.doesNotMatch(notices[0].options.body,/C:|command|file_path/);
          WorkspaceAttention.stop();WorkspaceAttention.start();await flush();assert.equal(notices.length,1);
          api=async()=>attention([]);await WorkspaceAttention.refresh();notices[0].onclick();
          assert.equal(notices[0].closed,true);assert.equal(focusCalls,0);assert.equal($('attention-open').hidden,true);
        })()""")

    def test_viewed_active_request_does_not_raise_browser_notification(self):
        self.run_case(r"""(async()=>{
          let notices=0;globalThis.Notification=class {static permission='granted';constructor(){notices++;}close(){}};
          await $('notifications-toggle').onclick();document.visibilityState='visible';document.hasFocus=()=>true;
          api=async()=>attention([{...pending,sessionId:'A'}]);WorkspaceAttention.start();await flush();assert.equal(notices,0);
          document.visibilityState='hidden';await WorkspaceAttention.refresh();assert.equal(notices,0);
          assert.equal($('attention-count').textContent,'1');
        })()""")

    def test_denied_notifications_keep_badge_without_repeated_permission_requests(self):
        self.run_case(r"""(async()=>{
          let permissionRequests=0;globalThis.Notification=class {static permission='denied';static async requestPermission(){permissionRequests++;return 'denied';}close(){}};
          api=async()=>attention([pending]);WorkspaceAttention.start();await flush();await $('notifications-toggle').onclick();
          assert.equal(permissionRequests,0);assert.match($('notifications-message').textContent,/차단/);
          assert.equal($('attention-count').textContent,'1');
        })()""")

    def test_native_binding_requires_visible_focused_window_and_preserves_exact_title(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body,title:document.title});return path==='/api/attention/bind'
            ? {native:{supported:true,bound:true}} : attention([pending],{supported:true,bound:false});};
          WorkspaceAttention.start();await flush();assert.equal(calls.filter(row=>row.path.endsWith('/bind')).length,0);
          document.visibilityState='visible';document.hasFocus=()=>true;await WorkspaceAttention.refresh();
          const bind=calls.find(row=>row.path.endsWith('/bind'));assert.ok(bind);assert.equal(Object.keys(bind.body).length,0);
          assert.equal(bind.title,'Company Workspace [instance-test]');assert.match($('native-attention-message').textContent,/연결됐어요/);
        })()""")

    def test_only_one_attention_read_is_in_flight_and_stopped_reply_is_ignored(self):
        self.run_case(r"""(async()=>{
          let reply,signal,calls=0;api=(path,body,s)=>{calls++;signal=s;return new Promise(resolve=>reply=resolve);};
          WorkspaceAttention.start();WorkspaceAttention.refresh();assert.equal(calls,1);
          WorkspaceAttention.stop();assert.equal(signal.aborted,true);reply(attention([pending]));await flush();
          assert.notEqual($('attention-count').textContent,'1');
        })()""")


if __name__=='__main__':unittest.main()
