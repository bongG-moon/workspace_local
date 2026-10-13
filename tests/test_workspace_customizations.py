import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from local_app.customizations import Customizations
from local_app.skill_inventory import discover_native_metadata


class CustomizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'profile'
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.file = self.config / 'skills/example/SKILL.md'
        self.file.parent.mkdir(parents=True)
        self.file.write_text('---\nname: example\ndescription: Example skill\n---\nOriginal body', encoding='utf-8')
        self.runner = Mock()
        self.manager = Customizations(self.config, ['claude'], {'version': '2.1.296'}, runner=self.runner)
        self.manager.policy = Mock(return_value=[])

    def change(self, value, *, kind='skills', name='example', scope='local', revision=None):
        data = self.manager.snapshot(str(self.workspace), scope)
        return self.manager.change(str(self.workspace), dict(kind=kind,name=name,value=value,scope=scope,
                                    revision=revision or data['revision']))

    def test_off_can_be_reenabled_without_copying_or_editing_skill(self):
        original = self.file.read_bytes()
        result = self.change('off')
        self.assertTrue(result['reconnectRequired'])
        self.assertEqual([], discover_native_metadata(self.config, str(self.workspace))['skills'])
        self.assertEqual('off', self.manager.snapshot(str(self.workspace))['skills'][0]['visibility'])
        self.change('user-invocable-only')
        self.assertTrue(discover_native_metadata(self.config, str(self.workspace))['skills'][0]['explicitOnly'])
        self.change(None)
        self.assertEqual('on', self.manager.snapshot(str(self.workspace))['skills'][0]['visibility'])
        self.assertEqual(original, self.file.read_bytes())
        self.runner.assert_not_called()
        self.assertFalse((self.workspace / '.claude/skills').exists())

    def test_scope_and_other_settings_are_preserved(self):
        target = self.config / 'settings.json'
        target.write_text(json.dumps({'model':'keep', 'env':{'TEST':'keep'},'skillOverrides':{'other':'off'}}))
        self.change('off',scope='user')
        data=json.loads(target.read_text())
        self.assertEqual('keep',data['model'])
        self.assertEqual({'TEST':'keep'},data['env'])
        self.assertEqual('off',data['skillOverrides']['other'])
        self.assertFalse((self.workspace / '.claude/settings.local.json').exists())

    def test_external_edit_and_unknown_or_managed_skill_are_rejected(self):
        old=self.manager.snapshot(str(self.workspace))['revision']
        self.change('off')
        with self.assertRaises(ValueError):self.change('on', revision=old)
        with self.assertRaises(ValueError):self.change('on', name='../outside')
        self.manager.policy.return_value=[{'skillOverrides':{'example':'off'}}]
        with self.assertRaises(ValueError):self.change('on')

    def test_frontmatter_restriction_and_older_cli_are_locked(self):
        self.file.write_text('---\nname: example\ndisable-model-invocation: true\n---\nBody')
        self.assertTrue(self.manager.snapshot(str(self.workspace))['skills'][0]['locked'])
        with self.assertRaises(ValueError):self.change('on')
        self.file.write_text('---\nname: example\n---\nBody')
        self.manager.info={'version':'2.1.100'}
        self.assertTrue(self.manager.snapshot(str(self.workspace))['skills'][0]['locked'])

    def plugin(self):
        path=self.config/'plugins/installed_plugins.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'plugins':{'example@test':[{'scope':'user','installPath':str(self.root/'plugin')}]}}))
        return self.workspace/'.claude/settings.local.json'

    def test_disabled_plugin_listed_and_official_cli_receives_exact_scope(self):
        target=self.plugin()
        self.assertFalse(self.manager.snapshot(str(self.workspace))['plugins'][0]['enabled'])
        def run(args, **kwargs):
            self.assertEqual(['claude','plugin','enable','example@test','--scope','local'],args)
            self.assertEqual(str(self.workspace),kwargs['cwd'])
            self.assertLessEqual(kwargs['timeout'],30)
            target.parent.mkdir()
            target.write_text(json.dumps({'enabledPlugins':{'example@test':True}}))
            return subprocess.CompletedProcess(args,0,'','')
        self.runner.side_effect=run
        self.assertTrue(self.change(True, kind='plugins',name='example@test')['ok'])
        self.assertEqual(1,self.runner.call_count)

    def test_cli_failure_or_unverified_profile_is_not_reported_as_success(self):
        self.plugin()
        for code in [1,0]:
            self.runner.return_value=subprocess.CompletedProcess([],code,'','secret response')
            with self.assertRaises(ValueError) as caught:self.change(True,kind='plugins',name='example@test')
            self.assertNotIn('secret response',str(caught.exception))

    def test_project_plugin_override_explains_user_scope_conflict(self):
        target=self.plugin();target.parent.mkdir()
        target.write_text(json.dumps({'enabledPlugins':{'example@test':False}}))
        row=self.manager.snapshot(str(self.workspace),'user')['plugins'][0]
        self.assertTrue(row['locked'])
        self.assertIn('프로젝트',row['reason'])
        with self.assertRaises(ValueError):self.change(True,kind='plugins',name='example@test',scope='user')
        self.runner.assert_not_called()

    def test_invalid_settings_not_replaced_and_overlap_does_not_spawn(self):
        path=self.config/'settings.json';path.write_text('{broken')
        with self.assertRaises(ValueError):self.change('off')
        self.assertEqual('{broken',path.read_text())
        path.unlink()
        self.manager.lock.acquire()
        try:
            with self.assertRaises(ValueError):self.change('off')
        finally:self.manager.lock.release()
        self.runner.assert_not_called()
