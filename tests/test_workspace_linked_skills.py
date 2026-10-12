"""Native directory links retain a single read-only source without widening trust."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_app.completions import CompletionDiscovery
from local_app.skill_inventory import discover_native_metadata, _native_skill_directory


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


class LinkedSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workspace-linked-skills-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'
        self.project = self.root / 'project'
        self.config.mkdir()
        (self.project / '.git').mkdir(parents=True)

    def skill(self, directory, name=None, description='Original description', extra=''):
        header = f'name: {name}\n' if name else ''
        write(directory / 'SKILL.md', f'---\n{header}description: {description}\n{extra}---\nPRIVATE INSTRUCTION BODY')
        return directory

    def link(self, link, target, *, symlink=False):
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt' and not symlink:
            def literal(value):
                return "'" + str(value).replace("'", "''") + "'"
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                 '$ErrorActionPreference = "Stop"; New-Item -ItemType Junction -Path ' + literal(link)
                 + ' -Target ' + literal(target) + ' | Out-Null'],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                self.fail('Could not create isolated test junction: ' + result.stderr)
        else:
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest('Directory symlink unavailable: ' + str(exc))
        # Never recursively remove a junction. The target is cleaned separately
        # by TemporaryDirectory only after every test-created link is removed.
        def unlink():
            if os.path.lexists(link):
                if os.name == 'nt' and not link.is_symlink():
                    os.rmdir(link)
                else:
                    link.unlink()
        self.addCleanup(unlink)
        return link

    def discover(self, **kwargs):
        return discover_native_metadata(self.config, self.project, **kwargs)

    def test_native_entry_junction_preserves_logical_name_and_source_unchanged(self):
        source = self.skill(self.root / 'managed-storage/random-internal-id')
        original = source / 'SKILL.md'
        before = (hashlib.sha256(original.read_bytes()).hexdigest(), original.stat().st_mtime_ns)
        self.link(self.project / '.claude/skills/public-name', source)
        with patch('subprocess.Popen', side_effect=AssertionError('metadata must not launch processes')):
            result = self.discover()
        self.assertEqual(['public-name'], [row['invocation'] for row in result['skills']])
        self.assertEqual('project', result['skills'][0]['storageScope'])
        self.assertFalse(result['skillsLimited'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(before, (hashlib.sha256(original.read_bytes()).hexdigest(), original.stat().st_mtime_ns))

    def test_personal_root_junction_discovers_existing_and_new_skills_after_cache_ttl(self):
        source = self.root / 'managed-storage/skills'
        self.skill(source / 'first')
        self.link(self.config / 'skills', source)
        cache = CompletionDiscovery(SimpleNamespace(
            completion_inventory=lambda workspace=None: discover_native_metadata(self.config, workspace)))
        with patch('local_app.completions.time.monotonic', return_value=10):
            self.assertEqual(['first'], [row['invocation'] for row in cache.inventory()['skills']])
        self.skill(source / 'first', description='Edited description')
        self.skill(source / 'new-skill')
        with patch('local_app.completions.time.monotonic', return_value=12):
            self.assertEqual(['first'], [row['invocation'] for row in cache.inventory()['skills']])
        with patch('local_app.completions.time.monotonic', return_value=16), \
                patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            rows = cache.inventory()['skills']
        self.assertEqual(['first', 'new-skill'], [row['invocation'] for row in rows])
        self.assertEqual('Edited description', rows[0]['description'])

    def test_project_root_and_immediate_entry_links_can_be_composed(self):
        source = self.skill(self.root / 'original-source', 'actual-name')
        collection = self.root / 'collection'
        self.link(collection / 'alias', source)
        self.link(self.project / '.claude/skills', collection)
        result = self.discover()
        self.assertEqual(['actual-name'], [row['invocation'] for row in result['skills']])
        self.assertFalse(result['skillsLimited'])

    def test_native_directory_symlink_is_supported(self):
        source = self.skill(self.root / 'symlink-source')
        self.link(self.project / '.claude/skills/symlink-name', source, symlink=True)
        self.assertEqual(['symlink-name'], [row['invocation'] for row in self.discover()['skills']])

    def test_same_target_once_with_personal_precedence_and_logical_name(self):
        source = self.skill(self.root / 'shared-physical-source')
        self.link(self.config / 'skills/personal-name', source)
        self.link(self.project / '.claude/skills/project-name', source)
        result = self.discover()
        self.assertEqual(['personal-name'], [row['invocation'] for row in result['skills']])
        self.assertEqual('personal', result['skills'][0]['storageScope'])
        self.assertEqual(0, result['skillConflicts'])

    def test_same_target_nearest_project_scope_wins(self):
        child = self.project / 'child'
        child.mkdir()
        source = self.skill(self.root / 'shared-physical-source')
        self.link(self.project / '.claude/skills/ancestor-name', source)
        self.link(child / '.claude/skills/nearest-name', source)
        result = discover_native_metadata(self.config, child)
        self.assertEqual(['nearest-name'], [row['invocation'] for row in result['skills']])
        self.assertEqual('선택 폴더', result['skills'][0]['scopeLabel'])

    def test_hidden_personal_target_does_not_reappear_via_project_alias(self):
        source = self.skill(self.root / 'shared-physical-source')
        self.link(self.config / 'skills/personal-name', source)
        self.link(self.project / '.claude/skills/project-name', source)
        write(self.config / 'settings.json', {'skillOverrides': {'personal-name': 'off'}})
        self.assertEqual([], self.discover()['skills'])

    def test_file_leaf_links_remain_rejected(self):
        source = self.skill(self.root / 'source')
        directory = self.project / '.claude/skills/leaf-link'
        directory.mkdir(parents=True)
        real = directory / 'SKILL.md'
        original = Path.lstat
        def fake_lstat(path):
            if path == real:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_reparse_tag=0xA000000C, st_file_attributes=0x400)
            return original(path)
        # exists() is true, while lstat identifies a name-surrogate leaf.
        write(real, (source / 'SKILL.md').read_text(encoding='utf-8'))
        with patch.object(Path, 'lstat', fake_lstat):
            result = self.discover()
        self.assertEqual([], result['skills'])
        self.assertTrue(result['skillsLimited'])

    def test_plugin_reference_and_command_links_remain_rejected(self):
        source = self.skill(self.root / 'source', 'not-native')
        plugin = self.root / 'plugin'
        write(plugin / '.claude-plugin/plugin.json', {'name': 'fixture'})
        write(self.config / 'settings.json', {'enabledPlugins': {'fixture@local': True}})
        write(self.config / 'plugins/installed_plugins.json', {'plugins': {'fixture@local': [
            {'scope': 'user', 'version': '1', 'installPath': str(plugin)}]}})
        self.link(plugin / 'skills/linked', source)
        reference = self.link(self.root / 'reference', source)
        self.link(self.config / 'commands', source)
        result = self.discover(reference_roots=((reference, 'personal', 'personal'),), include_commands=True)
        self.assertEqual([], result['skills'])
        self.assertTrue(result['skillsLimited'])
        self.assertGreaterEqual(result['readFailures'], 3)

    def test_linked_settings_parent_remains_rejected(self):
        source = self.skill(self.root / 'claude-source/skills/example', 'example').parent.parent
        self.link(self.project / '.claude', source)
        result = self.discover()
        self.assertEqual([], result['skills'])
        self.assertTrue(result['skillsLimited'])

    def test_dangling_junction_does_not_hide_other_skills(self):
        source = self.root / 'removed-target'
        source.mkdir()
        self.link(self.project / '.claude/skills/dangling', source)
        source.rmdir()
        self.skill(self.project / '.claude/skills/valid', 'valid')
        result = self.discover()
        self.assertEqual(['valid'], [row['invocation'] for row in result['skills']])
        self.assertTrue(result['skillsLimited'])

    def test_unknown_reparse_tag_is_rejected_before_readlink(self):
        link = self.project / '.claude/skills/unsupported'
        link.mkdir(parents=True)
        original = Path.lstat
        def fake_lstat(path):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0x8000001B)
            return original(path)
        with patch.object(Path, 'lstat', fake_lstat), \
                patch('os.readlink', side_effect=AssertionError('must reject unknown tag')):
            with self.assertRaises(ValueError):
                _native_skill_directory(link)

    def test_network_device_drive_root_and_other_profile_targets_rejected(self):
        link = self.project / '.claude/skills/rejected'
        link.mkdir(parents=True)
        original = Path.lstat
        def fake_lstat(path):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
            return original(path)
        mine = self.root / 'Users/mine'
        other = self.root / 'Users/other/skills'
        targets = [r'\\server\share\skills', r'\\?\UNC\server\share\skills',
                   r'\\.\C:\skills', r'\\?\Volume{test}\skills', str(Path(self.root.anchor)), str(other)]
        with patch.object(Path, 'lstat', fake_lstat), patch.object(Path, 'home', return_value=mine):
            for target in targets:
                with self.subTest(target=target), patch('os.readlink', return_value=target), self.assertRaises(ValueError):
                    _native_skill_directory(link)

    def test_link_loop_and_hop_limit_are_bounded(self):
        nodes = [self.root / ('link-' + str(index)) for index in range(10)]
        original = Path.lstat
        def fake_lstat(path):
            if path in nodes:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
            return original(path)
        with patch.object(Path, 'lstat', fake_lstat):
            with patch('os.readlink', return_value=str(nodes[0])), self.assertRaises(ValueError):
                _native_skill_directory(nodes[0])
            with patch('os.readlink', side_effect=lambda path: str(nodes[nodes.index(path) + 1])) as reader:
                with self.assertRaises(ValueError):
                    _native_skill_directory(nodes[0])
                self.assertLessEqual(reader.call_count, 8)

    def test_links_in_target_ancestors_are_not_followed(self):
        source = self.skill(self.root / 'real/entry')
        intermediary = self.link(self.root / 'intermediary', source.parent)
        self.link(self.project / '.claude/skills/outer', intermediary / 'entry')
        result = self.discover()
        self.assertEqual([], result['skills'])
        self.assertTrue(result['skillsLimited'])


if __name__ == '__main__':
    unittest.main()
