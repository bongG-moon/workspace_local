from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_frontend_state import HARNESS as BASE, NODE

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE.replace('const scenario=process.argv[3];', '''
context.flush = () => new Promise(resolve => setImmediate(resolve));
context.flatText = function flatText(node){return [node.textContent||'',...(node.children||[]).map(flatText)].join(' ');};
vm.runInContext(fs.readFileSync(process.argv[4],'utf8'),context,{filename:'customizations.js'});
const scenario=process.argv[3];
''')
SETUP = r'''
active={id:'A',title:'A',workspace:'C:/fixture/A',state:'running',trusted:true};sessions=[active];
const catalog=()=>({scope:'local',settingsPath:'C:/fixture/A/.claude/settings.local.json',revision:'revision-A',
  notice:'설정 상태',skills:[{name:'report',description:'Report',source:'user',visibility:'on',override:null,locked:false}],
  plugins:[{name:'format@test',enabled:false,locked:false}]});
let calls=[];api=async(path,body)=>{calls.push({path,body});if(path.startsWith('/api/session'))return active;
  if(body)return {ok:true,notice:'저장 완료 · 재시작 후 적용',catalog:catalog()};return catalog();};
'''


@unittest.skipUnless(NODE, 'Node.js required')
class CustomizationsFrontendTests(unittest.TestCase):
    def run_case(self, case):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/app.js'),SETUP+case,
                               str(ROOT/'local_app/web/customizations.js')],input=HARNESS,
                              text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_skills_command_opens_without_enqueue_connect_or_send(self):
        self.run_case(r'''(async()=>{
          let enqueued=0;globalThis.WorkspaceWorkflow={isSubmitting:()=>false,send:()=>enqueued++};
          $('prompt').value='/skills';attachments=['C:/fixture/A/input.csv'];await submit();
          assert.equal(enqueued,0);assert.equal(calls.length,1);assert.ok(calls[0].path.startsWith('/api/customizations?'));
          assert.equal($('customizations-dialog').open,true);assert.equal($('prompt').value,'');assert.equal(attachments.length,1);
          assert.ok(flatText($('customizations-list')).includes('report'));
        })()''')

    def test_change_is_scoped_to_displayed_task_and_preserves_running_request(self):
        self.run_case(r'''(async()=>{
          await WorkspaceCustomizations.open('skills');
          const selector=$('customizations-list').children[0].children[1];selector.value='off';await selector.onchange();
          const writes=calls.filter(call=>call.body);assert.equal(writes.length,1);
          assert.equal(writes[0].path,'/api/customizations');assert.equal(writes[0].body.id,'A');
          assert.equal(writes[0].body.scope,'local');assert.equal(writes[0].body.value,'off');
          assert.equal(active.state,'running');assert.match($('customizations-status').textContent,/저장 완료/);
        })()''')

    def test_scope_response_cannot_replace_newer_scope(self):
        self.run_case(r'''(async()=>{
          let resolveOld;api=async(path)=>path.includes('scope=user')?{...catalog(),settingsPath:'user settings'}:new Promise(resolve=>resolveOld=resolve);
          const old=WorkspaceCustomizations.open();$('customizations-scope').value='user';$('customizations-scope').onchange();await flush();
          resolveOld(catalog());await old;assert.equal($('customizations-path').textContent,'user settings');
        })()''')

    def test_large_catalog_has_bounded_initial_dom_and_locked_rows(self):
        self.run_case(r'''(async()=>{
          api=async()=>({...catalog(),skills:Array.from({length:350},(_,i)=>({name:'skill-'+i,visibility:'off',locked:i===0,reason:'Managed'}))});
          await WorkspaceCustomizations.open();assert.equal($('customizations-list').children.length,101);
          assert.equal($('customizations-list').children[0].children[1].disabled,true);
          $('customizations-list').children[100].onclick();assert.equal($('customizations-list').children.length,201);
        })()''')

    def test_plugin_failure_is_visible_without_success_or_retry_loop(self):
        self.run_case(r'''(async()=>{
          await WorkspaceCustomizations.open('plugins');
          api=async(path,body)=>{calls.push({path,body});if(body)throw Error('CLI 거절');return active;};
          const selector=$('customizations-list').children[0].children[1];selector.value='on';await selector.onchange();
          assert.match($('customizations-status').textContent,/CLI 거절/);
          assert.equal(calls.filter(call=>call.body).length,1);assert.equal($('customizations-refresh').disabled,false);
        })()''')
