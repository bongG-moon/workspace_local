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
function boot({width=1440, appWidth=null, saved=null, failRead=false, failWrite=false, request=null, notify=null, bootstrap=null}={}) {
  let currentWidth = width, writes = [], stored = saved, nextFrame = 1;
  const nodes = new Map(), listeners = new Map(), windowListeners = new Map(), media = new Map(), frames = new Map();
  let context;
  function event(properties={}) {
    return {defaultPrevented:false,stopped:false,preventDefault(){this.defaultPrevented=true;},
      stopPropagation(){this.stopped=true;},...properties};
  }
  class Element {
    constructor(id) {
      this.id=id; this.hidden=false; this.inert=false; this.disabled=false; this.isConnected=true;
      this.attributes={}; this.events=new Map(); this.parent=null;this.tabIndex=0;
      this.captured=new Set();this.styleWrites=[];this.attributeWrites=[];const styles=new Map();
      this.style={getPropertyValue:key=>styles.get(key)||'',setProperty:(key,value)=>{styles.set(key,value);this.styleWrites.push([key,value]);}};
      const classes=new Set();
      this.classList={contains:key=>classes.has(key),add:key=>classes.add(key),remove:key=>classes.delete(key),
        toggle:(key,on)=>on?classes.add(key):classes.delete(key)};
    }
    setAttribute(key,value){this.attributes[key]=String(value);this.attributeWrites.push([key,value]);}
    getAttribute(key){return this.attributes[key]??null;}
    removeAttribute(key){delete this.attributes[key];}
    addEventListener(key,callback){this.events.set(key,callback);}
    emit(name,properties={}){const value=event({target:this,...properties});this.events.get(name)?.(value);return value;}
    click(){return this.emit('click');}
    focus(){context.document.activeElement=this;listeners.get('focusin')?.({target:this});}
    contains(node){for(let current=node;current;current=current.parent)if(current===this)return true;return false;}
    querySelectorAll(){return [...nodes.values()].filter(node=>node!==this&&this.contains(node));}
    closest(){for(let current=this;current;current=current.parent)if(current.hidden||current.inert)return current;return null;}
    getClientRects(){return this.closest()?[]:[{}];}
    get clientWidth(){return this.id==='app'?(appWidth??currentWidth):this.getBoundingClientRect().width;}
    getBoundingClientRect(){return {width:this.rectWidth??(this.id==='sidebar-panel'?(get('app').classList.contains('sidebar-collapsed')?68:248):0)};}
    setPointerCapture(id){this.captured.add(id);}
    hasPointerCapture(id){return this.captured.has(id);}
    releasePointerCapture(id){this.captured.delete(id);this.emit('lostpointercapture',{pointerId:id});}
  }
  for (const id of ['app','main','sidebar-panel','inspector-panel','sidebar-toggle','materials-button','close-materials',
    'layout-backdrop','mini-mode-toggle','inspector-resizer','composer-controls-panel','composer-suggestions','prompt','inspector-content','session-button',
    'home-button','new-chat','import-open','capabilities-open','settings-open','schedule-overview-open','shortcuts-open','help']) nodes.set(id,new Element(id));
  const get = id => nodes.get(id) || null;
  get('composer-controls-panel').hidden=get('composer-suggestions').hidden=true;
  get('inspector-content').parent=get('inspector-panel'); get('session-button').parent=get('sidebar-panel');
  get('close-materials').parent=get('inspector-panel');get('sidebar-toggle').parent=get('sidebar-panel');
  get('materials-button').parent=get('main');get('prompt').parent=get('main');
  for(const id of ['mini-mode-toggle','composer-controls-panel','composer-suggestions'])get(id).parent=get('main');
  for(const id of ['main','sidebar-panel','inspector-panel','inspector-resizer'])get(id).parent=get('app');
  for(const id of ['home-button','new-chat','import-open','capabilities-open','settings-open','schedule-overview-open','shortcuts-open','help'])get(id).parent=get('sidebar-panel');
  context={console,...(request?{api:request}:{}),...(notify?{toast:notify}:{}),...(bootstrap?{boot:bootstrap}:{}),document:{activeElement:get('prompt'),
    getElementById:get, querySelector:selector=>selector==='.app'?get('app'):selector==='main'?get('main'):selector==='dialog[open]'?context.modal:null,
    addEventListener:(name,fn)=>listeners.set(name,fn)},
    localStorage:{getItem(key){assert.equal(key,'workspace.layout.v1');if(failRead)throw Error('denied');return stored;},
      setItem(key,value){assert.equal(key,'workspace.layout.v1');if(failWrite)throw Error('denied');stored=value;writes.push(JSON.parse(value));}},
    requestAnimationFrame(fn){const id=nextFrame++;frames.set(id,fn);return id;},cancelAnimationFrame(id){frames.delete(id);},
    addEventListener:(name,fn)=>windowListeners.set(name,fn),
    matchMedia(query){
      const limit=Number(query.match(/\d+/)[0]);
      const value={matches:currentWidth<=limit,limit,handlers:[],addEventListener(name,fn){assert.equal(name,'change');this.handlers.push(fn);}};
      media.set(query,value); return value;
    }};
  context.window=context;
  vm.createContext(context); vm.runInContext(script,context,{filename:'layout.js'});
  const api=context.WorkspaceLayout;
  function flushFrames(){const pending=[...frames];for(const [id,fn]of pending)if(frames.delete(id))fn();return pending.length;}
  return {api,get,context,writes,flushFrames,pendingFrames:()=>frames.size,
    blur(){windowListeners.get('blur')?.(event());},
    pointer(name,properties={}){return get('inspector-resizer').emit(name,{pointerId:1,button:0,isPrimary:true,clientX:1000,...properties});},
    resize(width,{flush=true}={}){
      currentWidth=width;const changed=[];
      for(const value of media.values()){const next=width<=value.limit;if(next!==value.matches)changed.push(value);value.matches=next;}
      for(const value of changed)for(const fn of value.handlers)fn({matches:value.matches});
      windowListeners.get('resize')?.(event());if(flush)flushFrames();
    },
    key(properties={}){const value=event({key:'Escape',target:context.document.activeElement,...properties});
      context.document.activeElement.events.get('keydown')?.(value);if(!value.stopped)listeners.get('keydown')(value);return value;}
  };
}
(async()=>{
  try {await vm.runInNewContext(process.argv[3],{assert,boot,console,flush:()=>new Promise(resolve=>setImmediate(resolve))},{filename:'scenario.js'});}
  catch(error){console.error(error.stack||error);process.exitCode=1;}
})();
"""


@unittest.skipUnless(NODE, "Node.js is required for layout UI checks")
class WorkspaceLayoutFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/layout.js"), javascript],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_wide_toggles_keep_icon_navigation_and_store_layout_preferences(self):
        self.run_case(r"""
          const {api,get,writes,key}=boot();
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
          get('sidebar-toggle').click();get('close-materials').click();
          assert.equal(get('app').classList.contains('sidebar-collapsed'),true);
          assert.equal(get('sidebar-panel').hidden,false);assert.equal(get('sidebar-panel').inert,false);
          assert.equal(get('inspector-panel').hidden,true);assert.equal(get('inspector-panel').inert,true);
          assert.equal(get('sidebar-toggle').attributes['aria-expanded'],'false');
          assert.equal(get('materials-button').attributes['aria-controls'],'inspector-panel');
          assert.equal(JSON.stringify(writes.at(-1)),JSON.stringify({sidebarCollapsed:true,inspectorCollapsed:true,inspectorWidth:292,miniMode:false}));
          const reloaded=boot({saved:JSON.stringify(writes.at(-1))});
          assert.equal(reloaded.api.snapshot().sidebarCollapsed,true);assert.equal(reloaded.api.snapshot().inspectorVisible,false);
          api.toggleSidebar();api.openInspector();assert.equal(key().defaultPrevented,false);
          assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
        """)

    def test_inspector_has_one_visible_action_and_preserves_keyboard_focus(self):
        self.run_case(r"""
          for(const width of [640,1000,1440]){
            const {api,get,context,key}=boot({width});
            const opener=get('materials-button'),closer=get('close-materials');
            assert.equal(opener.hidden,api.snapshot().inspectorVisible);
            if(api.snapshot().inspectorVisible)closer.click();
            assert.equal(opener.hidden,false);assert.equal(context.document.activeElement===closer,false);
            opener.focus();opener.click();
            assert.equal(opener.hidden,true);assert.equal(closer.getClientRects().length,1);
            assert.equal(context.document.activeElement,closer);
            closer.click();assert.equal(opener.hidden,false);assert.equal(closer.getClientRects().length,0);
            assert.equal(context.document.activeElement,opener);
            assert.equal(opener.getAttribute('aria-label'),'자료와 결과 펼치기');
          }
        """)

    def test_responsive_panel_restoration_moves_focus_to_visible_action(self):
        self.run_case(r"""
          const {api,get,context,resize,writes}=boot();
          get('close-materials').focus();resize(1000);
          assert.equal(api.snapshot().inspectorVisible,false);assert.equal(get('materials-button').hidden,false);
          assert.equal(context.document.activeElement,get('materials-button'));
          resize(1440);
          assert.equal(api.snapshot().inspectorVisible,true);assert.equal(get('materials-button').hidden,true);
          assert.equal(context.document.activeElement,get('close-materials'));assert.equal(writes.length,0);
          const restored=boot({saved:'{"inspectorCollapsed":true}'});
          restored.get('materials-button').focus();
          restored.api.hydrate({sidebarCollapsed:false,inspectorCollapsed:false,inspectorWidth:400,miniMode:false});
          assert.equal(restored.get('materials-button').hidden,true);
          assert.equal(restored.context.document.activeElement,restored.get('close-materials'));
        """)

    def test_invalid_or_unavailable_storage_does_not_break_panel_controls(self):
        self.run_case(r"""
          for(const saved of ['bad JSON','null','[]','{"sidebarCollapsed":"true","inspectorCollapsed":1}']){
            const {api}=boot({saved});assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,true);
          }
          const {api,writes}=boot({saved:'{"sidebarCollapsed":true,"inspectorCollapsed":false,"token":"do not copy"}'});
          api.toggleInspector();assert.deepEqual(Object.keys(writes[0]).sort(),['inspectorCollapsed','inspectorWidth','miniMode','sidebarCollapsed']);
          const denied=boot({failRead:true,failWrite:true});denied.get('sidebar-toggle').click();denied.get('close-materials').click();
          assert.equal(denied.api.snapshot().sidebarCollapsed,true);assert.equal(denied.api.snapshot().inspectorVisible,false);
        """)

    def test_mini_mode_restores_full_layout_and_keeps_sidebar_changes_temporary(self):
        self.run_case(r"""
          for(const sidebarCollapsed of [false,true])for(const inspectorCollapsed of [false,true]){
            const saved={sidebarCollapsed,inspectorCollapsed,inspectorWidth:480};
            const {api,get,writes}=boot({saved:JSON.stringify(saved)});
            get('mini-mode-toggle').click();
            assert.equal(api.snapshot().miniMode,true);assert.equal(api.snapshot().sidebarCollapsed,false);
            assert.equal(api.snapshot().inspectorVisible,false);assert.equal(get('app').classList.contains('mini-mode'),true);
            assert.equal(get('inspector-panel').hidden,true);assert.equal(get('inspector-panel').inert,true);
            assert.equal(get('inspector-resizer').hidden,true);assert.equal(get('inspector-resizer').tabIndex,-1);
            assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'true');
            assert.equal(get('mini-mode-toggle').getAttribute('aria-label'),'간편 화면');
            assert.equal(writes.length,1);assert.equal(JSON.stringify(writes[0]),JSON.stringify({...saved,miniMode:true}));
            api.toggleSidebar();assert.equal(api.snapshot().sidebarCollapsed,true);
            assert.equal(api.snapshot().preferences.sidebarCollapsed,sidebarCollapsed);assert.equal(writes.length,1);
            api.closeInspector();assert.equal(api.snapshot().preferences.inspectorCollapsed,inspectorCollapsed);
            api.refresh();assert.equal(api.snapshot().miniMode,true);assert.equal(writes.length,1);
            api.toggleMiniMode();
            assert.equal(api.snapshot().sidebarCollapsed,sidebarCollapsed);assert.equal(api.snapshot().inspectorVisible,!inspectorCollapsed);
            assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'false');
            assert.equal(get('mini-mode-toggle').getAttribute('aria-label'),'간편 화면');
            assert.equal(api.snapshot().preferences.inspectorWidth,480);assert.equal(writes.length,2);
            assert.equal(JSON.stringify(writes[1]),JSON.stringify({...saved,miniMode:false}));
          }
        """)

    def test_mini_mode_persistence_accepts_only_boolean_and_survives_denied_storage(self):
        self.run_case(r"""
          for(const raw of ['"true"','1','null','[]','{}']){
            const {api,get,writes}=boot({saved:`{"miniMode":${raw}}`});
            assert.equal(api.snapshot().miniMode,false);assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'false');
            assert.equal(writes.length,0);
          }
          const saved='{"miniMode":true,"sidebarCollapsed":true,"inspectorCollapsed":false,"inspectorWidth":440,"token":"ignored"}';
          const {api,get,writes}=boot({saved});
          assert.equal(api.snapshot().miniMode,true);assert.equal(api.snapshot().sidebarCollapsed,false);
          assert.equal(api.snapshot().inspectorVisible,false);assert.equal(writes.length,0);
          assert.equal(api.setMiniMode(true).miniMode,true);assert.equal(writes.length,0);
          api.toggleSidebar();api.setMiniMode(false);
          assert.deepEqual(Object.keys(writes[0]).sort(),['inspectorCollapsed','inspectorWidth','miniMode','sidebarCollapsed']);
          assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,true);
          assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'440');
          api.setMiniMode(true);const reloaded=boot({saved:JSON.stringify(writes.at(-1))});
          assert.equal(reloaded.api.snapshot().miniMode,true);assert.equal(reloaded.api.snapshot().sidebarCollapsed,false);
          const denied=boot({failRead:true,failWrite:true});denied.api.toggleMiniMode();
          assert.equal(denied.api.snapshot().miniMode,true);denied.api.toggleMiniMode();
          assert.equal(denied.api.snapshot().miniMode,false);assert.equal(denied.api.snapshot().inspectorVisible,true);
        """)

    def test_mini_mode_dismisses_popovers_without_editing_or_blurring_prompt(self):
        self.run_case(r"""
          const {api,get,context,writes}=boot(),prompt=get('prompt');let controlsClosed=0,suggestionsClosed=0;
          context.WorkspaceInlineControls={close(options){assert.equal(options.focus,false);controlsClosed++;get('composer-controls-panel').hidden=true;}};
          context.WorkspaceComposer={close(){suggestionsClosed++;get('composer-suggestions').hidden=true;}};
          prompt.value='아직 보내지 않은 요청';prompt.selectionStart=3;prompt.selectionEnd=8;prompt.focus();
          get('composer-controls-panel').hidden=get('composer-suggestions').hidden=false;
          api.setMiniMode(true);
          assert.equal(context.document.activeElement,prompt);assert.equal(prompt.value,'아직 보내지 않은 요청');
          assert.equal(prompt.selectionStart,3);assert.equal(prompt.selectionEnd,8);
          assert.equal(controlsClosed,1);assert.equal(suggestionsClosed,1);assert.equal(writes.length,1);
          api.setMiniMode(true);assert.equal(controlsClosed,1);assert.equal(suggestionsClosed,1);
          api.setMiniMode(false);get('composer-controls-panel').hidden=false;get('composer-controls-panel').focus();api.setMiniMode(true);
          assert.equal(context.document.activeElement,get('mini-mode-toggle'));assert.equal(prompt.value,'아직 보내지 않은 요청');
          api.setMiniMode(false);get('materials-button').getClientRects=()=>get('app').classList.contains('mini-mode')?[]:[{}];
          get('materials-button').focus();api.setMiniMode(true);assert.equal(context.document.activeElement,get('mini-mode-toggle'));
        """)

    def test_mini_mode_closes_overlays_and_keeps_mobile_navigation_and_exit_accessible(self):
        self.run_case(r"""
          for(const panel of ['sidebar','inspector']){
            const {api,get,context,writes,key,resize}=boot({width:640,saved:'{"sidebarCollapsed":true,"inspectorCollapsed":false,"inspectorWidth":460}'});
            if(panel==='sidebar'){api.toggleSidebar();get('session-button').focus();}
            else{api.openInspector();get('inspector-content').focus();}
            api.setMiniMode(true);
            assert.equal(api.snapshot().sidebarOverlay,false);assert.equal(api.snapshot().inspectorOverlay,false);
            assert.equal(get('layout-backdrop').hidden,true);assert.equal(get('main').inert,false);
            assert.equal(context.document.activeElement,get('mini-mode-toggle'));
            assert.equal(get('inspector-panel').getAttribute('aria-modal'),null);assert.equal(get('sidebar-panel').getAttribute('aria-modal'),null);
            api.toggleSidebar();assert.equal(api.snapshot().sidebarOverlay,true);assert.equal(get('main').inert,true);
            assert.equal(key({key:'Tab',shiftKey:true}).defaultPrevented,true);assert.equal(context.document.activeElement,get('help'));
            assert.equal(key().defaultPrevented,true);assert.equal(api.snapshot().sidebarOverlay,false);
            assert.equal(get('main').inert,false);assert.equal(context.document.activeElement,get('sidebar-toggle'));
            api.toggleSidebar();resize(1101);assert.equal(api.snapshot().sidebarCollapsed,false);assert.equal(api.snapshot().inspectorVisible,false);
            resize(640);assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(writes.length,1);
            get('mini-mode-toggle').click();assert.equal(api.snapshot().miniMode,false);assert.equal(writes.length,2);
            resize(1440);assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,true);
            assert.equal(api.snapshot().preferences.inspectorWidth,460);
          }
        """)

    def test_explicit_inspector_request_leaves_mini_and_restores_requested_panel(self):
        self.run_case(r"""
          for(const width of [640,1000,1440]){
            const {api,get,context}=boot({width,saved:'{"miniMode":true,"sidebarCollapsed":true,"inspectorCollapsed":true,"inspectorWidth":420}'});
            api.closeInspector();assert.equal(api.snapshot().miniMode,true);assert.equal(api.snapshot().preferences.inspectorCollapsed,true);
            api.openInspector();assert.equal(api.snapshot().miniMode,false);assert.equal(api.snapshot().inspectorVisible,true);
            assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'false');
            assert.equal(api.snapshot().preferences.sidebarCollapsed,true);assert.equal(api.snapshot().preferences.inspectorWidth,420);
            if(width<=1100){
              assert.equal(api.snapshot().inspectorOverlay,true);assert.equal(context.document.activeElement,get('close-materials'));
              assert.equal(api.snapshot().preferences.inspectorCollapsed,true);
            }else assert.equal(api.snapshot().preferences.inspectorCollapsed,false);
          }
        """)

    def test_mini_mode_cancels_drag_and_recovers_hidden_surface_focus(self):
        self.run_case(r"""
          const {api,get,context,pointer,flushFrames,pendingFrames,writes}=boot({saved:'{"inspectorWidth":400}'});
          pointer('pointerdown');pointer('pointermove',{clientX:950});flushFrames();
          pointer('pointermove',{clientX:930});assert.equal(pendingFrames(),1);api.setMiniMode(true);flushFrames();
          assert.equal(context.document.activeElement,get('mini-mode-toggle'));assert.equal(get('inspector-resizer').hasPointerCapture(1),false);
          assert.equal(get('inspector-resizer').hidden,true);assert.equal(get('inspector-resizer').inert,true);
          assert.equal(get('app').classList.contains('inspector-resizing'),false);assert.equal(pendingFrames(),0);
          assert.equal(writes.length,1);assert.equal(writes[0].inspectorWidth,400);
          assert.equal(pointer('pointerdown').defaultPrevented,false);pointer('pointerup',{clientX:930});assert.equal(writes.length,1);
          api.setMiniMode(false);assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'400');
          get('inspector-content').focus();api.setMiniMode(true);assert.equal(context.document.activeElement,get('mini-mode-toggle'));
          get('inspector-content').focus();api.refresh();assert.equal(context.document.activeElement,get('mini-mode-toggle'));
        """)

    def test_mini_mode_closes_catalog_through_existing_api_only_when_enabled(self):
        self.run_case(r"""
          const {api,get,context,writes}=boot();let closes=0;
          context.WorkspaceCapabilities={close(){closes++;get('app').classList.remove('catalog-open');api.refresh();}};
          get('app').classList.add('catalog-open');api.refresh();api.setMiniMode(true);
          assert.equal(closes,1);assert.equal(get('app').classList.contains('catalog-open'),false);
          assert.equal(get('app').classList.contains('mini-mode'),true);assert.equal(api.snapshot().inspectorVisible,false);
          api.refresh();assert.equal(api.snapshot().miniMode,true);assert.equal(closes,1);
          get('app').classList.add('chat-mode');api.refresh();assert.equal(api.snapshot().miniMode,true);
          api.setMiniMode(false);assert.equal(closes,1);assert.equal(get('app').classList.contains('chat-mode'),true);
          assert.equal(api.snapshot().inspectorVisible,false);assert.equal(writes.length,2);
          get('app').classList.remove('chat-mode');api.refresh();assert.equal(api.snapshot().inspectorVisible,true);
        """)

    def test_server_hydration_restores_new_origin_and_is_clean_idempotent_and_whitelisted(self):
        self.run_case(r"""(async()=>{
          const requests=[],saved={sidebarCollapsed:true,inspectorCollapsed:false,inspectorWidth:470,miniMode:true,token:'ignore'};
          const {api,get,writes,context}=boot({request:async(path,value)=>{requests.push({path,value});return value;}});
          get('prompt').value='복원 중 작성한 내용';get('prompt').selectionStart=4;get('prompt').selectionEnd=7;
          api.hydrate(saved);await flush();
          assert.equal(api.snapshot().miniMode,true);assert.equal(api.snapshot().sidebarCollapsed,false);
          assert.equal(api.snapshot().inspectorVisible,false);assert.equal(api.snapshot().preferences.sidebarCollapsed,true);
          assert.equal(api.snapshot().preferences.inspectorWidth,470);assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'true');
          assert.equal(requests.length,0);assert.equal(writes.length,1);
          assert.deepEqual(Object.keys(writes[0]).sort(),['inspectorCollapsed','inspectorWidth','miniMode','sidebarCollapsed']);
          assert.equal(context.document.activeElement,get('prompt'));assert.equal(get('prompt').value,'복원 중 작성한 내용');
          assert.equal(get('prompt').selectionStart,4);assert.equal(get('prompt').selectionEnd,7);
          api.hydrate({miniMode:false,inspectorWidth:292});assert.equal(api.snapshot().miniMode,true);assert.equal(writes.length,1);
          api.setMiniMode(false);await flush();assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/layout');
          assert.equal(requests[0].value.miniMode,false);assert.equal(requests[0].value.inspectorWidth,470);
          assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,true);
        })()""")

    def test_server_hydration_precedes_local_cache_and_missing_server_value_keeps_local(self):
        self.run_case(r"""
          const local=JSON.stringify({miniMode:true,sidebarCollapsed:true,inspectorCollapsed:true,inspectorWidth:500});
          const server={miniMode:false,sidebarCollapsed:false,inspectorCollapsed:false,inspectorWidth:350};
          const authoritative=boot({saved:local});authoritative.api.hydrate(server);
          assert.equal(authoritative.api.snapshot().miniMode,false);assert.equal(authoritative.api.snapshot().sidebarCollapsed,false);
          assert.equal(authoritative.api.snapshot().inspectorVisible,true);assert.equal(authoritative.api.snapshot().preferences.inspectorWidth,350);
          for(const saved of [null,undefined,'invalid',[]]){
            const state=boot({saved:local});state.api.hydrate(saved);
            assert.equal(state.api.snapshot().miniMode,true);assert.equal(state.api.snapshot().preferences.inspectorWidth,500);
            assert.equal(state.writes.length,0);state.api.setMiniMode(false);assert.equal(state.api.snapshot().sidebarCollapsed,true);
          }
          const fast=boot({bootstrap:{sessions:[],layout:server}});
          assert.equal(fast.api.snapshot().preferences.inspectorWidth,350);assert.equal(fast.writes.length,1);
          fast.api.hydrate({miniMode:true});assert.equal(fast.api.snapshot().miniMode,false);
        """)

    def test_user_changes_before_bootstrap_win_and_save_only_current_preferences(self):
        self.run_case(r"""(async()=>{
          const requests=[],{api,get,writes}=boot({request:async(path,value)=>{requests.push({path,value});return value;}});
          api.toggleSidebar();api.setMiniMode(true);api.toggleSidebar();
          assert.equal(requests.length,0);assert.equal(writes.length,2);
          api.hydrate({miniMode:false,sidebarCollapsed:false,inspectorCollapsed:true,inspectorWidth:540});await flush();
          assert.equal(api.snapshot().miniMode,true);assert.equal(api.snapshot().sidebarCollapsed,true);
          assert.equal(api.snapshot().preferences.sidebarCollapsed,true);assert.equal(api.snapshot().preferences.inspectorCollapsed,false);
          assert.equal(api.snapshot().preferences.inspectorWidth,292);assert.equal(writes.length,2);
          assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/layout');
          assert.equal(JSON.stringify(requests[0].value),JSON.stringify(api.snapshot().preferences));
          api.hydrate({miniMode:false});api.refresh();assert.equal(api.snapshot().miniMode,true);assert.equal(requests.length,1);
          assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'true');
        })()""")

    def test_layout_saves_are_single_inflight_and_coalesce_latest_without_stale_response(self):
        self.run_case(r"""(async()=>{
          const requests=[],pending=[];let inflight=0,maximum=0;
          const {api,get,writes,resize}=boot({request:(path,value)=>{
            requests.push({path,value});inflight++;maximum=Math.max(maximum,inflight);
            return new Promise(resolve=>pending.push(result=>{inflight--;resolve(result);}));
          }});
          api.hydrate(null);for(let index=0;index<5;index++)api.toggleMiniMode();
          assert.equal(requests.length,1);assert.equal(writes.length,5);assert.equal(api.snapshot().miniMode,true);
          assert.equal(requests[0].value.miniMode,true);assert.equal(maximum,1);
          pending.shift()({miniMode:false});await flush();
          assert.equal(api.snapshot().miniMode,true);assert.equal(requests.length,2);assert.equal(requests[1].value.miniMode,true);
          assert.equal(maximum,1);assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'true');
          pending.shift()(requests[1].value);await flush();api.refresh();resize(1000);api.toggleSidebar();
          assert.equal(requests.length,2);assert.equal(inflight,0);assert.equal(writes.length,5);
          for(const row of requests){assert.equal(row.path,'/api/layout');assert.deepEqual(Object.keys(row.value).sort(),['inspectorCollapsed','inspectorWidth','miniMode','sidebarCollapsed']);}
        })()""")

    def test_failed_layout_save_keeps_live_state_warns_once_and_does_not_retry(self):
        self.run_case(r"""(async()=>{
          const requests=[],notices=[],pending=[];
          const {api,get,writes}=boot({request:(path,value)=>{
            requests.push({path,value});return new Promise((resolve,reject)=>pending.push({resolve,reject}));
          },notify:message=>notices.push(message)});
          api.hydrate(null);api.setMiniMode(true);api.setMiniMode(false);
          pending.shift().reject(Error('storage unavailable'));await flush();
          assert.equal(api.snapshot().miniMode,false);assert.equal(get('mini-mode-toggle').getAttribute('aria-pressed'),'false');
          assert.equal(writes.at(-1).miniMode,false);assert.equal(requests.length,2);assert.equal(notices.length,1);
          assert.equal(requests[1].value.miniMode,false);pending.shift().reject(Error('still unavailable'));await flush();
          assert.match(notices[0],/화면 설정/);api.refresh();await flush();assert.equal(requests.length,2);
          api.setMiniMode(true);assert.equal(requests.length,3);pending.shift().resolve(requests[2].value);await flush();
          assert.equal(notices.length,1);assert.equal(api.snapshot().miniMode,true);assert.equal(writes.at(-1).miniMode,true);
          const deniedRequests=[],denied=boot({failWrite:true,request:async(path,value)=>deniedRequests.push({path,value})});
          denied.api.hydrate(null);denied.api.setMiniMode(true);await flush();
          assert.equal(deniedRequests.length,1);assert.equal(denied.api.snapshot().miniMode,true);
        })()""")

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
          for(const id of ['home-button','new-chat','import-open','capabilities-open','settings-open','schedule-overview-open','shortcuts-open','help']){
            const {api,get,context,writes}=boot({width:600});api.toggleSidebar();get(id).focus();get(id).click();
            assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(get('layout-backdrop').hidden,true);
            assert.equal(get('main').inert,false);assert.equal(context.document.activeElement,get(id));assert.equal(writes.length,0);
          }
          const {api,get,context,key}=boot({width:600});api.openInspector();context.modal={open:true};
          get('prompt').focus();assert.equal(context.document.activeElement,get('prompt'));
          assert.equal(key({key:'Tab'}).defaultPrevented,false);assert.equal(key().defaultPrevented,false);
          assert.equal(api.snapshot().inspectorOverlay,true);
        """)

    def test_inspector_width_validates_saved_values_without_discarding_old_preferences(self):
        self.run_case(r"""
          for(const [raw,expected] of [['420',420],['421.6',422],['0',260],['900',560],['1e400',292],
            ['"400"',292],['true',292],['null',292],['{}',292]]){
            const {api,get,writes}=boot({saved:`{"sidebarCollapsed":true,"inspectorCollapsed":false,"inspectorWidth":${raw}}`});
            assert.equal(api.snapshot().preferences.inspectorWidth,expected);
            assert.equal(api.snapshot().sidebarCollapsed,true);assert.equal(api.snapshot().inspectorVisible,true);
            assert.equal(get('app').style.getPropertyValue('--inspector-resized-width'),`${expected}px`);
            assert.equal(get('app').style.getPropertyValue('--inspector-width'),'');assert.equal(writes.length,0);
          }
          const old=boot({saved:'{"sidebarCollapsed":false,"inspectorCollapsed":true}'});
          assert.equal(old.api.snapshot().preferences.inspectorWidth,292);old.api.openInspector();
          assert.equal(old.writes[0].inspectorWidth,292);assert.equal(old.writes[0].inspectorCollapsed,false);
          const denied=boot({failRead:true,failWrite:true});denied.get('inspector-resizer').focus();
          denied.key({key:'ArrowLeft'});assert.equal(denied.api.snapshot().preferences.inspectorWidth,302);
        """)

    def test_dynamic_maximum_uses_container_and_current_sidebar_without_overwriting_saved_width(self):
        self.run_case(r"""
          const {api,get,resize,writes}=boot({saved:'{"inspectorWidth":500}'}),handle=get('inspector-resizer');
          resize(1101);assert.equal(get('app').style.getPropertyValue('--inspector-resized-width'),'433px');
          assert.equal(handle.getAttribute('aria-valuemax'),'433');assert.equal(api.snapshot().preferences.inspectorWidth,500);
          assert.equal(writes.length,0);api.toggleSidebar();
          assert.equal(get('app').style.getPropertyValue('--inspector-resized-width'),'500px');
          assert.equal(handle.getAttribute('aria-valuemax'),'560');assert.equal(writes[0].inspectorWidth,500);
          api.toggleSidebar();assert.equal(get('app').style.getPropertyValue('--inspector-resized-width'),'433px');
          resize(1440);assert.equal(get('app').style.getPropertyValue('--inspector-resized-width'),'500px');
          const constrained=boot({width:1440,appWidth:1120,saved:'{"inspectorWidth":560}'});
          assert.equal(constrained.get('inspector-resizer').getAttribute('aria-valuenow'),'452');
          constrained.get('sidebar-panel').rectWidth=320;constrained.api.refresh();
          assert.equal(constrained.get('inspector-resizer').getAttribute('aria-valuenow'),'380');
          assert.equal(constrained.api.snapshot().preferences.inspectorWidth,560);assert.equal(constrained.writes.length,0);
        """)

    def test_pointer_moves_coalesce_and_only_completed_change_is_saved(self):
        self.run_case(r"""
          const {api,get,pointer,flushFrames,pendingFrames,writes}=boot(),handle=get('inspector-resizer'),app=get('app');
          const styleCount=app.styleWrites.length,ariaCount=handle.attributeWrites.length;
          assert.equal(pointer('pointerdown').defaultPrevented,true);assert.equal(handle.hasPointerCapture(1),true);
          assert.equal(app.classList.contains('inspector-resizing'),true);
          for(let offset=1;offset<=108;offset++)pointer('pointermove',{clientX:1000-offset});
          assert.equal(pendingFrames(),1);assert.equal(app.styleWrites.length,styleCount);assert.equal(handle.attributeWrites.length,ariaCount);
          assert.equal(writes.length,0);assert.equal(api.snapshot().preferences.inspectorWidth,292);
          flushFrames();assert.equal(app.styleWrites.length,styleCount+1);assert.equal(handle.getAttribute('aria-valuenow'),'400');
          assert.equal(writes.length,0);pointer('pointerup',{clientX:892});
          assert.equal(writes.length,1);assert.equal(writes[0].inspectorWidth,400);assert.equal(pendingFrames(),0);
          assert.equal(handle.hasPointerCapture(1),false);assert.equal(app.classList.contains('inspector-resizing'),false);
          assert.equal(app.styleWrites.length,styleCount+1);
          const restored=boot({saved:JSON.stringify(writes[0])});assert.equal(restored.get('inspector-resizer').getAttribute('aria-valuenow'),'400');
        """)

    def test_clicks_and_drag_return_do_not_save_and_double_click_resets_once(self):
        self.run_case(r"""
          const {get,pointer,writes}=boot({width:1101,saved:'{"inspectorWidth":560}'});
          for(let count=0;count<2;count++){pointer('pointerdown');pointer('pointerup');}
          assert.equal(writes.length,0);
          pointer('pointerdown');pointer('pointermove',{clientX:1050});pointer('pointermove',{clientX:1000});pointer('pointerup');
          assert.equal(writes.length,0);assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'433');
          get('inspector-resizer').emit('dblclick');assert.equal(writes.length,1);assert.equal(writes[0].inspectorWidth,292);
          get('inspector-resizer').emit('dblclick');assert.equal(writes.length,1);
          const unchanged=boot();unchanged.get('inspector-resizer').emit('dblclick');assert.equal(unchanged.writes.length,0);
        """)

    def test_cancel_capture_loss_escape_blur_and_refresh_restore_starting_width(self):
        self.run_case(r"""
          for(const cancel of [state=>state.pointer('pointercancel'),state=>state.pointer('lostpointercapture'),
            state=>assert.equal(state.key().defaultPrevented,true),state=>state.blur(),state=>state.api.refresh()]){
            const state=boot({saved:'{"inspectorWidth":420}'}),{api,get,pointer,flushFrames,pendingFrames,writes}=state;
            pointer('pointerdown');pointer('pointermove',{clientX:900});flushFrames();
            assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'520');
            pointer('pointermove',{clientX:890});assert.equal(pendingFrames(),1);cancel(state);flushFrames();
            assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'420');assert.equal(api.snapshot().preferences.inspectorWidth,420);
            assert.equal(get('app').classList.contains('inspector-resizing'),false);
            assert.equal(get('inspector-resizer').hasPointerCapture(1),false);assert.equal(writes.length,0);assert.equal(pendingFrames(),0);
            pointer('pointerup',{clientX:890});assert.equal(writes.length,0);
          }
        """)

    def test_separator_keyboard_boundaries_and_accessibility(self):
        self.run_case(r"""
          const {api,get,key,writes,context}=boot({width:1101}),handle=get('inspector-resizer');handle.focus();
          for(const [name,value] of Object.entries({role:'separator','aria-orientation':'vertical','aria-controls':'inspector-panel',
            'aria-valuemin':'260','aria-valuemax':'433','aria-valuenow':'292','aria-valuetext':'292픽셀'}))assert.equal(handle.getAttribute(name),value);
          assert.equal(handle.hidden,false);assert.equal(handle.inert,false);assert.equal(handle.tabIndex,0);
          assert.equal(key({key:'ArrowLeft'}).defaultPrevented,true);assert.equal(api.snapshot().preferences.inspectorWidth,302);
          key({key:'ArrowLeft',shiftKey:true});assert.equal(api.snapshot().preferences.inspectorWidth,352);
          key({key:'ArrowRight',shiftKey:true});assert.equal(api.snapshot().preferences.inspectorWidth,302);
          key({key:'ArrowRight'});assert.equal(api.snapshot().preferences.inspectorWidth,292);
          key({key:'Home'});assert.equal(api.snapshot().preferences.inspectorWidth,260);
          key({key:'Home'});assert.equal(writes.length,5);key({key:'ArrowRight'});assert.equal(writes.length,5);
          key({key:'End'});assert.equal(api.snapshot().preferences.inspectorWidth,433);
          key({key:'ArrowLeft'});key({key:'End'});assert.equal(writes.length,6);
          key({key:'Enter'});assert.equal(api.snapshot().preferences.inspectorWidth,292);assert.equal(writes.length,7);
          key({key:'Enter'});assert.equal(writes.length,7);assert.equal(context.document.activeElement,handle);
          for(const options of [{key:'ArrowLeft',ctrlKey:true},{key:'ArrowLeft',altKey:true},{key:'ArrowLeft',metaKey:true},
            {key:'ArrowLeft',isComposing:true},{key:'Unidentified'}])assert.equal(key(options).defaultPrevented,false);
          assert.equal(writes.length,7);
        """)

    def test_hiding_or_switching_modes_cancels_drag_and_returns_separator_focus(self):
        self.run_case(r"""
          for(const mode of ['catalog-open','chat-mode']){
            const {api,get,pointer,flushFrames,context,writes}=boot({saved:'{"inspectorWidth":400}'});
            pointer('pointerdown');pointer('pointermove',{clientX:950});flushFrames();get('app').classList.add(mode);api.refresh();
            const handle=get('inspector-resizer');assert.equal(handle.hidden,true);assert.equal(handle.inert,true);assert.equal(handle.tabIndex,-1);
            assert.equal(context.document.activeElement,get('materials-button'));assert.equal(writes.length,0);
            assert.equal(get('app').classList.contains('inspector-resizing'),false);
            get('app').classList.remove(mode);api.refresh();assert.equal(handle.hidden,false);assert.equal(handle.getAttribute('aria-valuenow'),'400');
          }
          const state=boot();state.pointer('pointerdown');state.pointer('pointermove',{clientX:900});state.api.closeInspector();
          assert.equal(state.get('inspector-resizer').hidden,true);assert.equal(state.writes[0].inspectorWidth,292);
          assert.equal(state.context.document.activeElement,state.get('materials-button'));state.api.openInspector();
          assert.equal(state.get('inspector-resizer').getAttribute('aria-valuenow'),'292');
        """)

    def test_window_resize_is_coalesced_and_overlay_keeps_width_and_focus_contract(self):
        self.run_case(r"""
          const {api,get,resize,flushFrames,pendingFrames,pointer,writes,context,key}=boot({saved:'{"inspectorWidth":560}'});
          resize(1300,{flush:false});resize(1200,{flush:false});assert.equal(pendingFrames(),1);
          assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'560');flushFrames();
          assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'532');assert.equal(writes.length,0);
          pointer('pointerdown');pointer('pointermove',{clientX:1100});resize(1180);assert.equal(get('app').classList.contains('inspector-resizing'),false);
          assert.equal(api.snapshot().preferences.inspectorWidth,560);assert.equal(writes.length,0);
          resize(1000);assert.equal(get('inspector-resizer').hidden,true);assert.equal(get('inspector-resizer').tabIndex,-1);
          assert.equal(context.document.activeElement,get('materials-button'));api.openInspector();
          assert.equal(api.snapshot().inspectorOverlay,true);assert.equal(context.document.activeElement,get('close-materials'));
          resize(1001);assert.equal(api.snapshot().inspectorOverlay,true);assert.equal(get('main').inert,true);
          assert.equal(get('inspector-resizer').inert,true);assert.equal(get('inspector-panel').getAttribute('aria-modal'),'true');
          assert.equal(key({key:'Tab',shiftKey:true}).defaultPrevented,true);
          assert.equal(context.document.activeElement,get('inspector-content'));
          resize(1440);assert.equal(api.snapshot().inspectorOverlay,false);assert.equal(get('inspector-resizer').hidden,false);
          assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'560');assert.equal(writes.length,0);
        """)

    def test_pointer_filters_disabled_handle_and_unrelated_pointer_events(self):
        self.run_case(r"""
          const {api,get,pointer,flushFrames,writes}=boot();
          for(const options of [{button:2},{isPrimary:false}])assert.equal(pointer('pointerdown',options).defaultPrevented,false);
          assert.equal(get('app').classList.contains('inspector-resizing'),false);
          pointer('pointerdown');pointer('pointermove',{pointerId:2,clientX:800});pointer('pointercancel',{pointerId:2});
          pointer('pointerup',{pointerId:2,clientX:800});flushFrames();
          assert.equal(get('app').classList.contains('inspector-resizing'),true);assert.equal(get('inspector-resizer').getAttribute('aria-valuenow'),'292');
          pointer('pointerup');assert.equal(writes.length,0);
          for(const width of [1100,700,640]){
            const state=boot({width});state.api.openInspector();assert.equal(state.pointer('pointerdown').defaultPrevented,false);
            state.get('inspector-resizer').emit('keydown',{key:'ArrowLeft'});state.get('inspector-resizer').emit('dblclick');
            assert.equal(state.writes.length,0);assert.equal(state.get('app').classList.contains('inspector-resizing'),false);
          }
          api.closeInspector();assert.equal(pointer('pointerdown').defaultPrevented,false);
        """)


if __name__ == "__main__":
    unittest.main()
