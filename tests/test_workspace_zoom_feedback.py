"""Native zoom feedback behavior with deterministic DOM and timer doubles."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
let timers=new Map(),nextTimer=0,events=new Map();
class Element{
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.hidden=false;this.open=false;}
  setAttribute(k,v){this.attributes[k]=v;}
  append(...nodes){this.children.push(...nodes);}
  matches(selector){return selector===':popover-open'&&this.open;}
  showPopover(){this.open=true;}
  hidePopover(){this.open=false;}
}
const focused={tagName:'textarea'},body=new Element('body');
const context={document:{body,activeElement:focused,createElement:tag=>new Element(tag)},console,
  setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
  addEventListener(name,fn){events.set(name,fn);},devicePixelRatio:2.5};
context.globalThis=context;vm.createContext(context);
vm.runInContext(fs.readFileSync('local_app/web/zoom-feedback.js','utf8'),context);
const zoom=context.WorkspaceZoomFeedback;
function expire(){const pending=[...timers.values()];timers.clear();for(const timer of pending)timer.fn();}
"""

@unittest.skipUnless(NODE, "Node is needed for frontend behavior checks")
class ZoomFeedbackTests(unittest.TestCase):
    def run_js(self, script):
        result=subprocess.run([NODE,"-e",HARNESS+script],cwd=ROOT,text=True,capture_output=True,encoding="utf-8")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_native_percent_is_independent_of_monitor_dpi_and_initialization_is_silent(self):
        self.run_js(r"""
assert.equal(zoom.update({factor:1,visible:false}),true);
assert.equal(zoom.percent,100);assert.equal(body.children.length,0);assert.equal(timers.size,0);
zoom.update({factor:1.25,visible:true});
assert.equal(zoom.percent,125);assert.equal(body.children[0].children[2].textContent,'125%');
context.devicePixelRatio=1;zoom.update({factor:1.25,visible:true});
assert.equal(zoom.percent,125);assert.equal(body.children.length,1);
for(const factor of [undefined,null,'1.5',NaN,Infinity,0,.249,5.01]){
 assert.equal(zoom.update({factor,visible:true}),false);assert.equal(zoom.percent,125);
}
""")

    def test_latest_change_refreshes_one_timer_and_does_not_move_focus(self):
        self.run_js(r"""
for(const factor of [1.1,1.25,1.5,.8,1])zoom.update({factor,visible:true});
const surface=body.children[0];
assert.equal(timers.size,1);assert.equal([...timers.values()][0].ms,1800);
assert.equal(surface.hidden,false);assert.equal(surface.open,true);
assert.equal(surface.attributes.popover,'manual');assert.equal(surface.attributes.role,'status');
assert.equal(surface.children[2].textContent,'100%');assert.equal(context.document.activeElement,focused);
expire();assert.equal(surface.hidden,true);assert.equal(surface.open,false);assert.equal(timers.size,0);
assert.equal(zoom.percent,100);assert.equal(context.document.activeElement,focused);
""")

    def test_old_engine_fallback_and_page_exit_cleanup(self):
        self.run_js(r"""
Element.prototype.showPopover=undefined;Element.prototype.hidePopover=undefined;
zoom.update({factor:2,visible:true});assert.equal(body.children[0].hidden,false);
events.get('pagehide')();assert.equal(body.children[0].hidden,true);assert.equal(timers.size,0);
assert.equal(context.document.activeElement,focused);
""")

if __name__ == "__main__":
    unittest.main()
