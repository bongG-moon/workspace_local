"""Transient notices do not survive longer than intended or erase newer ones."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = HARNESS.replace(
    "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,",
    "const timers=new Map();let nextTimer=0;const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,timers,",
).replace(
    "setInterval(){},setTimeout(){return 1;},clearTimeout(){},requestAnimationFrame:fn=>fn(),",
    "setInterval(){},setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},requestAnimationFrame:fn=>fn(),",
)


@unittest.skipUnless(NODE, "Node.js is required for the shipped UI checks")
class WorkspaceToastFrontendTests(unittest.TestCase):
    def run_case(self, scenario):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), scenario],
                                input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_new_notice_cancels_old_fade_and_hide(self):
        self.run_case(r"""(()=>{
          toast('첫 번째');const first=[...timers.entries()].filter(([,v])=>[2820,3000].includes(v.ms));
          assert.deepEqual(first.map(([,v])=>v.ms),[2820,3000]);
          first[0][1].fn();assert.equal($('toast').classList.contains('is-dismissing'),true);
          toast('두 번째');assert.equal($('toast').textContent,'두 번째');assert.equal($('toast').hidden,false);
          assert.equal($('toast').classList.contains('is-dismissing'),false);
          for(const [id] of first)assert.equal(timers.has(id),false);
          const latest=[...timers.values()].filter(v=>[2820,3000].includes(v.ms));
          latest.find(v=>v.ms===2820).fn();assert.equal($('toast').classList.contains('is-dismissing'),true);
          latest.find(v=>v.ms===3000).fn();assert.equal($('toast').hidden,true);
        })()""")


if __name__ == '__main__':
    unittest.main()
