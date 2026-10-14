"""Exercise the actual fetch/confirmation path, including unchanged drafts."""
from pathlib import Path
import subprocess
import unittest
from tests.test_workspace_frontend_state import HARNESS, NODE
from tests.test_workspace_controls_frontend import SETUP

ROOT = Path(__file__).resolve().parents[1]
SETUP += r"""
const calls=[];
const full={error:'연결 확인',code:'connection_capacity_wait',replacement:{token:'one-use',title:'기존 업무',targetTitle:'새 업무',state:'running',limit:5,connectedAt:1}};
fetch=async(path,options)=>{calls.push(JSON.parse(options.body));return {ok:calls.length>1,json:async()=>calls.length>1?{ok:true}:full};};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
"""


@unittest.skipUnless(NODE, 'Node.js required')
class ReplacementFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_cancel_preserves_composer_and_never_sends_confirmation(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='작성 내용';attachments=['keep.zip'];
          const pending=submit();await settle();assert.equal($('action-dialog').open,true);
          assert.match($('action-message').textContent,/기존 업무/);assert.match($('action-message').textContent,/일시정지/);
          $('action-cancel').onclick();await pending;
          assert.equal(calls.length,1);assert.equal($('prompt').value,'작성 내용');assert.deepEqual(attachments,['keep.zip']);
          assert.equal(sending,false);assert.equal($('send').disabled,false);
        })()""")

    def test_accept_retries_only_the_same_payload_once(self):
        self.run_case(r"""(async()=>{
          const data={id:'A',text:'request',attachments:['keep.zip']};
          const pending=api('/api/send',data);await settle();$('action-confirm').onclick();await pending;
          assert.equal(calls.length,2);assert.deepEqual(calls[1],{...data,replacementToken:'one-use'});
          assert.equal(data.replacementToken,undefined);
        })()""")

    def test_automatic_requests_never_prompt_or_retry(self):
        self.run_case(r"""(async()=>{
          await assert.rejects(api('/api/connect',{id:'A',automatic:true}));
          assert.equal(calls.length,1);assert.notEqual($('action-dialog').open,true);
        })()""")

    def test_navigation_during_confirmation_cancels_replacement(self):
        self.run_case(r"""(async()=>{
          const pending=api('/api/connect',{id:'A'});await settle();
          selectionGeneration++;active={...active,id:'B'};$('action-confirm').onclick();
          await assert.rejects(pending);assert.equal(calls.length,1);
        })()""")

    def test_catalog_scope_change_invalidates_confirmation(self):
        self.run_case(r"""(async()=>{
          let scope='local';globalThis.WorkspaceCapabilities={capacityContext:()=>scope};
          const pending=api('/api/connect',{id:'other'});await settle();scope='common';$('action-confirm').onclick();
          await assert.rejects(pending);assert.equal(calls.length,1);
        })()""")

    def test_second_rejection_does_not_loop_or_prompt_again(self):
        self.run_case(r"""(async()=>{
          fetch=async(path,options)=>{calls.push(JSON.parse(options.body));return {ok:false,json:async()=>full};};
          const pending=api('/api/connect',{id:'A'});await settle();$('action-confirm').onclick();
          await assert.rejects(pending);assert.equal(calls.length,2);assert.equal($('action-dialog').open,false);
        })()""")
