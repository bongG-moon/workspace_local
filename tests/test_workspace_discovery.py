"""Native metadata discovery is independent of core execution and workspace trust."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_app.harness_client import HarnessClient, InstallationError
from local_app.skill_inventory import MAX_HEADER_BYTES, discover_native_metadata


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workspace-native-metadata-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.reg = self.root / 'registrations'
        self.client = HarnessClient(config=self.config, registrations=self.reg)
        self.plugin = self.root / 'plugin'
        self.plugin_id = 'fixture@local'
        self.entry = {'scope': 'user', 'version': '1', 'installPath': str(self.plugin)}
        self.skill(self.config / 'skills/shared/SKILL.md', 'shared')
        self.skill(self.project / '.claude/skills/shared/SKILL.md', 'shared')
        self.skill(self.plugin / 'skills/shared/SKILL.md', 'shared')
        write(self.plugin / '.claude-plugin/plugin.json', {'name': 'fixture'})
        write(self.config / 'settings.json', {'enabledPlugins': {self.plugin_id: True}, 'env': {'SECRET': 'DO-NOT-RETURN'}})
        self.installed([self.entry])

    def skill(self, path, name, extra=''):
        write(path, f'---\nname: {name}\ndescription: safe description\n{extra}---\nSECRET-INSTRUCTION-BODY')

    def installed(self, entries):
        write(self.config / 'plugins/installed_plugins.json', {'plugins': {self.plugin_id: entries}})

    def register(self, **changes):
        self.plugin_id = 'company-agent@company-agent-local'
        write(self.config / 'settings.json', {'enabledPlugins': {self.plugin_id: True}})
        self.entry = {**self.entry, 'installPath': str(self.plugin)}
        self.installed([self.entry])
        write(self.plugin / 'scripts/harness_cli.py', "raise RuntimeError('MUST-NOT-EXECUTE')")
        write(self.plugin / 'scripts/company_agent/workspace_api.py', "raise RuntimeError('MUST-NOT-IMPORT')")
        self.record = {'schemaVersion': 1, 'scope': 'User', 'nativeClaudeScope': 'user',
                       'claudeConfigRoot': str(self.config), 'userStateRoot': str(self.root / 'state'),
                       'knowledgeBaseRoot': str(self.root / 'knowledge'), 'coreVersion': '1',
                       'claudeConfigDirOverride': bool(os.environ.get('CLAUDE_CONFIG_DIR')),
                       'pythonCommand': sys.executable, 'pluginId': self.plugin_id, **changes}
        target = self.reg / ('user' if self.record['scope'] == 'User' else 'projects/fixture') / 'company-agent-install.json'
        write(target, self.record)
        return target

    def test_common_works_without_registration_and_never_executes_or_returns_bodies(self):
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()}
        with patch('local_app.harness_client.subprocess.run', side_effect=AssertionError('must not execute')):
            result = self.client.discovery_inventory()
        self.assertEqual({'user', 'plugin'}, {row['source'] for row in result['skills']})
        self.assertEqual('registration_missing', result['diagnostics']['code'])
        self.assertFalse(result['skillsLimited'])
        self.assertEqual({'configRootSource': 'explicit', 'actualCliContextVerified': False, 'scope': 'common'}, result['context'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(before, {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.rglob('*') if path.is_file()})

    def test_completion_inventory_includes_legacy_commands_without_executing_bodies(self):
        write(self.config / 'commands/skill-doctor.md', '---\nname: ignored-name\ndescription: Diagnose skills\n---\n!`dangerous-command`')
        write(self.project / '.claude/commands/skill-local.md', '---\ndescription: Local command\n---\nPRIVATE BODY')
        write(self.plugin / 'commands/skills.md', '---\ndescription: Plugin skills\n---\nPRIVATE BODY')
        write(self.plugin / 'extra/special.md', '---\ndescription: Extra plugin command\n---\nPRIVATE BODY')
        write(self.plugin / '.claude-plugin/plugin.json', {'name': 'fixture', 'commands': ['./extra/special.md']})
        with patch('local_app.harness_client.subprocess.run', side_effect=AssertionError('must not execute')), \
                patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            common = self.client.completion_inventory()
            project = self.client.completion_inventory(self.project)
        common_names = {row['invocation'] for row in common['skills']}
        self.assertTrue({'skill-doctor', 'fixture:skills', 'fixture:special'} <= common_names)
        self.assertNotIn('ignored-name', common_names)
        self.assertNotIn('skill-local', common_names)
        self.assertIn('skill-local', {row['invocation'] for row in project['skills']})
        self.assertNotIn('PRIVATE', json.dumps(project))
        self.assertNotIn('dangerous-command', json.dumps(project))
        # The catalog's existing skill-only contract remains unchanged.
        self.assertNotIn('skill-doctor', {row['invocation'] for row in self.client.discovery_inventory()['skills']})

    def test_folder_keeps_distinct_candidates_and_common_does_not_follow_last_folder(self):
        result = self.client.discovery_inventory(self.project)
        self.assertEqual(3, len(result['skills']))
        self.assertEqual(3, len({row['candidateId'] for row in result['skills']}))
        self.assertEqual({'user', 'project', 'plugin'}, {row['source'] for row in result['skills']})
        repeated = self.client.discovery_inventory(self.project)
        self.assertEqual([row['candidateId'] for row in result['skills']], [row['candidateId'] for row in repeated['skills']])
        self.assertEqual({'user', 'plugin'}, {row['source'] for row in self.client.discovery_inventory()['skills']})

    def test_nested_legacy_command_names_preserve_colon_namespace(self):
        write(self.config / 'commands/sc/task.md', '---\nname: ignored\ndescription: Scoped task\n---\nPRIVATE BODY')
        write(self.config / 'commands/sc/estimate.md', '---\ndescription: Estimate\n---\nPRIVATE BODY')
        write(self.config / 'commands/sc/spawn.md', '---\ndescription: Spawn\n---\nPRIVATE BODY')
        write(self.config / 'commands/task.md', '---\ndescription: Plain task\n---\nPRIVATE BODY')
        write(self.project / '.claude/commands/team/review/task.md', '---\ndescription: Team task\n---\nPRIVATE BODY')
        write(self.plugin / 'commands/team/task.md', '---\ndescription: Plugin task\n---\nPRIVATE BODY')
        common = self.client.completion_inventory()
        names = {row['invocation'] for row in common['skills']}
        self.assertTrue({'sc:task', 'sc:estimate', 'sc:spawn', 'task', 'fixture:team:task'} <= names)
        self.assertFalse({'estimate', 'spawn', 'ignored', 'team:review:task'} & names)
        folder_names = {row['invocation'] for row in self.client.completion_inventory(self.project)['skills']}
        self.assertIn('team:review:task', folder_names)
        plugin_row = next(row for row in common['skills'] if row['invocation'] == 'fixture:team:task')
        self.assertEqual('fixture', plugin_row['pluginNamespace'])
        self.assertNotIn('PRIVATE', json.dumps(common))

    def test_local_false_overrides_project_true_and_user_true(self):
        write(self.project / '.claude/settings.json', {'enabledPlugins': {self.plugin_id: True}})
        write(self.project / '.claude/settings.local.json', {'enabledPlugins': {self.plugin_id: False}})
        result = self.client.discovery_inventory(self.project)
        self.assertEqual({'user', 'project'}, {row['source'] for row in result['skills']})
        self.assertIn('plugin', {row['source'] for row in self.client.discovery_inventory()['skills']})

    def test_project_plugin_scope_cannot_leak_to_common_or_sibling_folder(self):
        self.installed([{**self.entry, 'scope': 'project', 'projectPath': str(self.project)}])
        self.assertNotIn('plugin', {row['source'] for row in self.client.discovery_inventory()['skills']})
        rows = self.client.discovery_inventory(self.project)['skills']
        self.assertEqual('project', next(row for row in rows if row['source'] == 'plugin')['storageScope'])
        sibling = self.root / 'sibling'; sibling.mkdir()
        self.assertNotIn('plugin', {row['source'] for row in self.client.discovery_inventory(sibling)['skills']})

    def test_parent_skills_are_bounded_by_git_root_and_siblings_are_not_scanned(self):
        self.skill(self.root / '.claude/skills/unrelated/SKILL.md', 'unrelated')
        self.skill(self.project / 'sibling/.claude/skills/sibling/SKILL.md', 'sibling')
        nested = self.project / 'nested'; nested.mkdir()
        result = self.client.discovery_inventory(nested)
        self.assertIn('project', {row['source'] for row in result['skills']})
        self.assertNotIn('unrelated', {row['name'] for row in result['skills']})
        self.assertNotIn('sibling', {row['name'] for row in result['skills']})

    def test_unreadable_settings_do_not_inherit_earlier_plugin_enabling(self):
        for value in ('INVALID-JSON', {'enabledPlugins': []}, {'enabledPlugins': {self.plugin_id: 'true'}}):
            with self.subTest(value=value):
                write(self.project / '.claude/settings.local.json', value)
                result = self.client.discovery_inventory(self.project)
                self.assertTrue(result['skillsLimited'])
                self.assertNotIn('plugin', {row['source'] for row in result['skills']})

    def test_malformed_registry_does_not_hide_native_skills(self):
        write(self.config / 'plugins/installed_plugins.json', {'plugins': []})
        result = self.client.discovery_inventory()
        self.assertEqual(['user'], [row['source'] for row in result['skills']])
        self.assertTrue(result['skillsLimited'])
        self.assertEqual('plugin_registry_invalid', result['diagnostics']['code'])

    def test_deep_json_is_partial_instead_of_raising_recursion_error(self):
        for path in (self.config / 'settings.json', self.plugin / '.claude-plugin/plugin.json',
                     self.config / 'plugins/installed_plugins.json'):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                try:
                    write(path, '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}')
                    result = self.client.discovery_inventory()
                    self.assertTrue(result['skillsLimited'])
                    self.assertIn('user', {row['source'] for row in result['skills']})
                finally:
                    path.write_bytes(before)

    def test_quoted_header_comments_are_metadata_and_not_instruction_body(self):
        write(self.config / 'skills/hidden/SKILL.md', '---\nname: "hidden" # name\ndescription: \'Quoted # text\' # comment\nuser-invocable: "false" # hidden\n---\nSECRET-BODY')
        row = next(row for row in self.client.discovery_inventory()['skills'] if row['name'] == 'hidden')
        self.assertEqual('Quoted # text', row['description'])
        self.assertEqual('internal', row['kind'])

    def test_ancestor_scope_labels_are_distinct_without_absolute_paths(self):
        nested = self.project / 'nested'; nested.mkdir()
        self.skill(nested / '.claude/skills/shared/SKILL.md', 'shared')
        rows = [row for row in self.client.discovery_inventory(nested)['skills'] if row['source'] == 'project']
        self.assertEqual({'선택 폴더', '상위 1단계 폴더'}, {row['scopeLabel'] for row in rows})
        self.assertNotIn(str(self.project), json.dumps(rows))

    def test_headers_support_blocks_invocation_flags_and_do_not_decode_body(self):
        path = self.config / 'skills/hidden/SKILL.md'
        write(path, '---\nname: hidden\ndescription: >-\n  first line\n  second line\nuser-invocable: false\ndisable-model-invocation: true\n---\n')
        with path.open('ab') as stream:
            stream.write(b'\xff BODY-MUST-NOT-BE-DECODED')
        row = next(row for row in self.client.discovery_inventory()['skills'] if row['name'] == 'hidden')
        self.assertEqual('first line second line', row['description'])
        self.assertEqual('internal', row['kind'])
        self.assertTrue(row['explicitOnly'])

    def test_oversized_duplicate_or_incomplete_headers_are_partial(self):
        path = self.config / 'skills/broken/SKILL.md'
        for content in ('---\nname: broken\n', '---\nname: a\nname: b\n---\n',
                        '---\nuser-invocable: maybe\n---\n', '---\ndescription: ' + 'x' * MAX_HEADER_BYTES + '\n---\n'):
            with self.subTest(content=content[:30]):
                write(path, content)
                result = self.client.discovery_inventory()
                self.assertTrue(result['skillsLimited'])
                self.assertGreater(result['readFailures'], 0)
                self.assertFalse(any(row['name'] == 'broken' for row in result['skills']))

    def test_manifest_custom_roots_work_but_traversal_is_not_read(self):
        self.skill(self.plugin / 'custom/extra/SKILL.md', 'extra')
        self.skill(self.root / 'outside/secret/SKILL.md', 'must-not-read')
        write(self.plugin / '.claude-plugin/plugin.json', {'name': 'fixture', 'skills': ['./custom', '../outside']})
        result = self.client.discovery_inventory()
        self.assertIn('extra', {row['name'] for row in result['skills']})
        self.assertNotIn('must-not-read', {row['name'] for row in result['skills']})
        self.assertTrue(result['skillsLimited'])

    def test_malformed_manifest_does_not_get_silently_assumed_valid(self):
        write(self.plugin / '.claude-plugin/plugin.json', 'BAD')
        result = self.client.discovery_inventory()
        self.assertNotIn('plugin', {row['source'] for row in result['skills']})
        self.assertTrue(result['skillsLimited'])

    def test_unknown_scope_and_ambiguous_plugin_installations_are_not_selected(self):
        self.installed([{**self.entry, 'scope': 'unknown'}])
        self.assertNotIn('plugin', {row['source'] for row in self.client.discovery_inventory()['skills']})
        self.installed([self.entry, {**self.entry, 'installPath': str(self.root / 'other-plugin')}])
        result = self.client.discovery_inventory()
        self.assertNotIn('plugin', {row['source'] for row in result['skills']})
        self.assertTrue(result['skillsLimited'])

    def test_relative_missing_and_linked_folder_inputs_are_rejected(self):
        for path in ('relative', self.root / 'missing', self.root / 'a/../project'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.client.discovery_inventory(path)
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFLNK)), self.assertRaises(ValueError):
            self.client.discovery_inventory(self.project)

    def test_reparse_skill_is_skipped_without_hiding_valid_siblings(self):
        path = self.config / 'skills/a-linked'
        self.skill(path / 'SKILL.md', 'a-linked')
        original = Path.lstat
        def fake_lstat(value):
            if value == path:
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            return original(value)
        with patch.object(Path, 'lstat', fake_lstat):
            result = self.client.discovery_inventory()
        self.assertTrue(result['skillsLimited'])
        self.assertNotIn('a-linked', {row['name'] for row in result['skills']})
        self.assertIn('shared', {row['name'] for row in result['skills']})

    def test_registered_reference_metadata_is_preserved_without_importing_core(self):
        self.register()
        self.skill(self.root / 'state/personal-root/.claude/skills/reference/SKILL.md', 'reference')
        self.skill(self.root / 'knowledge/.claude/skills/corporate/SKILL.md', 'corporate')
        key = str(self.project).casefold() if os.name == 'nt' else str(self.project)
        project_state = self.root / 'state/project-scopes' / hashlib.sha256(key.encode()).hexdigest()[:24]
        self.skill(project_state / 'personal-root/.claude/skills/local-reference/SKILL.md', 'local-reference')
        with patch('local_app.harness_client.subprocess.run', side_effect=AssertionError('must not execute')):
            common = self.client.discovery_inventory()
            folder = self.client.discovery_inventory(self.project)
        self.assertEqual('ready', common['diagnostics']['code'])
        reference = next(row for row in common['skills'] if row['name'] == 'reference')
        self.assertEqual('reference', reference['kind'])
        self.assertEqual('', reference['invocation'])
        self.assertNotIn('local-reference', {row['name'] for row in common['skills']})
        self.assertIn('local-reference', {row['name'] for row in folder['skills']})

    def test_registration_cannot_expand_discovery_into_another_user_profile(self):
        target = self.register()
        mine = self.root / 'Users/mine'
        other = self.root / 'Users/other'
        self.skill(other / 'state/personal-root/.claude/skills/other-user/SKILL.md', 'other-user')
        write(target, {**self.record, 'userStateRoot': str(other / 'state')})
        with patch.object(Path, 'home', return_value=mine):
            result = self.client.discovery_inventory()
        self.assertNotIn('other-user', {row['name'] for row in result['skills']})
        self.assertTrue(result['skillsLimited'])

    def test_row_limit_is_disclosed_and_does_not_merge_existing_candidates(self):
        with patch('local_app.skill_inventory.MAX_ITEMS', 2):
            result = self.client.discovery_inventory(self.project)
        self.assertEqual(2, len(result['skills']))
        self.assertTrue(result['skillsLimited'])
        self.assertEqual(2, len({row['candidateId'] for row in result['skills']}))

    def test_project_registration_reads_project_settings_not_local_file(self):
        self.register(scope='Project', nativeClaudeScope='project', projectRoot=str(self.project))
        self.installed([{**self.entry, 'scope': 'project', 'projectPath': str(self.project)}])
        write(self.project / '.claude/settings.json', {'enabledPlugins': {self.plugin_id: True}})
        self.assertEqual('ready', self.client.discovery_inventory(self.project)['diagnostics']['code'])
        write(self.project / '.claude/settings.local.json', {'enabledPlugins': {self.plugin_id: False}})
        self.assertEqual('plugin_disabled', self.client.discovery_inventory(self.project)['diagnostics']['code'])

    def test_user_registration_honors_project_disabled_overlay(self):
        self.register()
        write(self.project / '.claude/settings.json', {'enabledPlugins': {self.plugin_id: False}})
        with self.assertRaises(InstallationError) as caught:
            self.client.locate(self.project)
        self.assertEqual('plugin_disabled', caught.exception.code)

    def test_registration_failures_have_specific_codes_but_native_discovery_continues(self):
        target = self.register()
        for update, expected in [({'enabled': False}, 'registration_disabled'),
                                 ({'claudeConfigRoot': str(self.root / 'other-config')}, 'config_mismatch'),
                                 ({'coreVersion': 'old'}, 'version_mismatch'),
                                 ({'pythonCommand': 'relative-python'}, 'runtime_unavailable')]:
            with self.subTest(expected=expected):
                write(target, {**self.record, **update})
                result = self.client.discovery_inventory()
                self.assertEqual(expected, result['diagnostics']['code'])
                self.assertTrue(result['skills'])


if __name__ == '__main__':
    unittest.main()
