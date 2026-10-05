"""Real workflow module: expanded input, edits, validation and overview text."""
import unittest

from tests import test_workspace_productivity_frontend as frontend


@unittest.skipUnless(frontend.NODE, 'Node.js is required for scheduling UI checks')
class ScheduleTimingFrontendTests(unittest.TestCase):
    def run_case(self, script, modules=()):
        frontend.WorkspaceProductivityFrontendTests.run_case(self, "(async()=>{\n" + script + "\n})();", modules)

    def test_mode_changes_show_only_relevant_time_fields(self):
        self.run_case(r'''
          WorkspaceWorkflow.openEditor('schedule');
          for(const kind of ['once','weekdays','daily','weekly','monthly','interval']) {
            $('schedule-kind').value=kind;$('schedule-kind').onchange();
            assert.equal($('schedule-once-fields').hidden,kind!=='once');
            assert.equal($('schedule-time-fields').hidden,kind==='once'||kind==='interval');
            assert.equal($('schedule-weekdays').hidden,kind!=='weekly');
            assert.equal($('schedule-month-fields').hidden,kind!=='monthly');
            assert.equal($('schedule-interval-fields').hidden,kind!=='interval');
          }
          assert.match($('schedule-preview').textContent,/30分|30분/);
        ''', ('workflow',))

    def test_interval_hours_minutes_and_window_have_bounded_preview_and_exact_payload(self):
        self.run_case(r'''
          const calls=[];api=async(path,body)=>{calls.push(body);return {queue:[],schedules:[],revision:1};};
          WorkspaceWorkflow.openEditor('schedule');$('request-editor-text').value='existing prompt';
          $('schedule-kind').value='interval';$('schedule-interval-hours').value='8';$('schedule-interval-minutes').value='0';
          $('schedule-start-time').value='07:00';$('schedule-end-time').value='23:59';$('schedule-kind').onchange();
          assert.match($('schedule-preview').textContent,/07:00 → 15:00 → 23:00/);
          assert.match($('schedule-preview').textContent,/하루 3회/);
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(calls.length,1);assert.equal(calls[0].schedule.intervalMinutes,480);
          assert.equal(calls[0].schedule.startTime,'07:00');assert.equal(calls[0].schedule.endTime,'23:59');
          assert.equal(calls[0].schedule.time,undefined);assert.equal(calls[0].schedule.runAt,undefined);
          WorkspaceWorkflow.openEditor('schedule');$('schedule-kind').value='interval';
          $('schedule-interval-hours').value='0';$('schedule-interval-minutes').value='1';$('schedule-kind').onchange();
          assert.ok($('schedule-preview').textContent.length<140);assert.match($('schedule-preview').textContent,/하루 540회/);
        ''', ('workflow',))

    def test_invalid_interval_cannot_submit_or_discard_request(self):
        self.run_case(r'''
          const calls=[];api=async(path,body)=>{calls.push(body);return {queue:[],schedules:[]};};
          WorkspaceWorkflow.openEditor('schedule');$('request-editor-text').value='draft stays';$('schedule-kind').value='interval';
          for(const [h,m,start,end] of [['0','0','09:00','18:00'],['24','1','09:00','18:00'],['1.5','0','09:00','18:00'],['','10','09:00','18:00'],['1','0','18:00','09:00']]) {
            $('schedule-interval-hours').value=h;$('schedule-interval-minutes').value=m;$('schedule-start-time').value=start;$('schedule-end-time').value=end;
            await WorkspaceWorkflow.saveEditor({preventDefault(){}});assert.equal(calls.length,0);
            assert.equal($('request-editor-text').value,'draft stays');assert.ok($('request-editor-error').textContent);
          }
        ''', ('workflow',))

    def test_monthly_edit_retains_pause_date_and_request(self):
        self.run_case(r'''
          const calls=[];api=async(path,body)=>{calls.push(body);return {queue:[],schedules:[]};};
          const item={id:'month',kind:'monthly',text:'saved request',time:'09:30',dayOfMonth:31,enabled:false,pausedByUser:true,attachments:['data.csv']};
          WorkspaceWorkflow.openEditor('schedule',item);
          assert.equal($('schedule-month-day').value,'31');assert.match($('schedule-preview').textContent,/없는 달/);
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(calls[0].action,'schedule_update');assert.equal(calls[0].schedule.dayOfMonth,31);
          assert.equal(calls[0].schedule.enabled,false);assert.equal(calls[0].text,'saved request');assert.equal(calls[0].attachments[0],'data.csv');
        ''', ('workflow',))

    def test_changed_mode_sends_no_stale_interval_or_month_fields(self):
        self.run_case(r'''
          const calls=[];api=async(path,body)=>{calls.push(body);return {queue:[],schedules:[]};};
          WorkspaceWorkflow.openEditor('schedule',{id:'old',kind:'interval',text:'same prompt',intervalMinutes:150,startTime:'07:00',endTime:'23:59',enabled:true});
          assert.equal($('schedule-interval-hours').value,'2');assert.equal($('schedule-interval-minutes').value,'30');
          $('schedule-kind').value='weekdays';$('schedule-time').value='09:30';$('schedule-kind').onchange();
          await WorkspaceWorkflow.saveEditor({preventDefault(){}});
          assert.equal(calls[0].schedule.kind,'weekdays');assert.equal(calls[0].schedule.time,'09:30');
          for(const field of ['intervalMinutes','startTime','endTime','dayOfMonth'])assert.equal(calls[0].schedule[field],undefined);
        ''', ('workflow',))

    def test_overview_displays_extended_time_rule_without_starting_work(self):
        self.run_case(r'''
          const calls=[];api=async(path,body)=>{calls.push({path,body});return {counts:{total:2,active:2},schedules:[
            {id:'one',sessionId:'A',title:'interval task',workspaceLabel:'folder',requestSummary:'same prompt',kind:'interval',intervalMinutes:150,startTime:'09:00',endTime:'18:00',statusLabel:'예약 중',category:'active',nextRunAt:1900000000},
            {id:'two',sessionId:'A',title:'monthly task',workspaceLabel:'folder',requestSummary:'same prompt',kind:'monthly',time:'09:30',dayOfMonth:31,statusLabel:'예약 중',category:'active',nextRunAt:1900000000}]};};
          await WorkspaceWorkflow.openOverview();
          assert.match(flatText($('schedule-overview-list')),/2시간 30분마다/);
          assert.match(flatText($('schedule-overview-list')),/매월 31일 09:30/);
          assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/schedules');assert.equal(calls[0].body,undefined);
        ''', ('workflow',))
