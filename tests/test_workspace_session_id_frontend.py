"""Claude resume command always uses the selected native session identity."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(NODE, 'Node.js is required')
class SessionIdFrontendTests(unittest.TestCase):
    def run_case(self, scenario):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), scenario],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_copy_uses_native_id_in_work_and_chat(self):
        self.run_case(r"""(async()=>{
          const id='12345678-1234-1234-1234-123456789abc', copies=[], notices=[];
          globalThis.navigator={clipboard:{writeText:async text=>copies.push(text)}};toast=text=>notices.push(text);
          for(const mode of ['work','chat']){currentMode=mode;active={id:'app-internal-id',sessionId:id};await $('session-id-copy').onclick();}
          assert.deepEqual(copies,[`claude --resume "${id}"`,`claude --resume "${id}"`]);
          assert.equal(notices.length,2);assert.ok(notices.every(n=>n.includes('복사했어요')));
        })()""")

    def test_no_session_and_invalid_id_never_copy(self):
        self.run_case(r"""(async()=>{
          const copies=[],notices=[];globalThis.navigator={clipboard:{writeText:async text=>copies.push(text)}};toast=text=>notices.push(text);
          active={id:'app-id',sessionId:null};await $('session-id-copy').onclick();
          active.sessionId='app-id; unsafe';await $('session-id-copy').onclick();
          assert.equal(copies.length,0);assert.match(notices[0],/첫 요청/);assert.match(notices[1],/확인하지 못했어요/);
        })()""")

    def test_clipboard_failure_and_switch_are_scoped(self):
        self.run_case(r"""(async()=>{
          const notices=[];toast=text=>notices.push(text);active={id:'a',sessionId:'12345678-1234-1234-1234-123456789abc'};
          globalThis.navigator={clipboard:{writeText:async()=>{throw Error('private');}}};await $('session-id-copy').onclick();
          assert.match(notices[0],/복사하지 못했어요/);assert.doesNotMatch(notices[0],/private/);
          let resolve;globalThis.navigator.clipboard.writeText=()=>new Promise(r=>resolve=r);const pending=$('session-id-copy').onclick();
          active={id:'b',sessionId:null};resolve();await pending;assert.equal(notices.length,1);
        })()""")

    def test_button_location_and_semantics(self):
        html=(ROOT/'local_app/web/index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('id="task-pin"'),html.index('id="branch-open"'))
        self.assertLess(html.index('id="branch-open"'),html.index('id="session-id-copy"'))
        self.assertIn('href="#i-copy"',html)

if __name__ == '__main__': unittest.main()
