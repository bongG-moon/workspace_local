"""Skill/plugin writes cannot claim success when a higher scope wins."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from local_app.customizations import Customizations
from tests import test_workspace_customizations as fixtures


class CustomizationEffectTests(unittest.TestCase):
    setUp = fixtures.CustomizationTests.setUp
    change = fixtures.CustomizationTests.change
    plugin = fixtures.CustomizationTests.plugin

    def test_user_skill_toggle_is_locked_when_project_or_local_scope_wins(self):
        for filename in ('settings.json', 'settings.local.json'):
            with self.subTest(filename=filename):
                target = self.workspace / '.claude' / filename
                target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps({'skillOverrides':{'example':'off'}}))
                row = self.manager.snapshot(str(self.workspace), 'user')['skills'][0]
                self.assertEqual('off', row['visibility'])
                self.assertTrue(row['locked'])
                self.assertIn('이 프로젝트', row['reason'])
                with self.assertRaises(ValueError):
                    self.change('on', scope='user')
                self.assertFalse((self.config / 'settings.json').exists())
                self.assertEqual('off', json.loads(target.read_text())['skillOverrides']['example'])
                target.unlink()
        self.runner.assert_not_called()

    def test_other_project_skill_override_does_not_lock_requested_common_skill(self):
        target = self.workspace / '.claude/settings.local.json'
        target.parent.mkdir()
        target.write_text(json.dumps({'skillOverrides':{'other':'off'}}))
        result = self.change('off', scope='user')
        self.assertTrue(result['ok'])
        self.assertEqual('off', result['catalog']['skills'][0]['visibility'])
        self.assertEqual({'other':'off'}, json.loads(target.read_text())['skillOverrides'])

    def test_all_skill_states_survive_new_manager_and_local_reset_inherits_user_value(self):
        source = self.file.read_bytes()
        self.change('off', scope='user')
        for state in ('on', 'off', 'name-only', 'user-invocable-only'):
            with self.subTest(state=state):
                self.change(state)
                fresh = Customizations(self.config, ['claude'], {'version':'2.1.296'}, runner=self.runner)
                fresh.policy = self.manager.policy
                row = fresh.snapshot(str(self.workspace))['skills'][0]
                self.assertEqual(state, row['override'])
                self.assertEqual(state, row['visibility'])
        result = self.change(None)
        row = result['catalog']['skills'][0]
        self.assertIsNone(row['override'])
        self.assertEqual('off', row['visibility'])
        self.assertEqual(source, self.file.read_bytes())
        self.runner.assert_not_called()

    def test_new_project_override_during_common_skill_write_is_not_false_success(self):
        original_replace = os.replace
        def concurrent_override(source, destination):
            original_replace(source, destination)
            target = self.workspace / '.claude/settings.local.json'
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps({'skillOverrides':{'example':'on'}}))
        with patch('local_app.customizations.os.replace', side_effect=concurrent_override):
            with self.assertRaisesRegex(ValueError, '적용 상태'):
                self.change('off', scope='user')
        self.assertEqual('off', json.loads((self.config / 'settings.json').read_text())['skillOverrides']['example'])
        self.assertEqual('on', self.manager.snapshot(str(self.workspace))['skills'][0]['visibility'])

    def test_new_project_plugin_override_during_cli_write_is_not_false_success(self):
        project_path = self.plugin()
        def concurrent_override(args, **kwargs):
            (self.config / 'settings.json').write_text(json.dumps({'enabledPlugins':{'example@test':False}}))
            project_path.parent.mkdir(exist_ok=True)
            project_path.write_text(json.dumps({'enabledPlugins':{'example@test':True}}))
            return subprocess.CompletedProcess(args, 0, '', '')
        self.runner.side_effect = concurrent_override
        with self.assertRaisesRegex(ValueError, '적용 상태'):
            self.change(False, kind='plugins', name='example@test', scope='user')
        self.assertEqual(1, self.runner.call_count)
        self.assertTrue(self.manager.snapshot(str(self.workspace))['plugins'][0]['enabled'])

    def test_permission_rules_are_preserved_separately_from_skill_visibility(self):
        path = self.config / 'settings.json'
        permissions = {'deny':['Skill(example)', 'Read(./private/**)'], 'allow':['Read(./public/**)']}
        path.write_text(json.dumps({'permissions':permissions}))
        self.change('off', scope='user')
        self.change('on', scope='user')
        self.assertEqual(permissions, json.loads(path.read_text())['permissions'])


if __name__ == '__main__':
    unittest.main()
