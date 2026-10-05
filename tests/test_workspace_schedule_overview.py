"""Read-only, bounded all-task schedule overview and its UI navigation."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_app.server import LocalApp
from tests import test_workspace_productivity_frontend as frontend


class ScheduleOverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', command=['not-started'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.now = time.time()
        self.app.dispatch.queue.clock = lambda: self.now

    def task(self, title):
        work = self.root / title
        work.mkdir()
        sid = self.app.create(str(work), True, title=title)['id']
        return self.app.sessions[sid]

    def schedule(self, item, text='요청 내용', **kwargs):
        return self.app.dispatch.queue.add_schedule(item['id'], text, [], kind='once',
            run_at=self.now + 60, context=self.app.dispatch.context(item), **kwargs)

    def test_overview_never_hydrates_claims_or_changes_stored_state(self):
        first, second = self.task('분석 업무'), self.task('보고 업무')
        self.schedule(first, '  분기\n실적  정리 ' + '가' * 200)
        self.schedule(second)
        before = deepcopy(self.app.dispatch.queue.data)
        with (patch.object(self.app, 'get', side_effect=AssertionError('do not hydrate')),
             patch.object(self.app.history, 'hydrate', side_effect=AssertionError('do not hydrate')),
             patch.object(self.app.dispatch.queue, 'tick', side_effect=AssertionError('do not tick')),
             patch.object(self.app.dispatch.queue, 'claim', side_effect=AssertionError('do not claim')),
             patch.object(self.app, 'send', side_effect=AssertionError('do not send'))):
            result = self.app.dispatch.overview()
        self.assertEqual(before, self.app.dispatch.queue.data)
        self.assertEqual(2, result['counts']['total'])
        row = next(row for row in result['schedules'] if row['sessionId'] == first['id'])
        self.assertEqual('분석 업무', row['workspaceLabel'])
        self.assertEqual(120, len(row['requestSummary']))
        self.assertNotIn('\n', row['requestSummary'])
        for forbidden in ('text', 'attachments', 'context', 'messages', 'events'):
            self.assertNotIn(forbidden, row)

    def test_hidden_removing_and_missing_tasks_are_omitted(self):
        visible, hidden, removing, missing = [self.task(name) for name in ['보이는 업무', '숨긴 업무', '삭제중 업무', '없는 업무']]
        for item in (visible, hidden, removing, missing):
            self.schedule(item)
        self.app.session_visibility.set_hidden(hidden['id'], True)
        removing['_removingFromList'] = True
        self.app.sessions.pop(missing['id'])
        result = self.app.dispatch.overview()
        self.assertEqual([visible['id']], [row['sessionId'] for row in result['schedules']])
        self.assertEqual(1, result['counts']['total'])

    def test_pause_restart_and_missed_are_not_reported_as_ready(self):
        active, paused, restart, missed = [self.task(name) for name in ['정상', '중지', '재시작', '놓침']]
        self.schedule(active)
        pause_row = self.schedule(paused)
        self.app.dispatch.queue.set_schedule_enabled(paused['id'], pause_row['id'], False)
        self.schedule(restart)
        self.app.dispatch.queue.pause(restart['id'], 'restart')
        missed_row = self.schedule(missed)
        with self.app.dispatch.queue.lock:
            row = next(row for row in self.app.dispatch.queue.data['schedules'] if row['id'] == missed_row['id'])
            self.app.dispatch.queue._advance(row, self.now + 3600, 'missed')
        result = self.app.dispatch.overview()
        self.assertEqual({'total': 4, 'active': 1, 'paused': 1, 'attention': 2, 'completed': 0}, result['counts'])
        rows = {row['sessionId']: row for row in result['schedules']}
        self.assertIn('다시 열었습니다', rows[restart['id']]['waitReason'])
        self.assertEqual('놓친 실행', rows[missed['id']]['statusLabel'])
        self.assertEqual('일시 정지', rows[paused['id']]['statusLabel'])

    def test_consumed_once_pending_completed_and_uncertain_are_distinct(self):
        item = self.task('한 번 실행')
        self.schedule(item)
        queue = self.app.dispatch.queue
        self.now += 60
        queue.tick()
        result = self.app.dispatch.overview()
        self.assertEqual('실행 대기', result['schedules'][0]['statusLabel'])
        self.assertEqual(1, result['counts']['active'])
        row = queue.claim(item['id'], item, self.app.dispatch.context(item))
        queue.dispatched(row['id'], 'run-1')
        queue.observe(item['id'], 'result', {'lastRunId': 'run-1'})
        result = self.app.dispatch.overview()
        self.assertEqual('완료', result['schedules'][0]['statusLabel'])
        self.assertEqual(1, result['counts']['completed'])
        self.schedule(item, '다음 실행')
        self.now += 60
        queue.tick()
        row = queue.claim(item['id'], item, self.app.dispatch.context(item))
        queue.failed(row['id'])
        result = self.app.dispatch.overview()
        self.assertEqual(1, result['counts']['attention'])
        self.assertIn('전송 여부', next(row for row in result['schedules'] if row['category'] == 'attention')['waitReason'])

    def test_reports_approval_and_connection_capacity_wait_without_connecting(self):
        item = self.task('승인 대기')
        self.schedule(item)
        item['state'] = 'approval'
        self.assertIn('승인 또는 답변', self.app.dispatch.overview()['schedules'][0]['waitReason'])
        item['state'] = 'idle'
        with patch.object(self.app, 'connection_capacity_available', return_value=False), patch.object(self.app, 'connect', side_effect=AssertionError('do not connect')):
            self.assertIn('연결이 비기를', self.app.dispatch.overview()['schedules'][0]['waitReason'])

    def test_pausing_schedule_cannot_hide_uncertain_delivery_or_imply_running_request_stopped(self):
        item = self.task('실행중에 예약 중지')
        scheduled = self.schedule(item)
        queue = self.app.dispatch.queue
        self.now += 60
        queue.tick()
        row = queue.claim(item['id'], item, self.app.dispatch.context(item))
        queue.dispatched(row['id'], 'run-1')
        queue.set_schedule_enabled(item['id'], scheduled['id'], False)
        current = self.app.dispatch.overview()['schedules'][0]
        self.assertEqual('paused', current['category'])
        self.assertIn('이미 시작한 요청은 계속', current['waitReason'])
        queue.failed(row['id'])
        current = self.app.dispatch.overview()['schedules'][0]
        self.assertEqual('attention', current['category'])
        self.assertEqual('전송 확인 필요', current['statusLabel'])


@unittest.skipUnless(frontend.NODE, 'Node.js is required for UI checks')
class ScheduleOverviewFrontendTests(unittest.TestCase):
    def run_case(self, script, modules):
        frontend.WorkspaceProductivityFrontendTests.run_case(self, '(async()=>{' + script + '})()', modules)

    def test_home_overview_filters_pages_and_only_gets_while_open(self):
        self.run_case(r"""
          active=null;let calls=[];
          const data={revision:1,counts:{total:40,active:39,paused:1},schedules:Array.from({length:40},(_,i)=>({id:`s${i}`,sessionId:`task${i}`,title:`업무 ${i}`,workspaceLabel:'폴더',requestSummary:`요청 ${i}`,category:i===39?'paused':'active',statusLabel:'예약 중',kind:'once',runAt:1900000000,nextRunAt:1900000000}))};
          api=async(path,body)=>{calls.push({path,body});return data;};
          await WorkspaceWorkflow.refresh();assert.equal(calls.length,0);
          await WorkspaceWorkflow.openOverview();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/schedules');assert.equal(calls[0].body,undefined);
          assert.equal($('schedule-overview-list').children.length,15);
          $('schedule-overview-next').onclick();assert.match(flatText($('schedule-overview-list').children[0]),/업무 15/);
          $('schedule-overview-filter').value='paused';$('schedule-overview-filter').onchange();assert.equal($('schedule-overview-list').children.length,1);
          $('schedule-overview-search').value='없는내용';$('schedule-overview-search').oninput();assert.equal($('schedule-overview-list').children.length,0);assert.equal($('schedule-overview-empty').hidden,false);
          $('schedule-overview-dialog').close();emit($('schedule-overview-dialog'),'close');assert.equal($('schedule-overview-list').children.length,0);
          await WorkspaceWorkflow.refresh();assert.equal(calls.length,1);
        """, ('workflow',))

    def test_close_aborts_and_late_response_cannot_repopulate(self):
        self.run_case(r"""
          active=null;let reply,signal;api=(path,body,requestSignal)=>{signal=requestSignal;return new Promise(resolve=>reply=resolve);};
          const read=WorkspaceWorkflow.openOverview();$('schedule-overview-dialog').close();emit($('schedule-overview-dialog'),'close');assert.equal(signal.aborted,true);
          reply({schedules:[{title:'늦은 예약'}],counts:{total:1}});await read;assert.equal($('schedule-overview-list').children.length,0);
        """, ('workflow',))

    def test_manage_opens_existing_task_without_sending_or_resuming(self):
        self.run_case(r"""
          const calls=[];active.state='idle';
          api=async(path,body)=>{calls.push({path,body});return path==='/api/schedules'?{counts:{total:1,active:1},schedules:[{id:'reserved',sessionId:'A',title:'업무 A',requestSummary:'유지',kind:'once',runAt:1900000000,nextRunAt:1900000000,category:'active',statusLabel:'예약 중'}]}:{queue:[],schedules:[],revision:1};};
          $('prompt').value='작성하던 내용';await WorkspaceWorkflow.openOverview();
          await $('schedule-overview-list').children[0].querySelector('button').onclick();
          assert.equal($('workflow-dialog').open,true);assert.equal($('schedule-overview-dialog').open,false);assert.equal($('prompt').value,'작성하던 내용');
          assert.ok(calls.every(call=>call.body===undefined));
        """, ('workflow',))

    def test_poll_does_not_rebuild_unchanged_rows_or_duplicate_inflight_requests(self):
        self.run_case(r"""
          active=null;let calls=0,reply;api=()=>{calls++;return new Promise(resolve=>reply=resolve);};
          const first=WorkspaceWorkflow.openOverview();await WorkspaceWorkflow.refresh();assert.equal(calls,1);
          const data={revision:1,counts:{total:1,active:1},schedules:[{id:'one',sessionId:'A',title:'업무',kind:'once',runAt:1900000000,category:'active',statusLabel:'예약 중'}]};reply(data);await first;
          const row=$('schedule-overview-list').children[0];api=async()=>data;await WorkspaceWorkflow.refresh();assert.equal($('schedule-overview-list').children[0],row);
        """, ('workflow',))

    def test_overview_once_time_is_concise_and_paused_time_is_not_labelled_next(self):
        self.run_case(r"""
          active=null;const at=new Date(2030,3,5,9,12,37).getTime()/1000;
          api=async()=>({counts:{total:2,active:1,paused:1},schedules:[
            {id:'one',sessionId:'A',title:'실행 예정',kind:'once',runAt:at,nextRunAt:at,category:'active',statusLabel:'예약 중'},
            {id:'paused',sessionId:'B',title:'중지',kind:'once',runAt:at,nextRunAt:at,category:'paused',pausedByUser:true,statusLabel:'일시 정지'}]});
          await WorkspaceWorkflow.openOverview();
          const rows=$('schedule-overview-list').children,first=rows[0].querySelector('.schedule-overview-timing'),second=rows[1].querySelector('.schedule-overview-timing');
          assert.equal(first.children[0].textContent,'한 번');assert.match(first.children[1].textContent,/^다음 /);
          assert.match(second.children[1].textContent,/^예약 시각 /);assert.doesNotMatch(flatText(first),/12:37/);
          assert.equal((flatText(first).match(/2030/g)||[]).length,1);
        """, ('workflow',))

    def test_manage_another_task_uses_real_select_session_and_context_changed(self):
        self.run_case(r"""
          active.state='idle';$('prompt').value='A의 초안';attachments=['A.csv'];
          const target={id:'B',title:'업무 B',workspace:'C:/fixture/B',trusted:true,state:'idle',messages:[],requests:[],seq:0};
          sessions.push({...target});drafts.set('B',{text:'B의 초안',attachments:['B.csv']});
          const scheduled={id:'B-reserved',sessionId:'B',title:'업무 B',requestSummary:'보고',text:'보고',kind:'once',runAt:1900000000,nextRunAt:1900000000,enabled:true,pausedByUser:false,category:'active',statusLabel:'예약 중'};
          const calls=[];
          api=async(path,body)=>{calls.push({path,body});
            if(path==='/api/schedules')return {counts:{total:1,active:1},schedules:[scheduled]};
            if(path==='/api/session?id=B')return target;
            if(path.startsWith('/api/dispatch'))return {queue:[],schedules:[scheduled],revision:1};
            if(path.startsWith('/api/files'))return {files:[]};
            if(path.startsWith('/api/results'))return {artifacts:[]};
            if(path.startsWith('/api/events'))return new Promise(()=>{});
            throw new Error(`Unexpected route ${path}`);
          };
          await WorkspaceWorkflow.openOverview();await $('schedule-overview-list').children[0].querySelector('button').onclick();
          await settle();
          assert.equal(active.id,'B');assert.equal(selectionGeneration,1);
          assert.equal($('schedule-overview-dialog').open,false);assert.equal($('workflow-dialog').open,true);
          assert.match($('workflow-context').textContent,/업무 B/);assert.equal($('schedule-list').children[0].dataset.scheduleId,'B-reserved');
          assert.equal($('prompt').value,'B의 초안');assert.equal(drafts.get('A').text,'A의 초안');assert.equal(drafts.get('A').attachments[0],'A.csv');
          assert.ok(calls.every(call=>call.body===undefined));assert.ok(calls.some(call=>call.path==='/api/session?id=B'));
        """, ('workflow',))

    def test_manage_failed_or_closed_selection_cannot_open_another_modal(self):
        self.run_case(r"""
          active.state='idle';const scheduled={id:'B-reserved',sessionId:'B',title:'업무 B',kind:'once',runAt:1900000000,category:'active',statusLabel:'예약 중'};
          let reply,fail=true;api=async(path)=>{
            if(path==='/api/schedules')return {counts:{total:1},schedules:[scheduled]};
            if(path==='/api/session?id=B'){if(fail)throw new Error('업무를 찾을 수 없습니다');return new Promise(resolve=>reply=resolve);}
            if(path.startsWith('/api/dispatch'))return {queue:[],schedules:[],revision:1};
            if(path.startsWith('/api/files'))return {files:[]};if(path.startsWith('/api/results'))return {artifacts:[]};
            if(path.startsWith('/api/events'))return new Promise(()=>{});
            throw new Error(`Unexpected route ${path}`);
          };
          await WorkspaceWorkflow.openOverview();await $('schedule-overview-list').children[0].querySelector('button').onclick();
          assert.equal(active.id,'A');assert.equal($('schedule-overview-dialog').open,true);assert.notEqual($('workflow-dialog').open,true);
          assert.match($('schedule-overview-message').textContent,/업무를 찾을 수 없습니다/);
          fail=false;const pending=$('schedule-overview-list').children[0].querySelector('button').onclick();
          $('schedule-overview-dialog').close();emit($('schedule-overview-dialog'),'close');
          reply({id:'B',title:'업무 B',workspace:'C:/fixture/B',trusted:true,state:'idle',messages:[],requests:[],seq:0});
          await pending;assert.notEqual($('workflow-dialog').open,true);
        """, ('workflow',))


if __name__ == '__main__':
    unittest.main()
