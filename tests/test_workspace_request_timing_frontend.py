"""Request duration footers stay with their run across live events and history recovery."""
import subprocess
import unittest

from tests import test_workspace_frontend_state as frontend


DOM_SUPPORT = r'''
Element.prototype.matches=function(selector){
  return selector.split(',').some(value=>{
    value=value.trim().replace(/^:scope\s*>\s*/, '');
    const attrs=[...value.matchAll(/\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/g)];
    const head=value.replace(/\[[^\]]+\]/g,''),tag=head.match(/^[a-z][\w-]*/i)?.[0];
    if(tag&&this.tagName!==tag.toUpperCase())return false;
    if([...head.matchAll(/\.([\w-]+)/g)].some(match=>!this.classList.contains(match[1])))return false;
    return attrs.every(([,name,expected])=>{
      const actual=name.startsWith('data-')?this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]:this.getAttribute(name);
      return actual!==undefined&&actual!==null&&(expected===undefined||actual===expected);
    });
  });
};
Element.prototype.querySelectorAll=function(selector){const rows=[];for(const child of this.children){if(child.matches?.(selector))rows.push(child);rows.push(...(child.querySelectorAll?.(selector)||[]));}return rows;};
Element.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0]||null;};
Element.prototype.insertBefore=function(node,before){if(node===before)return node;if(!before){this.append(node);return node;}if(node.parent)node.parent.children=node.parent.children.filter(child=>child!==node);const index=this.children.indexOf(before);assert.ok(index>=0);this.children.splice(index,0,node);node.parent=this;node.isConnected=true;return node;};
Element.prototype.after=function(node){this.parent?.insertBefore(node,this.nextSibling);};
Element.prototype.before=function(node){this.parent?.insertBefore(node,this);};
Element.prototype.contains=function(node){return node===this||this.children.some(child=>child.contains?.(node));};
Element.prototype.removeAttribute=function(name){delete this.attributes[name];};
Object.defineProperties(Element.prototype,{
  parentElement:{get(){return this.parent?.tagName?this.parent:null;}},
  nextSibling:{get(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null;}},
  previousSibling:{get(){return this.parent?.children[this.parent.children.indexOf(this)-1]||null;}},
  lastElementChild:{get(){return this.children.filter(node=>node.tagName).at(-1)||null;}},
  firstElementChild:{get(){return this.children.find(node=>node.tagName)||null;}}
});
context.document.createElementNS=(_,tag)=>new Element(tag);
context.flatText=function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ').trim();};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'request-timing.js'});
'''

HARNESS = frontend.HARNESS.replace(
    'const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,',
    'const timingCounters={intervals:0,timeouts:0};\nconst context={assert,console,URLSearchParams,AbortController,Date,Map,Set,timingCounters,',
).replace(
    'setInterval(){},setTimeout(){return 1;},',
    'setInterval(){timingCounters.intervals++;},setTimeout(){timingCounters.timeouts++;return 1;},',
).replace('const scenario=process.argv[3];', DOM_SUPPORT + '\nconst scenario=process.argv[3];')

SETUP = r'''
const timing=(runId,durationMs,status='done')=>({runId,startedAt:100,finishedAt:100+durationMs/1000,durationMs,status});
const user=(runId,text='request',runTiming=null)=>({role:'user',text,runId,...(runTiming?{runTiming}:{})});
const assistant=(runId,text='answer')=>({role:'assistant',text,runId});
const a={id:'A',title:'A',kind:'work',workspace:'C:/A',state:'done',trusted:true,messages:[],requests:[],seq:0,connection:{connected:true}};
const b={...a,id:'B',title:'B',workspace:'C:/B',messages:[]};
const refreshSessionMetaFromApp=refreshSessionMeta;
sessions=[a,b];poll=()=>{};refreshInspector=()=>{};refreshSessionMeta=async()=>{};
const calls=[];api=async(path,body)=>{calls.push({path,body});assert.ok(path.startsWith('/api/session?'));const row=sessions.find(item=>item.id===decodeURIComponent(path.split('id=')[1]));return {...row,messages:row.messages.map(message=>({...message})),requests:[]};};
const footers=()=>[...$('conversation').children].filter(node=>node.classList.contains('request-timing'));
const footerFor=id=>footers().filter(node=>node.dataset.runId===id);
const messagesFor=id=>[...$('conversation').children].filter(node=>node.classList.contains('message')&&node.dataset.runId===id);
const assertAfterRun=id=>{const items=footerFor(id);assert.equal(items.length,1,'one footer for '+id);assert.equal(messagesFor(id).at(-1)?.nextSibling,items[0],'footer follows last message for '+id);return items[0];};
'''


@unittest.skipUnless(frontend.NODE, 'Node.js is required')
class WorkspaceRequestTimingFrontendTests(unittest.TestCase):
    def run_case(self, code):
        result = subprocess.run(
            [frontend.NODE, '-', str(frontend.ROOT/'local_app/web/app.js'), '(async()=>{' + SETUP + code + '})()',
             str(frontend.ROOT/'local_app/web/request-timing.js')],
            input=HARNESS, text=True, encoding='utf-8', capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_history_restores_each_request_duration_after_its_answer(self):
        self.run_case(r'''
          a.messages=[user('one','first',timing('one',12500)),assistant('one','first answer'),
            user('two','second',timing('two',62300)),assistant('two','second answer')];
          await selectSession('A');assert.equal(footers().length,2);
          assert.match(flatText(assertAfterRun('one')),/12초 소요/);
          assert.match(flatText(assertAfterRun('two')),/1분.*소요/);
          assert.equal(calls.length,1);assert.equal($('elapsed').textContent,'');
        ''')

    def test_duration_rounding_and_terminal_status_are_clear(self):
        self.run_case(r'''
          await selectSession('A');const cases=[['subsecond',999,'done',/1초 미만 소요/],
            ['seconds',59999,'done',/59초 소요/],['minutes',61500,'stopped',/1분 1초 소요 · 답변 중지/],
            ['hours',3661999,'error',/1시간 1분 1초 소요 · 오류로 종료/]];
          for(const [id,duration,status,label]of cases){renderMessage(user(id));renderMessage(assistant(id));WorkspaceRequestTiming.render(timing(id,duration,status));assert.match(assertAfterRun(id).getAttribute('aria-label'),label);}
        ''')

    def test_terminal_event_records_duration_before_immediate_queue_transition(self):
        self.run_case(r'''
          a.state='running';a.lastRunId='one';a.runStartedAt=100;a.messages=[user('one')];
          await selectSession('A');handleEvent({type:'assistant',data:{text:'finished',runId:'one'}});
          handleEvent({type:'result',data:{lastRunId:'one',runTiming:timing('one',15000)}});
          handleEvent({type:'queued_user',data:{requestId:'queue-two',text:'next',runId:'two',runStartedAt:115}});
          handleEvent({type:'status',data:{state:'running',runId:'two',runStartedAt:115}});
          const footer=assertAfterRun('one');assert.match(flatText(footer),/15초 소요/);
          assert.ok($('conversation').children.indexOf(footer)<$('conversation').children.indexOf(messagesFor('two')[0]));
          assert.equal(active.lastRunId,'two');assert.equal(started,115000);assert.equal(footerFor('two').length,0);
        ''')

    def test_late_answer_moves_the_same_footer_and_duplicate_terminal_does_not_duplicate(self):
        self.run_case(r'''
          await selectSession('A');renderMessage(user('one'));const finished=timing('one',8000);
          WorkspaceRequestTiming.render(finished);const footer=assertAfterRun('one');
          handleEvent({type:'assistant',data:{text:'arrived after timing',runId:'one'}});
          assert.equal(assertAfterRun('one'),footer);
          WorkspaceRequestTiming.render(finished);WorkspaceRequestTiming.render({...finished});
          assert.equal(footers().length,1);assert.equal(assertAfterRun('one'),footer);
        ''')

    def test_recovery_restores_duration_without_resend_or_draft_changes(self):
        self.run_case(r'''
          await selectSession('A');$('prompt').value='draft\nkeep';attachments=['C:/A/keep.txt'];saveDraft();$('prompt').focus();
          const beforeCalls=calls.length;
          const snapshot={...active,seq:500,messages:[user('recovered','request',timing('recovered',12300)),assistant('recovered')],streams:[]};
          restoreEventSnapshot(snapshot,'A');assert.match(flatText(assertAfterRun('recovered')),/12초 소요/);
          restoreEventSnapshot({...snapshot,seq:501},'A');assert.equal(footers().length,1);
          assert.equal($('prompt').value,'draft\nkeep');assert.deepEqual(attachments,['C:/A/keep.txt']);
          assert.equal(document.activeElement,$('prompt'));assert.equal(calls.length,beforeCalls);
        ''')

    def test_task_switch_does_not_leak_old_footer_and_history_restores_on_return(self):
        self.run_case(r'''
          a.messages=[user('A-run','request',timing('A-run',2000)),assistant('A-run')];
          b.messages=[user('B-run','request',timing('B-run',4000)),assistant('B-run')];
          await selectSession('A');assertAfterRun('A-run');await selectSession('B');
          assertAfterRun('B-run');WorkspaceRequestTiming.render(timing('A-run',2000));
          assert.equal(footerFor('A-run').length,0);assert.equal(footers().length,1);
          await selectSession('A');assertAfterRun('A-run');assert.equal(footerFor('B-run').length,0);
          showHome();assert.equal(footers().length,0);
        ''')

    def test_visible_history_bound_prunes_orphan_duration_footers(self):
        self.run_case(r'''
          await selectSession('A');
          for(let index=0;index<180;index++){const id='run-'+index;renderMessage(user(id));renderMessage(assistant(id));WorkspaceRequestTiming.render(timing(id,1000));}
          WorkspaceRequestTiming.prune();const visibleRuns=new Set([...$('conversation').children].filter(node=>node.classList.contains('message')).map(node=>node.dataset.runId));
          assert.ok(footers().length<=150);assert.ok(visibleRuns.size<=150);
          for(const footer of footers())assert.ok(visibleRuns.has(footer.dataset.runId),'no orphan footer');
          assert.equal(footerFor('run-0').length,0);assertAfterRun('run-179');
        ''')

    def test_render_does_not_add_polling_or_network_requests(self):
        self.run_case(r'''
          await selectSession('A');renderMessage(user('one'));renderMessage(assistant('one'));
          const timers={...timingCounters},beforeCalls=calls.length;
          for(let index=0;index<100;index++){WorkspaceRequestTiming.render(timing('one',7500));WorkspaceRequestTiming.sync('one');WorkspaceRequestTiming.prune();}
          assert.equal(timingCounters.intervals,timers.intervals);assert.equal(timingCounters.timeouts,timers.timeouts);assert.equal(calls.length,beforeCalls);assert.equal(footers().length,1);
        ''')

    def test_invalid_or_running_summary_cannot_show_a_completed_duration(self):
        self.run_case(r'''
          await selectSession('A');renderMessage(user('one'));renderMessage(assistant('one'));
          for(const value of [null,{},timing('one',-10),timing('one',NaN),timing('one',Infinity),
            timing('one',true),timing('one',1000.5),timing('one',1e12),
            timing('one',1000,'running'),timing('one',1000,{toString:()=> 'done'}),timing('one',1000,['done'])]){
            assert.equal(WorkspaceRequestTiming.render(value),false);assert.equal(footers().length,0);
          }
          assert.equal(footers().length,0);WorkspaceRequestTiming.render(timing('one',1000));assertAfterRun('one');
        ''')

    def test_wall_clock_rollback_keeps_stable_monotonic_duration(self):
        self.run_case(r'''
          await selectSession('A');renderMessage(user('one'));renderMessage(assistant('one'));
          const finished={runId:'one',startedAt:200,finishedAt:150,durationMs:12345,status:'done'};
          assert.equal(WorkspaceRequestTiming.render(finished),true);
          assert.match(assertAfterRun('one').getAttribute('aria-label'),/^12초 소요\./);
          WorkspaceRequestTiming.reset('A');WorkspaceRequestTiming.restore([user('one','request',finished)]);
          assert.match(assertAfterRun('one').getAttribute('aria-label'),/^12초 소요\./);
        ''')

    def test_first_terminal_summary_wins_over_delayed_conflicting_result(self):
        self.run_case(r'''
          await selectSession('A');renderMessage(user('one'));renderMessage(assistant('one'));
          WorkspaceRequestTiming.render(timing('one',2000,'stopped'));const first=assertAfterRun('one');
          WorkspaceRequestTiming.render(timing('one',6000,'done'));
          WorkspaceRequestTiming.restore([user('one','request',timing('one',10000,'error'))]);
          assert.equal(assertAfterRun('one'),first);assert.equal(first.dataset.state,'stopped');
          assert.match(first.getAttribute('aria-label'),/2초 소요 · 답변 중지/);
        ''')

    def test_connection_restart_restores_newly_completed_request_metadata(self):
        self.run_case(r'''
          a.messages=[user('one','old',timing('one',2000)),assistant('one')];await selectSession('A');
          $('prompt').value='keep draft';attachments=['C:/A/keep.txt'];
          applyRestartSession({...active,seq:50,messages:[...active.messages,user('two','new',timing('two',4000)),assistant('two')]});
          assertAfterRun('one');assertAfterRun('two');assert.equal(footers().length,2);
          assert.equal($('prompt').value,'keep draft');assert.deepEqual(attachments,['C:/A/keep.txt']);
        ''')

    def test_direct_send_without_assistant_uses_existing_metadata_read_to_anchor_timing(self):
        self.run_case(r'''
          await selectSession('A');refreshSessionMeta=refreshSessionMetaFromApp;
          const lookup=api;let acknowledge;api=(path,body)=>path==='/api/send'?new Promise(resolve=>acknowledge=resolve):lookup(path,body);
          $('prompt').value='fast request';const pending=submit();
          a.lastRunId='fast';a.state='error';a.messages=[user('fast','fast request',timing('fast',500,'error'))];
          handleEvent({type:'error',data:{message:'failed before answer',runTiming:timing('fast',500,'error')}});
          acknowledge({ok:true});await pending;
          assert.match(assertAfterRun('fast').getAttribute('aria-label'),/1초 미만 소요 · 오류로 종료/);
          assert.equal(messagesFor('fast').length,1);assert.equal(calls.length,2,'only selection and existing post-ack metadata read');
        ''')

    def test_previous_run_duration_does_not_change_live_followup_clock(self):
        self.run_case(r'''
          a.state='running';a.lastRunId='new';a.runStartedAt=200;
          a.messages=[user('old'),assistant('old'),user('new')];await selectSession('A');
          const clock=started;WorkspaceRequestTiming.render(timing('old',3000,'stopped'));
          assertAfterRun('old');assert.equal(active.lastRunId,'new');assert.equal(active.state,'running');
          assert.equal(started,clock);assert.equal(footerFor('new').length,0);
        ''')

    def test_chat_history_and_summary_on_assistant_are_restored(self):
        self.run_case(r'''
          a.kind='chat';a.messages=[user('one'),{...assistant('one'),runTiming:timing('one',7500)},
            {...user('two'),runTiming:timing('different-run',1000)},assistant('two')];
          await selectSession('A');assert.equal(currentMode,'chat');assertAfterRun('one');
          assert.equal(footerFor('two').length,0);assert.equal(footerFor('different-run').length,0);
        ''')


if __name__ == '__main__':
    unittest.main()
