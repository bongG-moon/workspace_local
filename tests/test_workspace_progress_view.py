"""Lazy progress history follows the selected request without touching execution."""
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, "Node.js is required")
class ProgressViewTests(unittest.TestCase):
    def run_case(self, javascript):
        harness = HARNESS.replace(
            "const nodes=new Map();",
            r"""
            Element.prototype.getAttribute=function(name){return this.attributes[name]??null;};
            Element.prototype.contains=function(other){for(let n=other;n;n=n.parent)if(n===this)return true;return false;};
            Element.prototype.insertBefore=function(value,next){value.remove();const index=next?this.children.indexOf(next):this.children.length;value.parent=this;value.isConnected=true;this.children.splice(index,0,value);};
            Element.prototype.getBoundingClientRect=function(){
              if(this.parent?.classList.contains('progress-records')){
                const scroller=this.parent.parent,top=this.parent.children.indexOf(this)*100-scroller.scrollTop;
                return {top,bottom:top+100};
              }
              return {top:0,bottom:this.clientHeight};
            };
            const progressTimers=new Map();let nextProgressTimer=1;
            const nodes=new Map();
            """,
        ).replace(
            "setInterval(){},setTimeout(){return 1;},clearTimeout(){},",
            "setInterval(){},setTimeout(fn,delay){const id=nextProgressTimer++;progressTimers.set(id,{fn,delay});return id;},clearTimeout(id){progressTimers.delete(id);},",
        ).replace(
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,",
            "const context={assert,console,URLSearchParams,AbortController,Date,Map,Set,progressTimers,"
            "flushProgress:async()=>{for(let i=0;i<12;i++)await Promise.resolve();},"
            "runProgressTimer:async()=>{const next=progressTimers.entries().next().value;assert.ok(next,'Expected refresh timer');progressTimers.delete(next[0]);next[1].fn();for(let i=0;i<12;i++)await Promise.resolve();},",
        ).replace(
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});",
            "vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'progress-view.js'});"
            "vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context,{filename:'app.js'});"
            "vm.runInContext(fs.readFileSync(process.argv[5],'utf8'),context,{filename:'tool-activity.js'});",
        )
        setup = r"""
          active={id:'A',state:'running',workspace:'C:/task',messages:[],lastRunId:'run-a'};
          const progress=WorkspaceProgressView,host=$('progress-view'),detail=host.querySelector('details');
          const meta=(count,lastSeq=count,revision=count)=>({count,lastSeq,revision,available:count>0,truncated:false});
          const record=(seq,text='actual report '+seq,runId='run-a')=>({seq,runId,time:1700000000,kind:'assistant',title:'실제 설명',text});
          const page=(records,more=false,extra={})=>({records,hasMore:more,nextBefore:more?records[0].seq:null,progress:meta(records.at(-1)?.seq||0),...extra});
          const find=selector=>host.querySelector(selector);
          const buttons=()=>{const result=[];const visit=node=>{if(node.tagName==='BUTTON')result.push(node);for(const child of node.children||[])visit(child);};visit(host);return result;};
          const button=label=>{const result=buttons().find(node=>node.textContent===label);assert.ok(result,'Missing button '+label);return result;};
          const calls=[];api=async(path,data,signal)=>{calls.push({path,data,signal});return page([]);};
          progress.reset('A',meta(0),'run-a');
        """
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/app.js"), setup + javascript,
             str(ROOT / "local_app/web/progress-view.js"), str(ROOT / "local_app/web/tool-activity.js")],
            input=harness, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_collapsed_metadata_never_fetches_or_changes_approval_focus_and_scroll(self):
        self.run_case(r"""
          const request=el('section','approval');$('requests').append(request);active.state='approval';setStatus('approval');
          $('prompt').focus();const area=$('work-area');area.scrollTop=9;area.scrollHeight=200;area.clientHeight=100;
          for(let i=1;i<30;i++)handleEvent({type:'progress_changed',data:{runId:'run-a',progress:meta(i)}});
          assert.equal(calls.length,0);assert.equal(progressTimers.size,0);assert.equal(find('.progress-body').children.length,0);
          assert.equal(find('.progress-count').textContent,'29개 기록');assert.equal(active.state,'approval');
          assert.equal($('status-text').textContent,statusLabels.approval);assert.equal(document.activeElement,$('prompt'));
          assert.equal(area.scrollTop,9);assert.equal($('requests').children[0],request);
        """)

    def test_literal_reports_are_lazy_filtered_and_long_text_expands(self):
        self.run_case(r"""(async()=>{
          const danger='<img src=x onerror=alert(1)>',text=danger+'x'.repeat(60000);
          api=async(path,data,signal)=>{calls.push({path,data,signal});return page([{...record(1,text),kind:'tool_result',tool:'Read',parentToolUseId:'agent-1',truncated:true}]);};
          progress.open('run-a');await flushProgress();
          assert.equal(calls.length,1);assert.match(calls[0].path,/limit=50&runId=run-a/);assert.equal(calls[0].data,undefined);
          assert.equal(find('.progress-records').children.length,1);assert.equal(find('.progress-record-text').textContent.length,1400);
          assert.ok(find('.progress-record-text').textContent.startsWith(danger));assert.equal(host.querySelector('img'),null);
          assert.equal(find('.progress-child').textContent,'추가 작업자의 보고');assert.equal(find('.progress-tool').textContent,'Read');
          button('내용 더 보기').onclick();assert.equal(find('.progress-record-text').textContent,text);
          button('내용 접기').onclick();assert.equal(find('.progress-record-text').textContent.length,1400);
          progress.close();assert.equal(find('.progress-body').children.length,0);assert.equal(detail.open,false);
          progress.metadata(meta(2));assert.equal(progressTimers.size,0);assert.equal(calls.length,1);
        })()""")

    def test_revision_updates_same_record_without_losing_expanded_text_or_focus(self):
        self.run_case(r"""(async()=>{
          let text='a'.repeat(2000),revision=1;api=async()=>page([record(1,text)],false,{progress:meta(1,1,revision)});
          progress.open();await flushProgress();button('내용 더 보기').onclick();button('내용 접기').focus();
          text+='new actual progress';revision=2;progress.metadata(meta(1,1,2));await runProgressTimer();
          assert.equal(find('.progress-record-text').textContent,text);assert.equal(find('.progress-records').children.length,1);
          assert.equal(document.activeElement,button('내용 접기'));assert.equal(button('내용 접기').getAttribute('aria-expanded'),'true');
        })()""")

    def test_metadata_bursts_and_inflight_changes_use_one_followup_request(self):
        self.run_case(r"""(async()=>{
          let reply;api=(path,data,signal)=>{calls.push({path,data,signal});return new Promise(resolve=>reply=resolve);};
          progress.open();for(let i=1;i<=20;i++)progress.metadata(meta(i));
          assert.equal(calls.length,1);assert.equal(progressTimers.size,0);
          reply(page([record(1)],false,{progress:meta(1)}));await flushProgress();
          assert.equal(find('.progress-count').textContent,'20개 기록');assert.equal(progressTimers.size,1);
          await runProgressTimer();assert.equal(calls.length,2);
          reply(page([record(20)],false,{progress:meta(20)}));await flushProgress();assert.equal(progressTimers.size,0);
          for(let i=21;i<=40;i++)progress.metadata(meta(i));assert.equal(progressTimers.size,1);
          progress.close();assert.equal(progressTimers.size,0);
        })()""")

    def test_session_and_request_switch_abort_and_ignore_stale_responses(self):
        self.run_case(r"""(async()=>{
          const replies=[];api=(path,data,signal)=>{calls.push({path,data,signal});return new Promise(resolve=>replies.push(resolve));};
          progress.open('run-a');progress.open('run-b');assert.equal(calls[0].signal.aborted,true);
          replies[0](page([record(1,'wrong run')]));await flushProgress();assert.equal(find('.progress-records').children.length,0);
          replies[1](page([record(2,'right run','run-b')]));await flushProgress();assert.equal(find('.progress-record-text').textContent,'right run');
          progress.open('run-a');progress.reset('B',meta(0),'run-b');assert.equal(calls[2].signal.aborted,true);
          replies[2](page([record(3,'wrong session')]));await flushProgress();assert.equal(find('.progress-body').children.length,0);
          assert.equal(detail.open,false);progress.reset();assert.equal(host.hidden,true);
        })()""")

    def test_scrolling_away_during_request_keeps_reading_position(self):
        self.run_case(r"""(async()=>{
          api=async()=>page([record(1)]);progress.open();await flushProgress();
          const scroller=find('.progress-scroll');scroller.scrollHeight=1000;scroller.clientHeight=100;scroller.scrollTop=900;
          let reply;api=()=>new Promise(resolve=>reply=resolve);progress.metadata(meta(2));await runProgressTimer();
          scroller.scrollTop=17;reply(page([record(1),record(2)]));await flushProgress();
          assert.equal(scroller.scrollTop,17);assert.equal(find('.progress-records').children.length,1);
          assert.equal(button('새 진행 내용 보기').hidden,false);
          api=async()=>page([record(2)]);button('새 진행 내용 보기').onclick();await flushProgress();assert.equal(scroller.scrollTop,1000);
        })()""")

    def test_older_pagination_is_exclusive_bounded_and_keeps_the_visible_anchor(self):
        self.run_case(r"""(async()=>{
          let last=350;api=async(path)=>{calls.push({path});const match=path.match(/before=(\d+)/),end=match?Number(match[1])-1:last;
            return page(Array.from({length:50},(_,i)=>record(end-49+i)),end>50,{progress:meta(last)});};
          progress.open();await flushProgress();const scroller=find('.progress-scroll');
          Object.defineProperty(scroller,'scrollHeight',{get:()=>find('.progress-records').children.length*100});
          for(let i=0;i<5;i++){scroller.scrollTop=20;button('이전 기록 50개 보기').onclick();await flushProgress();assert.equal(scroller.scrollTop,5020);}
          const seqs=[...find('.progress-records').children].map(row=>Number(row.dataset.seq));
          assert.equal(seqs.length,250);assert.equal(new Set(seqs).size,250);assert.equal(seqs[0],51);assert.equal(seqs.at(-1),300);
          assert.match(calls[1].path,/before=301/);const previous=calls.length;progress.metadata(meta(351));await runProgressTimer();
          assert.equal(calls.length,previous);assert.equal(button('새 진행 내용 보기').hidden,false);
          last=351;button('새 진행 내용 보기').onclick();await flushProgress();assert.equal(find('.progress-records').children.length,50);
          assert.equal(find('.progress-records').children.at(-1).dataset.seq,'351');
        })()""")

    def test_old_history_notice_and_errors_are_visible_and_retry_is_explicit(self):
        self.run_case(r"""(async()=>{
          api=async()=>page([],false,{notice:'이전 버전에서 수행한 상세 기록은 없습니다.',progress:meta(0)});
          progress.open();await flushProgress();assert.equal(find('.progress-note').textContent,'이전 버전에서 수행한 상세 기록은 없습니다.');assert.equal(find('.progress-state').textContent,'');
          progress.close();api=async()=>{throw Error('연결 확인 필요');};progress.open();await flushProgress();
          assert.equal(find('.progress-state').textContent,'연결 확인 필요');assert.equal(button('다시 확인').hidden,false);
          assert.equal(progressTimers.size,0);api=async()=>page([record(1)]);button('다시 확인').onclick();await flushProgress();
          assert.equal(find('.progress-records').children.length,1);
        })()""")

    def test_old_tool_summary_is_preserved_when_a_mixed_session_has_no_run_log(self):
        self.run_case(r"""(async()=>{
          active.toolActivity=[{id:'old',runId:'old-run',tool:'Read',action:'자료 읽기',target:'old.csv',state:'completed'},
            {id:'new',runId:'run-a',tool:'Bash',action:'확인',target:'new task',state:'running'}];
          api=async(path)=>path.includes('runId=old-run')?page([],false,{progress:meta(3),notice:'이 요청은 상세 기록이 없습니다.'}):page([record(3,'new actual result')]);
          progress.reset('A',meta(3),'run-a');progress.open('old-run');await flushProgress();
          const legacy=find('.progress-legacy');assert.match(legacy.querySelector('p').textContent,/도구 요약/);
          assert.equal(legacy.querySelector('.progress-record-text').textContent,'old.csv');assert.equal(legacy.querySelector('ol').children.length,1);
          progress.open('run-a');await flushProgress();assert.equal(find('.progress-legacy').children.length,0);
          assert.equal(find('.progress-record-text').textContent,'new actual result');
        })()""")

    def test_explicit_open_reveals_only_inside_work_area_without_outer_page_scroll(self):
        self.run_case(r"""(async()=>{
          host.scrollIntoView=()=>{throw Error('Outer document must not scroll');};
          const area=$('work-area');area.scrollHeight=1500;area.clientHeight=200;area.scrollTop=0;
          area.getBoundingClientRect=()=>({top:40,bottom:240});let panelHeight=100;
          host.getBoundingClientRect=()=>({top:340-area.scrollTop,bottom:340+panelHeight-area.scrollTop});
          let focusOptions;detail.querySelector('summary').focus=options=>{focusOptions=options;};
          let reply;api=()=>new Promise(resolve=>reply=resolve);progress.open();
          assert.equal(area.scrollTop,200);assert.equal(focusOptions.preventScroll,true);
          panelHeight=160;reply(page([record(1)]));await flushProgress();assert.equal(area.scrollTop,260);
          area.scrollTop=7;progress.metadata(meta(2));api=async()=>page([record(2)]);await runProgressTimer();assert.equal(area.scrollTop,7);
          panelHeight=400;button('최신 내용 보기').onclick();await flushProgress();assert.equal(area.scrollTop,300);
        })()""")

    def test_existing_tool_openers_use_one_reader_with_request_filter(self):
        self.run_case(r"""(async()=>{
          const conversation=$('conversation');conversation.insertBefore=function(value,next){value.remove();const index=next?this.children.indexOf(next):this.children.length;value.parent=this;this.children.splice(index,0,value);};
          WorkspaceToolActivity.reset('A');WorkspaceToolActivity.restore([]);
          WorkspaceToolActivity.render({id:'tool',runId:'run-a',tool:'Read',state:'running',action:'자료 읽기'});
          api=async(path)=>{calls.push({path});return page([record(1)]);};$('tool-activity-open').onclick();await flushProgress();
          assert.match(calls[0].path,/runId=run-a/);assert.equal($('tool-activity-open').textContent,'진행 내용');
          const card=conversation.children[0];assert.equal(card.querySelector('details'),null);assert.equal(card.querySelector('ol').children.length,1);
          progress.close();card.querySelector('.tool-activity-details-open').onclick();await flushProgress();assert.equal(detail.open,true);
          $('progress-open').onclick();await flushProgress();assert.ok(!calls.at(-1).path.includes('runId='));assert.equal(host.querySelector('details'),detail);
          showHome();assert.equal(host.hidden,true);assert.equal(find('.progress-body').children.length,0);
        })()""")

    def test_malformed_or_foreign_request_records_never_replace_valid_rows(self):
        self.run_case(r"""(async()=>{
          api=async()=>page([record(1,'wrong','run-b')]);progress.open('run-a');await flushProgress();
          assert.equal(find('.progress-records').children.length,0);assert.match(find('.progress-state').textContent,/응답/);
          api=async()=>page([record(1)],true,{nextBefore:2});button('다시 확인').onclick();await flushProgress();
          assert.equal(find('.progress-records').children.length,0);assert.match(find('.progress-state').textContent,/이전 진행/);
        })()""")

    def test_module_loads_before_app_and_old_short_activity_list_is_removed(self):
        markup = (ROOT / "local_app/web/index.html").read_text(encoding="utf-8")
        self.assertLess(markup.index('src="/progress-view.js"'), markup.index('src="/app.js"'))
        self.assertEqual(1, markup.count('id="progress-view"'))
        self.assertNotIn('id="activity"', markup)


if __name__ == "__main__":
    unittest.main()
