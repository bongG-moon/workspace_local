"""Mode boundaries in the shipped UI. No browser, account, or model is used."""
from pathlib import Path
import json
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE
from tests.test_workspace_productivity_frontend import HARNESS as MODULE_HARNESS

ROOT = Path(__file__).resolve().parents[1]
SETUP = r'''
const work={id:'work',kind:'work',title:'업무',workspace:'C:/work',state:'idle',messages:[],trusted:true,seq:0};
const chat={id:'chat',kind:'chat',title:'대화',workspace:'C:/owned/chat',state:'idle',messages:[],trusted:true,seq:0};
sessions=[work,chat];boot={};poll=()=>{};refreshInspector=()=>{};toast=()=>{};
let calls=[];api=async(path,body)=>{calls.push({path,body});if(path.startsWith('/api/session?'))return {...sessions.find(row=>row.id===decodeURIComponent(path.split('id=')[1]))};return {ok:true};};
'''


@unittest.skipUnless(NODE, 'Node.js required')
class ModesFrontendTests(unittest.TestCase):
    def run_case(self, code, modules=()):
        script = SETUP + code
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), script,
                                 json.dumps([str(ROOT / f'local_app/web/{module}.js') for module in modules])],
                                input=MODULE_HARNESS if modules else HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_chat_starter_keeps_existing_draft_and_attachments_without_execution(self):
        self.run_case(r'''(()=>{
          currentMode='chat';active=null;attachments=[{path:'C:/owned/copy.txt'}];
          $('prompt').value='내가 먼저 쓴 질문';
          assert.equal(insertChatStarter('생각을 정리해 줘.'),true);
          assert.equal($('prompt').value,'내가 먼저 쓴 질문\n\n생각을 정리해 줘.');
          assert.equal(drafts.get('home:chat').text,$('prompt').value);
          assert.equal(drafts.get('home:chat').attachments[0].path,'C:/owned/copy.txt');
          assert.equal(attachments.length,1);assert.equal(calls.length,0);
        })()''')

    def test_chat_starter_never_changes_work_active_chat_or_locked_inputs(self):
        self.run_case(r'''(()=>{
          $('prompt').value='유지할 초안';
          currentMode='work';assert.equal(insertChatStarter('예시'),false);
          currentMode='chat';active=chat;assert.equal(insertChatStarter('예시'),false);
          active=null;globalThis.WorkspaceUpgrade={isLocked:()=>true};assert.equal(insertChatStarter('예시'),false);
          globalThis.WorkspaceUpgrade={isLocked:()=>false};appClosed=true;assert.equal(insertChatStarter('예시'),false);
          assert.equal($('prompt').value,'유지할 초안');assert.equal(calls.length,0);
        })()''')

    def test_chat_starter_respects_composer_limit_without_truncating_draft(self):
        self.run_case(r'''(()=>{
          currentMode='chat';active=null;$('prompt').value='보관할 원문';$('prompt').maxLength=10;
          assert.equal(insertChatStarter('추가하면 한도를 넘는 예시'),false);
          assert.equal($('prompt').value,'보관할 원문');assert.equal(calls.length,0);
        })()''')

    def test_upload_policy_and_destination_stay_with_work_after_switch_to_chat(self):
        self.run_case(r'''(async()=>{
          active=work;currentMode='work';
          boot={attachmentPolicy:{extensions:['.txt','.exe'],maxUploadBytes:100,maxAttachments:12},chatAttachmentPolicy:{extensions:['.txt'],maxUploadBytes:1,maxAttachments:1}};
          let reply;const prepared=[],uploaded=[];
          api=async(path,data)=>{prepared.push(data);if(prepared.length===1)return new Promise(resolve=>reply=resolve);return {ok:true};};
          fetch=async(path,options)=>{uploaded.push({path,name:options.body.name});return {ok:true,json:async()=>({path:'C:/work-copies/'+options.body.name,copied:true})};};
          const pending=WorkspaceAttachments.uploadFiles([{name:'first.txt',size:10},{name:'second.exe',size:20}]);
          assert.equal(prepared.length,1);currentMode='chat';active=chat;selectionGeneration++;attachments=[];$('prompt').value='대화 초안';
          reply({ok:true});await pending;
          assert.equal(prepared.length,2);assert.ok(prepared.every(row=>row.id==='work'));
          assert.deepEqual(uploaded.map(row=>row.name),['first.txt','second.exe']);assert.ok(uploaded.every(row=>row.path.includes('id=work')));
          assert.equal(drafts.get('work').attachments.length,2);assert.equal(attachments.length,0);
          assert.equal(currentMode,'chat');assert.equal(active.id,'chat');assert.equal($('prompt').value,'대화 초안');assert.equal($('send').disabled,false);
        })()''', ('attachments',))

    def test_chat_upload_keeps_restricted_types_after_switch_to_work(self):
        self.run_case(r'''(async()=>{
          active=chat;currentMode='chat';boot={attachmentPolicy:{extensions:['.txt','.exe']},chatAttachmentPolicy:{extensions:['.txt']}};
          let reply;const prepared=[],uploaded=[];
          api=async(path,data)=>{prepared.push(data);return new Promise(resolve=>reply=resolve);};
          fetch=async(path,options)=>{uploaded.push(path);return {ok:true,json:async()=>({path:'C:/chat-copies/'+options.body.name,copied:true})};};
          const pending=WorkspaceAttachments.uploadFiles([{name:'first.txt',size:10},{name:'second.exe',size:20}]);
          currentMode='work';active=work;selectionGeneration++;attachments=[];reply({ok:true});await pending;
          assert.equal(prepared.length,1);assert.equal(prepared[0].id,'chat');assert.equal(uploaded.length,1);
          assert.equal(drafts.get('chat').attachments.length,1);assert.equal(attachments.length,0);assert.equal(currentMode,'work');
        })()''', ('attachments',))

    def test_chat_upload_still_rechecks_server_admission_after_mode_switch(self):
        self.run_case(r'''(async()=>{
          active=chat;currentMode='chat';boot={attachmentPolicy:{extensions:['.txt']},chatAttachmentPolicy:{extensions:['.txt']}};
          let reply;const prepared=[],uploaded=[];
          api=async(path,data)=>{prepared.push(data);if(prepared.length===1)return new Promise(resolve=>reply=resolve);throw Error('첨부 저장 한도 초과');};
          fetch=async(path,options)=>{uploaded.push(path);return {ok:true,json:async()=>({path:'C:/chat-copies/'+options.body.name,copied:true})};};
          const pending=WorkspaceAttachments.uploadFiles([{name:'first.txt',size:10},{name:'second.txt',size:20}]);
          currentMode='work';active=work;selectionGeneration++;attachments=[];reply({ok:true});await pending;
          assert.equal(prepared.length,2);assert.ok(prepared.every(row=>row.id==='chat'));assert.equal(uploaded.length,1);
          assert.equal(drafts.get('chat').attachments.length,1);assert.equal(WorkspaceAttachments.isUploading(),false);assert.equal(currentMode,'work');
        })()''', ('attachments',))

    def test_mode_badges_appear_for_activity_and_hide_after_completion(self):
        self.run_case(r'''(()=>{
          for(const kind of ['chat','work']){const badge=el('small');badge.className='mode-state';$('mode-'+kind).append(badge);}
          const chatBadge=$('mode-chat').querySelector('.mode-state'),workBadge=$('mode-work').querySelector('.mode-state');
          updateModeNavigation();assert.equal(chatBadge.hidden,true);assert.equal(workBadge.hidden,true);
          chat.state='running';updateModeNavigation();assert.equal(chatBadge.hidden,false);assert.equal(chatBadge.textContent,'1');
          assert.equal(workBadge.hidden,true);assert.equal($('mode-work').getAttribute('aria-pressed'),'true');
          chat.state='idle';globalThis.WorkspaceAttention={countFor:id=>id==='work'?2:0};
          updateModeNavigation();assert.equal(chatBadge.hidden,true);assert.equal(workBadge.hidden,false);
          assert.equal(workBadge.textContent,'2');assert.equal(workBadge.classList.contains('needs-response'),true);
          globalThis.WorkspaceAttention={countFor:()=>0};updateModeNavigation();
          assert.equal(workBadge.hidden,true);assert.equal(workBadge.classList.contains('needs-response'),false);
          assert.equal(calls.length,0);
        })()''')

    def test_mode_filter_keeps_legacy_tasks_and_hides_other_kind(self):
        self.run_case(r'''(()=>{
          sessions.push({...work,id:'legacy',kind:undefined});renderSessions();
          assert.equal($('sessions').children.length,2);
          currentMode='chat';renderSessions();assert.equal($('sessions').children.length,1);
          assert.equal($('sessions').children[0].dataset.sessionId,'chat');
          assert.equal(sessionMetadata(chat).kind,'chat');
          assert.equal(sessionMetadata(work).kind,'work');
        })()''')

    def test_mode_switch_remembers_selection_home_draft_and_search_without_connections(self):
        self.run_case(r'''(async()=>{
          await selectSession('work');$('prompt').value='업무 초안';$('session-search').value='업무 검색';
          await switchMode('chat');assert.equal(active,null);assert.equal($('prompt').value,'');
          $('prompt').value='대화 홈 초안';$('session-search').value='대화 검색';
          await switchMode('work');assert.equal(active.id,'work');assert.equal($('prompt').value,'업무 초안');assert.equal($('session-search').value,'업무 검색');
          await switchMode('chat');assert.equal($('prompt').value,'대화 홈 초안');assert.equal($('session-search').value,'대화 검색');
          assert.equal(drafts.get('home:chat').text,'대화 홈 초안');
          assert.ok(calls.every(row=>row.path.startsWith('/api/session?')));
        })()''')

    def test_opening_notification_for_other_mode_selects_correct_profile(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';await selectSession('work');assert.equal(currentMode,'work');
          await selectSession('chat');assert.equal(currentMode,'chat');
          assert.equal(active.kind,'chat');assert.equal($('sessions').children.length,1);
          assert.ok(calls.every(row=>row.path.startsWith('/api/session?')));
        })()''')

    def test_busy_general_chat_keeps_input_instead_of_enqueuing_work(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';active={...chat,state:'running'};$('prompt').value='다음 질문';
          globalThis.WorkspaceWorkflow={isSubmitting:()=>false,send(){throw Error('Must not enqueue a general chat');}};
          await submit();assert.equal(calls.length,0);assert.equal($('prompt').value,'다음 질문');
          assert.equal(drafts.get('chat').text,'다음 질문');
        })()''')

    def test_general_chat_creation_is_lazy_and_keeps_home_text_without_trust(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';sessions=[work];$('prompt').value='처음 질문';
          api=async(path,body)=>{calls.push({path,body});if(path==='/api/create')return {...chat};if(path.startsWith('/api/session?'))return {...chat};return {ok:true};};
          const result=await ensureChatSession();assert.equal(result.id,'chat');
          assert.equal(calls[0].path,'/api/create');assert.equal(calls[0].body.kind,'chat');
          assert.equal(calls[0].body.workspace,undefined);assert.equal(calls[0].body.trusted,undefined);
          assert.equal($('prompt').value,'처음 질문');assert.equal($('folder-dialog').open,undefined);
          assert.ok(!calls.some(row=>row.path==='/api/connect'));
        })()''')

    def test_general_chat_delete_cancel_and_confirm_use_permanent_cleanup(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';active=chat;renderSessions();$('prompt').value='삭제 전 초안';
          let forgotten=null;globalThis.WorkspaceDraftPersistence={flush:async()=>{},changed(){},forget(id){forgotten=id;drafts.delete(id);}};
          const pending=hideSession('chat');assert.match($('action-message').textContent,/전용 저장공간/);
          $('action-cancel').onclick();assert.equal(await pending,false);assert.equal(calls.length,0);
          const confirmed=hideSession('chat');$('action-confirm').onclick();assert.equal(await confirmed,true);
          assert.equal(calls[0].path,'/api/session/delete');assert.equal(calls[0].body.confirmed,true);
          assert.equal(forgotten,'chat');assert.equal(active,null);assert.equal(sessions.length,1);
          assert.equal(drafts.has('chat'),false);
        })()''')

    def test_cleanup_pending_is_not_claimed_as_complete(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';active=chat;api=async()=>({ok:true,cleanupPending:true,message:'다음 실행 때 다시 정리합니다.'});
          const promise=hideSession('chat');$('action-confirm').onclick();await promise;
          assert.equal(boot.chatCleanup.pendingCount,1);assert.equal($('chat-cleanup-note').hidden,false);
          assert.match($('chat-cleanup-note').textContent,/다음 실행/);
        })()''')

    def test_sidebar_rename_does_not_select_or_connect_other_session(self):
        self.run_case(r'''(()=>{
          active=work;renderSessions();openRename(chat);
          assert.equal(renameContext.id,'chat');assert.equal($('rename-input').value,'대화');
          assert.equal(active.id,'work');assert.equal(calls.length,0);
        })()''')

    def test_chat_home_recovery_restores_mode_and_keeps_home_draft(self):
        self.run_case(r'''(async()=>{
          currentMode='chat';$('prompt').value='복원할 대화 초안';
          const snapshot=captureScreenRecovery();assert.equal(snapshot.mode,'chat');
          await switchMode('work');drafts.clear();
          const restored=await restoreScreenRecovery(snapshot);
          assert.equal(currentMode,'chat');assert.equal(active,null);
          assert.equal($('prompt').value,'복원할 대화 초안');assert.deepEqual(restored.conflicts,[]);
        })()''')

    def test_attachment_read_notice_is_plain_text_and_preserves_scope(self):
        self.run_case(r'''(()=>{
          const article=el('article');renderAttachmentReads(article,[{name:'<script>x</script>.txt',status:'partial',message:'앞부분만 읽음'}]);
          assert.equal(article.children[0].textContent,'읽기 범위');const list=article.children[1];assert.equal(list.children[0].dataset.status,'partial');
          assert.equal(list.children[0].children[0].textContent,'<script>x</script>.txt');
          assert.equal(list.children[0].children[1].textContent,'앞부분만 읽음');
        })()''')


if __name__ == '__main__':
    unittest.main()
