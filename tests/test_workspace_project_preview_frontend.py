"""Project previews opt in to one live document and revoke every stale lease."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT = Path(__file__).resolve().parents[1]
SETUP = r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'done',trusted:true};sessions=[{...active}];
const html={kind:'html',name:'index.html',html:'<h1>정적 화면</h1>',text:'<script src="main.js"></script>',webPreviewAvailable:true};
const projectUrl='/project-preview/'+'a'.repeat(32)+'/index.html';
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
"""


@unittest.skipUnless(NODE, "Node.js is required for project preview lifecycle checks")
class WorkspaceProjectPreviewFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + script], input=HARNESS,
            text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_web_is_lazy_script_isolated_and_reselecting_reuses_the_document(self):
        self.run_case(r"""(async()=>{
          const calls=[];let reply;
          api=(path,body)=>{calls.push({path,body});return path==='/api/project-preview/open'?
            new Promise(resolve=>reply=resolve):Promise.resolve({...html});};
          await preview('C:/fixture/A/index.html');
          const staticFrame=$('preview-content').querySelector('iframe');
          assert.equal(staticFrame.attributes.sandbox,'');assert.equal(calls.length,1);
          $('preview-web').onclick();for(let i=0;i<5;i++)$('preview-web').onclick();
          assert.equal(calls.filter(c=>c.path==='/api/project-preview/open').length,1);
          assert.equal($('preview-content').querySelector('iframe'),null);
          reply({id:'lease-1',url:projectUrl,message:'프로젝트 화면'});await flush();
          const frame=$('preview-content').querySelector('iframe');assert.ok(frame);
          assert.equal(frame.attributes.sandbox,'allow-scripts');assert.equal(frame.attributes.referrerpolicy,'no-referrer');
          assert.equal(frame.src,projectUrl);assert.equal(frame.srcdoc,undefined);
          assert.equal(frame.attributes.allow,"camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'");
          frame.scrollTop=88;$('preview-content').scrollTop=22;
          for(let i=0;i<5;i++)$('preview-web').onclick();
          assert.equal($('preview-content').querySelector('iframe'),frame);assert.equal(frame.scrollTop,88);
          assert.equal($('preview-content').scrollTop,22);assert.equal(calls.length,2);
          assert.equal($('preview-screen').attributes['aria-pressed'],'false');
          assert.equal($('preview-web').attributes['aria-pressed'],'true');
        })()""")

    def test_code_and_close_remove_live_frame_and_release_lease_once(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return path==='/api/project-preview/open'?
            {id:'live-lease',url:projectUrl}:body?{ok:true}:{...html};};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();await flush();
          const frame=$('preview-content').querySelector('iframe');$('preview-code').onclick();await flush();
          assert.equal(frame.parent,null);assert.equal(frame.src,'about:blank');
          assert.equal($('preview-content').querySelector('iframe'),null);
          assert.equal($('preview-content').querySelector('pre').textContent,html.text);
          closePreview();closePreview();await flush();
          const close=calls.filter(c=>c.path==='/api/project-preview/close');assert.equal(close.length,1);
          assert.equal(close[0].body.id,'A');assert.equal(close[0].body.previewId,'live-lease');
          assert.equal(projectPreview,null);
        })()""")

    def test_late_open_after_code_selection_is_revoked_without_showing_a_frame(self):
        self.run_case(r"""(async()=>{
          let reply;const calls=[];api=(path,body)=>{calls.push({path,body});return path==='/api/project-preview/open'?
            new Promise(resolve=>reply=resolve):Promise.resolve(body?{ok:true}:{...html});};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();$('preview-code').onclick();
          reply({id:'late-lease',url:projectUrl});await flush();
          assert.equal($('preview-content').querySelector('iframe'),null);
          assert.equal($('preview-content').querySelector('pre').textContent,html.text);
          assert.equal(calls.filter(c=>c.path==='/api/project-preview/close').length,1);
          assert.equal(calls.at(-1).body.previewId,'late-lease');assert.equal(projectPreview,null);
        })()""")

    def test_late_reply_cannot_replace_new_file_or_use_the_new_task_identity(self):
        self.run_case(r"""(async()=>{
          const calls=[];let reply;api=(path,body)=>{calls.push({path,body});
            if(path==='/api/project-preview/open')return new Promise(resolve=>reply=resolve);
            return Promise.resolve(body?{ok:true}:active.id==='A'?{...html}:{kind:'text',name:'B.txt',text:'업무 B 내용'});};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();
          active={...active,id:'B',workspace:'C:/fixture/B'};await preview('C:/fixture/B/B.txt');
          reply({id:'old-A',url:projectUrl});await flush();
          assert.equal(previewContext.sessionId,'B');assert.equal(previewData.name,'B.txt');
          assert.equal($('preview-content').querySelector('iframe'),null);
          const close=calls.find(c=>c.path==='/api/project-preview/close');assert.equal(close.body.id,'A');
          assert.equal(close.body.previewId,'old-A');assert.equal($('preview-web').hidden,true);
        })()""")

    def test_rejected_preview_leaves_static_and_code_usable_without_auto_retry(self):
        self.run_case(r"""(async()=>{
          let opens=0;api=async(path,body)=>{if(path==='/api/project-preview/open'){opens++;throw Error('연결이 끝났어요.');}
            return body?{ok:true}:{...html};};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();await flush();
          assert.equal(opens,1);assert.equal(projectPreview,null);assert.equal($('preview-content').querySelector('iframe'),null);
          const notice=$('preview-content').querySelector('.preview-static-notice');assert.ok(notice);
          assert.ok(notice.children.some(n=>n.textContent==='웹 화면을 열 수 없어요.'));
          notice.children.find(n=>n.textContent==='화면으로 보기').onclick();
          assert.equal($('preview-content').querySelector('iframe').attributes.sandbox,'');assert.equal(opens,1);
          $('preview-code').onclick();assert.equal($('preview-content').querySelector('pre').textContent,html.text);
        })()""")

    def test_unexpected_url_never_navigates_a_script_enabled_frame(self):
        self.run_case(r"""(async()=>{
          const closed=[];api=async(path,body)=>{if(path==='/api/project-preview/close'){closed.push(body.previewId);return {ok:true};}
            return path==='/api/project-preview/open'?{id:'bad-url',url:'/api/quit'}:{...html};};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();await flush();
          assert.equal($('preview-content').querySelector('iframe'),null);assert.deepEqual(closed,['bad-url']);
          assert.ok($('preview-content').querySelector('.preview-static-notice'));
        })()""")

    def test_unavailable_and_demo_views_never_open_live_web_content(self):
        self.run_case(r"""(async()=>{
          let reads=0;api=async()=>{reads++;return {...html,scriptOnly:true,webPreviewAvailable:false,webPreviewReason:'업무 폴더의 파일을 선택해 주세요.'};};
          await preview('C:/fixture/A/index.html');assert.equal($('preview-web').disabled,true);
          assert.match($('preview-web').title,/업무 폴더/);$('preview-web').onclick();assert.equal(reads,1);
          const notice=$('preview-content').querySelector('.preview-static-notice');
          assert.ok(notice.children.some(n=>/업무 폴더/.test(n.textContent)));
          assert.ok(!notice.children.some(n=>n.textContent==='웹으로 보기'));
          boot.demo=true;api=async()=>{reads++;return {...html};};await preview('C:/fixture/A/demo.html');
          assert.equal($('preview-web').hidden,true);$('preview-web').onclick();assert.equal(reads,2);
          assert.equal($('preview-content').querySelector('iframe').attributes.sandbox,'');
        })()""")

    def test_only_bounded_web_preferences_survive_closing_and_explicit_screen_stops_restore(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return path==='/api/project-preview/open'?
            {id:'lease-'+calls.length,url:projectUrl}:body?{ok:true}:{...html};};
          await preview('C:/fixture/A/index.html');$('preview-web').onclick();await flush();closePreview();await flush();
          await preview('C:/fixture/A/index.html');await flush();
          assert.equal(calls.filter(c=>c.path==='/api/project-preview/open').length,2);
          $('preview-screen').onclick();await flush();closePreview();await preview('C:/fixture/A/index.html');await flush();
          assert.equal(calls.filter(c=>c.path==='/api/project-preview/open').length,2);
          assert.equal($('preview-content').querySelector('iframe').attributes.sandbox,'');
          for(let i=0;i<40;i++){previewContext.path='C:/fixture/A/'+i+'.html';rememberHtmlProjectChoice('web');}
          assert.equal(htmlProjectChoices.size,24);assert.ok([...htmlProjectChoices.values()].every(v=>v===true));
          closePreview();assert.equal(projectPreview,null);
        })()""")


if __name__ == '__main__':
    unittest.main()
