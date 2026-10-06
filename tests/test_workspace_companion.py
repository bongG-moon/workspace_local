from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Optional integration input: an external Company Agent plugin source root.
# Never discover a live user's Claude installation just to run these tests.
CORE = Path(os.environ.get('COMPANY_AGENT_SOURCE', '')).expanduser().resolve() if 'COMPANY_AGENT_SOURCE' in os.environ else ROOT / 'company-agent-plugin'
CORE_REQUIRED = ('scripts/harness_cli.py', 'scripts/company_agent/workspace_api.py',
                 'scripts/company_agent/skill_registry.py')
if not all((CORE / name).is_file() for name in CORE_REQUIRED):
    if 'COMPANY_AGENT_SOURCE' in os.environ:
        raise RuntimeError('COMPANY_AGENT_SOURCE must point to a complete Company Agent plugin source root (containing scripts/harness_cli.py).')
    CORE = None
CORE_SKIP = 'Optional Company Agent integration: set COMPANY_AGENT_SOURCE to its plugin source root.'
if CORE is not None:
    sys.path.insert(0, str(CORE / 'scripts'))
    from company_agent.workspace_api import WorkspaceService, BRIEF
    from company_agent.memory import search_memory
from local_app.companion import Companion, begin_turn, observe, telemetry, course
from local_app.harness_client import HarnessClient


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


@unittest.skipUnless(CORE is not None, CORE_SKIP)
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='업무 안내 test ')
        self.base = Path(self.tmp.name)
        self.project, self.state, self.config = [self.base / name for name in ('자료 폴더', '개인 상태', '설정')]
        self.project.mkdir(); self.config.mkdir()
        self.record = {'scope': 'User', 'userStateRoot': str(self.state), 'claudeConfigRoot': str(self.config), 'coreVersion': 'test'}
        self.service = WorkspaceService(self.record, self.project, CORE)

    def tearDown(self):
        self.tmp.cleanup()

    def memory(self, **kwargs):
        return self.service.plan({'kind': 'memory', 'storageScope':'personal', 'title': '보고서 표현', 'body': '보고서는 결론부터 간단히 정리한다.', **kwargs})

    def test_snapshot_is_read_only_and_reports_skills_not_fake_performance(self):
        value = self.service.snapshot()
        self.assertFalse(self.state.exists())
        self.assertIn('html-report', [x['name'] for x in value['skills']])
        self.assertEqual([x['status'] for x in value['checks'] if x['id'] in {'office','model'}], ['unverified','unverified'])
        self.assertNotIn('knowledge', value)
        self.assertTrue(self.service.snapshot('memory')['learning']['enabled'])

    def test_memory_plan_apply_new_service_retrieval_idempotency_and_inactive(self):
        plan = self.memory()
        self.assertFalse(self.state.exists())
        with self.assertRaises(ValueError):
            self.service.dispatch({'operation':'apply','plan':plan})
        saved = self.service.apply(plan)
        self.assertTrue(Path(saved['path']).is_file())
        sha = Path(saved['path']).read_bytes()
        self.assertTrue(self.service.apply(plan)['reused'])
        self.assertEqual(sha, Path(saved['path']).read_bytes())
        new_session = WorkspaceService(self.record, self.project, CORE)
        row = new_session.memories()['items'][0]
        self.assertEqual(row['body'], '보고서는 결론부터 간단히 정리한다.')
        self.assertTrue(search_memory(self.state, '보고서'))
        off = self.memory(itemId=row['id'], expectedSha256=row['sha256'], status='inactive')
        self.service.apply(off)
        self.assertEqual(self.service.memories()['items'][0]['status'], 'inactive')
        self.assertFalse(search_memory(self.state, '보고서'))

    def test_large_korean_knowledge_is_paged_and_bodies_are_lazy(self):
        from company_agent.frontmatter import dump_frontmatter
        folder = self.state/'knowledge/entries'
        folder.mkdir(parents=True)
        for number in range(60):
            (folder/f'personal.term.{number:03}.md').write_text(dump_frontmatter(
                {'id':f'personal.term.{number:03}','kind':'term','title':f'지식 {number}','status':'active'}, '가'*12000), encoding='utf-8')
        with patch.object(self.service, 'detail', wraps=self.service.detail) as detail:
            result = self.service.snapshot('knowledge')
            self.assertEqual(20, detail.call_count)
        page = result['knowledge']
        self.assertTrue(all('body' not in row for row in page['items']))
        self.assertLess(len(json.dumps(result, ensure_ascii=True).encode()), 50000)
        ids = [row['id'] for row in page['items']]
        while page['nextCursor']:
            page = self.service.dispatch({'operation':'list','category':'knowledge','cursor':page['nextCursor']})
            ids += [row['id'] for row in page['items']]
        self.assertEqual(60, len(set(ids)))
        self.assertEqual(12000, len(self.service.dispatch({'operation':'detail','category':'knowledge',
                                                        'entryKey':page['items'][0]['entryKey']})['body']))

    def test_pages_reject_stale_cursor_other_scope_and_traversal(self):
        self.service.apply(self.memory())
        _, _, _ = self.service._record_paths('memory')
        cursor = {'offset':0,'revision':'a'*64}
        with self.assertRaises(ValueError):
            self.service.listing('memory', cursor)
        for key in ('../settings.json','memory/../CLAUDE.md','memory/C:/secret.md','company/x.md','memory/nested/x.md'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.service.detail('memory',key)

    def test_shared_and_personal_memory_filter_before_paging_and_reject_cross_owner_keys(self):
        from company_agent.frontmatter import dump_frontmatter
        company = self.base / 'company-knowledge'
        personal = self.state / 'knowledge/entries'
        for folder, prefix in ((company, 'company'), (personal, 'personal')):
            folder.mkdir(parents=True)
            for i in range(25):
                (folder/f'{prefix}.term.{i:02}.md').write_text(dump_frontmatter(
                    {'id': f'{prefix}.term.{i:02}', 'kind': 'term', 'status': 'active', 'title': f'{prefix} {i}'},
                    '가상 업무 지식'), encoding='utf-8')
        service = WorkspaceService({**self.record, 'knowledgeBaseRoot':str(company)}, self.project, CORE)
        with patch('company_agent.skill_registry.inventory_skills', side_effect=AssertionError('memory does not scan skills')):
            shared = service.snapshot('shared-memory')
            mine = service.snapshot('my-memory')
        for view, category, owner in ((shared, 'shared-knowledge', 'company'), (mine, 'personal-knowledge', 'personal')):
            page = view['knowledge']
            self.assertEqual(20, len(page['items']))
            self.assertTrue(all(row['ownership'] == owner and 'body' not in row for row in page['items']))
            last = service.dispatch({'operation':'list','category':category,'cursor':page['nextCursor']})
            self.assertEqual(5, len(last['items']))
            self.assertIsNone(last['nextCursor'])
            self.assertEqual(25, len({row['id'] for row in page['items'] + last['items']}))
        with self.assertRaises(ValueError):
            service.listing('personal-knowledge', shared['knowledge']['nextCursor'])
        for category,key in [('personal-knowledge','company/company.term.00.md'),
                             ('shared-knowledge','entries/personal.term.00.md')]:
            with self.subTest(category=category), self.assertRaises(ValueError):
                service.detail(category,key)
        before = {p.name:p.read_bytes() for p in company.glob('*.md')}
        with self.assertRaises(ValueError):
            service.plan({'kind':'shared-memory','title':'edit common','body':'not allowed'})
        self.assertEqual(before, {p.name:p.read_bytes() for p in company.glob('*.md')})
        self.assertNotIn('policy', shared)
        self.assertIn('learning', mine)

    def test_four_views_are_read_only_and_do_not_create_or_migrate_personal_state(self):
        for view in ('shared-memory','my-memory','shared-harness','my-harness'):
            with self.subTest(view=view):
                result = self.service.snapshot(view)
                self.assertEqual(self.service.scope(), result['scope'])
                self.assertFalse(self.state.exists())
        self.assertIn('설치', self.service.scope()['label'])
        self.assertFalse(self.service.snapshot('shared-memory')['configured'])
        self.assertIn('PreToolUse', self.service.snapshot('shared-harness')['hooks']['events'])

    def test_harness_views_preserve_sources_and_do_not_read_tool_secrets(self):
        sources = ('company','corporate','personal','user','project','plugin')
        inv = {'skills':[{'id':s,'name':s,'source':s,'description':'예제'} for s in sources],
               'complete':True,'conflicts':[],'warnings':[]}
        write_json(self.state/'assets/registry.json', {'assets':[
            {'type':'mcp','name':'mine','status':'candidate','env':{'TOKEN':'DO-NOT-EXPOSE'},'path':'DO-NOT-OPEN'},
            {'type':'script-tool','name':'calc','status':'active','command':'DO-NOT-EXECUTE'},
            {'type':'skill','name':'already-listed','status':'active'}]})
        before = (self.state/'assets/registry.json').read_bytes()
        with patch('company_agent.skill_registry.inventory_skills',return_value=inv):
            common = self.service.snapshot('shared-harness')
            mine = self.service.snapshot('my-harness')
        self.assertEqual(['company','corporate'], [s['source'] for s in common['inventory']['items']])
        self.assertEqual(['personal','user','project','plugin'], [s['source'] for s in mine['inventory']['items']])
        self.assertEqual(['mine','calc'], [t['name'] for t in mine['tools']['items']])
        self.assertNotIn('DO-NOT-', json.dumps(mine))
        self.assertNotIn('policy', mine)
        self.assertNotIn('brief', common)
        self.assertEqual(before, (self.state/'assets/registry.json').read_bytes())
        self.assertFalse((self.project/BRIEF).exists())
        write_json(self.state/'assets/registry.json', ['malformed'])
        self.assertEqual('unavailable', self.service.personal_tools()['status'])

    def test_guide_and_usage_do_not_scan_documents_or_inventory(self):
        with patch.object(self.service,'listing',side_effect=AssertionError('no document scans')), \
                patch('company_agent.skill_registry.inventory_skills',side_effect=AssertionError('no inventory')):
            self.assertEqual(self.service.scope(), self.service.snapshot('guide')['scope'])
            self.assertEqual(self.service.scope(), self.service.snapshot('usage')['scope'])

    def test_memory_versions_restore_and_manual_edit_collision(self):
        self.service.apply(self.memory())
        old = self.service.memories()['items'][0]
        changed = self.memory(itemId=old['id'], expectedSha256=old['sha256'], body='보고서는 표를 먼저 보여준다.')
        self.service.apply(changed)
        versions = self.service.versions(old['id'])['versions']
        self.assertEqual(versions[0]['body'], old['body'])
        row = self.service.memories()['items'][0]
        restore = self.memory(itemId=row['id'], expectedSha256=row['sha256'], body=versions[0]['body'])
        self.service.apply(restore)
        row = self.service.memories()['items'][0]
        planned = self.memory(itemId=row['id'], expectedSha256=row['sha256'], body='간단히 작성한다.')
        path = self.service.memory_path(row['id'])
        path.write_text(path.read_text(encoding='utf-8') + '\n직접 편집', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '변경'):
            self.service.apply(planned)
        self.assertIn('직접 편집', path.read_text(encoding='utf-8'))

    def test_other_scope_cannot_apply_plan_and_manual_config_is_unchanged(self):
        untouched = self.config / 'settings.json'
        write_json(untouched, {'model':'existing','mcpServers':{'own':{}},'personal':'unchanged'})
        raw = untouched.read_bytes()
        other = WorkspaceService({**self.record, 'scope':'Project','projectRoot':str(self.project),'userStateRoot':str(self.base/'separate')}, self.project, CORE)
        with self.assertRaises(ValueError):
            other.apply(self.memory())
        self.assertFalse(other.memories()['items'])
        self.service.apply(self.memory())
        self.assertEqual(raw, untouched.read_bytes())

    def test_short_brief_review_existing_empty_manual_and_global_preservation(self):
        global_file = self.config / 'CLAUDE.md'
        global_file.write_text('개인 전역 기준', encoding='utf-8')
        spec = {'kind':'brief','goal':'월간 보고','inputs':'확인한 월간 표','outputs':'편집 가능한 보고서','checks':'합계와 원본 보존'}
        plan = self.service.plan(spec)
        self.service.apply(plan)
        row = self.service.brief()
        self.assertTrue(row['owned'])
        updated = self.service.plan({**spec,'outputs':'HTML 보고서','expectedSha256':row['sha256']})
        self.service.apply(updated)
        self.assertTrue(list((self.state/'workspace/brief-versions').glob('*.md')))
        (self.project/BRIEF).write_text('직접 작성한 업무 지침',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.service.plan(spec)
        (self.project/BRIEF).write_text('',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.service.plan(spec)
        self.assertEqual(global_file.read_text(encoding='utf-8'),'개인 전역 기준')

    def test_knowledge_draft_review_activation_and_local_export(self):
        spec = {'kind':'knowledge','storageScope':'personal','title':'테스트 지표','body':'목표 대비 실적 비율로 확인한다.','reference':'가상 자료 직접 검토','reviewAfter':'2027-01-01'}
        saved = self.service.apply(self.service.plan(spec))
        rows = self.service.knowledge()['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'draft')
        self.assertEqual(rows[0]['review']['reviewAfter'],'2027-01-01')
        self.service.apply(self.service.plan({**spec,'itemId':rows[0]['id'],'expectedSha256':rows[0]['sha256'],'status':'active'}))
        self.assertEqual(self.service.knowledge()['items'][0]['status'],'active')
        result = self.service.dispatch({'operation':'share','itemIds':[rows[0]['id']],'confirmed':True})
        self.assertTrue(Path(result['path']).is_file())
        self.assertEqual(result['status'],'review-candidate')
        self.assertIn('외부 전송',result['notice'])

    def test_secrets_raw_artifacts_and_traversal_not_stored(self):
        for body in ('password=not-a-real-password', '"messages": []', 'assistant: some raw turn'):
            with self.assertRaises(ValueError):
                self.memory(body=body)
        with self.assertRaises(ValueError):
            self.memory(itemId='../outside')
        self.assertFalse(self.state.exists())

    def test_company_navigation_and_bad_item_do_not_hide_valid_knowledge(self):
        from company_agent.frontmatter import dump_frontmatter
        base = self.base / 'company-knowledge'
        base.mkdir()
        (base / 'README.md').write_text('# 회사 지식 안내', encoding='utf-8')
        valid = base / 'z-term.md'
        valid.write_text(dump_frontmatter({'id':'company.term.sample','kind':'term',
            'title':'회사 용어','status':'active'}, '회사가 확인한 용어 설명'), encoding='utf-8')
        service = WorkspaceService({**self.record, 'knowledgeBaseRoot': str(base)}, self.project, CORE)
        first = service.knowledge()
        self.assertEqual([x['id'] for x in first['items']], ['company.term.sample'])
        self.assertEqual(first['warnings'], [])
        (base / 'a-broken.md').write_text('---\nmalformed header\n---\n깨진 항목', encoding='utf-8')
        second = service.knowledge()
        self.assertEqual([x['id'] for x in second['items']], ['company.term.sample'])
        self.assertEqual(len(second['warnings']), 1)
        self.assertFalse(self.state.exists())

    def test_malformed_plan_is_rejected_without_state_changes(self):
        for value in (None, [], 'not an object'):
            with self.assertRaises(ValueError):
                self.service.dispatch({'operation':'plan', 'data':value})
        self.assertFalse(self.state.exists())

    def test_learning_toggle_keeps_memory_and_does_not_write_company(self):
        self.service.apply(self.memory())
        before = self.service.memories()
        result = self.service.dispatch({'operation':'learning','enabled':False,'confirmed':True})
        self.assertFalse(result['enabled'])
        self.assertEqual(before,self.service.memories())
        with self.assertRaises(ValueError):
            self.service.dispatch({'operation':'learning','enabled':True})


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workspace-register-')
        self.root = Path(self.tmp.name)
        self.config, self.reg, self.project = [self.root/name for name in ('config','installations','work')]
        self.project.mkdir()
        self.client = HarnessClient(config=self.config, registrations=self.reg)
        self.record = {'schemaVersion':1,'scope':'User','nativeClaudeScope':'user','userStateRoot':str(self.root/'state'),
            'claudeConfigRoot':str(self.config),'claudeConfigDirOverride':bool(os.environ.get('CLAUDE_CONFIG_DIR')),
            'coreVersion':'test','pythonCommand':sys.executable,'pluginId':'company-agent@company-agent-local'}
        write_json(self.reg/'user/company-agent-install.json',self.record)
        self.plugin = CORE or self.root / 'fixture-core'
        if CORE is None:
            # Locator tests only need existing paths; no fake core is executed.
            for relative in ('scripts/harness_cli.py', 'scripts/company_agent/workspace_api.py'):
                file = self.plugin / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('# Locator fixture only; not executable core.\n', encoding='utf-8')
        self.entry = {'scope':'user','version':'test','installPath':str(self.plugin)}
        write_json(self.config/'plugins/installed_plugins.json',{'plugins':{'company-agent@company-agent-local':[self.entry]}})
        write_json(self.config/'settings.json',{'enabledPlugins':{'company-agent@company-agent-local':True}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_installed_executable_and_core_selected_not_other_profile(self):
        command=self.client.locate(self.project)
        self.assertEqual(command[0],sys.executable)
        self.assertEqual(command[-1],str(self.project))
        self.assertIn('workspace',command)
        self.assertFalse(any('claude.exe' in x for x in command))

    def test_disabled_wrong_version_missing_registration_fail_without_fallback(self):
        write_json(self.config/'settings.json',{'enabledPlugins':{}})
        with self.assertRaises(ValueError):self.client.locate(self.project)
        write_json(self.config/'settings.json',{'enabledPlugins':{'company-agent@company-agent-local':True}})
        write_json(self.config/'plugins/installed_plugins.json',{'plugins':{'company-agent@company-agent-local':[{**self.entry,'version':'old'}]}})
        with self.assertRaises(ValueError):self.client.locate(self.project)

    def test_project_over_user_scope_and_subdirectory(self):
        local={**self.record,'scope':'Project','nativeClaudeScope':'local','projectRoot':str(self.project),'userStateRoot':str(self.root/'project-state')}
        write_json(self.reg/'projects/one/company-agent-install.json',local)
        write_json(self.project/'.claude/settings.local.json',{'enabledPlugins':{'company-agent@company-agent-local':True}})
        local_entry={**self.entry,'scope':'local','projectPath':str(self.project)}
        write_json(self.config/'plugins/installed_plugins.json',{'plugins':{'company-agent@company-agent-local':[self.entry,local_entry]}})
        sub=self.project/'하위 폴더';sub.mkdir()
        self.assertEqual(self.client.locate(sub)[-1],str(sub))
        # A duplicate equal-priority installation is not chosen arbitrarily.
        write_json(self.reg/'projects/two/company-agent-install.json',local)
        with self.assertRaisesRegex(ValueError,'겹칩니다'):self.client.locate(sub)

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_local_core_subprocess_roundtrip_uses_authoritative_registration(self):
        # Core uses the normal per-user registration root independently of the UI.
        registrations=self.root/'CompanyAgent/installations'
        write_json(registrations/'user/company-agent-install.json',self.record)
        client=HarnessClient(config=self.config,registrations=registrations)
        self.record['claudeConfigDirOverride']=True
        write_json(registrations/'user/company-agent-install.json',self.record)
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root),'CLAUDE_CONFIG_DIR':str(self.config)}):
            snap=client.call(self.project,{'operation':'snapshot'})
            self.assertEqual(snap['scope']['stateRoot'],str(self.root/'state'))
            plan=client.call(self.project,{'operation':'plan','data':{'kind':'memory','storageScope':'personal','title':'간단히','body':'결론 먼저'}})
            result=client.call(self.project,{'operation':'apply','confirmed':True,'plan':plan})
            self.assertTrue(Path(result['path']).exists())


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.item={'id':'s1','workspace':str(self.root/'work'),'state':'starting','connection':{'model':'HCP-Unknown'}}
        self.item['workspace']=str(self.root)
        self.companion=Companion(self.root/'ui',demo=True)

    def tearDown(self):self.tmp.cleanup()

    def test_course_and_progress_do_not_execute_cli_or_store_prompts(self):
        with patch('local_app.harness_client.run_owned',side_effect=AssertionError('must not call')):
            self.assertEqual([x['id'] for x in course()['steps']],['read','report','revise','remember','reuse'])
            self.companion.action(self.item,{'action':'progress','step':'read','checked':True})
            snap=self.companion.snapshot(self.item)
        self.assertTrue(snap['records']['steps']['read']['checked'])
        stored=json.dumps(snap['records'])
        self.assertNotIn('prompt',stored)
        self.assertEqual(snap['assessment']['total'],0)

    def test_guide_and_usage_do_not_launch_core_process(self):
        self.companion.demo = False
        with patch.object(self.companion.client,'call',side_effect=AssertionError('no subprocess')):
            for view in ('guide','usage'):
                self.assertIn('course',self.companion.snapshot(self.item,view))

    def test_four_domain_views_are_forwarded_without_model_calls(self):
        self.companion.demo = False
        with patch.object(self.companion.client,'call',return_value={'scope':{'id':'test','kind':'User'}}) as call:
            for view in ('shared-memory','my-memory','shared-harness','my-harness'):
                self.assertIn('harness',self.companion.snapshot(self.item,view))
                call.assert_called_with(self.item['workspace'],{'operation':'snapshot','view':view})

    def test_old_core_shows_update_hint_without_install_or_retry(self):
        self.companion.demo = False
        with patch.object(self.companion.client,'call',side_effect=ValueError('지원하지 않는 관리 화면입니다.')) as call:
            result = self.companion.snapshot(self.item,'my-memory')
        self.assertIn('같은 배포본으로 업데이트',result['unavailable'])
        self.assertNotIn('harness',result)
        self.assertEqual(1,call.call_count)

    def test_missing_fields_duplicate_result_and_tool_id_are_not_totals(self):
        begin_turn(self.item)
        observe(self.item,'activity',{'tool':'Skill','id':'t1','skill':'company-agent:html-report'})
        observe(self.item,'activity',{'tool':'Skill','id':'t1','skill':'company-agent:html-report'})
        self.item['state']='done'
        result={'usage':{'input_tokens':7},'costUsd':None,'durationMs':150}
        observe(self.item,'result',result)
        observe(self.item,'result',{'usage':{'input_tokens':999},'costUsd':40})
        t=telemetry(self.item)
        self.assertEqual(t['tokens']['input_tokens'],7)
        self.assertIsNone(t['tokens']['output_tokens'])
        self.assertIsNone(t['cliReportedCostUsd'])
        self.assertEqual(t['tools'],{'Skill':1})
        begin_turn(self.item)
        self.assertIsNone(telemetry(self.item)['tokens']['input_tokens'])

    def test_user_wait_is_separate_not_pure_model_time(self):
        with patch('local_app.companion.time.monotonic',return_value=10):begin_turn(self.item)
        with patch('local_app.companion.time.monotonic',return_value=12):
            self.item['state']='approval';observe(self.item,'request',{})
        with patch('local_app.companion.time.monotonic',return_value=17):
            self.item['state']='running';observe(self.item,'request_closed',{})
        with patch('local_app.companion.time.monotonic',return_value=20):
            self.item['state']='done';observe(self.item,'result',{})
        t=telemetry(self.item)
        self.assertEqual(t['wallMs'],10000)
        self.assertEqual(t['phaseMs']['approval'],5000)
        self.assertEqual(t['phaseMs']['starting'],2000)

    def test_user_assessments_include_nonpassing_denominators_and_idempotency(self):
        begin_turn(self.item)
        req={'action':'outcome','confirmed':True,'requestId':self.item['observation']['id'],'workflow':'read','status':'passed','checks':[]}
        with self.assertRaises(ValueError):self.companion.action(self.item,req)
        self.item['state']='done';observe(self.item,'result',{})
        with self.assertRaises(ValueError):self.companion.action(self.item,req)
        req['checks']=['content','scope']
        self.companion.action(self.item,req);self.companion.action(self.item,req)
        self.assertEqual(len(self.companion.records(self.item['workspace'])['outcomes']),1)
        begin_turn(self.item);self.item['state']='stopped';observe(self.item,'status',{'state':'stopped'})
        req.update(requestId=self.item['observation']['id'],status='passed')
        with self.assertRaises(ValueError):self.companion.action(self.item,req)
        req.update(status='cancelled')
        self.companion.action(self.item,req)
        a=self.companion.snapshot(self.item)['assessment']
        self.assertEqual(a['total'],2)
        self.assertEqual(a['counts'],{'passed':1,'cancelled':1})

    def test_demo_cannot_mutate_real_harness_or_import_logs(self):
        for action in ('plan','apply','learning','rollback','usage','share'):
            with self.assertRaises(ValueError):self.companion.action(self.item,{'action':action})

    def test_alert_is_explicit_next_request_and_not_a_cost_or_hard_limit(self):
        self.companion.action(self.item,{'action':'budget','tokenAlert':50})
        begin_turn(self.item);self.item['state']='done'
        result={'usage':{'input_tokens':60},'costUsd':None}
        observe(self.item,'result',result)
        self.assertIn('강제 예산',result['budgetWarning'])
        self.assertIsNone(telemetry(self.item)['cliReportedCostUsd'])

    def test_export_is_minimal_and_clear_does_not_touch_memory(self):
        self.companion.action(self.item,{'action':'progress','step':'read','checked':True})
        begin_turn(self.item);self.item['state']='error';observe(self.item,'error',{})
        self.companion.action(self.item,{'action':'outcome','confirmed':True,'requestId':self.item['observation']['id'],'workflow':'read','status':'failed'})
        exported=self.companion.action(self.item,{'action':'records-export','confirmed':True})
        self.assertNotIn(str(self.root),json.dumps(exported));self.assertNotIn('HCP-Unknown',json.dumps(exported))
        original=self.root/'preserve.md';original.write_text('개인 기억 그대로',encoding='utf-8')
        self.companion.action(self.item,{'action':'records-clear','confirmed':True})
        self.assertEqual(self.companion.records(self.item['workspace'])['outcomes'],[])
        self.assertEqual(original.read_text(encoding='utf-8'),'개인 기억 그대로')

    def test_canonical_guide_and_shared_course_preserve_beginner_workflow(self):
        source = ROOT / 'company-agent-plugin/resources/onboarding-course.json'
        expected = json.loads(source.read_text(encoding='utf-8'))
        with patch.object(self.companion.client, 'call', side_effect=AssertionError('no model call')):
            self.assertEqual(expected, course())
            self.assertEqual(expected, self.companion.snapshot(self.item, 'guide')['course'])
        self.assertEqual(['read', 'report', 'revise', 'remember', 'reuse'], [step['id'] for step in expected['steps']])
        for step in expected['steps']:
            for field in ('prompt', 'concept', 'check', 'recovery'):
                self.assertTrue(step[field].strip(), (step['id'], field))
        self.assertIn('실습_가상자료.md', expected['steps'][0]['prompt'])
        self.assertIn('alternativePrompt', expected['steps'][0])
        canonical = (ROOT / 'docs/Company-Agent-사용자-안내서.html').read_bytes()
        page = canonical.decode('utf-8')
        for section in ('onboarding', 'basics', 'usage', 'handbook', 'commands'):
            self.assertIn('id="' + section + '"', page)
        self.assertIn('실습_가상자료.md', page)
        self.assertIn('사용자 안내서', page)
        self.assertNotIn('<script', page.lower())

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_external_core_manual_matches_bundled_guide(self):
        canonical = (ROOT / 'docs/Company-Agent-사용자-안내서.html').read_bytes()
        self.assertEqual(canonical, (CORE / 'resources/manuals/Company-Agent-사용자-안내서.html').read_bytes())

    def test_pending_plan_bound_to_session_and_immutable_duplicate_apply(self):
        from unittest.mock import Mock
        client=Mock();client.call.side_effect=[{'schemaVersion':1,'spec':{'title':'test'}},{'ok':True}]
        cp=Companion(self.root/'real',client=client)
        plan=cp.action(self.item,{'action':'plan','data':{'kind':'memory'}})
        with self.assertRaises(ValueError):cp.action({**self.item,'id':'s2'},{'action':'apply','token':plan['token'],'confirmed':True})
        req={'action':'apply','token':plan['token'],'confirmed':True,'plan':{'spec':'evil'}}
        self.assertTrue(cp.action(self.item,req)['ok']);self.assertTrue(cp.action(self.item,req)['ok'])
        self.assertEqual(client.call.call_count,2)
        self.assertEqual(client.call.call_args.args[1]['plan']['spec'],{'title':'test'})


if __name__ == '__main__':unittest.main()
