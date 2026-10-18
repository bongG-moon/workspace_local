"""File refresh uses authoritative snapshots without stale task/request replies."""
from html.parser import HTMLParser
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


SETUP = r"""
// The shared DOM double exposes setAttribute; header rendering also reads it.
Object.getPrototypeOf(document.createElement('div')).getAttribute=function(name){return this.attributes[name]??null;};
active={id:'A',title:'A',workspace:'C:/task/A',state:'done',messages:[]};
sessions=[{...active}];
const snapshot=name=>({files:name?[{name,path:'C:/task/A/'+name}]:[],lastRunId:'run',
  artifacts:name?[{name,path:'C:/task/A/'+name,runId:'run',change:'created'}]:[]});
"""


class RefreshMarkupTests(unittest.TestCase):
    def test_one_refresh_button_is_outside_both_tab_panels(self):
        class Buttons(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.refresh = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get('id') == 'refresh-files':
                    self.refresh.append((list(self.stack), attrs))
                if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr', 'use'}:
                    self.stack.append((tag, attrs))

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index:]
                        return

        parser = Buttons()
        parser.feed((ROOT / 'local_app/web/index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(parser.refresh), 1)
        ancestors, attrs = parser.refresh[0]
        self.assertTrue(any('inspector-head' in parent.get('class', '').split() for _, parent in ancestors))
        self.assertFalse(any(parent.get('role') == 'tabpanel' for _, parent in ancestors))
        self.assertEqual(attrs['aria-label'], '자료와 결과 새로고침')


@unittest.skipUnless(NODE, 'Node.js is required for file refresh UI checks')
class FileRefreshFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + script],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_folder_navigation_is_lazy_refreshes_current_folder_and_adds_one_reference(self):
        self.run_case(r"""(async()=>{
          const calls=[];saveDraft=()=>{};
          api=async path=>{calls.push(path);return path.includes('directory=')
            ?{directory:'docs',files:[{name:'setup.exe',path:'C:/task/A/docs/setup.exe',kind:'file'}]}
            :{directory:'',files:[{name:'docs',relativePath:'docs',path:'C:/task/A/docs',kind:'directory'}]};};
          await refreshFiles();assert.equal(calls.length,1);
          const row=$('files').children[0];
          row.children[1].onclick();assert.deepEqual(attachments,['C:/task/A/docs']);
          row.children[1].onclick();assert.equal(attachments.length,1);
          await row.children[0].onclick();
          assert.equal(calls[1],'/api/files?id=A&directory=docs');
          assert.equal($('files').children[1].title,'C:/task/A/docs/setup.exe');
          await refreshFiles();assert.equal(calls[2],calls[1]);
          await $('files').children[0].children[0].onclick();
          assert.equal(calls[3],'/api/files?id=A');
          active={...active,id:'B'};selectionGeneration++;
          row.children[1].onclick();assert.equal(attachments.length,1);
          await refreshFiles();assert.equal(calls[4],'/api/files?id=B');
        })()""")

    def test_executable_preview_offers_reveal_without_launch(self):
        self.run_case(r"""(async()=>{
          boot.demo=false;const calls=[];
          api=async path=>{calls.push(path);return {name:'setup.exe',kind:'external',message:'미리보기 없음'};};
          await preview('C:/task/A/setup.exe');
          assert.equal($('open-file').hidden,true);assert.equal($('reveal-file').hidden,false);
          assert.equal($('open-text-file').hidden,true);
          assert.equal(calls.length,1);assert.ok(calls[0].startsWith('/api/preview?'));
        })()""")

    def test_nested_parent_root_and_deleted_folder_recovery(self):
        self.run_case(r"""(async()=>{
          const calls=[],warnings=[];let missing=false;toast=value=>warnings.push(value);
          api=async path=>{calls.push(path);const directory=decodeURIComponent(path.split('&directory=')[1]||'');
            if(missing&&directory)throw Error('자료 폴더가 없어졌어요');
            return {directory,files:[]};};
          await refreshFiles('자료/2026/보고서');
          assert.equal($('files').children[0].children[0].textContent,'← 상위 폴더');
          $('files').scrollTop=100;
          await $('files').children[0].children[0].onclick();
          assert.equal(materialLocation.directory,'자료/2026');assert.equal($('files').scrollTop,0);
          await $('files').children[0].children[1].onclick();
          assert.equal(materialLocation.directory,'');assert.equal($('files').querySelector('.material-location'),null);
          await refreshFiles('자료/2026');missing=true;await refreshFiles();
          assert.equal(materialLocation.directory,'');assert.equal(calls.at(-1),'/api/files?id=A');
          assert.equal(warnings.length,1);assert.equal(fileRefreshRequests.size,0);
          assert.equal($('refresh-files').disabled,false);
        })()""")

    def test_slow_child_response_cannot_replace_parent_navigation(self):
        self.run_case(r"""(async()=>{
          const pending=[];api=(path,body,signal)=>new Promise(resolve=>pending.push({path,signal,resolve}));
          const child=refreshFiles('docs/deep'),parent=refreshFiles('docs');
          assert.equal(pending[0].signal.aborted,true);
          pending[1].resolve({directory:'docs',files:[]});await parent;
          pending[0].resolve({directory:'docs/deep',files:[{name:'stale.exe',path:'C:/task/A/docs/deep/stale.exe'}]});await child;
          assert.equal(materialLocation.directory,'docs');assert.equal($('file-count').textContent,0);
          assert.equal($('files').querySelector('.file'),null);
        })()""")

    def test_shared_manual_refresh_removes_deleted_rows_and_preserves_tab(self):
        self.run_case(r"""(async()=>{
          let response=snapshot('existing.zip'),calls=[];
          api=async(path,body)=>{calls.push(path);assert.equal(body,undefined);return response;};
          await refreshInspector();setPanel('results');
          assert.equal($('files').children[0].title,'C:/task/A/existing.zip');
          assert.equal($('result-count').textContent,1);
          response=snapshot('');await $('refresh-files').onclick();
          assert.deepEqual(calls,['/api/files?id=A','/api/results?id=A','/api/files?id=A','/api/results?id=A']);
          assert.equal($('file-count').textContent,0);assert.equal($('files').querySelector('.file'),null);
          assert.equal($('result-count').textContent,0);assert.equal($('results-list').children.length,0);
          assert.equal($('empty-results').hidden,false);assert.equal($('results-panel').hidden,false);
          assert.equal($('refresh-files').disabled,false);
        })()""")

    def test_newer_same_task_snapshot_wins_even_if_aborted_reply_resolves(self):
        self.run_case(r"""(async()=>{
          const pending=[],galleries=[];let warnings=[];
          globalThis.WorkspaceExecutionView={results:(...args)=>galleries.push(args)};toast=text=>warnings.push(text);
          api=(path,body,signal)=>new Promise((resolve,reject)=>pending.push({path,signal,resolve,reject}));
          setPanel('sources');const old=refreshInspector(true),current=refreshInspector();
          assert.equal(pending[0].signal.aborted,true);assert.equal(pending[1].signal.aborted,true);
          assert.equal(fileRefreshRequests.size,2);
          pending[2].resolve(snapshot(''));pending[3].resolve(snapshot(''));await current;
          pending[0].resolve(snapshot('deleted.txt'));pending[1].resolve(snapshot('deleted.txt'));await old;
          assert.equal($('file-count').textContent,0);assert.equal($('result-count').textContent,0);
          assert.equal($('source-panel').hidden,false);assert.equal(galleries.length,1);
          assert.equal(warnings.length,0);assert.equal(fileRefreshRequests.size,0);
        })()""")

    def test_selection_generation_rejects_reply_after_a_b_a_round_trip(self):
        self.run_case(r"""(async()=>{
          const pending=[];api=(path,body,signal)=>new Promise((resolve,reject)=>pending.push({resolve,reject,signal}));
          const old=refreshInspector(true);active={...active,id:'B'};selectionGeneration++;
          active={...active,id:'A'};selectionGeneration++;setPanel('sources');
          $('file-count').textContent='current';$('result-count').textContent='current';
          pending[0].resolve(snapshot('stale.txt'));pending[1].resolve(snapshot('stale.txt'));await old;
          assert.equal($('file-count').textContent,'current');assert.equal($('result-count').textContent,'current');
          assert.equal($('source-panel').hidden,false);assert.equal($('refresh-files').disabled,false);
        })()""")

    def test_stale_failures_are_silent_and_do_not_finish_newer_busy_state(self):
        self.run_case(r"""(async()=>{
          const pending=[],warnings=[];toast=text=>warnings.push(text);
          api=(path,body,signal)=>new Promise((resolve,reject)=>pending.push({resolve,reject,signal}));
          const old=refreshInspector(),current=refreshInspector();
          pending[0].reject(Error('stale file failure'));pending[1].reject(Error('stale result failure'));await old;
          assert.equal(warnings.length,0);assert.equal($('refresh-files').disabled,true);
          assert.equal($('refresh-files').attributes['aria-busy'],'true');
          pending[2].resolve(snapshot(''));await Promise.resolve();
          assert.equal($('refresh-files').disabled,true);
          pending[3].reject(Error('current failure'));await current;
          assert.deepEqual(warnings,['current failure']);assert.equal($('refresh-files').disabled,false);
          assert.equal($('refresh-files').attributes['aria-busy'],'false');
        })()""")

    def test_home_cancels_requests_and_disables_refresh_without_new_work(self):
        self.run_case(r"""(async()=>{
          const pending=[];api=(path,body,signal)=>new Promise(resolve=>pending.push({resolve,signal}));
          const old=refreshInspector();showHome();
          assert.ok(pending.every(request=>request.signal.aborted));assert.equal(fileRefreshRequests.size,0);
          assert.equal($('refresh-files').disabled,true);await refreshInspector();assert.equal(pending.length,2);
          pending.forEach(request=>request.resolve(snapshot('stale.txt')));await old;
          assert.equal($('file-count').textContent,'0');assert.equal($('result-count').textContent,'0');
          active={id:'A'};appClosed=true;await refreshInspector();assert.equal(pending.length,2);
        })()""")

    def test_completion_stop_and_error_refresh_both_lists(self):
        self.run_case(r"""(()=>{
          const calls=[];refreshFiles=()=>{calls.push('files');};refreshResults=reveal=>{calls.push(['results',reveal]);};
          refreshSessionMeta=()=>{};
          for(const event of [{type:'result',data:{}},{type:'status',data:{state:'stopped'}},
              {type:'status',data:{state:'error'}},{type:'error',data:{message:'failed'}}]){
            calls.length=0;handleEvent(event);assert.equal(calls.length,2);assert.equal(calls[0],'files');
            assert.equal(calls[1][0],'results');
          }
        })()""")

    def test_partial_availability_keeps_returned_rows_and_explains_uncertainty(self):
        self.run_case(r"""(async()=>{
          api=async()=>({...snapshot('unknown.txt'),availability:{missing:1,errors:1,limited:false}});
          await refreshResults();assert.equal($('result-count').textContent,1);
          assert.equal($('results-list').children[0].title,'C:/task/A/unknown.txt');
          assert.ok($('results-list').children.some(node=>node.textContent.includes('현재 상태를 확인하지 못했어요')));
        })()""")


if __name__ == '__main__':
    unittest.main()
