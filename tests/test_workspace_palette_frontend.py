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
             str(ROOT/'local_app/web/palette.js')], input=HARNESS, text=True,
            encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_search_is_local_and_switches_tasks_without_sending_draft(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='아직 보내지 않은 초안';attachments=['report.csv'];$('prompt').focus();
          let selected;selectSession=async id=>{selected=id;return true;};
          const event=keyEvent('k',{ctrlKey:true});assert.equal(WorkspacePalette.keydown(event),true);
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

    def test_ime_and_existing_modal_keep_keyboard_ownership(self):
        self.run_case(r"""(()=>{
          assert.equal(WorkspacePalette.keydown(keyEvent('k',{ctrlKey:true,isComposing:true})),false);
          showDialog('action-dialog');const shortcut=keyEvent('k',{ctrlKey:true});
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


if __name__ == '__main__':
    unittest.main()
