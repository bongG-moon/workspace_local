"""Exercise the shipped input editor without Claude, network, or personal state."""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const listeners=new Map();
const input={value:'',selectionStart:0,selectionEnd:0,readOnly:false,disabled:false,
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
  setRangeText(value,start,end){this.value=this.value.slice(0,start)+value+this.value.slice(end);this.setSelectionRange(start+value.length,start+value.length);},
  addEventListener(type,listener){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(listener);},
  removeEventListener(type,listener){listeners.get(type)?.delete(listener);}};
let task={id:'A',history:[]},attachments=[],edits=0,restores=0;
const document={execCommand(){return false;}};
const ctx={assert,console,Intl,input,document};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);
const editor=ctx.WorkspaceInputKeys;
const options={input,context:()=>task,capture:()=>({text:input.value,attachments}),
 restore(draft){restores++;input.value=draft.text;attachments=draft.attachments;},edited(){edits++;}};
editor.attach(options);
function emit(type){for(const callback of listeners.get(type)||[])callback({target:input});}
function set(text,start=text.length,end=start){input.value=text;input.setSelectionRange(start,end);emit('input');}
function press(key,options={}){const event={key,target:input,defaultPrevented:false,ctrlKey:false,altKey:false,shiftKey:false,metaKey:false,
 preventDefault(){this.defaultPrevented=true;},...options};const handled=editor.keydown(event);return {handled,prevented:event.defaultPrevented};}
function ctrl(key,options={}){return press(key,{ctrlKey:true,...options});}
function alt(key,options={}){return press(key,{altKey:true,...options});}
const scenario=process.argv[3];
try{eval(scenario);}catch(error){console.error(error.stack||error);process.exitCode=1;}
"""


@unittest.skipUnless(NODE, "Node.js is required for input-key checks")
class WorkspaceInputKeysTests(unittest.TestCase):
    def run_case(self, javascript):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/input-keys.js"), javascript],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_history_restores_draft_and_attachments_without_mutating_sent_history(self):
        self.run_case(r"""
          task.history=[{text:'첫 요청',files:['C:/first.png']},{text:'둘째 요청',files:['C:/second.csv']}];
          set('보내지 않은 초안',0);attachments=['C:/draft.docx'];
          assert.equal(press('ArrowUp').handled,true);assert.equal(input.value,'둘째 요청');
          assert.deepEqual([...attachments],['C:/second.csv']);assert.notEqual(attachments,task.history[1].files);
          assert.equal(press('ArrowUp').handled,true);assert.equal(input.value,'첫 요청');
          input.setSelectionRange(input.value.length,input.value.length);
          press('ArrowDown');assert.equal(input.value,'둘째 요청');assert.deepEqual([...attachments],['C:/second.csv']);
          press('ArrowDown');assert.equal(input.value,'보내지 않은 초안');assert.deepEqual([...attachments],['C:/draft.docx']);
          assert.equal(input.selectionStart,0);assert.deepEqual(task.history[1].files,['C:/second.csv']);
          assert.equal(press('ArrowDown').handled,false);assert.equal(restores,4);
        """)

    def test_history_is_scoped_and_an_edited_recalled_prompt_becomes_the_new_draft(self):
        self.run_case(r"""
          task.history=[{text:'업무 A 요청',files:[]}];set('A 초안',0);press('ArrowUp');
          set('수정한 요청',0);attachments=['C:/new.pdf'];press('ArrowUp');
          input.setSelectionRange(input.value.length,input.value.length);press('ArrowDown');
          assert.equal(input.value,'수정한 요청');assert.deepEqual([...attachments],['C:/new.pdf']);
          task={id:'B',history:[{text:'업무 B 요청',files:[]}]};set('B 초안',0);press('ArrowUp');
          assert.equal(input.value,'업무 B 요청');input.setSelectionRange(input.value.length,input.value.length);press('ArrowDown');
          assert.equal(input.value,'B 초안');editor.reset();assert.equal(ctrl('n').prevented,true);
        """)

    def test_shared_restore_hook_may_reset_editor_without_losing_history_transaction(self):
        self.run_case(r"""
          const restore=options.restore;options.restore=draft=>{restore(draft);editor.reset();};editor.attach(options);
          task.history=[{text:'older',files:[]},{text:'latest',files:[]}];set('draft',0);attachments=['D:/draft.png'];
          press('ArrowUp');assert.equal(input.value,'latest');press('ArrowUp');assert.equal(input.value,'older');
          input.setSelectionRange(input.value.length,input.value.length);press('ArrowDown');assert.equal(input.value,'latest');
          press('ArrowDown');assert.equal(input.value,'draft');assert.deepEqual([...attachments],['D:/draft.png']);
        """)

    def test_attachment_only_edits_reset_history_without_overwriting_new_files(self):
        self.run_case(r"""
          task.history=[{text:'older',files:['D:/old.png']},{text:'latest',files:['D:/latest.png']}];
          set('original draft',0);attachments=['D:/original.pdf'];press('ArrowUp');
          attachments.push('D:/new.xlsx');input.setSelectionRange(input.value.length,input.value.length);
          assert.equal(press('ArrowDown').handled,false);assert.equal(input.value,'latest');
          assert.deepEqual([...attachments],['D:/latest.png','D:/new.xlsx']);
          input.setSelectionRange(0,0);press('ArrowUp');assert.equal(input.value,'latest');
          input.setSelectionRange(input.value.length,input.value.length);press('ArrowDown');
          assert.deepEqual([...attachments],['D:/latest.png','D:/new.xlsx']);
          input.setSelectionRange(0,0);press('ArrowUp');attachments.splice(0,1);
          input.setSelectionRange(input.value.length,input.value.length);ctrl('n');
          assert.deepEqual([...attachments],[]);assert.equal(input.value,'latest');
          assert.deepEqual(task.history[1].files,['D:/latest.png']);
        """)

    def test_unwrapped_single_line_and_first_last_rows_use_cli_history_from_any_column(self):
        self.run_case(r"""
          ctx.getComputedStyle=()=>({paddingLeft:'10px',paddingRight:'10px',letterSpacing:'normal',wordSpacing:'normal',fontSize:'16px',font:'16px sans-serif'});
          let created=0;document.createElement=()=>{created++;return {getContext:()=>({measureText:value=>({width:[...value].length*10})})};};
          input.clientWidth=400;task.history=[{text:'previous',files:[]}];
          set('single line');press('ArrowUp');assert.equal(input.value,'previous');
          input.setSelectionRange(3,3);press('ArrowDown');assert.equal(input.value,'single line');assert.equal(input.selectionStart,11);
          set('first line\nlast line',4);press('ArrowUp');assert.equal(input.value,'previous');
          press('ArrowDown');assert.equal(input.value,'first line\nlast line');assert.equal(input.selectionStart,4);
          input.setSelectionRange(13,13);assert.equal(press('ArrowDown').handled,false);
          input.setSelectionRange(4,4);assert.equal(press('ArrowDown').handled,false);
          input.setSelectionRange(13,13);assert.equal(press('ArrowUp').handled,false);
          assert.equal(created,1);
        """)

    def test_wrapped_or_unknown_measurements_keep_native_arrows_until_absolute_edge(self):
        self.run_case(r"""
          const style={paddingLeft:'10px',paddingRight:'10px',letterSpacing:'normal',wordSpacing:'normal',fontSize:'16px',font:'16px sans-serif'};
          ctx.getComputedStyle=()=>style;document.createElement=()=>({getContext:()=>({measureText:value=>({width:[...value].length*10})})});
          input.clientWidth=100;task.history=[{text:'previous',files:[]}];set('long wrapped input',9);
          assert.equal(press('ArrowUp').handled,false);assert.equal(press('ArrowDown').handled,false);
          input.clientWidth=400;document.fonts={status:'loading'};assert.equal(press('ArrowUp').handled,false);
          document.fonts.status='loaded';set('tab\there',6);assert.equal(press('ArrowUp').handled,false);
          set('short',3);style.letterSpacing='20px';input.clientWidth=100;assert.equal(press('ArrowUp').handled,false);
          style.letterSpacing='normal';input.clientWidth=0;assert.equal(press('ArrowUp').handled,false);
          input.clientWidth=400;style.writingMode='vertical-rl';assert.equal(press('ArrowUp').handled,false);
          style.writingMode='horizontal-tb';style.fontSize='unknown';assert.equal(press('ArrowUp').handled,false);
          input.setSelectionRange(0,0);assert.equal(press('ArrowUp').handled,true);assert.equal(input.value,'previous');
        """)

    def test_history_memory_keeps_only_last_100_and_skips_bad_rows(self):
        self.run_case(r"""
          task.history=[null,{text:null},{text:''},...Array.from({length:140},(_,i)=>({text:String(i),files:[]}))];
          set('',0);for(let i=0;i<150;i++)press('ArrowUp');
          assert.equal(input.value,'40');assert.equal(restores,100);
          for(let i=0;i<100;i++){input.setSelectionRange(input.value.length,input.value.length);press('ArrowDown');}
          assert.equal(input.value,'');
        """)

    def test_plain_arrows_preserve_multiline_navigation_and_selection(self):
        self.run_case(r"""
          task.history=[{text:'old',files:[]}];set('첫째 줄\n둘째 줄',3);
          assert.equal(press('ArrowUp').handled,false);assert.equal(press('ArrowDown').handled,false);
          input.setSelectionRange(0,2);assert.equal(press('ArrowUp').handled,false);
          input.setSelectionRange(0,0);assert.equal(press('ArrowUp',{shiftKey:true}).handled,false);
          assert.equal(restores,0);
        """)

    def test_control_p_n_move_logical_rows_before_history_with_grapheme_columns(self):
        self.run_case(r"""
          task.history=[{text:'old',files:[]}];set('😀한글\nabc\n끝',7);
          ctrl('p');assert.equal(input.selectionStart,3);assert.equal(input.value,'😀한글\nabc\n끝');
          ctrl('n');assert.equal(input.selectionStart,7);ctrl('n');assert.equal(input.selectionStart,input.value.length);
          ctrl('p');ctrl('p');ctrl('p');assert.equal(input.value,'old');
          ctrl('n');assert.equal(input.value,'😀한글\nabc\n끝');
        """)

    def test_control_a_e_and_u_k_preserve_other_lines_and_delete_boundary_newline_only(self):
        self.run_case(r"""
          set('one\ntwo\nthree',6);ctrl('a');assert.equal(input.selectionStart,4);
          ctrl('e');assert.equal(input.selectionStart,7);ctrl('u');assert.equal(input.value,'one\n\nthree');
          ctrl('u');assert.equal(input.value,'one\nthree');assert.equal(input.selectionStart,3);
          ctrl('k');assert.equal(input.value,'onethree');assert.equal(input.selectionStart,3);
          ctrl('k');assert.equal(input.value,'one');ctrl('y');assert.equal(input.value,'onethree');
          set('\nx',0);ctrl('a');assert.equal(input.selectionStart,0);ctrl('u');assert.equal(input.value,'\nx');
        """)

    def test_whitespace_word_delete_is_distinct_from_punctuation_word_delete(self):
        self.run_case(r"""
          set('use path/to/file.csv');ctrl('w');assert.equal(input.value,'use ');
          ctrl('y');assert.equal(input.value,'use path/to/file.csv');ctrl('Backspace');assert.equal(input.value,'use path/to/file.');
          set('keep --flag=value  ');ctrl('w');assert.equal(input.value,'keep ');
          set('go foo_bar/baz.txt',3);alt('f');assert.equal(input.selectionStart,6);
          alt('f');assert.equal(input.selectionStart,10);alt('b');assert.equal(input.selectionStart,7);
          alt('d');assert.equal(input.value,'go foo_/baz.txt');
        """)

    def test_unicode_and_selected_edits_never_split_surrogates_or_combining_graphemes(self):
        self.run_case(r"""
          set('가😀나',2,3);ctrl('w');assert.equal(input.value,'가나');
          ctrl('y');assert.equal(input.value,'가😀나');
          set('A👩‍💻B',2,3);ctrl('k');assert.equal(input.value,'AB');
          ctrl('y');assert.equal(input.value,'A👩‍💻B');
          set('한글 문장');alt('b');assert.equal(input.selectionStart,3);alt('d');assert.equal(input.value,'한글 ');
          set('ae\u0301z',2,3);ctrl('u');assert.equal(input.value,'az');
        """)

    def test_control_f_and_h_use_graphemes_without_browser_find_history_or_kill_buffer(self):
        self.run_case(r"""
          set('remember');ctrl('u');set('A👩‍💻한e\u0301Z',1);
          assert.equal(ctrl('f').prevented,true);assert.equal(input.selectionStart,6);
          ctrl('h');assert.equal(input.value,'A한e\u0301Z');assert.equal(input.selectionStart,1);
          ctrl('f');ctrl('f');assert.equal(input.selectionStart,4);ctrl('h');assert.equal(input.value,'A한Z');
          input.setSelectionRange(1,2);ctrl('h');assert.equal(input.value,'AZ');
          ctrl('y');assert.equal(input.value,'ArememberZ');
          set('😀x',0);ctrl('h');assert.equal(input.value,'😀x');ctrl('f');assert.equal(input.selectionStart,2);
          input.setSelectionRange(0,2);ctrl('f');assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,2);
        """)

    def test_yank_cycle_requires_last_yank_and_is_bounded_and_context_private(self):
        self.run_case(r"""
          set('first');ctrl('u');set('second');ctrl('u');ctrl('y');assert.equal(input.value,'second');
          alt('y');assert.equal(input.value,'first');alt('y');assert.equal(input.value,'second');
          press('ArrowLeft');alt('y');assert.equal(input.value,'second');
          task.id='B';set('');ctrl('y');assert.equal(input.value,'');
          for(let i=0;i<30;i++){set('entry-'+i);ctrl('u');}ctrl('y');
          for(let i=0;i<20;i++)alt('y');assert.equal(input.value,'entry-29');
          set('x'.repeat(64001));ctrl('u');ctrl('y');assert.equal(input.value,'');
        """)

    def test_native_edit_undo_path_does_not_duplicate_applied_edit_or_fallback(self):
        self.run_case(r"""
          const calls=[],undo=[];
          document.execCommand=(name,_ui,value)=>{calls.push(name);if(name==='insertText'){undo.push(input.value);input.setRangeText(value,input.selectionStart,input.selectionEnd);emit('input');return false;}
            if(name==='undo'){input.value=undo.pop();emit('input');return true;}};
          set('before after');ctrl('w');assert.equal(input.value,'before ');ctrl('y');assert.equal(input.value,'before after');
          ctrl('_');assert.equal(input.value,'before ');ctrl('-', {shiftKey:true});assert.equal(input.value,'before after');
          assert.deepEqual(calls,['insertText','insertText','undo','undo']);assert.equal(edits,4);
        """)

    def test_ime_modifiers_disabled_and_claimed_events_cannot_change_or_send_input(self):
        self.run_case(r"""
          set('안전한 초안');const before=input.value;
          for(const options of [{isComposing:true},{keyCode:229},{defaultPrevented:true},{altKey:true},{metaKey:true},{shiftKey:true},{target:{}}]){
            assert.equal(ctrl('u',options).handled,false);assert.equal(input.value,before);}
          emit('compositionstart');assert.equal(ctrl('u').handled,false);emit('compositionend');
          input.readOnly=true;assert.equal(ctrl('u').handled,false);input.readOnly=false;
          input.disabled=true;assert.equal(ctrl('u').handled,false);input.disabled=false;
          for(const key of ['Enter','Escape','Tab'])assert.equal(press(key).handled,false);
          for(const key of ['b','c','d','g','o','r','s'])assert.equal(ctrl(key).handled,false);
          assert.equal(input.value,before);assert.equal(edits,0);assert.equal(restores,0);
          editor.attach(options);assert.equal(listeners.get('input').size,1);
        """)


if __name__ == "__main__":
    unittest.main()
