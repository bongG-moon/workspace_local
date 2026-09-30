"""Exercise panel layout, keyboard focus, and preferences without a browser."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
function boot({width=1440, saved=null, failRead=false, failWrite=false}={}) {
  let currentWidth = width, writes = [], stored = saved;
  const nodes = new Map(), listeners = new Map(), media = new Map();
  let context;
  class Element {
    constructor(id) {
      this.id=id; this.hidden=false; this.inert=false; this.disabled=false; this.isConnected=true;
      this.attributes={}; this.events=new Map(); this.parent=null;this.tabIndex=0;
      const classes=new Set();
      this.classList={contains:key=>classes.has(key),add:key=>classes.add(key),remove:key=>classes.delete(key),
        toggle:(key,on)=>on?classes.add(key):classes.delete(key)};
    }
    setAttribute(key,value){this.attributes[key]=String(value);}
    getAttribute(key){return this.attributes[key]??null;}
    removeAttribute(key){delete this.attributes[key];}
    addEventListener(key,callback){this.events.set(key,callback);}
    click(){this.events.get('click')?.({target:this});}
    focus(){context.document.activeElement=this;listeners.get('focusin')?.({target:this});}
    contains(node){for(let current=node;current;current=current.parent)if(current===this)return true;return false;}
    querySelectorAll(){return [...nodes.values()].filter(node=>node!==this&&this.contains(node));}
    closest(){for(let current=this;current;current=current.parent)if(current.hidden||current.inert)return current;return null;}
    getClientRects(){return this.closest()?[]:[{}];}
  }
  for (const id of ['app','main','sidebar-panel','inspector-panel','sidebar-toggle','materials-button','close-materials',
    'layout-backdrop','composer-controls-panel','composer-suggestions','prompt','inspector-content','session-button',
    'home-button','new-chat','import-open','palette-open','capabilities-open','settings-open','help']) nodes.set(id,new Element(id));
  const get = id => nodes.get(id) || null;
  get('composer-controls-panel').hidden=get('composer-suggestions').hidden=true;
  get('inspector-content').parent=get('inspector-panel'); get('session-button').parent=get('sidebar-panel');
  get('close-materials').parent=get('inspector-panel');get('sidebar-toggle').parent=get('sidebar-panel');
  get('materials-button').parent=get('main');get('prompt').parent=get('main');
  for(const id of ['home-button','new-chat','import-open','palette-open','capabilities-open','settings-open','help'])get(id).parent=get('sidebar-panel');
  context={console,document:{activeElement:get('prompt'),
    getElementById:get, querySelector:selector=>selector==='.app'?get('app'):selector==='main'?get('main'):selector==='dialog[open]'?context.modal:null,
    addEventListener:(name,fn)=>listeners.set(name,fn)},
    localStorage:{getItem(key){assert.equal(key,'workspace.layout.v1');if(failRead)throw Error('denied');return stored;},
      setItem(key,value){assert.equal(key,'workspace.layout.v1');if(failWrite)throw Error('denied');stored=value;writes.push(JSON.parse(value));}},
    matchMedia(query){
      const limit=Number(query.match(/\d+/)[0]);
      const value={matches:currentWidth<=limit,limit,handlers:[],addEventListener(name,fn){assert.equal(name,'change');this.handlers.push(fn);}};
      media.set(query,value); return value;
    }};
  vm.createContext(context); vm.runInContext(script,context,{filename:'layout.js'});
  const api=context.WorkspaceLayout;
  return {api,get,context,writes,
    resize(width){
      currentWidth=width;const changed=[];
      for(const value of media.values()){const next=width<=value.limit;if(next!==value.matches)changed.push(value);value.matches=next;}
      for(const value of changed)for(const fn of value.handlers)fn({matches:value.matches});
    },
    key(properties={}){const event={key:'Escape',defaultPrevented:false,stopped:false,
      preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...properties};listeners.get('keydown')(event);return event;}
  };
}
try {vm.runInNewContext(process.argv[3],{assert,boot,console},{filename:'scenario.js'});}
catch(error){console.error(error.stack||error);process.exitCode=1;}
"""


@unittest.skipUnless(NODE, "Node.js is required for layout UI checks")
class WorkspaceLayoutFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/layout.js"), javascript],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_wide_toggles_keep_icon_navigation_and_store_only_two_booleans(self):
        self.run_case(r"""
          const {api,get,writes,key}=boot();
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
          get('sidebar-toggle').click();get('materials-button').click();
          assert.equal(get('app').classList.contains('sidebar-collapsed'),true);
          assert.equal(get('sidebar-panel').hidden,false);assert.equal(get('sidebar-panel').inert,false);
          assert.equal(get('inspector-panel').hidden,true);assert.equal(get('inspector-panel').inert,true);
          assert.equal(get('sidebar-toggle').attributes['aria-expanded'],'false');
          assert.equal(get('materials-button').attributes['aria-controls'],'inspector-panel');
          assert.equal(JSON.stringify(writes.at(-1)),JSON.stringify({sidebarCollapsed:true,inspectorCollapsed:true}));
          const reloaded=boot({saved:JSON.stringify(writes.at(-1))});
          assert.equal(reloaded.api.snapshot().sidebarCollapsed,true);assert.equal(reloaded.api.snapshot().inspectorVisible,false);
          api.toggleSidebar();api.openInspector();assert.equal(key().defaultPrevented,false);
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
        """)

    def test_invalid_or_unavailable_storage_does_not_break_panel_controls(self):
        self.run_case(r"""
          for(const saved of ['bad JSON','null','[]','{"sidebarCollapsed":"true","inspectorCollapsed":1}']){
            const {api}=boot({saved});assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
          }
          const {api,writes}=boot({saved:'{"sidebarCollapsed":true,"inspectorCollapsed":false,"token":"do not copy"}'});
          api.toggleInspector();assert.deepEqual(Object.keys(writes[0]).sort(),['inspectorCollapsed','sidebarCollapsed']);
          const denied=boot({failRead:true,failWrite:true});denied.get('sidebar-toggle').click();denied.get('materials-button').click();
          assert.equal(denied.api.snapshot().sidebarCollapsed,true);assert.equal(denied.api.snapshot().inspectorVisible,false);
        """)

    def test_tablet_inspector_overlay_closes_on_escape_with_focus_return(self):
        self.run_case(r"""
          const {api,get,context,writes,key}=boot({width:1000});
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,false);
          get('materials-button').click();assert.equal(api.snapshot().inspectorOverlay,true);
          assert.equal(get('layout-backdrop').hidden,false);get('inspector-content').focus();
          assert.equal(key().defaultPrevented,true);assert.equal(api.snapshot().inspectorVisible,false);
          assert.equal(context.document.activeElement,get('materials-button'));assert.equal(writes.length,0);
          api.openInspector();get('close-materials').click();assert.equal(get('layout-backdrop').hidden,true);
        """)

    def test_mobile_overlays_are_mutually_exclusive_and_backdrop_keeps_preferences(self):
        self.run_case(r"""
          const {api,get,context,writes}=boot({width:640});
          assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,false);
          get('sidebar-toggle').click();assert.equal(api.snapshot().sidebarOverlay,true);
          api.openInspector();assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorOverlay,true);
          api.toggleSidebar();assert.equal(api.snapshot().sidebarOverlay,true);assert.equal(api.snapshot().inspectorVisible,false);
          get('session-button').focus();get('layout-backdrop').click();
          assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(get('layout-backdrop').hidden,true);
          assert.equal(context.document.activeElement,get('sidebar-toggle'));assert.equal(writes.length,0);
        """)

    def test_resize_closes_temporary_overlays_and_restores_saved_desktop_arrangement(self):
        self.run_case(r"""
          const {api,get,context,resize,writes}=boot({saved:'{"sidebarCollapsed":false,"inspectorCollapsed":true}'});
          resize(1100);assert.equal(api.snapshot().inspectorNarrow,true);api.openInspector();get('inspector-content').focus();
          resize(700);assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,false);
          assert.equal(context.document.activeElement,get('materials-button'));
          api.toggleSidebar();get('session-button').focus();resize(1101);
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,false);
          resize(700);assert.equal(context.document.activeElement,get('sidebar-toggle'));
          resize(1101);assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(writes.length,0);
        """)

    def test_catalog_hides_inspector_without_losing_preferences_or_leaving_backdrop(self):
        self.run_case(r"""
          const {api,get,context,writes}=boot();get('inspector-content').focus();
          get('app').classList.add('catalog-open');api.refresh();
          assert.equal(get('inspector-panel').hidden,true);assert.equal(get('materials-button').attributes['aria-expanded'],'false');
          assert.equal(context.document.activeElement,get('materials-button'));
          get('app').classList.remove('catalog-open');api.refresh();assert.equal(api.snapshot().inspectorVisible,true);assert.equal(writes.length,0);
          const phone=boot({width:600});phone.api.openInspector();phone.get('app').classList.add('catalog-open');phone.api.refresh();
          assert.equal(phone.get('layout-backdrop').hidden,true);assert.equal(phone.api.snapshot().inspectorOverlay,false);
        """)

    def test_escape_leaves_modal_composer_and_consumed_events_to_existing_controls(self):
        self.run_case(r"""
          const {api,get,context,key}=boot({width:600});api.toggleSidebar();
          context.modal={open:true};assert.equal(key().defaultPrevented,false);assert.equal(api.snapshot().sidebarOverlay,true);context.modal=null;
          for(const id of ['composer-controls-panel','composer-suggestions']){
            get(id).hidden=false;assert.equal(key().defaultPrevented,false);assert.equal(api.snapshot().sidebarOverlay,true);get(id).hidden=true;
          }
          for(const options of [{defaultPrevented:true},{isComposing:true},{key:'Tab',shiftKey:true},{ctrlKey:true},{shiftKey:true}]){
            key(options);assert.equal(api.snapshot().sidebarOverlay,true);
          }
          assert.equal(key().defaultPrevented,true);assert.equal(api.snapshot().sidebarOverlay,false);
        """)

    def test_overlay_takes_focus_traps_tab_and_restores_background_inert_state(self):
        self.run_case(r"""
          const {api,get,context,key}=boot({width:640});
          get('main').inert=true;get('sidebar-panel').setAttribute('role','navigation');
          api.openInspector();
          assert.equal(context.document.activeElement,get('close-materials'));
          assert.equal(get('main').inert,true);assert.equal(get('sidebar-panel').inert,true);
          assert.equal(get('inspector-panel').attributes['role'],'dialog');assert.equal(get('inspector-panel').attributes['aria-modal'],'true');
          assert.equal(key({key:'Tab',shiftKey:true}).defaultPrevented,true);
          assert.equal(context.document.activeElement,get('inspector-content'));
          assert.equal(key({key:'Tab'}).defaultPrevented,true);assert.equal(context.document.activeElement,get('close-materials'));
          get('prompt').focus();assert.equal(context.document.activeElement,get('close-materials'));
          api.closeInspector();assert.equal(get('main').inert,true);assert.equal(get('sidebar-panel').inert,false);
          assert.equal(get('inspector-panel').getAttribute('aria-modal'),null);
          api.toggleSidebar();assert.equal(context.document.activeElement,get('sidebar-toggle'));
          assert.equal(get('sidebar-panel').getAttribute('role'),'dialog');assert.equal(get('inspector-panel').inert,true);
          assert.equal(key({key:'Tab',shiftKey:true}).defaultPrevented,true);assert.equal(context.document.activeElement,get('help'));
          assert.equal(key({key:'Tab'}).defaultPrevented,true);assert.equal(context.document.activeElement,get('sidebar-toggle'));
          api.closeSidebar();assert.equal(get('sidebar-panel').getAttribute('role'),'navigation');assert.equal(get('main').inert,true);
        """)

    def test_overlay_leaves_native_dialog_focus_and_navigation_closes_without_stealing_focus(self):
        self.run_case(r"""
          for(const id of ['home-button','new-chat','import-open','palette-open','capabilities-open','settings-open','help']){
            const {api,get,context,writes}=boot({width:600});api.toggleSidebar();get(id).focus();get(id).click();
            assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(get('layout-backdrop').hidden,true);
            assert.equal(get('main').inert,false);assert.equal(context.document.activeElement,get(id));assert.equal(writes.length,0);
          }
          const {api,get,context,key}=boot({width:600});api.openInspector();context.modal={open:true};
          get('prompt').focus();assert.equal(context.document.activeElement,get('prompt'));
          assert.equal(key({key:'Tab'}).defaultPrevented,false);assert.equal(key().defaultPrevented,false);
          assert.equal(api.snapshot().inspectorOverlay,true);
        """)


if __name__ == "__main__":
    unittest.main()
