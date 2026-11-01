"""Execution evidence joins the right conversation without triggering work."""
from pathlib import Path
import tempfile
import time
import unittest

from local_app.server import LocalApp
from test_workspace_frontend_state import HARNESS, NODE, ROOT
import subprocess


class ExecutionRoutingTests(unittest.TestCase):
    def test_records_persist_and_unknown_terminal_cannot_become_success(self):
        with tempfile.TemporaryDirectory() as temp:
            app=LocalApp(Path(temp),demo=True)
            task=app.create('',True,managed=True,title='record test')
            item=app.get(task['id']);item['lastRunId']='run-a';item['state']='running'
            record={'id':'tool-1','tool':'Bash','command':'python report.py','description':'계산','state':'requested','output':'','startedAt':time.time()}
            app.emit(item['id'],'execution',record)
            self.assertEqual(item['state'],'running')
            self.assertEqual(app.public(item)['executions'][0]['runId'],'run-a')
            app.emit(item['id'],'assistant_delta',{'text':'partial','messageId':'message-a','index':0})
            self.assertEqual(item['events'][-1]['data']['runId'],'run-a')
            app.emit(item['id'],'assistant',{'text':'response'})
            self.assertEqual(item['messages'][-1]['runId'],'run-a')
            self.assertEqual(item['events'][-1]['data']['runId'],'run-a')
            app.emit(item['id'],'status',{'state':'stopped'})
            self.assertEqual(item['executions'][0]['state'],'interrupted')
            app.close()
            resumed=LocalApp(Path(temp),demo=True)
            self.assertEqual(resumed.public(resumed.get(item['id']))['executions'][0]['state'],'interrupted')
            resumed.close()

    def test_foreign_runid_cannot_override_current_and_records_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            app=LocalApp(Path(temp),demo=True);item=app.get(app.create('',True,managed=True)['id'])
            item['lastRunId']='run-a'
            record={'id':'tool-1','tool':'Bash','command':'echo example','state':'requested','output':'','startedAt':time.time(),'runId':'other'}
            app.emit(item['id'],'execution',record)
            app.emit(item['id'],'execution',{**record,'state':'completed','output':'example'})
            app.emit(item['id'],'execution',record)
            self.assertEqual(len(item['executions']),1)
            self.assertEqual(item['executions'][0]['state'],'completed')
            self.assertEqual(item['executions'][0]['runId'],'run-a')
            self.assertEqual(item['state'],'idle')
            app.close()


@unittest.skipUnless(NODE,'Node required')
class ExecutionViewTests(unittest.TestCase):
    def run_case(self,body):
        script=HARNESS.replace("const scenario=process.argv[3];", "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context);\nconst scenario=process.argv[3];")
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),body,str(ROOT/'local_app/web/execution-view.js')],input=script,text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_streaming_and_final_reply_keep_received_run_identity(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[],lastRunId:'unrelated-run'};
          applyDelta({messageId:'first',index:0,text:'partial',runId:'run-a'});
          const first=streaming.get('first:0');assert.equal(first.dataset.runId,'run-a');
          applyDelta({messageId:'first',index:0,text:' more'});
          assert.equal(first.dataset.runId,'run-a');
          assert.equal(renderMessage({role:'assistant',messageId:'first',index:0,text:'final'}),first);
          assert.equal(first.dataset.runId,'run-a');
          applyDelta({messageId:'second',index:0,text:'legacy partial'});
          const second=streaming.get('second:0');
          assert.equal(renderMessage({role:'assistant',messageId:'second',index:0,text:'final',runId:'run-b'}),second);
          assert.equal(second.dataset.runId,'run-b');
        ''')

    def test_restored_execution_and_files_join_the_correct_finalized_stream(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          const conversation=$('conversation');
          conversation.insertBefore=function(node,next){node.remove();const index=this.children.indexOf(next);node.parent=this;this.children.splice(index,0,node);};
          Object.defineProperty(Object.getPrototypeOf(conversation),'nextSibling',{get(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null;}});
          globalThis.WorkspaceRichContent={render(){return false;},files(){}};
          for(const run of ['run-a','run-b']){
            renderMessage({role:'user',text:'request '+run,runId:run});
            applyDelta({messageId:run,text:'partial',runId:run});
            renderMessage({role:'assistant',messageId:run,text:'final '+run,runId:run});
          }
          WorkspaceExecutionView.restore(
            ['run-a','run-b'].map(run=>({id:'tool-'+run,runId:run,tool:'Bash',state:'completed',command:'echo done'})),
            ['run-a','run-b'].map(run=>({path:'C:/task/'+run+'.png',runId:run,observedAt:1})));
          const rows=conversation.children.map(node=>`${node.dataset.runId}:${node.classList.contains('execution-card')?'execution':node.classList.contains('run-results')?'files':node.classList.contains('assistant')?'assistant':'user'}`);
          assert.equal(rows.join('|'),'run-a:user|run-a:execution|run-a:assistant|run-a:files|run-b:user|run-b:execution|run-b:assistant|run-b:files');
        ''')

    def test_execution_updates_same_card_and_never_executes(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          let calls=0;api=async()=>{calls++;};
          globalThis.WorkspaceRichContent={code(parent,text){parent.append(el('pre',text));},files(){}};
          const record={id:'tool-1',runId:'run-a',tool:'Bash',state:'requested',command:'echo 1'};
          WorkspaceExecutionView.render(record);WorkspaceExecutionView.render({...record,state:'completed',output:'1'});
          assert.equal($('conversation').children.length,1);assert.equal(calls,0);
          assert.equal($('conversation').children[0].dataset.executionState,'completed');
          assert.equal($('conversation').children[0].querySelector('.execution-output'),null);
          const detail=$('conversation').children[0].querySelector('details');detail.open=true;detail.ontoggle();
          assert.equal($('conversation').children[0].querySelector('.execution-output').textContent,'1');
          active={id:'B'};WorkspaceExecutionView.render({...record,id:'other'});
          assert.equal($('conversation').children.length,1);
        ''')

    def test_result_gallery_is_deduplicated_and_uses_its_own_run(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          let rows=[];globalThis.WorkspaceRichContent={files(parent,paths){rows.push(paths);}};
          const files=[{path:'C:/task/a.png',runId:'a',observedAt:1},{path:'C:/task/b.png',runId:'b',observedAt:1}];
          WorkspaceExecutionView.results('a',files);WorkspaceExecutionView.results('a',files);
          assert.equal(rows.length,1);assert.equal(rows[0].join(','),'C:/task/a.png');
          assert.equal($('conversation').children.length,1);
        ''')

    def test_disclosure_follows_native_toggle_and_live_record_replacement(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          let renders=0;globalThis.WorkspaceRichContent={code(){renders++;}};
          const record={id:'tool-1',runId:'run-a',tool:'Bash',state:'requested',command:'echo 1'};
          WorkspaceExecutionView.render(record);
          const card=$('conversation').children[0];let detail=card.querySelector('details');
          assert.equal(card.querySelector('.disclosure-action').textContent,'펼치기');
          assert.equal(card.querySelector('summary').querySelector('button'),null);assert.equal(renders,0);
          detail.open=true;detail.ontoggle();
          assert.equal(card.querySelector('.disclosure-action').textContent,'접기');assert.equal(renders,1);
          WorkspaceExecutionView.render({...record,state:'completed',output:'1'});detail=card.querySelector('details');
          assert.equal(detail.open,true);assert.equal(card.querySelector('.disclosure-action').textContent,'접기');
          assert.equal(card.querySelector('.execution-state').textContent,'실행 완료');assert.equal(renders,2);
          detail.open=false;detail.ontoggle();
          assert.equal(card.querySelector('.disclosure-action').textContent,'펼치기');assert.equal(renders,2);
          WorkspaceExecutionView.render({...record,state:'completed',output:'1'});
          assert.equal(card.querySelector('details').open,false);assert.equal(renders,2);
        ''')

    def test_empty_result_snapshot_removes_only_matching_gallery_and_can_restore(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          globalThis.WorkspaceRichContent={files(){}};
          const files=[{path:'C:/task/a.png',runId:'a',observedAt:1},{path:'C:/task/b.png',runId:'b',observedAt:1}];
          const message=el('article','original reply','message assistant');message.dataset.runId='a';$('conversation').append(message);
          WorkspaceExecutionView.results('a',files);WorkspaceExecutionView.results('b',files);
          const deleted=$('conversation').children[1];
          WorkspaceExecutionView.results('a',[]);
          assert.equal($('conversation').children.length,2);assert.equal(deleted.isConnected,false);
          assert.equal($('conversation').children[0],message);assert.equal(message.textContent,'original reply');
          assert.equal($('conversation').children[1].dataset.runId,'b');
          WorkspaceExecutionView.results('a',files);
          assert.equal($('conversation').children.length,3);
          assert.notEqual($('conversation').children[2],deleted);
          assert.equal($('conversation').children[2].dataset.runId,'a');
        ''')

    def test_report_is_visible_before_scratch_files_and_all_files_remain_accessible(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',messages:[]};WorkspaceExecutionView.reset('A');
          const rendered=[];globalThis.WorkspaceRichContent={files(parent,paths){assert.ok(paths.length<=24);rendered.push(paths);}};
          const report={path:'C:/task/output/q2/report.html',runId:'a',observedAt:1};
          const files=[report,...Array.from({length:25},(_,i)=>({path:`C:/task/scratch/check-${i}.txt`,runId:'a',observedAt:2}))];
          WorkspaceExecutionView.results('a',files);
          assert.equal(rendered.length,1);assert.equal(rendered[0].length,1);assert.equal(rendered[0][0],report.path);
          const card=$('conversation').children[0],detail=card.querySelector('details');assert.equal(detail.open,false);
          detail.open=true;detail.ontoggle();
          assert.equal(rendered.slice(1).flat().length,26);assert.equal(new Set(rendered.slice(1).flat()).size,26);
          detail.ontoggle();assert.equal(rendered.length,3);
          WorkspaceExecutionView.results('a',[...files,{path:'C:/task/notes.md',runId:'a',observedAt:3}]);
          assert.equal(card.querySelector('details').open,true);
        ''')

    def test_recovery_notice_is_read_only_and_does_not_hide_draft(self):
        self.run_case(r'''
          active={id:'A',workspace:'C:/task',state:'idle',lastRunId:'a',pendingRunId:'a',messages:[{role:'user',text:'request',runId:'a'}]};
          $('prompt').value='unsent draft';attachments=['C:/task/input.csv'];let sent=0,opened=[];
          api=async()=>{sent++;};WorkspaceProgressView={open(run){opened.push(run);}};
          renderRequestRecovery();assert.equal($('request-recovery').hidden,false);
          const button=$('request-recovery').querySelector('button');button.onclick();assert.equal(opened.join(','),'a');
          assert.equal(sent,0);assert.equal($('prompt').value,'unsent draft');assert.equal(attachments.length,1);
          active.state='running';renderRequestRecovery();assert.equal($('request-recovery').hidden,true);
          active.state='idle';active.messages[0].runTiming={status:'done'};renderRequestRecovery();assert.equal($('request-recovery').hidden,true);
          active={id:'B',state:'idle',lastRunId:'legacy',messages:[]};renderRequestRecovery();assert.equal($('request-recovery').hidden,true);
        ''')

    def test_sidebar_prioritizes_report_and_disclosure_is_scoped_to_request(self):
        self.run_case(r'''(async()=>{
          active={id:'A',workspace:'C:/task',kind:'work',messages:[]};WorkspaceExecutionView.reset('A');
          let run='a';api=async()=>({lastRunId:run,artifacts:[{path:'C:/task/report.html',runId:run},...Array.from({length:25},(_,i)=>({path:`C:/task/scratch/${i}.txt`,runId:run}))]});
          await refreshResults();const list=$('results-list');
          assert.equal(list.children.filter(n=>n.classList.contains('result-card')).length,1);
          let detail=list.querySelector('details');assert.equal(detail.open,false);
          const content=detail.children[1];assert.equal(content.children.length,0);
          detail.open=true;detail.ontoggle();assert.equal(content.children.length,26);
          await refreshResults();assert.equal(list.querySelector('details').open,true);
          run='b';await refreshResults();assert.equal(list.querySelector('details').open,false);
          assert.equal($('result-count').textContent,26);
        })()''')

    def test_incomplete_scan_does_not_claim_no_files_and_offers_bounded_folder_browse(self):
        self.run_case(r'''(async()=>{
          active={id:'A',workspace:'C:/task',kind:'work',messages:[]};
          const calls=[];api=async(path)=>{calls.push(path);return path.startsWith('/api/files')?{files:[],directory:''}:{lastRunId:'a',artifacts:[],observation:{limited:true}};};
          $('prompt').value='draft';await refreshResults();
          assert.equal($('empty-results').hidden,true);const note=$('results-list').querySelector('.result-observation');
          assert.ok(note);note.querySelector('button').onclick();
          assert.equal($('source-panel').hidden,false);assert.equal($('prompt').value,'draft');
          assert.equal(calls.length,2);assert.ok(calls[1].startsWith('/api/files'));
        })()''')
