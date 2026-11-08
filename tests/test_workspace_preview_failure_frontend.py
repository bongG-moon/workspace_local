"""Unavailable previews keep friendly, explicitly scoped file actions."""
import unittest
from tests import test_workspace_file_actions_frontend as file_actions


@unittest.skipUnless(file_actions.NODE,"Node.js is required for preview failure UI checks")
class WorkspacePreviewFailureFrontendTests(unittest.TestCase):
    run_case = file_actions.WorkspaceFileActionsFrontendTests.run_case

    def test_unreadable_file_keeps_friendly_preview_and_explicit_scoped_actions(self):
        self.run_case(r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{calls.push({path,body});if(!body)throw Error('[Errno 13] Permission denied: secret path');return {ok:true};};
          await preview('C:/fixture/A/report.csv');
          assert.equal($('preview-dialog').open,true);
          assert.equal($('preview-title').textContent,'report.csv');
          assert.equal($('preview-content').querySelector('strong').textContent,'미리보기를 열 수 없어요.');
          assert.doesNotMatch($('preview-content').querySelector('p').textContent,/Errno|secret path/);
          assert.equal($('open-file').hidden,false);assert.equal($('open-file').disabled,false);
          assert.equal($('reveal-file').hidden,false);assert.equal($('open-text-file').hidden,true);
          assert.equal(calls.length,1);
          await $('open-file').onclick();await $('reveal-file').onclick();
          assert.equal(calls.length,3);
          for(const call of calls.slice(1))assert.deepEqual(call.body,{id:'A',path:'C:/fixture/A/report.csv',action:call.body.action});
          assert.deepEqual(calls.slice(1).map(call=>call.body.action),['open','reveal']);
          active={...active,id:'B'};await $('open-file').onclick();assert.equal(calls.length,3);
        })()""")

    def test_source_failure_cannot_execute_default_association_and_next_document_resets(self):
        self.run_case(r"""(async()=>{
          api=async()=>{throw Error('denied');};await preview('C:/fixture/A/tool.py');
          assert.equal($('open-file').hidden,false);assert.equal($('open-file').disabled,true);
          assert.equal($('reveal-file').hidden,false);
          assert.match($('preview-content').querySelector('p').textContent,/사용할 앱/);
          api=async()=>({kind:'text',name:'ok.txt',text:'current content'});await preview('C:/fixture/A/ok.txt');
          assert.equal($('open-file').disabled,false);assert.equal($('open-file').title,'');
          assert.equal($('preview-body').dataset.previewKind,'text');
        })()""")

    def test_late_failure_and_image_decode_error_never_replace_a_newer_preview(self):
        self.run_case(r"""(async()=>{
          let reject;
          api=()=>new Promise((resolve,rejectOld)=>reject=rejectOld);
          const old=preview('C:/fixture/A/old.csv');
          api=async()=>({kind:'image',name:'bad.png',data:'data:image/png;base64,AA=='});
          await preview('C:/fixture/A/bad.png');const image=$('preview-content').querySelector('img');
          reject(Error('old failure'));await old;
          assert.equal(previewData.name,'bad.png');
          image.onerror();assert.equal(previewData.kind,'unavailable');
          api=async()=>({kind:'text',name:'ok.txt',text:'new content'});await preview('C:/fixture/A/ok.txt');
          image.onerror();assert.equal(previewData.name,'ok.txt');assert.equal(previewData.kind,'text');
        })()""")
