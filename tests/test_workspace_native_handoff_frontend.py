"""Native handoff waits for cleanup evidence instead of only stop acceptance."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'done',trusted:true};sessions=[{...active}];
$('prompt').value='/login 입력 초안';attachments=['C:/fixture/A/report.md'];
setTimeout=(callback,ms)=>{if(ms===200)callback();return 1;};
"""


@unittest.skipUnless(NODE, 'Node.js is required for native handoff UI checks')
class NativeHandoffFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_delayed_stop_does_not_open_native_until_cleanup_is_confirmed(self):
        self.run_case(r"""(async()=>{
          const paths=[];let polls=0;
          api=async(path)=>{paths.push(path);if(path.startsWith('/api/session'))return {state:'stopped',connectionStopped:++polls>=3,connection:{connected:false}};return {ok:true};};
          await $('native').onclick();
          assert.deepEqual(paths,['/api/stop','/api/session?id=A','/api/session?id=A','/api/session?id=A','/api/native']);
          assert.equal($('prompt').value,'/login 입력 초안');assert.equal(attachments.length,1);
          assert.equal(drafts.get('A').text,'/login 입력 초안');assert.equal(nativeOpening,false);
          assert.equal($('native').disabled,false);assert.match($('toast').textContent,/기존 Claude 창/);
        })()""")

    def test_duplicate_click_during_stop_is_ignored(self):
        self.run_case(r"""(async()=>{
          const paths=[];let release;
          api=async(path)=>{paths.push(path);return path==='/api/stop'?new Promise(resolve=>release=resolve):{state:'stopped',connectionStopped:true};};
          const first=$('native').onclick();await $('native').onclick();assert.equal(paths.length,1);
          assert.equal($('native').disabled,true);release({ok:true});await first;
          assert.equal(paths.filter(path=>path==='/api/native').length,1);assert.equal(nativeOpening,false);
        })()""")

    def test_task_switch_after_stop_ack_cancels_handoff(self):
        self.run_case(r"""(async()=>{
          const paths=[];let release;api=async(path)=>{paths.push(path);return new Promise(resolve=>release=resolve);};
          const pending=$('native').onclick();active={...active,id:'B'};selectionGeneration++;
          $('prompt').value='다른 업무 초안';release({ok:true});await pending;
          assert.deepEqual(paths,['/api/stop']);assert.equal($('prompt').value,'다른 업무 초안');
          assert.equal(nativeOpening,false);
        })()""")

    def test_closing_app_while_polling_cancels_even_a_stopped_reply(self):
        self.run_case(r"""(async()=>{
          const paths=[];let release;
          api=async(path)=>{paths.push(path);return path==='/api/stop'?{}:new Promise(resolve=>release=resolve);};
          const pending=$('native').onclick();await Promise.resolve();await Promise.resolve();
          appClosed=true;release({state:'stopped',connectionStopped:true});await pending;
          assert.equal(paths.includes('/api/native'),false);assert.equal($('native').disabled,true);
          assert.equal(nativeOpening,false);
        })()""")

    def test_unconfirmed_cleanup_times_out_without_opening_native(self):
        self.run_case(r"""(async()=>{
          const paths=[];let now=0;Date.now=()=>now+=16000;
          api=async(path)=>{paths.push(path);return {state:'error',connectionStopped:false,connection:{connected:false}};};
          await $('native').onclick();assert.equal(paths.includes('/api/native'),false);
          assert.match($('toast').textContent,/종료를 아직 확인하지 못/);assert.equal(nativeOpening,false);
          assert.equal($('prompt').value,'/login 입력 초안');
        })()""")


if __name__ == '__main__':
    unittest.main()
