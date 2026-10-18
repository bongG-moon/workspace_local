"""Motion belongs to committed navigation, not streamed content or delayed replies."""
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS, NODE, ROOT


@unittest.skipUnless(NODE, 'Node.js is required')
class WorkspaceMotionTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run([NODE, '-', str(ROOT / 'local_app/web/app.js'), script],
                                input=HARNESS, text=True, encoding='utf-8',
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_stream_and_status_updates_do_not_restart_navigation_effect(self):
        self.run_case(r"""(()=>{
          active={id:'A',state:'running'};sessions=[{...active}];
          markViewTransition('task-view');const marker=$('task-view').dataset.motionStep;
          for(let i=0;i<100;i++)renderDelta({messageId:'answer',index:0,text:'part '});
          handleEvent({type:'status',data:{state:'running'}});
          assert.equal($('task-view').dataset.motionStep,marker);
          assert.equal(streaming.size,1);
          assert.equal(streaming.get('answer:0').querySelector('.message-body').textContent,'part '.repeat(100));
        })()""")

    def test_popup_close_and_focus_never_wait_for_motion(self):
        self.run_case(r"""(()=>{
          showDialog('settings-dialog');$('settings-close').focus();
          assert.equal($('settings-dialog').open,true);
          assert.equal(document.activeElement,$('settings-close'));
          $('settings-close').onclick();assert.equal($('settings-dialog').open,false);
        })()""")

    def test_failed_or_stale_selection_does_not_restart_current_screen(self):
        self.run_case(r"""(async()=>{
          active={id:'A',state:'idle',messages:[]};sessions=[{...active}];poll=()=>{};
          markViewTransition('task-view');const marker=$('task-view').dataset.motionStep;
          api=async()=>{throw Error('unavailable');};
          await assert.rejects(selectSession('B'),/unavailable/);
          assert.equal($('task-view').dataset.motionStep,marker);assert.equal(active.id,'A');
          let reply;api=()=>new Promise(resolve=>reply=resolve);
          const pending=selectSession('B');selectionGeneration++;
          reply({id:'B',state:'idle',messages:[]});assert.equal(await pending,false);
          assert.equal($('task-view').dataset.motionStep,marker);assert.equal(active.id,'A');
        })()""")

    def test_navigation_keeps_drafts_and_only_marks_the_destination(self):
        self.run_case(r"""(async()=>{
          const a={id:'A',title:'A',kind:'work',workspace:'C:/A',state:'idle',messages:[],requests:[]};
          const b={...a,id:'B',title:'B',workspace:'C:/B'};
          active=a;sessions=[a,b];poll=()=>{};refreshInspector=()=>{};
          api=async path=>path.includes('id=B')?b:a;
          $('prompt').value='A draft';attachments=['C:/A/file.txt'];
          await selectSession('B');const first=$('task-view').dataset.motionStep;
          $('prompt').value='B draft';await selectSession('A');
          assert.notEqual($('task-view').dataset.motionStep,first);
          assert.equal($('prompt').value,'A draft');assert.deepEqual(attachments,['C:/A/file.txt']);
          showHome();const home=$('welcome').dataset.motionStep;
          await switchMode('chat');assert.notEqual($('welcome').dataset.motionStep,home);
          assert.equal(currentMode,'chat');assert.equal($('welcome').hidden,false);
        })()""")
