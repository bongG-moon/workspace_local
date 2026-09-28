"""Read-only installed-core adapter, visibility metadata, and failure bounds."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_app.harness_client import HarnessClient
from local_app.skill_inventory import MAX_HEADER_BYTES, MAX_ITEMS, project_inventory, user_invocable

ROOT = Path(__file__).resolve().parents[1]

# Optional source dependency for one real read-only helper integration test.
CORE = Path(os.environ.get('COMPANY_AGENT_SOURCE', '')).expanduser().resolve() if 'COMPANY_AGENT_SOURCE' in os.environ else ROOT / 'company-agent-plugin'
if not all((CORE / name).is_file() for name in ('scripts/harness_cli.py', 'scripts/company_agent/workspace_api.py', 'scripts/company_agent/skill_registry.py')):
    if 'COMPANY_AGENT_SOURCE' in os.environ:
        raise RuntimeError('COMPANY_AGENT_SOURCE must point to a complete Company Agent plugin source root (containing scripts/harness_cli.py).')
    CORE = None
CORE_SKIP = 'Optional Company Agent integration: set COMPANY_AGENT_SOURCE to its plugin source root.'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'sample' / 'SKILL.md'

    def test_flag_is_only_top_level_frontmatter_and_explicit_only_is_different(self):
        for text, expected in [
            ('---\nname: x\nuser-invocable: false\n---\nbody', False),
            ('---\nuser-invocable: "false" # hidden\n---\nbody', False),
            ('---\ndisable-model-invocation: true\n---\nuser-invocable: false', True),
            ('---\nmetadata:\n  user-invocable: false\n---\nbody', True),
            ('plain body\nuser-invocable: false', True),
        ]:
            with self.subTest(text=text):
                write(self.file, text)
                self.assertEqual(expected, user_invocable(self.file))

    def test_does_not_decode_or_read_body_after_header(self):
        write(self.file, '---\nuser-invocable: false\n---\n')
        with self.file.open('ab') as stream:
            stream.write(b'\xff\x00 BODY-INSTRUCTIONS-ARE-NOT-METADATA')
        self.assertFalse(user_invocable(self.file))

    def test_ambiguous_malformed_or_oversized_headers_are_not_assumed_visible(self):
        for header in ['---\nuser-invocable: false\nuser-invocable: true\n---\n',
                       '---\nuser-invocable: maybe\n---\n',
                       '---\nuser-invocable: "false\n---\n',
                       '---\nuser-invocable: false\'\n---\n',
                       '---\nuser-invocable: false\n',
                       '---\ndescription: ' + 'x' * MAX_HEADER_BYTES + '\n---\n']:
            with self.subTest(header=header[:70]):
                write(self.file, header)
                with self.assertRaises(ValueError):
                    user_invocable(self.file)

    def test_linked_and_non_skill_paths_are_rejected(self):
        write(self.file, '---\nname: x\n---\n')
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFLNK)):
            with self.assertRaises(ValueError):
                user_invocable(self.file)
        for path in [Path('relative/SKILL.md'), self.file.parent / 'other.md',
                     self.file.parent / '..' / 'SKILL.md']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                user_invocable(path)

    def test_projection_omits_unknown_metadata_and_never_leaks_paths_or_bodies(self):
        write(self.file, '---\nuser-invocable: false\n---\nSECRET-BODY')
        inv = {'complete': True, 'skills': [
            {'name': 'helper', 'description': 'safe', 'path': str(self.file),
             'source': 'plugin', 'storageScope': 'personal', 'invocation': 'plugin:helper',
             'body': 'SECRET-BODY', 'env': {'TOKEN': 'SECRET-TOKEN'}},
            {'name': 'missing', 'path': str(self.file.parent / 'missing' / 'SKILL.md')}],
            'warnings': ['SECRET-PATH'], 'conflicts': []}
        projected = project_inventory(inv, {'scope': 'User'})
        self.assertEqual('internal', projected['skills'][0]['kind'])
        self.assertEqual('plugin', projected['skills'][0]['pluginNamespace'])
        self.assertEqual(1, projected['readFailures'])
        self.assertTrue(projected['skillsLimited'])
        self.assertNotIn(str(self.file), json.dumps(projected))
        self.assertNotIn('SECRET', json.dumps(projected))

    def test_row_limit_is_disclosed(self):
        item = {'name': 'x', 'source': 'project', 'path': str(self.file), 'invocation': 'x'}
        with patch('local_app.skill_inventory.user_invocable', return_value=True) as read:
            projected = project_inventory({'complete': True, 'skills': [item] * (MAX_ITEMS + 1)}, {'scope': 'User'})
        self.assertEqual(MAX_ITEMS, len(projected['skills']))
        self.assertEqual(MAX_ITEMS, read.call_count)
        self.assertTrue(projected['skillsLimited'])


class HelperProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workspace-inventory-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config, self.reg, self.project = [self.root / name for name in ('config', 'registrations', 'work')]
        self.project.mkdir()
        self.record = {'schemaVersion': 1, 'scope': 'User', 'nativeClaudeScope': 'user',
                       'userStateRoot': str(self.root / 'state'), 'claudeConfigRoot': str(self.config),
                       'claudeConfigDirOverride': bool(os.environ.get('CLAUDE_CONFIG_DIR')),
                       'coreVersion': 'test', 'pythonCommand': sys.executable,
                       'pluginId': 'company-agent@company-agent-local'}
        self.plugin = CORE or self.root / 'fixture-core'
        if CORE is None:
            # Failure/locator tests never execute these marker files.
            for relative in ('scripts/harness_cli.py', 'scripts/company_agent/workspace_api.py'):
                write(self.plugin / relative, '# Locator fixture only; not executable core.\n')
        write(self.reg / 'user/company-agent-install.json', self.record)
        write(self.config / 'plugins/installed_plugins.json', {'plugins': {
            'company-agent@company-agent-local': [{'scope': 'user', 'version': 'test',
                                                  'installPath': str(self.plugin)}],
            'fixture@local': [{'scope': 'user', 'version': 'test', 'installPath': str(self.root / 'plugin')}]
        }})
        write(self.config / 'settings.json', {'enabledPlugins': {
            'company-agent@company-agent-local': True, 'fixture@local': True}})
        write(self.root / 'plugin/.claude-plugin/plugin.json', {'name': 'fixture'})
        write(self.root / 'plugin/skills/helper/SKILL.md', '---\nname: helper\nuser-invocable: false\n---\nSECRET-BODY')
        write(self.config / 'skills/manual/SKILL.md', '---\nname: manual\ndisable-model-invocation: true\n---\nbody')
        self.client = HarnessClient(config=self.config, registrations=self.reg)

    @unittest.skipUnless(CORE is not None, CORE_SKIP)
    def test_subprocess_reads_exact_enabled_installation_without_writes(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        result = self.client.skill_inventory(self.project)
        rows = {item['name']: item for item in result['skills']}
        self.assertEqual('internal', rows['helper']['kind'])
        self.assertEqual('personal', rows['helper']['storageScope'])
        self.assertEqual('fixture:helper', rows['helper']['invocation'])
        self.assertEqual('skill', rows['manual']['kind'])
        self.assertTrue(rows['manual']['explicitOnly'])
        self.assertFalse(result['skillsLimited'])
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')))
        self.assertNotIn('SECRET-BODY', json.dumps(result))

    def test_disabled_core_cannot_launch_discovery_or_fall_back(self):
        write(self.config / 'settings.json', {'enabledPlugins': {}})
        with patch('local_app.harness_client.subprocess.run', side_effect=AssertionError('must not execute')):
            with self.assertRaises(ValueError):
                self.client.skill_inventory(self.project)

    def test_helper_rechecks_installation_and_rejects_wrong_plugin(self):
        command = [sys.executable, '-B', str(ROOT / 'local_app/skill_inventory.py'),
                   '--workspace', str(self.project), '--plugin', str(self.root / 'different'),
                   '--config', str(self.config), '--registrations', str(self.reg)]
        result = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(1, result.returncode)
        self.assertEqual(b'', result.stdout)
        self.assertNotIn(str(self.root).encode(), result.stderr)

    def test_unsupported_core_timeout_and_oversized_results_are_generic_errors(self):
        with patch('local_app.harness_client.subprocess.run',
                   return_value=SimpleNamespace(returncode=1, stdout=b'', stderr=b'SECRET-TRACE')):
            with self.assertRaisesRegex(ValueError, '메타데이터') as caught:
                self.client.skill_inventory(self.project)
            self.assertNotIn('SECRET', str(caught.exception))
        with patch('local_app.harness_client.subprocess.run', side_effect=subprocess.TimeoutExpired('fixture', 20)):
            with self.assertRaises(ValueError):
                self.client.skill_inventory(self.project)
        with patch('local_app.harness_client.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=b' ' * (4 * 1024 * 1024 + 1))):
            with self.assertRaises(ValueError):
                self.client.skill_inventory(self.project)


if __name__ == '__main__':
    unittest.main()
