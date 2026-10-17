"""General-chat model selection uses the existing controls without tool access."""
import subprocess
import unittest

from tests.test_workspace_inline_controls_frontend import HARNESS, NODE, ROOT, SETUP


@unittest.skipUnless(NODE, 'Node.js required')
class ChatModelFrontendTests(unittest.TestCase):
    def run_case(self, script):
        setup = SETUP + "currentMode='chat';active.kind='chat';sessions=[{...active}];WorkspaceInlineControls.render();"
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), setup + script,
             str(ROOT / 'local_app/web/inline-controls.js'), str(ROOT / 'local_app/web/composer.js')],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_chat_selects_model_only_and_does_not_change_personal_settings(self):
        self.run_case(r'''(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {connection:{...connection(),model:body.model,modelOverride:body.model},modelOverride:body.model};};
          await $('composer-model').onclick();
          assert.equal($('composer-controls-panel').hidden,false);
          assert.match($('runtime-panel-note').textContent,/이 대화에만/);
          assert.equal([...$('runtime-panel-options').children].filter(b=>b.attributes['aria-pressed']==='true').length,1);
          assert.equal(choose('기존 설정 사용').attributes['aria-pressed'],'true');
          await choose('회사 모델').onclick();
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/model');assert.equal(calls[0].body.id,'A');
          assert.equal(textFor('composer-model'),'회사 모델');assert.match($('composer-control-status').textContent,/이 대화/);assertDraft();
          await WorkspaceInlineControls.open('effort');await WorkspaceInlineControls.open('permission');
          assert.equal($('composer-controls-panel').hidden,true);assert.equal(calls.length,1);
        })()''')

    def test_home_click_creates_once_and_keeps_text_typed_during_creation(self):
        self.run_case(r'''(async()=>{
          active=null;sessions=[];poll=()=>{};refreshInspector=()=>{};
          const created={id:'new-chat',kind:'chat',title:'새 대화',workspace:'C:/owned/new-chat',state:'idle',trusted:true,messages:[],seq:0};
          let finish;const calls=[];api=async(path,body)=>{calls.push({path,body});if(path==='/api/create')return new Promise(r=>finish=r);if(path.startsWith('/api/session?'))return {...created};if(path==='/api/connect')return {connection:connection()};throw Error(path);};
          const opening=$('composer-model').onclick();assert.equal(connectionPreparing,true);
          await $('composer-model').onclick();assert.equal(calls.length,1);
          $('prompt').value='연결 준비 중 더 작성한 질문';finish(created);await opening;
          assert.equal(active.id,'new-chat');assert.equal(connectionPreparing,false);
          assert.equal($('prompt').value,'연결 준비 중 더 작성한 질문');
          assert.equal(attachments[0],'C:/fixture/A/report.csv');
          assert.deepEqual(calls.map(c=>c.path),['/api/create','/api/session?id=new-chat','/api/connect']);
          assert.equal($('folder-dialog').open,undefined);assert.equal($('composer-controls-panel').hidden,false);
        })()''')

    def test_home_session_lookup_keeps_latest_input_and_attachments(self):
        self.run_case(r'''(async()=>{
          active=null;sessions=[];poll=()=>{};refreshInspector=()=>{};
          const created={id:'new-chat',kind:'chat',title:'새 대화',workspace:'C:/owned/new-chat',state:'idle',trusted:true,messages:[],seq:0};
          let finish;const calls=[];api=async(path,body)=>{calls.push({path,body});if(path==='/api/create')return created;if(path.startsWith('/api/session?'))return new Promise(r=>finish=r);if(path==='/api/connect')return {connection:connection()};throw Error(path);};
          const opening=$('composer-model').onclick();for(let i=0;i<12&&!finish;i++)await Promise.resolve();assert.ok(finish);
          $('prompt').value='대화를 불러오는 중 더 입력한 질문';attachments.push('C:/fixture/new-attachment.csv');finish(created);await opening;
          assert.equal(active.id,'new-chat');assert.equal($('prompt').value,'대화를 불러오는 중 더 입력한 질문');
          assert.equal(attachments.length,2);assert.equal(drafts.get('new-chat').text,$('prompt').value);assert.equal(drafts.get('new-chat').attachments.length,2);
          assert.equal(drafts.has('home:chat'),false);assert.equal(calls.filter(c=>c.path==='/api/connect').length,1);
        })()''')

    def test_session_lookup_failure_is_visible_without_discarding_home_draft(self):
        self.run_case(r'''(async()=>{
          active=null;sessions=[];const calls=[];
          api=async(path,body)=>{calls.push({path,body});if(path==='/api/create')return {id:'new-chat',kind:'chat',title:'새 대화',state:'idle',trusted:true,messages:[]};throw Error('대화를 불러오지 못했어요.');};
          await $('composer-model').onclick();assert.equal(active,null);assert.equal(connectionPreparing,false);
          assert.equal($('composer-control-status').hidden,false);assert.match($('composer-control-status').textContent,/불러오지 못했어요/);
          assert.equal(calls.length,2);assert.equal(drafts.get('home:chat').text,$('prompt').value);assertDraft();
        })()''')

    def test_home_create_failure_is_visible_and_can_retry(self):
        self.run_case(r'''(async()=>{
          active=null;sessions=[];let calls=0;api=async()=>{calls++;throw Error('대화 저장 공간을 확인해 주세요.');};
          await $('composer-model').onclick();assert.equal(active,null);assert.equal(connectionPreparing,false);
          assert.equal($('composer-control-status').hidden,false);assert.match($('composer-control-status').textContent,/저장 공간/);
          await $('composer-model').onclick();assert.equal(calls,2);assertDraft();
        })()''')

    def test_navigation_during_chat_creation_never_connects_wrong_mode(self):
        self.run_case(r'''(async()=>{
          active=null;sessions=[];let finish;const calls=[];
          api=async(path,body)=>{calls.push({path,body});return new Promise(r=>finish=r);};
          const opening=$('composer-model').onclick();
          currentMode='work';selectionGeneration++;finish({id:'late-chat',kind:'chat',title:'새 대화',state:'idle',trusted:true,messages:[]});await opening;
          assert.equal(currentMode,'work');assert.equal(active,null);assert.equal(connectionPreparing,false);
          assert.equal(calls.length,1);assert.equal($('composer-controls-panel').hidden,true);assertDraft();
        })()''')

    def test_restore_failure_blocks_send_and_opens_model_recovery(self):
        self.run_case(r'''(async()=>{
          active.connection.controlRestore={status:'needs_input',canSend:false,issues:[{control:'model',message:'이전 모델을 적용하지 못했어요.'}]};
          let calls=0;api=async()=>{calls++;throw Error('Must not send');};
          await submit();assert.equal(calls,0);assert.equal($('composer-controls-panel').hidden,false);
          assert.equal($('runtime-panel-title').textContent,'모델 선택');assertDraft();
        })()''')

    def test_escape_closes_model_picker_but_shift_tab_never_changes_chat_permissions(self):
        self.run_case(r'''(async()=>{
          await $('composer-model').onclick();$('prompt').focus();
          let prevented=false;assert.equal(WorkspaceInlineControls.keydown({key:'Escape',preventDefault(){prevented=true;}}),true);
          assert.equal(prevented,true);assert.equal($('composer-controls-panel').hidden,true);
          assert.equal(WorkspaceInlineControls.keydown({key:'Tab',shiftKey:true,preventDefault(){throw Error('Must not cycle permissions');}}),false);
          active.state='running';WorkspaceInlineControls.render();assert.equal($('composer-model').disabled,true);
          assert.match($('composer-control-status').textContent,/답변이 끝나면/);assert.doesNotMatch($('composer-control-status').textContent,/승인|Effort/);
          await $('composer-model').onclick();assert.equal($('composer-controls-panel').hidden,true);
        })()''')


if __name__ == '__main__':
    unittest.main()
