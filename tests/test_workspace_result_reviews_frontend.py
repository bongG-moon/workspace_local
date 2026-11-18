"""Folder observations require an explicit decision; deletions are not files to open."""
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from local_app.history import HistoryStore
from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT
from tests.test_workspace_review_frontend import HARNESS as REVIEW_HARNESS, FIXTURE


SETUP = r"""
active={id:'A',workspace:'C:/task',kind:'work',state:'done',messages:[],lastRunId:'now'};
WorkspaceExecutionView.reset('A');
const walk=node=>[node,...node.children.filter(child=>child?.children).flatMap(walk)];
const button=(node,text)=>walk(node).find(child=>child.tagName==='BUTTON'&&child.textContent===text);
const candidate={id:'candidate-old',runId:'older-run',path:'C:/task/report.html',name:'report.html',
  change:'created',attribution:'observed',conflict:true};
const snapshot=(artifacts=[],observedChanges=[])=>({lastRunId:'now',artifacts,observedChanges});
"""


class DeletedHistoryTests(unittest.TestCase):
    def test_confirmed_deletion_survives_normal_save_and_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = {'path': str(root / 'gone.py'), 'name': 'gone.py', 'runId': 'run-a',
                        'observedAt': 1, 'change': 'deleted', 'attribution': 'confirmed',
                        'size': None, 'lineCounts': {'added': 0, 'removed': 2}}
            item = {'id': str(uuid.uuid4()), 'title': 'work', 'workspace': str(root),
                    'created': 1, 'messages': [], 'artifacts': [artifact, {**artifact, 'attribution': 'observed'}]}
            HistoryStore(root).save([item])
            loaded = HistoryStore(root).load()
            self.assertEqual(loaded[0]['artifacts'], [artifact])
            self.assertFalse((root / 'gone.py').exists())


@unittest.skipUnless(NODE, 'Node required')
class ResultReviewsFrontendTests(unittest.TestCase):
    def run_case(self, body):
        harness = HARNESS.replace('const scenario=process.argv[3];',
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context);\nconst scenario=process.argv[3];")
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + body,
                                 str(ROOT / 'local_app/web/execution-view.js')], input=harness,
                                text=True, encoding='utf-8', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_deleted_result_only_offers_saved_comparison(self):
        self.run_case(r"""(async()=>{
          const opened=[],richPaths=[],dragged=[];
          globalThis.WorkspaceRichContent={files(parent,paths){richPaths.push(...paths);}};
          globalThis.WorkspaceAttachments={makeDraggable(node,path){dragged.push(path);}};
          globalThis.WorkspaceProductivityActions={openChanges:options=>opened.push(options)};
          const file={path:'C:/task/deleted.csv',name:'deleted.csv',runId:'now',change:'deleted',attribution:'confirmed',observedAt:1};
          api=async()=>snapshot([file]);await refreshResults();
          const row=$('results-list').querySelector('.result-card');
          assert.equal(row.querySelector('.result-change').textContent,'삭제된 파일');
          assert.deepEqual(dragged,[]);assert.deepEqual(richPaths,[]);
          for(const label of ['미리보기','기본 앱에서 열기','폴더에서 보기','이 결과로 요청'])assert.equal(button(row,label),undefined);
          button(row,'변경 내용 보기').onclick();assert.equal(opened[0].path,file.path);
          const gallery=$('conversation').querySelector('.run-results');
          assert.ok(gallery.querySelector('.run-result-deleted'));
          assert.equal(button(gallery,'원본 표 보기'),undefined);
          assert.equal(gallery.querySelector('.run-results-label').textContent,'이번 요청 중 바뀐 파일');
        })()""")

    def test_candidate_is_separate_never_auto_opens_or_becomes_primary(self):
        self.run_case(r"""(async()=>{
          const opened=[],richPaths=[],calls=[];
          globalThis.WorkspaceRichContent={files(parent,paths){richPaths.push(...paths);}};
          globalThis.WorkspaceProductivityActions={openChanges:options=>opened.push(options)};
          api=async(path,body)=>{calls.push({path,body});return snapshot([candidate],[candidate]);};
          await refreshResults();const section=$('results-list').querySelector('.observed-changes');
          assert.ok(section);assert.equal($('result-count').textContent,0);assert.deepEqual(richPaths,[]);
          assert.equal($('results-list').querySelector('.result-card'),null);
          assert.equal(calls.length,1);assert.equal(calls[0].body,undefined);
          button(section,'변경 내용 보기').onclick();
          assert.equal(opened[0].candidateId,candidate.id);assert.equal(opened[0].runId,'older-run');
          assert.equal(opened[0].path,candidate.path);
          assert.equal($('conversation').querySelector('.run-results').dataset.runId,'older-run');
        })()""")

    def test_decision_is_single_flight_across_chat_and_sidebar(self):
        self.run_case(r"""(async()=>{
          let answer,payload;const calls=[];let current=snapshot([],[candidate]);
          api=(path,body)=>{calls.push(path);if(body){payload=body;return new Promise(resolve=>answer=resolve);}return Promise.resolve(current);};
          await refreshResults();
          const side=$('results-list').querySelector('.observed-changes'),chat=$('conversation').querySelector('.observed-changes');
          const first=button(side,'이 업무 결과에 추가').onclick();
          assert.equal(button(chat,'이 업무 결과에 추가').disabled,true);
          await button(chat,'이 업무 결과에 추가').onclick();await button(side,'제외').onclick();
          assert.equal(calls.filter(path=>path==='/api/results/confirm').length,1);
          assert.equal(payload.id,'A');assert.equal(payload.runId,'older-run');assert.equal(payload.candidateId,candidate.id);assert.equal(payload.action,'include');
          current=snapshot([{...candidate,attribution:'confirmed'}],[]);answer(current);await first;
          assert.equal($('results-list').querySelector('.observed-changes'),null);
          const gallery=$('conversation').querySelector('.run-results');
          assert.equal(gallery.querySelector('.run-results-label').textContent,'이번 요청 중 바뀐 파일');
          assert.equal(gallery.querySelector('.observed-changes'),null);
        })()""")

    def test_other_candidate_changes_keep_sidebar_buttons_and_expanded_rows(self):
        self.run_case(r"""(async()=>{
          let rows=Array.from({length:8},(_,i)=>({...candidate,id:'c-'+i}));
          const opened=[];globalThis.WorkspaceProductivityActions={openChanges:value=>opened.push(value.candidateId)};
          api=async()=>snapshot([],rows);
          await refreshResults();
          const section=$('results-list').querySelector('.observed-changes');
          const detail=section.querySelector('details');detail.open=true;detail.ontoggle();
          const compare=id=>button(walk(section).find(node=>node.dataset.candidateId===id),'변경 내용 보기');
          const first=compare('c-0'),inDetail=compare('c-6'),last=compare('c-7');
          rows=rows.slice(0,7);
          await refreshResults();
          assert.equal($('results-list').querySelector('.observed-changes'),section);
          assert.equal(section.querySelector('details'),detail);assert.equal(detail.open,true);
          assert.equal(compare('c-0'),first);assert.equal(compare('c-6'),inDetail);
          first.onclick();inDetail.onclick();last.onclick();
          assert.deepEqual(opened,['c-0','c-6']);
        })()""")

    def test_late_decision_cannot_refresh_another_task_or_change_draft(self):
        self.run_case(r"""(async()=>{
          let answer;const calls=[];api=(path,body)=>{calls.push(path);return body?new Promise(resolve=>answer=resolve):Promise.resolve(snapshot([],[candidate]));};
          await refreshResults();const section=$('results-list').querySelector('.observed-changes');
          const pending=button(section,'제외').onclick();
          active={id:'B',workspace:'C:/task',kind:'work',messages:[]};selectionGeneration++;$('prompt').value='B unsent draft';
          answer(snapshot());await pending;
          assert.equal(calls.length,2);assert.equal(active.id,'B');assert.equal($('prompt').value,'B unsent draft');
          await button(section,'이 업무 결과에 추가').onclick();assert.equal(calls.length,2);
        })()""")

    def test_folding_more_changes_preserves_an_inflight_review_outcome(self):
        self.run_case(r"""(async()=>{
          const rows=Array.from({length:6},(_,i)=>({...candidate,id:'c-'+i,path:'C:/task/'+i+'.txt'}));
          let approved=false,finish;const posts=[],warnings=[];
          api=async(path,body)=>{
            if(body){posts.push(body);return new Promise((resolve,reject)=>{finish={resolve,reject};});}
            return snapshot(approved?[{...rows[5],attribution:'confirmed'}]:[],approved?rows.slice(0,5):rows);
          };
          toast=text=>warnings.push(text);
          await refreshResults();
          const section=$('results-list').querySelector('.observed-changes'),detail=section.querySelector('details');
          const toggle=value=>{detail.open=value;detail.ontoggle();};
          toggle(true);
          const failed=button(detail,'제외').onclick();toggle(false);
          finish.reject(Error('저장하지 못했습니다.'));await failed;
          assert.deepEqual(warnings,['저장하지 못했습니다.']);
          toggle(true);
          const accepted=button(detail,'이 업무 결과에 추가').onclick();toggle(false);
          approved=true;finish.resolve({ok:true});await accepted;
          assert.deepEqual(posts.map(p=>[p.action,p.candidateId]),[['dismiss','c-5'],['include','c-5']]);
          assert.equal($('results-list').querySelector('details'),null);
          assert.deepEqual(active.artifacts.map(f=>f.path),[rows[5].path]);
        })()""")

    def test_dismiss_removes_only_own_candidate_gallery(self):
        self.run_case(r"""(async()=>{
          let current=snapshot([],[candidate]);api=async(path,body)=>{if(body){assert.equal(body.action,'dismiss');current=snapshot();}return current;};
          await refreshResults();const original=el('article','original reply','assistant');$('conversation').prepend(original);
          await button($('results-list'),'제외').onclick();
          assert.equal($('conversation').querySelector('.run-results'),null);assert.equal(original.isConnected,true);
          assert.equal($('results-list').children.length,0);assert.equal($('empty-results').hidden,false);
        })()""")

    def test_failed_confirmation_can_be_retried_and_bulk_rows_are_lazy(self):
        self.run_case(r"""(async()=>{
          const candidates=Array.from({length:20},(_,i)=>({...candidate,id:'candidate-'+i}));
          let mutations=0;const warnings=[];toast=message=>warnings.push(message);
          api=async(path,body)=>{if(body){mutations++;throw Error('일시 오류');}return snapshot([],candidates);};
          await refreshResults();const section=$('results-list').querySelector('.observed-changes');
          assert.equal(walk(section).filter(node=>node.classList.contains('observed-change')).length,5);
          const include=button(section,'이 업무 결과에 추가');await include.onclick();
          assert.equal(include.disabled,false);await include.onclick();assert.equal(mutations,2);assert.equal(warnings.length,2);
          const detail=section.querySelector('details');detail.open=true;detail.ontoggle();detail.ontoggle();
          assert.equal(walk(section).filter(node=>node.classList.contains('observed-change')).length,20);
        })()""")

    def test_recovered_older_confirmed_results_render_once_with_bounded_runs(self):
        self.run_case(r"""(async()=>{
          globalThis.WorkspaceRichContent={files(){}};
          const files=Array.from({length:20},(_,i)=>({path:'C:/task/'+i+'.md',name:i+'.md',
            runId:'run-'+i,observedAt:i+1,change:'created',attribution:'confirmed'}));
          WorkspaceExecutionView.syncResults('run-19',files,[]);
          const galleries=()=>walk($('conversation')).filter(node=>node.classList.contains('run-results'));
          assert.equal(galleries().length,8);assert.equal(galleries()[0].dataset.runId,'run-12');
          const nodes=galleries();WorkspaceExecutionView.syncResults('run-19',files,[]);
          assert.deepEqual(galleries(),nodes);
          const recovered={path:'C:/task/recovered.md',name:'recovered.md',runId:'older-recovered',observedAt:100,
            change:'deleted',attribution:'confirmed'};
          WorkspaceExecutionView.syncResults('run-19',[...files,recovered],[]);
          assert.equal(galleries().length,8);assert.ok(galleries().some(node=>node.dataset.runId==='older-recovered'));
          assert.equal(galleries().some(node=>node.dataset.runId==='run-12'),false);
        })()""")


@unittest.skipUnless(NODE, 'Node required')
class ObservedComparisonFrontendTests(unittest.TestCase):
    def test_candidate_scope_is_preserved_for_summary_and_file(self):
        scenario = FIXTURE + r"""(async()=>{
          const calls=[];
          api=async(path,body)=>{assert.equal(body,undefined);calls.push(path);return path.startsWith('/api/changes?')
            ?{runs:[{...run('old',[file('deleted-id','removed.py')]),attribution:'observed'}]}
            :{status:'text',diff:'-removed\n'};};
          await WorkspaceProductivityActions.openChanges({sessionId:'A',runId:'old',candidateId:'candidate-one',path:'C:/fixture/A/removed.py'});await flush();
          assert.deepEqual(calls,['/api/changes?id=A&run=old&candidate=candidate-one','/api/changes/file?id=A&run=old&file=deleted-id&candidate=candidate-one']);
          assert.match($('changes-intro').textContent,/아직/);assert.equal($('changes-run').disabled,true);
        })()"""
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/productivity.js'), scenario],
                                input=REVIEW_HARNESS, text=True, encoding='utf-8',
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
