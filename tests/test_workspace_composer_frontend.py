"""Synthetic keyboard/cursor/IME and stale-response tests for task completion."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE_HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace("const scenario=process.argv[3];", r"""
Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end;};
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'composer.js'});
const scenario=process.argv[3];
""")
SETUP = r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'done',trusted:true};sessions=[{...active}];
const enter = (options={}) => ({key:'Enter',preventDefault(){this.prevented=true;},...options});
const type = (text,cursor=text.length) => {$('prompt').focus();$('prompt').value=text;$('prompt').setSelectionRange(cursor,cursor);};
const flush = async () => { for(let i=0;i<8;i++) await Promise.resolve(); };
"""


@unittest.skipUnless(NODE, "Node.js is required for completion UI checks")
class WorkspaceComposerFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT/'local_app/web/app.js'), SETUP+script,
                                 str(ROOT/'local_app/web/composer.js')], input=HARNESS, text=True,
                                encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_slash_uses_active_task_and_preserves_existing_arguments_and_cursor(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {items:[
            {id:'report',label:'/company:report',invocation:'/company:report',supported:true}]};};
          type('/rep 월간 자료 정리',4);await WorkspaceComposer.refresh();
          assert.equal(calls[0].path,'/api/completions');assert.equal(calls[0].body.id,'A');assert.equal(calls[0].body.kind,'slash');
          assert.equal('workspace' in calls[0].body,false);assert.equal(calls[0].body.query,'rep');
          const event=enter();$('prompt').onkeydown(event);assert.equal(event.prevented,true);
          assert.equal($('prompt').value,'/company:report 월간 자료 정리');assert.equal($('prompt').selectionStart,15);
          assert.equal(drafts.get('A').text,$('prompt').value);assert.equal($('composer-suggestions').hidden,true);
        })()""")

    def test_file_selection_attaches_once_preserving_surrounding_text(self):
        self.run_case(r"""(async()=>{
          let request;api=async(path,body)=>{request=body;return {items:[{id:'file',label:'월간 실적.xlsx',path:'C:/자료/월간 실적.xlsx',supported:true}]};};
          attachments=['c:\\자료\\월간 실적.xlsx'];type('이 @월간 을 검토해줘',5);
          await WorkspaceComposer.refresh();$('prompt').onkeydown(enter());
          assert.equal(request.kind,'file');assert.equal(request.attachments.length,1);
          assert.equal(attachments.length,1);assert.equal($('prompt').value,'이 @"C:/자료/월간 실적.xlsx" 을 검토해줘');
          assert.equal($('prompt').selectionStart,'이 @"C:/자료/월간 실적.xlsx"'.length);assert.equal(drafts.get('A').attachments.length,1);
        })()""")

    def test_new_file_is_selected_by_mouse_without_blur_or_double_attachment(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:[{id:'file',label:'한글 & 자료.txt',path:'C:/자료/한글 & 자료.txt',supported:true}]});
          type('검토 @한글');await WorkspaceComposer.refresh();const button=$('composer-suggestion-list').children[0];
          let prevented=false;button.onmousedown({preventDefault(){prevented=true;}});assert.equal(prevented,true);
          button.onclick();button.onclick();assert.equal(attachments.length,1);assert.equal(attachments[0],'C:/자료/한글 & 자료.txt');
          assert.equal($('prompt').value,'검토 @"C:/자료/한글 & 자료.txt" ');
        })()""")

    def test_unsupported_command_never_changes_or_sends_the_prompt(self):
        self.run_case(r"""(async()=>{
          let sends=0;submit=async()=>sends++;api=async()=>({items:[{id:'terminal',label:'/terminal',invocation:'/terminal',
            supported:false,reason:'원본 Claude Code에서 확인해 주세요.'}]});
          type('/ter');await WorkspaceComposer.refresh();$('prompt').onkeydown(enter());
          assert.equal($('prompt').value,'/ter');assert.equal(sends,0);assert.match($('toast').textContent,/원본 Claude Code/);
          assert.match(flatText($('composer-suggestion-list')),/원본 Claude Code/);
        })()""")

    def test_ime_and_ctrl_enter_do_not_select_or_send_during_composition(self):
        self.run_case(r"""(async()=>{
          let calls=0,sends=0;api=async()=>{calls++;return {items:[{id:'file',label:'자료',path:'C:/자료.txt',supported:true}]};};submit=async()=>sends++;
          type('@자료');$('prompt').oncompositionstart();await WorkspaceComposer.refresh();
          $('prompt').onkeydown(enter({ctrlKey:true,isComposing:true}));$('prompt').onkeydown(enter({ctrlKey:true,keyCode:229}));
          $('prompt').onkeydown(enter({ctrlKey:true}));
          assert.equal(calls,0);assert.equal(sends,0);assert.equal(attachments.length,0);
          $('prompt').oncompositionend();await WorkspaceComposer.refresh();assert.equal(calls,1);
          $('prompt').onkeydown(enter({isComposing:true}));assert.equal(attachments.length,0);
          $('prompt').onkeydown(enter());assert.equal(attachments.length,1);
        })()""")

    def test_keyboard_navigation_escape_and_input_draft_are_preserved(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:[{id:'a',label:'/a',invocation:'/a',supported:true},{id:'b',label:'/b',invocation:'/b',supported:true}]});
          type('/');$('prompt').oninput({});assert.equal(drafts.get('A').text,'/');await WorkspaceComposer.refresh();
          $('prompt').onkeydown({key:'ArrowDown',preventDefault(){}});$('prompt').onkeydown(enter());assert.equal($('prompt').value,'/b ');
          type('/');await WorkspaceComposer.refresh();$('prompt').onkeydown({key:'Escape',preventDefault(){}});
          assert.equal($('composer-suggestions').hidden,true);assert.equal($('prompt').value,'/');assert.equal($('prompt').selectionStart,1);
        })()""")

    def test_shift_enter_keeps_newline_action_and_mid_token_file_suffix_is_removed(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:[{id:'file',label:'report.md',path:'C:/fixture/A/report.md',supported:true}]});
          const prefix='정리 @rep';type(prefix+'ort.md 이후',prefix.length);await WorkspaceComposer.refresh();
          const shift=enter({shiftKey:true});$('prompt').onkeydown(shift);assert.equal(shift.prevented,undefined);
          assert.equal(attachments.length,0);assert.equal($('prompt').value,'정리 @report.md 이후');
          $('prompt').onkeydown(enter());assert.equal($('prompt').value,'정리 @report.md 이후');assert.equal($('prompt').selectionStart,'정리 @report.md'.length);
          assert.equal(attachments.length,1);
        })()""")

    def test_modified_navigation_keeps_native_selection_and_system_shortcuts(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:[{id:'a',invocation:'/a',supported:true},{id:'b',invocation:'/b',supported:true}]});
          type('/');await WorkspaceComposer.refresh();const selected=$('prompt').attributes['aria-activedescendant'];
          for(const key of ['ArrowUp','ArrowDown','Escape'])for(const modifier of ['shiftKey','ctrlKey','altKey','metaKey']){
            const event={key,[modifier]:true,preventDefault(){this.prevented=true;}};$('prompt').onkeydown(event);
            assert.notEqual(event.prevented,true);assert.equal($('prompt').attributes['aria-activedescendant'],selected);
            assert.equal($('composer-suggestions').hidden,false);assert.equal($('prompt').value,'/');
          }
          const plain={key:'ArrowDown',preventDefault(){this.prevented=true;}};$('prompt').onkeydown(plain);
          assert.equal(plain.prevented,true);assert.notEqual($('prompt').attributes['aria-activedescendant'],selected);
        })()""")

    def test_ctrl_enter_sends_without_accepting_suggestion_and_respects_prevented_event(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:[{id:'a',invocation:'/skills',supported:true}]});let sends=0;submit=async()=>sends++;
          type('/sk');await WorkspaceComposer.refresh();
          const prevented=enter({ctrlKey:true,defaultPrevented:true});$('prompt').onkeydown(prevented);
          assert.equal(sends,0);assert.equal($('prompt').value,'/sk');
          const send=enter({ctrlKey:true});$('prompt').onkeydown(send);
          assert.equal(send.prevented,true);assert.equal(sends,1);assert.equal($('prompt').value,'/sk');
          assert.equal(attachments.length,0);
        })()""")

    def test_late_task_and_cursor_replies_never_replace_current_candidates(self):
        self.run_case(r"""(async()=>{
          const pending=[];api=(path,body,signal)=>new Promise(resolve=>pending.push({body,signal,resolve}));
          type('/a');const old=WorkspaceComposer.refresh();active={...active,id:'B',workspace:'C:/fixture/B'};selectionGeneration++;
          WorkspaceComposer.contextChanged();type('/b');const current=WorkspaceComposer.refresh();
          pending[1].resolve({items:[{id:'b',label:'B only',invocation:'/b',supported:true}]});await current;
          pending[0].resolve({items:[{id:'a',label:'A stale',invocation:'/a',supported:true}]});await old;
          assert.equal(pending[0].signal.aborted,true);assert.match(flatText($('composer-suggestion-list')),/B\s+only/);
          assert.doesNotMatch(flatText($('composer-suggestion-list')),/A stale/);
          type('/late');const moved=WorkspaceComposer.refresh();$('prompt').setSelectionRange(1,1);
          pending[2].resolve({items:[{id:'late',label:'Cursor stale',invocation:'/late',supported:true}]});await moved;
          assert.doesNotMatch(flatText($('composer-suggestion-list')),/Cursor stale/);
        })()""")

    def test_metadata_stays_text_and_limit_is_disclosed(self):
        self.run_case(r"""(async()=>{
          api=async()=>({items:Array.from({length:41},(_,index)=>({id:String(index),label:'<img src=x onerror=bad()>',invocation:'/safe-'+index,supported:true}))});
          type('/');await WorkspaceComposer.refresh();assert.equal($('composer-suggestion-list').children.length,40);
          assert.match($('composer-suggestion-note').textContent,/검색어를 더/);assert.equal($('composer-suggestion-list').querySelector('img'),null);
          assert.match(flatText($('composer-suggestion-list')),/<img src=x/);
        })()""")

    def test_partial_empty_is_not_reported_as_absence(self):
        self.run_case(r"""(async()=>{
          let limited=true;api=async()=>({items:[],limited});type('@');await WorkspaceComposer.refresh();
          assert.match($('composer-suggestion-note').textContent,/일부만 확인/);assert.doesNotMatch($('composer-suggestion-note').textContent,/파일이 없어요/);
          type('/');await WorkspaceComposer.refresh();assert.match($('composer-suggestion-note').textContent,/충분히 확인하지 못/);
          limited=false;await WorkspaceComposer.refresh();assert.match($('composer-suggestion-note').textContent,/목록이 비어/);
        })()""")

    def test_mid_sentence_namespace_completion_accepts_tab_without_sending(self):
        self.run_case(r"""(async()=>{
          let query,sends=0;submit=async()=>sends++;
          api=async(path,body)=>{query=body.query;return {items:[{id:'r',label:'/company:report',invocation:'/company:report',supported:true}]};};
          const prefix='이어서 /rep';type(prefix+'ort 월간 자료',prefix.length);await WorkspaceComposer.refresh();
          assert.equal(query,'rep');const event={key:'Tab',preventDefault(){this.prevented=true;}};$('prompt').onkeydown(event);
          assert.equal(event.prevented,true);assert.equal($('prompt').value,'이어서 /company:report 월간 자료');assert.equal(sends,0);
          type('/r');await WorkspaceComposer.refresh();const shift={key:'Tab',shiftKey:true,preventDefault(){this.prevented=true;}};
          $('prompt').onkeydown(shift);assert.equal(shift.prevented,undefined);assert.equal($('prompt').value,'/r');
        })()""")

    def test_quoted_file_replacement_preserves_prose_and_source_path(self):
        self.run_case(r"""(async()=>{
          let query;api=async(path,body)=>{query=body.query;return {items:[{id:'f',label:'src/팀 A.py',path:'C:/fixture/A/src/팀 A.py',supported:true}]};};
          const prefix='분석 @"src/팀';type(prefix+' old.py" 이후 요청',prefix.length);await WorkspaceComposer.refresh();
          assert.equal(query,'src/팀');$('prompt').onkeydown(enter());
          assert.equal($('prompt').value,'분석 @"src/팀 A.py" 이후 요청');assert.equal(attachments[0],'C:/fixture/A/src/팀 A.py');
          $('attachments').children[0].querySelector('button').onclick();
          assert.equal(attachments.length,0);assert.equal($('prompt').value,'분석  이후 요청');
          assert.equal(drafts.get('A').attachments.length,0);
          type('email@example.com');await WorkspaceComposer.refresh();assert.equal($('composer-suggestions').hidden,true);
          type('검토 @src/팀 이후 요청');await WorkspaceComposer.refresh();assert.equal($('composer-suggestions').hidden,true);
        })()""")

    def test_trusted_task_automatically_prepares_once_without_sending_or_changing_draft(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return path==='/api/connect'
            ? {ok:true,connection:{reported:{commands:true},connected:true,slashCommands:['report']}}
            : active.connection ? {items:[{id:'r',label:'/report',invocation:'/report',supported:true}]}
            : {items:[],limited:true,connectRequired:true};};
          type('/rep');attachments=['C:/fixture/A/a.txt'];await WorkspaceComposer.refresh();
          assert.equal($('prompt').value,'/rep');assert.equal(attachments.length,1);
          assert.equal(active.state,'done');assert.equal(active.connection.connected,true);assert.equal($('composer-connect').hidden,true);
          await WorkspaceComposer.refresh();
          assert.equal(calls.filter(call=>call.path==='/api/connect').length,1);assert.equal(calls.filter(call=>call.path==='/api/send').length,0);
          assert.equal($('composer-connect').disabled,false);assert.match(flatText($('composer-suggestion-list')),/rep/);
        })()""")

    def test_prepare_reply_from_other_task_is_ignored(self):
        self.run_case(r"""(async()=>{
          let reply;api=async(path)=>path==='/api/connect'?new Promise(resolve=>reply=resolve):{items:[],connectRequired:true};
          type('/');const pending=WorkspaceComposer.refresh();await flush();assert.equal(typeof reply,'function');
          active={id:'B',workspace:'C:/fixture/B',state:'idle',trusted:true};selectionGeneration++;type('B 초안');WorkspaceComposer.contextChanged();
          reply({connection:{model:'A model'}});await pending;
          assert.equal(active.connection,undefined);assert.equal($('prompt').value,'B 초안');assert.equal($('composer-suggestions').hidden,true);
        })()""")

    def test_home_slash_shows_installed_candidates_and_inserts_exact_name_without_task_or_send(self):
        self.run_case(r"""(async()=>{
          active=null;sessions=[];const calls=[];let sends=0;submit=async()=>sends++;
          api=async(path,body)=>{calls.push({path,body});return {discovery:true,items:[
            {id:'skill',label:'/company-agent:skills',invocation:'/company-agent:skills',
             description:'사용 가능한 스킬 목록',source:'installed',scope:'common',supported:true}]};};
          type('/sk');$('prompt').oninput({});assert.equal($('composer-suggestions').hidden,false);
          await WorkspaceComposer.refresh();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/completions');
          assert.equal(calls[0].body.id,null);assert.equal(calls[0].body.query,'sk');
          assert.equal($('composer-suggestions').hidden,false);assert.equal($('composer-suggestion-list').children.length,1);
          assert.match(flatText($('composer-suggestion-list')),/사용 가능한 스킬 목록/);
          assert.match($('composer-suggestion-context').textContent,/공통 스킬/);
          assert.match($('composer-suggestion-note').textContent,/설치 정보/);
          const event={key:'Tab',preventDefault(){this.prevented=true;}};$('prompt').onkeydown(event);
          assert.equal(event.prevented,true);assert.equal($('prompt').value,'/company-agent:skills ');
          assert.equal(drafts.get('home').text,$('prompt').value);assert.equal(active,null);assert.equal(sessions.length,0);
          assert.equal(sends,0);assert.equal(calls.length,1);
        })()""")

    def test_home_file_trigger_gives_folder_guidance_without_api_or_task_creation(self):
        self.run_case(r"""(async()=>{
          active=null;sessions=[];let calls=0;api=async()=>{calls++;throw Error('API should not run');};
          type('@월간');await WorkspaceComposer.refresh();
          assert.equal(calls,0);assert.equal($('composer-suggestions').hidden,false);
          assert.match($('composer-suggestion-note').textContent,/업무.*폴더/);
          assert.equal($('composer-suggestion-list').children.length,0);assert.equal($('prompt').value,'@월간');
          assert.equal(active,null);assert.equal(sessions.length,0);
        })()""")

    def test_restored_untrusted_task_never_initializes_until_explicit_trust_action(self):
        self.run_case(r"""(async()=>{
          active.trusted=false;const calls=[];api=async(path,body)=>{calls.push({path,body});return {items:[],connectRequired:true};};
          type('/sk');await WorkspaceComposer.refresh();await WorkspaceComposer.refresh();
          assert.equal(calls.length,2);assert.ok(calls.every(call=>call.path==='/api/completions'));
          assert.equal($('folder-dialog').open,undefined);assert.equal($('composer-connect').hidden,false);
          assert.equal($('prompt').value,'/sk');assert.equal(active.trusted,false);
          await $('composer-connect').onclick();assert.equal($('folder-dialog').open,true);
          assert.equal(calls.length,2);assert.equal($('folder-form').dataset.afterTrust,'commands');
        })()""")

    def test_initialization_retains_latest_query_and_only_starts_one_connection(self):
        self.run_case(r"""(async()=>{
          const calls=[];let reply;api=async(path,body)=>{calls.push({path,body});
            if(path==='/api/connect')return new Promise(resolve=>reply=resolve);
            return active.connection ? {items:[{id:body.query,label:'/skill-creator',invocation:'/skill-creator',supported:true}]}
              : {items:[],connectRequired:true};};
          type('/s');const first=WorkspaceComposer.refresh();await flush();assert.equal(typeof reply,'function');
          type('/skill-c');await WorkspaceComposer.refresh();assert.equal($('prompt').value,'/skill-c');
          assert.equal(calls.filter(call=>call.path==='/api/connect').length,1);
          reply({connection:{connected:true,reported:{commands:true}}});await first;
          assert.equal(calls.filter(call=>call.path==='/api/completions').at(-1).body.query,'skill-c');
          assert.equal($('composer-suggestion-list').children.length,1);assert.equal($('composer-suggestions').hidden,false);
          assert.equal($('prompt').value,'/skill-c');assert.equal(calls.filter(call=>call.path==='/api/send').length,0);
          assert.equal(calls.filter(call=>call.path==='/api/connect').length,1);
        })()""")

    def test_automatic_initialization_does_not_steal_focus_or_reopen_after_blur(self):
        self.run_case(r"""(async()=>{
          let reply;const calls=[];api=async(path)=>{calls.push(path);return path==='/api/connect'
            ? new Promise(resolve=>reply=resolve) : {items:[],connectRequired:true};};
          type('/sk');const pending=WorkspaceComposer.refresh();await flush();assert.equal(typeof reply,'function');
          const other=$('settings-open');other.focus();$('prompt').onblur({relatedTarget:other});
          reply({connection:{connected:true}});await pending;
          assert.equal(document.activeElement,other);assert.equal($('composer-suggestions').hidden,true);
          assert.equal($('prompt').value,'/sk');assert.equal(calls.filter(path=>path==='/api/completions').length,1);
          assert.equal(active.connection.connected,true);
        })()""")

    def test_escape_during_initialization_stays_closed_until_the_user_types_again(self):
        self.run_case(r"""(async()=>{
          let reply;const calls=[];api=async(path)=>{calls.push(path);
            if(path==='/api/connect')return new Promise(resolve=>reply=resolve);
            return active.connection ? {items:[{id:'s',label:'/skills',invocation:'/skills',supported:true}]}
              : {items:[],connectRequired:true};};
          type('/sk');const pending=WorkspaceComposer.refresh();await flush();assert.equal(typeof reply,'function');
          const escape={key:'Escape',preventDefault(){this.prevented=true;}};$('prompt').onkeydown(escape);
          assert.equal(escape.prevented,true);assert.equal($('composer-suggestions').hidden,true);
          reply({connection:{connected:true}});await pending;await WorkspaceComposer.connectionChanged();
          assert.equal(document.activeElement,$('prompt'));assert.equal($('prompt').value,'/sk');
          assert.equal($('composer-suggestions').hidden,true);assert.equal(calls.filter(path=>path==='/api/completions').length,1);
          type('/ski');$('prompt').oninput({});assert.equal($('composer-suggestions').hidden,false);
          await WorkspaceComposer.refresh();assert.equal($('composer-suggestion-list').children.length,1);
          assert.equal(calls.filter(path=>path==='/api/connect').length,1);
        })()""")

    def test_failed_automatic_initialization_requires_manual_retry_without_loop(self):
        self.run_case(r"""(async()=>{
          let connects=0;const calls=[];api=async(path)=>{calls.push(path);
            if(path==='/api/connect'){connects++;if(connects===1)throw Error('연결 실패 안내');return {connection:{connected:true}};}
            return active.connection ? {items:[{id:'s',label:'/skills',invocation:'/skills',supported:true}]} : {items:[],connectRequired:true};};
          type('/sk');await WorkspaceComposer.refresh();assert.equal(connects,1);
          assert.match($('composer-suggestion-note').textContent,/연결 실패 안내/);assert.equal($('composer-connect').hidden,false);
          type('/ski');await WorkspaceComposer.refresh();await WorkspaceComposer.refresh();assert.equal(connects,1);
          assert.equal($('composer-connect').disabled,false);await $('composer-connect').onclick();assert.equal(connects,2);
          assert.equal($('composer-connect').hidden,true);assert.equal($('composer-suggestion-list').children.length,1);
          assert.equal($('prompt').value,'/ski');assert.equal(calls.filter(path=>path==='/api/send').length,0);
        })()""")

    def test_no_match_panel_stays_visible_for_home_and_connected_task(self):
        self.run_case(r"""(async()=>{
          const task=active;api=async()=>({items:[],limited:false});active=null;type('/does-not-exist');
          await WorkspaceComposer.refresh();assert.equal($('composer-suggestions').hidden,false);
          assert.match($('composer-suggestion-note').textContent,/공통 스킬.*일치하는 항목/);
          assert.equal($('composer-connect').hidden,true);
          active=task;active.connection={connected:true};selectionGeneration++;type('/does-not-exist');
          await WorkspaceComposer.refresh();assert.equal($('composer-suggestions').hidden,false);
          assert.match($('composer-suggestion-note').textContent,/일치하는 명령이 없어요/);
          assert.equal($('composer-suggestion-list').children.length,0);assert.equal($('composer-connect').hidden,true);
        })()""")

    def test_late_connected_event_refreshes_instead_of_dismissing_typed_completion(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;return {items:[{id:'r',label:'/report',invocation:'/report',supported:true}]};};
          type('/rep');$('prompt').focus();await WorkspaceComposer.refresh();
          await WorkspaceComposer.connectionChanged();assert.equal(calls,2);assert.equal($('composer-suggestions').hidden,false);
          assert.equal($('prompt').value,'/rep');assert.equal($('composer-suggestion-list').children.length,1);
          $('settings-open').focus();await WorkspaceComposer.connectionChanged();assert.equal($('composer-suggestions').hidden,true);
        })()""")

    def test_prepare_trust_confirmation_never_sends_the_draft(self):
        self.run_case(r"""(async()=>{
          active.trusted=false;type('/rep');const paths=[];let sends=0;submit=async()=>sends++;
          api=async(path)=>{paths.push(path);return path==='/api/trust'?{}:path==='/api/connect'?{connection:{reported:{commands:true}}}:{items:[]};};
          await $('composer-connect').onclick();assert.equal($('folder-dialog').open,true);assert.equal(paths.length,0);
          assert.equal($('folder-form').dataset.afterTrust,'commands');$('trust').checked=true;
          await $('folder-form').onsubmit({submitter:{value:'ok',disabled:false},preventDefault(){}});
          assert.equal(sends,0);assert.equal($('prompt').value,'/rep');assert.ok(paths.includes('/api/connect'));
        })()""")

    def test_shell_prefix_never_silently_becomes_ai_request(self):
        self.run_case(r"""(async()=>{
          let calls=0;api=async()=>{calls++;return {};};type('! git status');await WorkspaceComposer.refresh();
          assert.equal(calls,0);assert.equal($('composer-native').hidden,false);
          await submit();assert.equal(calls,0);assert.equal($('prompt').value,'! git status');assert.match($('toast').textContent,/원본 Claude Code/);
        })()""")


if __name__=='__main__':unittest.main()
