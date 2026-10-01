"""Explicit file opening and cross-task preview/attachment isolation."""
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE

ROOT=Path(__file__).resolve().parents[1]
SETUP=r"""
active={id:'A',title:'업무 A',workspace:'C:/fixture/A',state:'done',trusted:true};sessions=[{...active}];
"""


@unittest.skipUnless(NODE,"Node.js is required for file action UI checks")
class WorkspaceFileActionsFrontendTests(unittest.TestCase):
    def run_case(self,script):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),SETUP+script],input=HARNESS,
                              text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_html_preview_remains_sandboxed_and_external_actions_require_click(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return body?{ok:true}:{kind:'html',name:'보고서.html',html:'<p>보고서</p>',message:'정적 미리보기'};};
          await preview('C:/fixture/A/보고서.html');assert.equal(calls.length,1);
          assert.equal($('preview-content').querySelector('iframe').attributes.sandbox,'');
          assert.equal($('open-file').hidden,false);assert.equal($('external-html-note').hidden,false);assert.equal($('open-text-file').hidden,false);
          for(const id of ['open-file','reveal-file','open-text-file'])await $(id).onclick();
          assert.equal(calls.length,4);assert.equal(calls[1].body.action,'open');assert.equal(calls[2].body.action,'reveal');assert.equal(calls[3].body.action,'text');
          assert.ok(calls.slice(1).every(row=>row.path==='/api/open'&&row.body.id==='A'&&row.body.path==='C:/fixture/A/보고서.html'));
        })()""")

    def test_office_files_have_default_app_and_reveal_without_text_action(self):
        self.run_case(r"""(async()=>{
          api=async()=>({kind:'external',name:'업무.xlsx',message:'기본 앱으로 확인'});await preview('C:/fixture/A/업무.xlsx');
          assert.equal($('open-file').hidden,false);assert.equal($('reveal-file').hidden,false);
          assert.equal($('open-text-file').hidden,true);assert.equal($('external-html-note').hidden,true);
          boot.demo=true;await preview('C:/fixture/A/업무.xlsx');assert.equal($('open-file').hidden,true);assert.equal($('reveal-file').hidden,true);
        })()""")

    def test_html_mode_switch_is_local_literal_and_releases_frame_on_close(self):
        self.run_case(r"""(async()=>{
          const source='<script>parent.fetch("/api/quit")</script><h1>원본</h1>';
          let calls=0;api=async()=>{calls++;return {kind:'html',name:'일반.html',html:'<h1>원본</h1>',text:source,truncated:true};};
          await preview('C:/fixture/A/일반.html');assert.equal($('preview-modes').hidden,false);
          assert.equal($('preview-screen').attributes['aria-pressed'],'true');
          const first=$('preview-content').querySelector('iframe');assert.equal(first.attributes.sandbox,'');
          $('preview-code').onclick();assert.equal($('preview-content').querySelector('iframe'),null);
          assert.equal($('preview-content').querySelector('pre').textContent,source);
          assert.equal($('preview-content').querySelector('script'),null);
          assert.ok($('preview-content').children.some(n=>n.textContent.includes('100,000')));
          assert.equal($('preview-code').attributes['aria-pressed'],'true');assert.equal($('open-file').hidden,false);
          $('preview-screen').onclick();assert.notEqual($('preview-content').querySelector('iframe'),first);
          assert.equal($('preview-content').querySelector('iframe').attributes.referrerpolicy,'no-referrer');
          $('preview-dialog').close();assert.equal(previewData,null);assert.equal(previewContext,null);
          assert.equal($('preview-content').children.length,0);assert.equal($('preview-modes').hidden,true);
          assert.equal(calls,1);
        })()""")

    def test_html_switch_cannot_reveal_stale_task_and_next_file_resets_mode(self):
        self.run_case(r"""(async()=>{
          api=async()=>({kind:'html',name:'a.html',html:'<p>A</p>',text:'<p>A</p>'});
          await preview('C:/fixture/A/a.html');$('preview-code').onclick();
          active={...active,id:'B'};$('preview-screen').onclick();
          assert.equal($('preview-content').querySelector('iframe'),null);
          closePreview();api=async()=>({kind:'text',name:'b.txt',text:'B 내용'});
          await preview('C:/fixture/B/b.txt');assert.equal($('preview-modes').hidden,true);
          $('preview-code').onclick();assert.equal($('preview-content').querySelector('pre'),null);
          api=async()=>({kind:'html',name:'b.html',html:'<p>B</p>',text:'<p>B</p>'});
          await preview('C:/fixture/B/b.html');assert.ok($('preview-content').querySelector('iframe'));
          assert.equal($('preview-screen').attributes['aria-pressed'],'true');
          // A previous dialog's queued close event must not tear down this new preview.
          $('preview-dialog').onclose();assert.equal($('preview-dialog').open,true);
          assert.equal(previewData.name,'b.html');assert.ok($('preview-content').querySelector('iframe'));
        })()""")

    def test_late_preview_and_close_cannot_replace_current_task_context(self):
        self.run_case(r"""(async()=>{
          const pending=[];api=(path,body)=>new Promise(resolve=>pending.push({path,body,resolve}));
          const old=preview('C:/fixture/A/a.txt');active={...active,id:'B',workspace:'C:/fixture/B'};
          const current=preview('C:/fixture/B/b.txt');pending[1].resolve({kind:'text',name:'B 파일',text:'B 내용'});await current;
          pending[0].resolve({kind:'text',name:'A 파일',text:'A 내용'});await old;
          assert.equal($('preview-title').textContent,'B 파일');assert.equal(previewContext.sessionId,'B');
          const closing=preview('C:/fixture/B/late.txt');closePreview();pending[2].resolve({kind:'text',name:'늦은 파일',text:'늦은 내용'});await closing;
          assert.equal(previewContext,null);assert.equal($('preview-dialog').open,false);
        })()""")

    def test_stale_file_action_context_never_opens_with_new_task_id(self):
        self.run_case(r"""(async()=>{
          api=async()=>({kind:'text',name:'a.txt',text:'A 내용'});await preview('C:/fixture/A/a.txt');const captured=previewContext;
          active={...active,id:'B',workspace:'C:/fixture/B'};let calls=0;api=async()=>{calls++;};
          await $('open-file').onclick();await openFileAction('reveal',captured);assert.equal(calls,0);
          assert.match($('toast').textContent,/파일을 선택한 업무/);
        })()""")

    def test_result_actions_are_explicit_and_keep_captured_task_path(self):
        self.run_case(r"""(async()=>{
          const calls=[];api=async(path,body)=>{calls.push({path,body});return body?{ok:true}:{lastRunId:'run',artifacts:[{runId:'run',path:'C:/fixture/A/결과.html',name:'결과.html',change:'created'}]};};
          await refreshResults();assert.equal(calls.length,1);const actions=$('results-list').children[0].querySelector('.result-actions');
          await actions.children[1].onclick();assert.equal(calls[1].path,'/api/open');assert.equal(calls[1].body.path,'C:/fixture/A/결과.html');
          active={...active,id:'B'};await actions.children[2].onclick();assert.equal(calls.length,2);
        })()""")

    def test_late_attachment_picker_and_manual_path_cannot_leak_into_other_task(self):
        self.run_case(r"""(async()=>{
          let reply;api=()=>new Promise(resolve=>reply=resolve);const pending=$('attach').onclick();
          active={...active,id:'B',workspace:'C:/fixture/B'};selectionGeneration++;attachments=['C:/fixture/B/original.csv'];
          reply({paths:['C:/fixture/A/late.csv']});await pending;
          assert.equal(attachments.length,1);assert.equal(attachments[0],'C:/fixture/B/original.csv');assert.equal($('attach').disabled,false);
          $('attach-path').onclick();$('path-input').value='C:/fixture/B/manual.csv';
          active={...active,id:'C'};selectionGeneration++;$('path-form').onsubmit({submitter:{value:'ok'}});
          assert.equal(attachments.length,1);
        })()""")


if __name__=='__main__':unittest.main()
