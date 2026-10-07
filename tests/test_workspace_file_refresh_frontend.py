"""File refresh uses authoritative snapshots without stale task/request replies."""
from html.parser import HTMLParser
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


SETUP = r"""
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

    def test_shared_manual_refresh_removes_deleted_rows_and_preserves_tab(self):
        self.run_case(r"""(async()=>{
          let response=snapshot('existing.txt'),calls=[];
          api=async(path,body)=>{calls.push(path);assert.equal(body,undefined);return response;};
          await refreshInspector();setPanel('results');
          assert.equal($('files').children[0].title,'C:/task/A/existing.txt');
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
