"""Error guidance classifies failures without reproducing raw CLI secrets."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.request import urlopen
from tests.test_workspace_frontend_state import HARNESS, NODE
from local_app.server import LocalApp, Server

ROOT=Path(__file__).resolve().parents[1]
HELP=ROOT/'local_app/web/error-help.js'
HARNESS_WITH_HELP=HARNESS.replace("const scenario=process.argv[3];", "vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'),context,{filename:'error-help.js'});\nconst scenario=process.argv[3];")

@unittest.skipUnless(NODE,'Node.js is required')
class WorkspaceErrorHelpTests(unittest.TestCase):
    def run_case(self, scenario):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),scenario,str(HELP)],input=HARNESS_WITH_HELP,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr or result.stdout)

    def test_shell_controls_are_on_demand_and_copy_only_known_fields(self):
        self.run_case(r'''(async()=>{
          const calls=[],notices=[];
          const controls={context:()=>({version:'0.23.48',build:'a'.repeat(64),root:'C:/private/updates/versions/0.23.48'}),
            toast:text=>notices.push(text),openSettings(){},applied:async()=>calls.push('applied'),
            api:async(path,body)=>{
              calls.push(body||'read');
              if(!body)return {choice:'default',available:{default:true,windows:true,pwsh:true}};
              if(body.choice==='windows'){const e=Error('점검 실패');e.diagnostic={category:'profile_parse',stage:'profile_restore',shellVersion:'5.1',errorLine:12,
                encodingHint:'utf8_without_bom',profile:'PRIVATE-PATH',token:'PRIVATE-TOKEN'};throw e;}
              return {choice:body.choice,diagnostic:{stage:'help',shellVersion:'7.4',commandType:'Application'}};
            }};
          globalThis.WorkspaceErrorHelp.bindShell(controls);
          const panel=$('claude-shell-panel'),select=$('claude-shell-choice'),check=$('claude-shell-check'),apply=$('claude-shell-apply');
          select.options=[{value:'default'},{value:'windows'},{value:'pwsh'}];
          assert.deepEqual(calls,[]);panel.open=true;await panel.ontoggle();
          assert.deepEqual(calls,['read']);assert.equal(apply.disabled,true);
          select.value='windows';select.onchange();await apply.onclick();assert.equal(apply.disabled,false);
          const report=$('claude-shell-diagnostic').textContent;
          assert.match(report,/profile_parse/);assert.match(report,/12행/);assert.match(report,/UTF-8/);
          assert.match(report,/업데이트 버전 폴더/);assert.doesNotMatch(report,/PRIVATE|private/);
          select.value='pwsh';select.onchange();await check.onclick();assert.equal(apply.disabled,false);
          await apply.onclick();assert.equal(apply.disabled,true);
          assert.deepEqual(calls,['read',{choice:'windows',checkOnly:false,configDir:'',executable:''},{choice:'pwsh',checkOnly:true,configDir:'',executable:''},{choice:'pwsh',checkOnly:false,configDir:'',executable:''},'applied']);
          error('연결 실패',null,'launch_cli_version',{category:'cli_version',stage:'version',startupProfile:{category:'profile_parse',
            stage:'launcher_profile',errorLine:42,path:'PRIVATE-PATH'}});
          $('error-help-open').onclick();
          assert.equal($('error-launch-details').hidden,false);assert.match($('error-launch-summary').textContent,/42행/);
          assert.doesNotMatch($('error-launch-summary').textContent,/PRIVATE|private/);
          error('로그인 만료',null,'authentication_failed');$('error-help-open').onclick();
          assert.equal($('error-launch-details').hidden,false);assert.equal($('error-launch-summary').textContent,'');
          assert.equal($('error-launch-summary').hidden,true);assert.equal($('error-launch-copy').hidden,true);
          assert.equal($('error-launch-open').textContent,'터미널과 경로 비교');
          $('error-launch-open').onclick();assert.equal($('claude-shell-paths').open,true);
          error('로그인 만료',null,'authentication_failed',{startupProfile:{category:'profile_parse',
            stage:'launcher_profile',errorLine:15,source:'PRIVATE'}});
          $('error-help-open').onclick();assert.match($('error-help-title').textContent,/인증/);
          assert.equal($('error-launch-details').hidden,false);assert.match($('error-launch-summary').textContent,/15행/);
          assert.doesNotMatch($('error-launch-summary').textContent,/PRIVATE/);
        })()''')

    def test_paths_can_be_checked_applied_and_restored_individually(self):
        self.run_case(r'''(async()=>{
          const saved={choice:'default',configDir:'D:/기존 설정',executable:'',runtime:{entry:'C:/기본/claude.cmd',configRoot:'D:/기존 설정'}};
          const calls=[];
          let picked={status:'cancel',paths:[]};
          WorkspaceErrorHelp.bindShell({
            context:()=>({}),applied:async()=>{},toast:()=>{},api:async(path,body)=>{
              calls.push([path,body]);if(path==='/api/pick')return picked;
              if(!body)return saved;
              if(body.configDir==='D:/없는 폴더')throw Error('설정 폴더를 확인하세요.');
              return {...body,runtime:{entry:body.executable,configRoot:body.configDir||'D:/자동'}};
            }});
          const panel=$('claude-shell-panel'),sel=$('claude-shell-choice'),folder=$('claude-config-dir');
          const exe=$('claude-executable'),check=$('claude-shell-check'),apply=$('claude-shell-apply');
          sel.options=[];panel.open=$('settings-dialog').open=true;await panel.ontoggle();
          assert.equal(folder.value,saved.configDir);assert.equal($('claude-executable-current').textContent,saved.runtime.entry);
          assert.equal(apply.disabled,true);
          await $('claude-config-pick').onclick();assert.equal(folder.value,saved.configDir);
          assert.deepEqual(calls.at(-1),['/api/pick',{kind:'claude-config'}]);
          picked={status:'success',paths:['D:/도구/claude.cmd']};await $('claude-executable-pick').onclick();
          assert.equal(exe.value,picked.paths[0]);assert.deepEqual(calls.at(-1),['/api/pick',{kind:'claude-executable'}]);
          folder.value='D:/없는 폴더';folder.oninput();await apply.onclick();assert.equal(apply.disabled,false);
          assert.match($('claude-shell-status').textContent,/설정 폴더/);
          assert.equal($('claude-config-current').textContent,saved.configDir);
          folder.value='"D:/변경한 설정"';folder.oninput();await check.onclick();
          assert.equal(calls.at(-1)[1].configDir,'D:/변경한 설정');assert.equal(calls.at(-1)[1].checkOnly,true);
          assert.equal($('claude-config-current').textContent,saved.configDir);assert.equal(apply.disabled,false);
          await apply.onclick();assert.equal(apply.disabled,true);assert.equal($('claude-config-current').textContent,'D:/변경한 설정');
          $('claude-config-clear').onclick();assert.equal(folder.value,'');assert.equal(exe.value,picked.paths[0]);
          await apply.onclick();assert.deepEqual(calls.at(-1)[1],{choice:'default',configDir:'',executable:picked.paths[0],checkOnly:false});
          assert.equal($('claude-config-current').textContent,'D:/자동');assert.equal($('claude-config-clear').hidden,true);
        })()''')

    def test_classification_covers_known_messages_and_unknown_errors(self):
        self.run_case(r'''(()=>{const h=globalThis.WorkspaceErrorHelp;
          const cases=[['Claude Code를 찾지 못했습니다.',null,'not_found'],['이 Claude Code 버전에는 필요한 대화 연결 기능이 없습니다.',null,'unsupported'],['기존 Claude Code의 인증을 확인하지 못했습니다.',null,'auth'],['CLI 준비 응답이 60초 동안 없습니다.',null,'timeout'],['CLI 연결이 종료되었습니다.',null,'closed'],['모델 변경이 거절되었습니다.',null,'model_rejected'],['모델 변경 응답을 확인하지 못해 업무 연결을 종료했습니다.',null,'model_timeout'],['API Error 403: team not allowed to access model: secret', 'task_failed','model_permission'],['Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요. (진단: ede_diagnostic)',null,'diagnostic'],['다른 파일 읽기 오류', 'task_failed','generic']];
          for(const [message,code,expected] of cases)assert.equal(h.classify(message,code),expected,message);
          assert.equal(h.classify('opaque', 'cli_authentication'),'auth');assert.equal(h.classify('opaque','cli_turn_incomplete'),'diagnostic');
          assert.equal(h.classify('opaque','model_not_found'),'model_not_found');
          assert.equal(h.classify('Failed to authenticate: API Error 403 team not allowed to access model', 'cli_authentication'),'model_permission');
          assert.equal(h.classify('API Error 403: token does not have access to the model','task_failed'),'model_permission');
          assert.equal(h.classify('API Error 403: invalid authentication credentials','cli_authentication'),'auth');
        })()''')

    def test_model_error_opens_picker_without_resending_or_reconnecting(self):
        self.run_case(r'''(()=>{active={id:'work-a',state:'error'};appClosed=false;let opened=[];
          api=()=>{throw Error('Recovery action must not send API requests itself');};
          globalThis.WorkspaceInlineControls={open:kind=>opened.push(kind)};
          for(const code of ['model_permission','model_not_found','model_rejected']){
            error('현재 모델을 사용할 수 없어요.',null,code);
            assert.equal($('recovery-model').hidden,false);$('recovery-model').onclick();
          }
          assert.deepEqual(opened,['model','model','model']);
          for(const [text,kind,code] of [['인증 오류',null,'cli_authentication'],['중지 오류','stop','model_permission'],['',null,null]]){
            error(text,kind,code);assert.equal($('recovery-model').hidden,true);$('recovery-model').onclick();
          }
          active=null;error('모델 오류',null,'model_permission');assert.equal($('recovery-model').hidden,true);
          active={id:'a'};appClosed=true;error('모델 오류',null,'model_permission');assert.equal($('recovery-model').hidden,true);
          assert.equal(opened.length,3);
        })()''')

    def test_model_settings_file_opens_once_for_the_current_model_error_only(self):
        self.run_case(r'''(async()=>{
          const button=$('recovery-settings-file'),requests=[],notices=[];
          let resolveOpen;
          api=(path,body)=>{requests.push({path,body});return new Promise(resolve=>resolveOpen=resolve);};
          toast=text=>notices.push(text);
          active={id:'work-a',kind:'work'};boot={demo:false};appClosed=false;
          for(const kind of ['work','chat']){
            active={id:kind+'-a',kind};error('모델 오류',null,'model_permission');
            assert.equal(button.hidden,false);
            const before=requests.length,opening=button.onclick();
            await button.onclick();assert.equal(requests.length,before+1);
            assert.deepEqual(requests.at(-1),{path:'/api/open-claude-settings',body:{id:active.id}});
            resolveOpen({ok:true});await opening;assert.equal(button.disabled,false);
            assert.match(notices.at(-1),/저장.*재시작/);
          }
          for(const [message,kind,code] of [['만료',null,'cli_authentication'],['중지','stop','model_permission'],['',null,null]]){
            error(message,kind,code);assert.equal(button.hidden,true);await button.onclick();
          }
          boot.demo=true;error('모델 오류',null,'model_not_found');
          assert.equal(button.hidden,true);await button.onclick();assert.equal(requests.length,2);
        })()''')

    def test_settings_open_failure_leaves_picker_and_stale_response_is_quiet(self):
        self.run_case(r'''(async()=>{
          active={id:'work-a'};appClosed=false;boot={demo:false};
          const button=$('recovery-settings-file'),notices=[];
          toast=text=>notices.push(text);let rejectOpen;
          api=()=>new Promise((_,reject)=>rejectOpen=reject);
          error('모델 오류',null,'model_not_found');
          const first=button.onclick();rejectOpen(Error('현재 사용자 설정 파일을 찾을 수 없어요.'));await first;
          assert.equal(button.disabled,false);assert.match(notices[0],/파일을 찾을 수 없어요/);
          assert.equal($('recovery-model').hidden,false);assert.equal($('error-message').textContent,'모델 오류');
          const pending=button.onclick();selectionGeneration++;active={id:'other'};error('새 오류',null,'model_rejected');
          rejectOpen(Error('이전 업무 오류'));await pending;assert.equal(notices.length,1);
          appClosed=true;error('모델 오류',null,'model_permission');assert.equal(button.hidden,true);
        })()''')

    def test_send_failure_retains_model_diagnostic_and_unsent_draft(self):
        self.run_case(r'''(async()=>{
          active={id:'work-a',state:'idle',trusted:true,messages:[]};sessions=[{...active}];
          $('prompt').value='아직 보내지 못한 질문';attachments=['keep.csv'];let requests=0;
          api=async()=>{requests++;const failure=Error('현재 선택 모델을 사용할 수 없어요.');failure.code='model_permission';throw failure;};
          await submit();assert.equal(requests,1);assert.equal($('recovery-model').hidden,false);
          assert.equal($('prompt').value,'아직 보내지 못한 질문');assert.deepEqual(attachments,['keep.csv']);
          assert.equal(active.messages.length,0);$('error-help-open').onclick();
          assert.match($('error-help-title').textContent,/모델 사용 권한/);
        })()''')

    def test_error_banner_button_opens_static_help_and_preserves_reconnect(self):
        self.run_case(r'''(()=>{active={id:'work-a'};appClosed=false;const secret='secret-never-repeat';
          error('API error 403 team not allowed to access model '+secret,null,'task_failed');
          assert.equal($('error-message').textContent.includes(secret),true);assert.equal($('recovery-actions').hidden,false);
          $('error-help-open').onclick();assert.equal($('error-help-dialog').open,true);assert.match($('error-help-title').textContent,/모델 사용 권한/);
          const all=[$('error-help-title'),$('error-help-summary'),$('error-help-note'),...$('error-help-causes').children,...$('error-help-actions').children].map(n=>n.textContent).join(' ');
          assert.doesNotMatch(all,/secret-never-repeat/);assert.equal($('reconnect').textContent,'연결 다시 확인');
          $('error-help-done').onclick();assert.equal($('error-help-dialog').open,false);
        })()''')

    def test_changed_error_or_work_closes_old_guidance(self):
        self.run_case(r'''(()=>{active={id:'a'};appClosed=false;error('기존 Claude Code의 인증을 확인하지 못했습니다.');$('error-help-open').onclick();
          assert.equal($('error-help-dialog').open,true);active={id:'b'};error('CLI 연결이 종료되었습니다.');assert.equal($('error-help-dialog').open,false);
          $('error-help-open').onclick();assert.match($('error-help-title').textContent,/연결이 종료/);error('');assert.equal($('error-help-dialog').open,false);assert.equal($('recovery-actions').hidden,true);
        })()''')

    def test_guide_has_concrete_recovery_steps_and_preserves_result_safety(self):
        self.run_case(r'''(()=>{active={id:'work-a'};appClosed=false;
          const actionText=()=>[...$('error-help-actions').children].map(n=>n.textContent);
          error('이 Claude Code 버전에는 필요한 대화 연결 기능이 없습니다.');$('error-help-open').onclick();
          let steps=actionText();assert.ok(steps.some(s=>s.includes('PowerShell')&&s.includes('claude --version')));
          assert.ok(steps.some(s=>s.includes('claude를 실행')&&s.includes('최신 버전으로 업데이트')));
          assert.ok(steps.some(s=>s.includes('담당자')&&s.includes('제한')));assert.ok(steps.some(s=>s.includes('완전히 종료')));
          error('기존 Claude Code의 인증을 확인하지 못했습니다.',null,'cli_authentication');$('error-help-open').onclick();
          steps=actionText();assert.ok(steps.some(s=>s.includes('PowerShell')&&s.includes('claude를 실행')&&s.includes('질문')));
          assert.ok(steps.every(s=>!s.includes('cmd /c')));
          assert.ok(steps.some(s=>s.includes('터미널과 경로 비교')&&s.includes('설정 폴더')&&s.includes('실행 파일')));
          assert.ok(steps.some(s=>s.includes('터미널에서도 인증 오류')&&s.includes('로그인')));
          assert.match($('error-help-note').textContent,/파일의 위치/);
          error('API error 403 team not allowed to access model',null,'task_failed');$('error-help-open').onclick();
          steps=actionText();assert.ok(steps.some(s=>s.includes('입력창')&&s.includes('모델 버튼')));
          assert.ok(steps.findIndex(s=>s.includes('파일 변경')) < steps.findIndex(s=>s.includes('필요한 내용만 요청')));
          assert.match($('error-help-note').textContent,/KEY|토큰/);
          error('모델 변경 응답을 확인하지 못해 업무 연결을 종료했습니다.');$('error-help-open').onclick();
          steps=actionText();assert.ok(steps[0].includes('계속할 준비가 되면'));assert.ok(steps[0].includes('자동으로 재실행하지 않습니다'));
          error('Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요. (진단: ede_diagnostic)');$('error-help-open').onclick();
          steps=actionText();assert.ok(steps[1].includes('이번 결과'));assert.ok(steps[2].includes('확인한 결과'));
          assert.match($('error-help-note').textContent,/자동으로 다시 실행하지 않습니다/);
        })()''')

    def test_model_help_explains_the_available_settings_editor_and_restart(self):
        self.run_case(r'''(()=>{
          const steps=()=>Array.from($('error-help-actions').children, li=>li.textContent);
          const openHelp=code=>{
            error('테스트 오류',null,code);
            $('error-help-open').onclick();
            assert.equal($('error-help-dialog').open,true);
          };
          for(const kind of ['chat','work']){
            active={id:kind,kind};boot={demo:false};
            for(const code of ['model_permission','model_not_found','model_rejected']){
              openHelp(code);
              const editorSteps=steps().filter(text=>text.includes('Claude 설정 파일 열기'));
              assert.equal(editorSteps.length,1);
              assert.match(editorSteps[0],/이 창을 닫고.*Claude 설정 파일 열기.*메모장.*settings\.json.*저장.*상단.*Claude Code 재시작/);
            }
          }
          for(const code of ['cli_authentication','model_timeout']){
            openHelp(code);
            assert.ok(steps().every(text=>!text.includes('Claude 설정 파일 열기')));
          }
          boot.demo=true;openHelp('model_permission');
          assert.ok(steps().every(text=>!text.includes('Claude 설정 파일 열기')));
        })()''')

    def test_stop_recovery_action_is_unchanged(self):
        self.run_case(r'''(()=>{active={id:'a',stopState:'failed'};appClosed=false;error('중지 상태를 확인하지 못했어요.','stop');
          assert.equal($('connection-settings').hidden,true);assert.notEqual($('reconnect').textContent,'연결 다시 확인');
          $('error-help-open').onclick();assert.match($('error-help-title').textContent,/오류 내용/);$('error-help-close').onclick();assert.equal($('error-help-dialog').open,false);
        })()''')

    def test_served_script_is_in_page_and_bundle(self):
        page=(ROOT/'local_app/web/index.html').read_text(encoding='utf-8')
        self.assertLess(page.index('/error-help.js'),page.index('/app.js'))
        self.assertIn('id="error-help-dialog"',page)
        contact='추가 문의 사항은 문봉건TL(2069026)한테 연락 바랍니다.'
        self.assertEqual(page.count(contact),1)
        self.assertLess(page.index('id="error-help-note"'),page.index(contact))
        self.assertLess(page.index(contact),page.index('id="error-help-done"'))
        banner=page[page.index('id="error-banner"'):page.index('</div>',page.index('id="error-banner"'))]
        self.assertIn('id="error-message"',banner)
        self.assertIn('role="alert"',banner)
        self.assertIn('id="error-help-open"',banner)
        self.assertNotIn('id="error-help-open"',page[page.index('id="recovery-actions"'):page.index('</div>',page.index('id="recovery-actions"'))])
        self.assertIn('"/error-help.js": ("error-help.js"', (ROOT/'local_app/server.py').read_text(encoding='utf-8'))
        self.assertIn('local_app\\web\\error-help.js',(ROOT/'deploy/New-WorkspaceBundle.ps1').read_text(encoding='utf-8'))
        self.assertIn('local_app/web/error-help.js',(ROOT/'scripts/test-lab/check-workspace-bundle.py').read_text(encoding='utf-8'))

    def test_local_server_serves_help_script(self):
        with tempfile.TemporaryDirectory() as directory:
            app=LocalApp(Path(directory),demo=True); server=Server(app)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with urlopen(server.origin+'/error-help.js',timeout=5) as response:
                    self.assertEqual(response.status,200)
                    self.assertIn('text/javascript',response.headers['Content-Type'])
                    self.assertIn(b'WorkspaceErrorHelp',response.read())
            finally:
                server.shutdown();server.server_close();app.close();thread.join(timeout=5)

if __name__=='__main__': unittest.main()
