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
