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

    def test_classification_covers_known_messages_and_unknown_errors(self):
        self.run_case(r'''(()=>{const h=globalThis.WorkspaceErrorHelp;
          const cases=[['Claude Code를 찾지 못했습니다.',null,'not_found'],['이 Claude Code 버전에는 필요한 대화 연결 기능이 없습니다.',null,'unsupported'],['기존 Claude Code의 인증을 확인하지 못했습니다.',null,'auth'],['CLI 준비 응답이 60초 동안 없습니다.',null,'timeout'],['CLI 연결이 종료되었습니다.',null,'closed'],['모델 변경이 거절되었습니다.',null,'model_rejected'],['모델 변경 응답을 확인하지 못해 업무 연결을 종료했습니다.',null,'model_timeout'],['API Error 403: team not allowed to access model: secret', 'task_failed','model_permission'],['Claude가 도구 처리 뒤 최종 답변을 완료하지 못했어요. (진단: ede_diagnostic)',null,'diagnostic'],['다른 파일 읽기 오류', 'task_failed','generic']];
          for(const [message,code,expected] of cases)assert.equal(h.classify(message,code),expected,message);
          assert.equal(h.classify('opaque', 'cli_authentication'),'auth');assert.equal(h.classify('opaque','cli_turn_incomplete'),'diagnostic');
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
