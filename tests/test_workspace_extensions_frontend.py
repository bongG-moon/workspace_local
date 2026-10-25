"""Run the optional extension host, including isolation and close races, in Node.

The host's source is exercised directly. No installed extension, Claude profile,
network service, or application state is touched by these checks.
"""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const created = [], listeners = new Set(), observers = [], calls = [], opened = [];
class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.style={};this.hidden=false;this.isConnected=false;this.textContent='';this.classes=new Set();this.classList={add:(...values)=>values.forEach(x=>this.classes.add(x)),remove:(...values)=>values.forEach(x=>this.classes.delete(x)),contains:value=>this.classes.has(value)};created.push(this);
    if(tag==='iframe')this.contentWindow={received:[],postMessage(data,target){this.received.push({data,target});}};
  }
  connect(value){this.isConnected=value;this.children.forEach(child=>child.connect(value));}
  append(...children){for(const child of children){child.parent=this;child.connect(this.isConnected);this.children.push(child);}}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;this.connect(false);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  focus(){context.document.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
}
const body=new Element('body'), head=new Element('head'), html=new Element('html'), nav=new Element('nav'), app=new Element('div'), area=new Element('div');area.id='work-area';
body.isConnected=head.isConnected=html.isConnected=true;body.append(app);app.append(nav,area);html.dataset.theme='light';
const find=(node,id)=>node.id===id?node:node.children.map(x=>find(x,id)).find(Boolean);
let seed=0, saved=0, closed=false, viewChanges=[], state={sessionId:'task-7',kind:'work',workspace:'D:/example',title:'My task'};
let apiHandler=async(path,data)=>path.endsWith('/view')?{url:'/extension-ui/example.store?ticket=one-use'}:{ok:true,result:{status:'done'}};
const context={assert,console,URL,URLSearchParams,Uint8Array,Set,Map,Promise,location:{origin:'http://127.0.0.1:8790'},
  crypto:{getRandomValues(bytes){bytes.fill(++seed);return bytes;}},
  document:{body,head,documentElement:html,activeElement:null,createElement:tag=>new Element(tag),querySelector:selector=>selector==='.primary-nav'?nav:selector==='.app'?app:null,getElementById:id=>find(body,id)},
  addEventListener(type,fn){assert.equal(type,'message');listeners.add(fn);},removeEventListener(type,fn){listeners.delete(fn);},
  MutationObserver:class {constructor(fn){this.fn=fn;this.disconnected=false;observers.push(this);}observe(node,opts){this.opts=opts;}disconnect(){this.disconnected=true;}},
  open(...args){opened.push(args);},setTimeout(){throw Error('unexpected timer');},setInterval(){throw Error('unexpected polling');}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'extensions.js'});
const host=context.WorkspaceExtensions, extension={id:'example.store',name:'Feature store',version:'1.0',hostApi:1,actions:['catalog','install']};
const options=()=>({extensions:[extension],api:async(...args)=>{calls.push(args);return apiHandler(...args);},beforeOpen:()=>{saved++;},viewChanged:opened=>viewChanges.push(opened),isClosed:()=>closed,getContext:()=>state,showDialog:id=>find(body,id).showModal()});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const currentFrame=()=>created.filter(x=>x.tagName==='iframe'&&x.isConnected).at(-1);
async function dispatch(frame,data,overrides={}){for(const listener of [...listeners])listener({source:frame.contentWindow,origin:'null',data,...overrides});await flush();}
async function ready(frame=currentFrame()){await dispatch(frame,{type:'ax-extension-ready'});return frame.contentWindow.received.find(x=>x.data.type==='ax-extension-init').data;}
async function request(frame,init,id,action='catalog',payload={}){await dispatch(frame,{type:'ax-extension-request',channel:init.channel,requestId:id,action,payload});}
const response=(frame,id)=>frame.contentWindow.received.find(x=>x.data.type==='ax-extension-response'&&x.data.requestId===id)?.data;
const scenario=process.argv[3];
(async()=>{await eval('(async()=>{'+scenario+'})()');})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
"""


@unittest.skipUnless(NODE, "Node.js is required for frontend host checks")
class WorkspaceExtensionsFrontendTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/extensions.js"), javascript],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_no_extension_is_a_zero_activity_fast_path(self):
        self.run_case("""const count=created.length;
          host.mount({extensions:[]});host.mount({});host.stop();
          assert.equal(created.length,count);assert.equal(nav.children.length,0);
          assert.equal(calls.length,0);assert.equal(listeners.size,0);assert.equal(observers.length,0);
          assert.equal(head.children.length,0);assert.equal(saved,0);""")

    def test_metadata_is_text_and_only_valid_installed_entries_add_navigation(self):
        self.run_case("""const opts=options();opts.extensions=[{...extension,name:'<img src=x onerror=alert(1)>'},extension,
          {...extension,id:'../bad'}, {...extension,id:'bad/api'}, {...extension,id:'unsupported',hostApi:2}];
          host.mount(opts);assert.equal(nav.children.length,1);assert.equal(calls.length,0);
          assert.equal(nav.children[0].children[0].textContent,opts.extensions[0].name);
          assert.ok(!nav.children[0].innerHTML.includes('<img'));assert.equal(head.children.length,1);
          assert.equal(nav.classList.contains('workspace-extension-navigation'),true);""")

    def test_invalid_entries_do_not_unhide_chat_navigation_or_load_styles(self):
        self.run_case("""const opts=options();opts.extensions=[{...extension,id:'../bad'}];host.mount(opts);
          assert.equal(nav.children.length,0);assert.equal(head.children.length,0);
          assert.equal(nav.classList.contains('workspace-extension-navigation'),false);""")

    def test_open_saves_draft_uses_opaque_sandbox_and_shares_only_context(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(), init=await ready();
          assert.equal(saved,1);assert.equal(head.children.length,1);assert.equal(listeners.size,1);
          assert.equal(host.isOpen(),true);assert.equal(host.title(),'Feature store');
          assert.equal(find(body,'workspace-extension-page').tagName,'section');
          assert.equal(find(body,'workspace-extension-page').parent,area);assert.equal(created.some(x=>x.tagName==='dialog'),false);
          assert.equal(app.classList.contains('extension-open'),true);assert.equal(app.classList.contains('catalog-open'),true);
          assert.deepEqual(viewChanges,[true]);
          assert.equal(frame.getAttribute('sandbox'),'allow-scripts');assert.equal(frame.getAttribute('referrerpolicy'),'no-referrer');
          assert.equal(frame.src,'/extension-ui/example.store?ticket=one-use');assert.match(init.channel,/^[0-9a-f]{32}$/);
          assert.equal(init.theme,'light');assert.equal(init.hostApi,1);assert.equal(init.context.workspace,'D:/example');
          assert.equal(init.context.sessionId,undefined);assert.equal(init.token,undefined);
          assert.equal(calls[0][0],'/api/extensions/example.store/view');
          state={...state,sessionId:'different',workspace:'D:/different'};
          await request(frame,init,'r1');assert.equal(calls[1][1].sessionId,'task-7');""")

    def test_general_chat_does_not_share_workspace(self):
        self.run_case("""state={sessionId:'chat-1',kind:'chat',workspace:'D:/must-not-share',title:'Chat'};
          host.mount(options());await nav.children[0].onclick();const init=await ready();
          assert.equal(init.context.kind,'chat');assert.equal(init.context.workspace,'');""")

    def test_spoofed_messages_and_wrong_channel_are_ignored(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame();
          await dispatch(frame,{type:'ax-extension-ready'},{source:{}});assert.equal(frame.contentWindow.received.length,0);
          await dispatch(frame,{type:'ax-extension-ready'},{origin:'http://127.0.0.1:8790'});assert.equal(frame.contentWindow.received.length,0);
          const init=await ready();await dispatch(frame,{type:'ax-extension-request',channel:'wrong',requestId:'wrong',action:'install',payload:{}});
          await dispatch(frame,{type:'ax-extension-request',channel:init.channel,requestId:'spoof',action:'install',payload:{}},{source:{}});
          assert.equal(calls.length,1);assert.equal(response(frame,'wrong'),undefined);""")

    def test_action_allowlist_payload_limit_and_request_replay(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(),init=await ready();
          await request(frame,init,'unknown','anything');assert.equal(response(frame,'unknown').ok,false);
          await request(frame,init,'array','catalog',[]);assert.equal(response(frame,'array').ok,false);
          await request(frame,init,'large','catalog',{text:'x'.repeat(100000)});assert.equal(response(frame,'large').ok,false);
          await request(frame,init,'once','install',{selected:'demo'});await request(frame,init,'once','install',{selected:'demo'});
          assert.equal(calls.length,2);assert.equal(calls[1][1].action,'install');assert.equal(response(frame,'once').ok,true);""")

    def test_parallel_actions_do_not_start_second_mutation(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(),init=await ready();
          let finish;apiHandler=()=>new Promise(resolve=>finish=resolve);
          await request(frame,init,'first','install');await request(frame,init,'second','install');
          assert.equal(calls.length,2);assert.equal(response(frame,'second').ok,false);assert.equal(response(frame,'first'),undefined);
          finish({ok:true,result:{status:'installed'}});await flush();assert.equal(response(frame,'first').result.status,'installed');""")

    def test_close_releases_resources_and_never_delivers_late_action_to_next_frame(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const old=currentFrame(),init=await ready();
          let finish;apiHandler=()=>new Promise(resolve=>finish=resolve);await request(old,init,'install','install');
          host.close();assert.equal(host.isOpen(),false);assert.equal(host.title(),'');assert.equal(app.classList.contains('catalog-open'),false);assert.deepEqual(viewChanges,[true,false]);assert.equal(listeners.size,0);assert.equal(old.isConnected,false);assert.equal(observers[0].disconnected,true);
          apiHandler=async()=>({url:'/extension-ui/example.store?ticket=new'});await nav.children[0].onclick();
          const next=currentFrame(),nextInit=await ready();assert.notEqual(nextInit.channel,init.channel);
          finish({ok:true,result:{status:'installed'}});await flush();
          assert.equal(response(old,'install'),undefined);assert.equal(response(next,'install'),undefined);
          assert.equal(calls.filter(x=>x[0].endsWith('/action')).length,1);
          host.stop();assert.equal(listeners.size,0);assert.equal(nav.children[0].disabled,true);""")

    def test_theme_observer_exists_only_while_open_and_shutdown_does_not_reopen(self):
        self.run_case("""host.mount(options());assert.equal(observers.length,0);await nav.children[0].onclick();const frame=currentFrame();await ready();
          html.dataset.theme='dark';observers[0].fn();assert.equal(frame.contentWindow.received.at(-1).data.type,'ax-extension-theme');
          assert.equal(frame.contentWindow.received.at(-1).data.theme,'dark');closed=true;host.stop();await nav.children[0].onclick();
          assert.equal(calls.length,1);assert.equal(listeners.size,0);assert.equal(observers[0].disconnected,true);""")

    def test_external_links_reject_credentials_and_keep_explicit_safe_link(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(),init=await ready();
          const urls=['file:///C:/x','javascript:alert(1)','https://user:pass@example.com','https://example.com?key=secret',
            'https://example.com#secret','http://127.0.0.1:8790/api','http://localhost/test'];
          for(let i=0;i<urls.length;i++){await request(frame,init,'bad'+i,'host.openExternal',{url:urls[i]});assert.equal(response(frame,'bad'+i).ok,false);}
          assert.equal(opened.length,0);await request(frame,init,'safe','host.openExternal',{url:'https://portal.example.test/local-access'});
          assert.equal(response(frame,'safe').ok,true);assert.equal(opened.length,1);assert.equal(calls.length,1);
          const anchor=created.find(x=>x.tagName==='a');assert.equal(anchor.rel,'noopener noreferrer');assert.equal(anchor.target,'_blank');
          assert.equal(anchor.hidden,false);assert.equal(anchor.href,'https://portal.example.test/local-access');""")

    def test_native_directory_picker_never_accepts_child_path_and_forwards_handle_separately(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(),init=await ready();
          apiHandler=async(path)=>path.endsWith('/directory')?{cancelled:false,directoryToken:'x'.repeat(43),path:'D:/chosen'}:{ok:true,result:{ready:true}};
          await request(frame,init,'choose','host.pickDirectory',{path:'C:/arbitrary',initialDirectory:'C:/arbitrary'});
          assert.equal(calls[1][0],'/api/extensions/example.store/directory');assert.equal(Object.keys(calls[1][1]).length,0);
          assert.equal(response(frame,'choose').result.path,'D:/chosen');
          await request(frame,init,'install','install',{directoryToken:'x'.repeat(43),planId:'plan'});
          assert.equal(calls[2][1].directoryToken,'x'.repeat(43));assert.equal(calls[2][1].payload.directoryToken,undefined);
          assert.equal(calls[2][1].payload.planId,'plan');assert.equal(calls[2][1].sessionId,'task-7');
          await request(frame,init,'bad','install',{directoryToken:'D:/forged'});assert.equal(calls.length,3);assert.equal(response(frame,'bad').ok,false);""")

    def test_native_directory_picker_serializes_with_actions_and_cancel_is_not_an_error(self):
        self.run_case("""host.mount(options());await nav.children[0].onclick();const frame=currentFrame(),init=await ready();
          let finish;apiHandler=()=>new Promise(resolve=>finish=resolve);
          await request(frame,init,'choose','host.pickDirectory');await request(frame,init,'install','install');
          assert.equal(calls.length,2);assert.equal(response(frame,'install').ok,false);
          finish({cancelled:true});await flush();assert.equal(response(frame,'choose').ok,true);assert.equal(response(frame,'choose').result.cancelled,true);""")

    def test_view_address_must_be_exact_same_origin_ticket_endpoint(self):
        self.run_case("""host.mount(options());
          for(const url of ['https://evil.example/extension-ui/example.store?ticket=x','/api/bootstrap?ticket=x',
            '/extension-ui/example.store?ticket=x&token=secret','/extension-ui/example.store#x']){
            apiHandler=async()=>({url});await nav.children[0].onclick();assert.equal(currentFrame(),undefined);assert.equal(listeners.size,0);host.close();
          }""")

    def test_bootstrap_lazy_loader_does_nothing_when_not_installed_or_shutting_down(self):
        self.run_case("""const app=fs.readFileSync(process.argv[2].replace('extensions.js','app.js'),'utf8');
          const loader=app.slice(app.indexOf('let extensionsLoad;'),app.indexOf('async function init(){try{'));
          vm.runInContext('let appClosed=false;'+loader,context);const count=created.length;
          context.loadExtensions([]);context.loadExtensions(undefined);assert.equal(created.length,count);
          vm.runInContext('appClosed=true',context);context.loadExtensions([extension]);assert.equal(created.length,count);
          assert.equal(head.children.length,0);""")


@unittest.skipUnless(NODE, "Node.js is required for frontend lifecycle checks")
class WorkspaceExtensionAppLifecycleTests(unittest.TestCase):
    def run_case(self, javascript):
        from tests.test_workspace_frontend_state import HARNESS as APP_HARNESS
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), javascript],
            input=APP_HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_settings_warning_does_not_add_ui_or_load_assets_when_extensions_are_absent(self):
        self.run_case(r"""(()=>{
          boot={extensions:[],extensionWarnings:[]};
          const create=document.createElement;document.createElement=()=>{throw Error('unexpected DOM');};
          renderExtensionWarning();boot={};renderExtensionWarning();
          document.createElement=create;assert.equal(extensionWarningElement,null);
        })()""")

    def test_settings_warns_once_about_broken_install_and_clears_after_recovery(self):
        self.run_case(r"""(()=>{
          const body=document.createElement('div');body.className='dialog-scroll-body';$('settings-dialog').append(body);
          boot={extensions:[],extensionWarnings:['<img src=x onerror=alert(1)>']};
          renderExtensionWarning();renderExtensionWarning();assert.equal(body.children.length,1);
          assert.equal(extensionWarningElement.getAttribute('role'),'status');
          assert.match(extensionWarningElement.textContent,/다시 설치/);assert.match(extensionWarningElement.textContent,/기존 채팅과 업무/);
          assert.ok(!extensionWarningElement.textContent.includes('<img'));
          boot.extensionWarnings=[];renderExtensionWarning();assert.equal(body.children.length,0);
          assert.equal(extensionWarningElement,null);
        })()""")

    def test_session_switch_closes_extension_without_disabling_its_navigation(self):
        self.run_case(r"""(async()=>{
          let closes=0,stops=0;globalThis.WorkspaceExtensions={close(){closes++;},stop(){stops++;}};
          const row={id:'work-2',kind:'work',title:'Second',workspace:'D:/second',state:'idle',messages:[],requests:[]};
          api=async()=>row;poll=()=>{};refreshInspector=()=>{};
          active={id:'work-1',kind:'work',title:'First',workspace:'D:/first',state:'idle'};
          $('prompt').value='keep my draft';attachments=['first.txt'];sessions=[active,row];
          await selectSession(row.id);assert.equal(closes,1);assert.equal(stops,0);
          assert.equal(drafts.get('work-1').text,'keep my draft');assert.deepEqual(drafts.get('work-1').attachments,['first.txt']);
          showHome();assert.equal(closes,2);assert.equal(stops,0);
        })()""")

    def test_page_header_and_same_mode_navigation_preserve_current_task_and_draft(self):
        self.run_case(r"""(async()=>{
          let opened=true,closes=0;globalThis.WorkspaceExtensions={isOpen:()=>opened,title:()=>"Example extension",close(){opened=false;closes++;taskHeader();}};
          active={id:'work-1',kind:'work',title:'First',workspace:'D:/first',state:'running'};sessions=[active];
          $('prompt').value='unsent text';attachments=['first.txt'];
          taskHeader();assert.equal($('chat-title').textContent,'Example extension');assert.equal($('task-view').hidden,true);
          assert.equal($('task-title').hidden,true);assert.equal($('task-pin').hidden,true);assert.equal($('restart-connection').hidden,true);
          await switchMode('work');assert.equal(closes,1);assert.equal(active.id,'work-1');
          assert.equal(active.state,'running');assert.equal($('chat-title').textContent,'First');assert.equal($('task-view').hidden,false);
          assert.equal($('prompt').value,'unsent text');assert.deepEqual(attachments,['first.txt']);
        })()""")

    def test_shutdown_stops_optional_host_and_preserves_draft(self):
        self.run_case(r"""(async()=>{
          let stops=0;globalThis.WorkspaceExtensions={stop(){stops++;}};
          $('prompt').value='keep my draft';attachments=['first.txt'];enterShutdown('closing');
          assert.equal(stops,1);assert.equal(appClosed,true);assert.equal($('prompt').value,'keep my draft');
          assert.equal(drafts.get(homeDraftKey()).text,'keep my draft');
        })()""")


if __name__ == "__main__":
    unittest.main()
