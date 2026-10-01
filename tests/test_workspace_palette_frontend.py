"""Navigation palette contracts without a browser, CLI connection, or AI request."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.removeAttribute=function(name){delete this.attributes[name];};
context.flush=()=>new Promise(resolve=>setImmediate(resolve));
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'palette.js'});
context.installLayout=(width=640)=>{
  const listeners=new Map(),originalClosest=Element.prototype.closest;
  Element.prototype.getAttribute=function(name){return this.attributes[name]??null;};
  Element.prototype.addEventListener=function(name,fn){this.events??=new Map();this.events.set(name,fn);};
  Element.prototype.contains=function(node){for(let item=node;item;item=item.parent)if(item===this)return true;return false;};
  Element.prototype.querySelectorAll=function(){return [...nodes.values()].filter(node=>node!==this&&this.contains(node));};
  Element.prototype.closest=function(selector){if(selector==='[hidden],[inert]'){for(let node=this;node;node=node.parent)if(node.hidden||node.inert)return node;return null;}return originalClosest.call(this,selector);};
  Element.prototype.getClientRects=function(){return this.closest('[hidden],[inert]')?[]:[{}];};
  Element.prototype.focus=function(){context.document.activeElement=this;for(const fn of listeners.get('focusin')||[])fn({target:this});};
  context.document.querySelector=selector=>selector==='dialog[open]'?[...nodes.values()].find(node=>node.open):get(selector);
  context.document.addEventListener=(name,fn)=>{if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);};
  context.layoutWrites=[];context.localStorage={getItem:()=>null,setItem:(_,value)=>context.layoutWrites.push(value)};
  context.matchMedia=query=>({matches:width<=Number(query.match(/\d+/)[0]),addEventListener(){}});
  get('main').append(get('prompt'),get('materials-button'));
  get('sidebar-panel').append(get('sidebar-toggle'),get('palette-open'));
  get('inspector-panel').append(get('close-materials'));
  get('composer-controls-panel').hidden=get('composer-suggestions').hidden=true;
  for(const node of nodes.values()){node.inert=false;node.tabIndex=0;}
  vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context,{filename:'layout.js'});
};
const scenario=process.argv[3];
""")
SETUP = r"""
active={id:'A',title:'이번 보고서',workspace:'C:/fixture/A',state:'idle',trusted:true};
sessions=[{...active},{id:'B',title:'지난 실적',workspace:'D:/지난 자료',state:'done',pinned:true}];
const keyEvent=(key,extras={})=>({key,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...extras});
const find=id=>$('palette-items').children.find(node=>node.dataset.commandId===id);
let calls=[];api=async(...args)=>{calls.push(args);throw new Error('Unexpected API');};
"""


@unittest.skipUnless(NODE, "Node.js is required for palette UI checks")
class WorkspacePaletteFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script,
             str(ROOT/'local_app/web/palette.js'),str(ROOT/'local_app/web/layout.js')], input=HARNESS, text=True,
            encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_search_is_local_and_switches_tasks_without_sending_draft(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='아직 보내지 않은 초안';attachments=['report.csv'];$('prompt').focus();
          let selected;selectSession=async id=>{selected=id;return true;};
          const event=keyEvent('p',{ctrlKey:true,shiftKey:true});assert.equal(WorkspacePalette.keydown(event),true);
          assert.equal(event.defaultPrevented,true);assert.equal($('palette-dialog').open,true);
          $('palette-search').value='지난';$('palette-search').oninput();
          assert.ok(find('task:B'));assert.equal(find('task:A'),undefined);
          await find('task:B').onclick();assert.equal(selected,'B');assert.equal($('palette-dialog').open,false);
          assert.equal($('prompt').value,'아직 보내지 않은 초안');assert.equal(attachments[0],'report.csv');assert.equal(calls.length,0);
        })()""")

    def test_escape_releases_index_and_restores_previous_focus(self):
        self.run_case(r"""(()=>{
          $('prompt').focus();WorkspacePalette.open();assert.equal(document.activeElement,$('palette-search'));
          WorkspacePalette.keydown(keyEvent('Escape'));assert.equal(document.activeElement,$('prompt'));
          assert.equal($('palette-items').children.length,0);assert.equal($('palette-dialog').open,false);
          WorkspacePalette.refresh();assert.equal($('palette-items').children.length,0);
        })()""")

    def test_palette_reserves_shift_p_and_keeps_cli_editing_keys_unhandled(self):
        self.run_case(r"""(()=>{
          $('prompt').value='보존할 입력';$('prompt').focus();
          for(const event of [keyEvent('k',{ctrlKey:true}),keyEvent('p',{ctrlKey:true}),
              keyEvent('p',{altKey:true}),keyEvent('P',{ctrlKey:true,shiftKey:true,altKey:true})]){
            assert.equal(WorkspacePalette.keydown(event),false);assert.notEqual(event.defaultPrevented,true);
            assert.notEqual($('palette-dialog').open,true);
          }
          const open=keyEvent('P',{ctrlKey:true,shiftKey:true});assert.equal(WorkspacePalette.keydown(open),true);
          assert.equal($('palette-dialog').open,true);assert.equal(open.defaultPrevented,true);
          const repeat=keyEvent('P',{ctrlKey:true,shiftKey:true,repeat:true});WorkspacePalette.keydown(repeat);
          assert.equal($('palette-dialog').open,true);assert.equal(repeat.defaultPrevented,true);
          WorkspacePalette.keydown(keyEvent('p',{ctrlKey:true,shiftKey:true}));
          assert.equal($('palette-dialog').open,false);assert.equal($('prompt').value,'보존할 입력');
          assert.equal(calls.length,0);
        })()""")

    def test_ime_and_existing_modal_keep_keyboard_ownership(self):
        self.run_case(r"""(()=>{
          assert.equal(WorkspacePalette.keydown(keyEvent('p',{ctrlKey:true,shiftKey:true,isComposing:true})),false);
          showDialog('action-dialog');const shortcut=keyEvent('p',{ctrlKey:true,shiftKey:true});
          assert.equal(WorkspacePalette.keydown(shortcut),false);assert.notEqual(shortcut.defaultPrevented,true);
          $('action-dialog').close();WorkspacePalette.open();$('palette-search').oncompositionstart();
          const before=$('palette-search').attributes['aria-activedescendant'];
          assert.equal(WorkspacePalette.keydown(keyEvent('Enter',{keyCode:229})),false);
          assert.equal(WorkspacePalette.keydown(keyEvent('ArrowDown')),false);
          assert.equal($('palette-search').attributes['aria-activedescendant'],before);
          $('palette-search').oncompositionend();assert.equal(calls.length,0);
        })()""")

    def test_controls_use_existing_session_ui_and_never_send_prompt(self):
        self.run_case(r"""(async()=>{
          let opened;globalThis.WorkspaceInlineControls={close(){},open:kind=>{opened=kind;}};
          WorkspacePalette.open();$('palette-search').value='effort';$('palette-search').oninput();
          await find('effort').onclick();assert.equal(opened,'effort');assert.equal(calls.length,0);
          assert.equal(document.activeElement,$('prompt'));
        })()""")

    def test_shortcut_reference_handoff_preserves_running_task_and_draft(self):
        self.run_case(r"""(async()=>{
          $('shortcuts-list').querySelectorAll=()=>[];
          $('palette-dialog').tagName='DIALOG';$('palette-dialog').append($('palette-search'));
          active.state='running';$('prompt').value='보내지 않은 후속 요청';attachments=['report.csv'];
          const task=active;$('prompt').focus();WorkspacePalette.open();
          $('palette-search').value='단축키';$('palette-search').oninput();
          await find('shortcuts').onclick();
          assert.equal($('palette-dialog').open,false);assert.equal($('shortcuts-dialog').open,true);
          assert.equal(document.activeElement,$('shortcuts-search'));
          $('shortcuts-search').value='없는단축키';
          const close=keyEvent('Escape');$('shortcuts-search').onkeydown(close);
          assert.equal(close.defaultPrevented,true);assert.equal(close.stopped,true);
          assert.equal($('shortcuts-dialog').open,false);assert.equal(document.activeElement,$('shortcuts-open'));
          assert.equal(active,task);assert.equal(active.state,'running');
          assert.equal($('prompt').value,'보내지 않은 후속 요청');assert.deepEqual(attachments,['report.csv']);
          assert.equal(calls.length,0);
        })()""")

    def test_disabled_actions_and_stale_task_cannot_execute(self):
        self.run_case(r"""(async()=>{
          let executions=0;globalThis.WorkspaceProductivityActions={openBranch(){executions++;}};
          WorkspacePalette.open();const old=find('branch');assert.ok(old);
          active={id:'B',state:'idle'};await old.onclick();assert.equal(executions,0);
          active.state='running';WorkspacePalette.refresh();assert.equal(find('model').attributes['aria-disabled'],'true');
          await find('model').onclick();assert.equal(calls.length,0);assert.equal($('palette-dialog').open,true);
        })()""")

    def test_arrow_selection_has_one_active_option_and_keeps_editing_shortcuts(self):
        self.run_case(r"""(()=>{
          WorkspacePalette.open();const first=$('palette-search').attributes['aria-activedescendant'];
          const modified=keyEvent('ArrowDown',{ctrlKey:true});assert.equal(WorkspacePalette.keydown(modified),false);
          assert.equal($('palette-search').attributes['aria-activedescendant'],first);
          WorkspacePalette.keydown(keyEvent('ArrowDown'));assert.notEqual($('palette-search').attributes['aria-activedescendant'],first);
          assert.equal($('palette-items').children.filter(node=>node.attributes['aria-selected']==='true').length,1);
        })()""")

    def test_metadata_index_and_rendered_results_are_bounded_without_history_reads(self):
        self.run_case(r"""(()=>{
          sessions=Array.from({length:800},(_,i)=>({id:'task-'+i,title:'보고서 '+i,workspace:'C:/reports',state:'idle',get messages(){throw Error('history read');}}));
          WorkspacePalette.open();$('palette-search').value='보고서';$('palette-search').oninput();
          assert.equal($('palette-items').children.length,24);assert.match($('palette-status').textContent,/500/);
          const first=$('palette-items').children[0];WorkspacePalette.refresh();assert.equal($('palette-items').children[0],first);
          $('palette-search').value='보고서 799';$('palette-search').oninput();assert.equal($('palette-items').children.length,0);
          assert.equal(calls.length,0);
        })()""")

    def test_failed_navigation_reports_error_and_restores_focus(self):
        self.run_case(r"""(async()=>{
          $('prompt').focus();let message;toast=value=>message=value;
          selectSession=async()=>{throw Error('선택한 업무를 찾지 못했어요.');};
          WorkspacePalette.open();$('palette-search').value='지난';$('palette-search').oninput();await find('task:B').onclick();
          assert.equal(message,'선택한 업무를 찾지 못했어요.');assert.equal(document.activeElement,$('prompt'));
          assert.equal($('palette-dialog').open,false);
        })()""")

    def test_ctrl_shift_p_closes_narrow_overlay_before_model_controls_and_restores_visible_focus(self):
        self.run_case(r"""(async()=>{
          installLayout();WorkspaceLayout.openInspector();assert.equal(document.activeElement,$('close-materials'));
          assert.equal(document.querySelector('main').inert,true);
          WorkspacePalette.keydown(keyEvent('p',{ctrlKey:true,shiftKey:true}));
          assert.equal(WorkspaceLayout.snapshot().inspectorOverlay,false);assert.equal(document.querySelector('main').inert,false);
          WorkspacePalette.keydown(keyEvent('Escape'));assert.equal(document.activeElement,$('materials-button'));
          for(const kind of ['model','effort']){
            WorkspaceLayout.toggleSidebar();assert.equal(WorkspaceLayout.snapshot().sidebarOverlay,true);
            let opened;globalThis.WorkspaceInlineControls={close(){},open:value=>{opened=value;assert.equal(document.querySelector('main').inert,false);}};
            WorkspacePalette.keydown(keyEvent('p',{ctrlKey:true,shiftKey:true}));await find(kind).onclick();
            assert.equal(opened,kind);assert.equal(document.activeElement,$('prompt'));assert.equal(WorkspaceLayout.snapshot().sidebarOverlay,false);
          }
          assert.equal(layoutWrites.length,0);assert.equal(calls.length,0);
        })()""")

    def test_palette_dispatch_clears_late_overlay_and_preserves_desktop_panel_preferences(self):
        self.run_case(r"""(async()=>{
          installLayout();WorkspacePalette.open();WorkspaceLayout.openInspector();
          let opened=false;globalThis.WorkspaceCapabilities={open(){opened=true;assert.equal(document.querySelector('main').inert,false);}};
          await find('skills').onclick();assert.equal(opened,true);assert.equal(WorkspaceLayout.snapshot().inspectorOverlay,false);
          assert.equal(layoutWrites.length,0);
        })()""")
        self.run_case(r"""(async()=>{
          installLayout(1440);WorkspaceLayout.toggleSidebar();WorkspaceLayout.closeInspector();
          const before=JSON.stringify(WorkspaceLayout.snapshot().preferences),writes=layoutWrites.length;
          WorkspacePalette.open();await find('effort').onclick();
          assert.equal(JSON.stringify(WorkspaceLayout.snapshot().preferences),before);assert.equal(layoutWrites.length,writes);
        })()""")


if __name__ == '__main__':
    unittest.main()
