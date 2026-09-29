"""Pending request review, scope selection and viewport-following regressions.

The DOM double checks interaction and exact review content. Card sizing at
different viewport scales is exercised separately in the browser fixture.
"""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_controls_frontend import SETUP
from tests.test_workspace_frontend_state import HARNESS, NODE


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(NODE, "Node.js is required for request card checks")
class WorkspaceRequestCardsFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), SETUP + script],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_long_command_remains_fully_reviewable_with_actions_outside_scrolling_body(self):
        self.run_case(r"""
          const command=Array.from({length:60},(_,i)=>`echo "검토 ${i}"`).join('\n');
          const request={id:'long-command',tool:'Bash',input:{command,description:'설명 '.repeat(300)}};
          renderRequest(request);const card=$('requests').children[0],body=card.querySelector('.request-body');
          assert.equal(body.tabIndex,0);assert.equal(body.attributes.role,'region');
          assert.equal(card.querySelector('.request-header').parent,card);
          assert.equal(card.querySelector('.request-actions').parent.parent,card);
          assert.equal(body.querySelector('.request-actions'),null);
          const commandPreview=body.querySelector('.approval-command').querySelector('pre');
          assert.equal(commandPreview.textContent,command);assert.equal(commandPreview.tabIndex,0);
          const details=body.querySelector('.approval-input-detail');assert.equal(!!details.open,false);
          assert.deepEqual(JSON.parse(details.querySelector('pre').textContent),request.input);
          assert.equal(card.querySelector('.request-selection').textContent,'이번 요청에만 적용됩니다.');
        """)

    def test_large_edit_and_cli_reason_are_plain_text_and_not_discarded(self):
        self.run_case(r"""
          const before='<script>old()</script>\n'.repeat(80),after='<img src=x onerror="bad()">\n'.repeat(80);
          const decisionReason='검토가 필요한 이유 <b>원문</b> '.repeat(60);
          renderRequest({id:'large-edit',tool:'Edit',input:{file_path:'C:/업무/보고서.txt',old_string:before,new_string:after},
            decisionReason,matchedAskRule:{source:'userSettings',toolName:'Edit',ruleContent:'C:/업무/**'}});
          const body=$('requests').children[0].querySelector('.request-body'),diff=body.querySelector('.approval-change-detail');
          assert.equal(!!diff.open,false);assert.equal(diff.querySelector('.change-before').querySelector('pre').textContent,before);
          assert.equal(diff.querySelector('.change-after').querySelector('pre').textContent,after);
          assert.equal(body.querySelector('.approval-reason').querySelector('p').textContent,decisionReason);
          const rule=body.querySelector('.approval-ask-rule');assert.equal(!!rule.open,false);
          assert.match(flatten(rule),/userSettings · Edit · C:\/업무\/\*\*/);
          assert.equal(descendants(body,'SCRIPT').length,0);assert.equal(descendants(body,'IMG').length,0);
        """)

    def test_scope_summary_tracks_explicit_selection_without_changing_default_or_payload(self):
        self.run_case(r"""(async()=>{
          const label='모든 프로젝트에서 항상 허용 · 내 설정',description='다음 연결에도 적용됩니다. '.repeat(100);
          renderRequest({id:'scopes',tool:'Bash',input:{command:'git status'},permissionChoices:[
            {id:'global',destination:'userSettings',label,description,rules:[{toolName:'Bash',ruleContent:'git status'}]}]});
          const card=$('requests').children[0],scopes=card.querySelector('.approval-scopes'),inputs=descendants(scopes,'INPUT');
          assert.equal(!!scopes.open,false);assert.equal(inputs[0].checked,true);assert.equal(inputs[1].checked,false);
          assert.equal(card.querySelector('.request-selection').textContent,'이번만 허용');
          assert.ok(flatten(scopes).includes(description));let sent;api=async(path,data)=>{sent=data;return {ok:true};};
          inputs[0].checked=false;inputs[1].checked=true;inputs[1].onchange();
          assert.equal(card.querySelector('.request-selection').textContent,label);
          assert.equal(scopes.querySelector('summary').textContent,'승인 범위: '+label);
          assert.equal(sent,undefined);await card.querySelector('.request-actions').children[1].onclick();
          assert.equal(sent.permissionChoiceId,'global');assert.equal('rules' in sent,false);
        })()""")

    def test_multiple_questions_and_duplicate_events_preserve_answers_and_scroll_position(self):
        self.run_case(r"""(async()=>{
          const request={id:'questions',tool:'AskUserQuestion',input:{questions:Array.from({length:4},(_,i)=>({
            question:`질문 ${i+1}`,options:[{label:`선택 ${i+1}`,description:'긴 설명 '.repeat(70)}]}))}};
          const area=$('work-area');area.scrollHeight=1800;area.scrollTop=900;area.clientHeight=500;
          handleEvent({type:'request',data:request});const card=$('requests').children[0],body=card.querySelector('.request-body');
          assert.equal(card.scrolledIntoView,true);card.scrolledIntoView=false;body.scrollTop=260;
          const fields=descendants(body,'FIELDSET'),customs=descendants(body,'INPUT').filter(input=>input.type==='text');
          assert.equal(fields.length,4);customs[0].value='직접 입력한 답변';let calls=[];
          api=async(path,data)=>{calls.push(data);return {ok:true};};
          handleEvent({type:'request',data:request});assert.equal($('requests').children.length,1);
          assert.equal(card.scrolledIntoView,false);assert.equal(body.scrollTop,260);assert.equal(area.scrollTop,900);
          assert.equal(customs[0].value,'직접 입력한 답변');
          await card.querySelector('.request-actions').children[1].onclick();assert.equal(calls.length,0);
          fields.slice(1).forEach(field=>descendants(field,'INPUT').find(input=>input.type==='radio').checked=true);
          await card.querySelector('.request-actions').children[1].onclick();assert.equal(calls.length,1);
          assert.deepEqual(calls[0].answers,{'질문 1':'직접 입력한 답변','질문 2':'선택 2','질문 3':'선택 3','질문 4':'선택 4'});
        })()""")

    def test_pending_request_does_not_jump_to_page_bottom_on_status_or_assistant_event(self):
        self.run_case(r"""
          const area=$('work-area');area.scrollHeight=1000;area.clientHeight=500;area.scrollTop=450;
          handleEvent({type:'request',data:{id:'read',tool:'Read',input:{file_path:'C:/자료.txt'}}});
          const card=$('requests').children[0];card.scrolledIntoView=false;
          handleEvent({type:'status',data:{state:'approval'}});
          handleEvent({type:'assistant',data:{text:'확인을 기다리고 있습니다.'}});
          assert.equal(area.scrollTop,450);assert.equal(card.scrolledIntoView,false);
          handleEvent({type:'request_closed',data:{id:'read',state:'running'}});
          assert.equal($('requests').children.length,0);assert.equal(area.scrollTop,1000);
        """)

    def test_deferred_reveal_ignores_removed_cards_new_session_and_catalog(self):
        self.run_case(r"""
          let frames=[];requestAnimationFrame=fn=>frames.push(fn);
          const request={id:'deferred',tool:'Read',input:{}};
          handleEvent({type:'request',data:request});const first=$('requests').children[0];
          first.remove();frames.splice(0).forEach(fn=>fn());assert.equal(!!first.scrolledIntoView,false);
          handleEvent({type:'request',data:request});const next=$('requests').children[0];active.id='B';
          frames.splice(0).forEach(fn=>fn());assert.equal(!!next.scrolledIntoView,false);
          next.remove();handleEvent({type:'request',data:request});const hidden=$('requests').children[0];
          globalThis.WorkspaceCapabilities={contextChanged(){},isOpen:()=>true};
          frames.splice(0).forEach(fn=>fn());assert.equal(!!hidden.scrolledIntoView,false);
        """)


if __name__ == "__main__":
    unittest.main()
