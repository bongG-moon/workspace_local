"""The active app discovers native metadata without any harness registration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.claude_inventory import ClaudeInventory
from local_app.completions import CompletionDiscovery
from local_app.bridge import ClaudeSession


class GenericClaudeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.config = self.root / 'config'
        self.work = self.root / 'work'; self.work.mkdir()
        self.config.mkdir()
        self.skill(self.config / 'skills/global-skill', 'global-skill')
        self.skill(self.work / '.claude/skills/folder-skill', 'folder-skill')
        self.client = ClaudeInventory(config=self.config)

    def skill(self, path, name):
        path.mkdir(parents=True); (path / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Metadata test\n---\nNever execute this body.', encoding='utf-8')

    def test_common_and_folder_native_skills_without_process_or_registration(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('subprocess.run', side_effect=AssertionError('No child')), patch('subprocess.Popen', side_effect=AssertionError('No child')):
            common = self.client.discovery_inventory()
            folder = self.client.discovery_inventory(self.work)
            completion = CompletionDiscovery(self.client).inventory(self.work)
        self.assertEqual({'global-skill'}, {row['name'] for row in common['skills']})
        self.assertEqual({'global-skill', 'folder-skill'}, {row['name'] for row in folder['skills']})
        self.assertEqual({'global-skill', 'folder-skill'}, {row['name'] for row in completion['skills']})
        self.assertNotIn('registration', json.dumps(common))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_unconfigured_profile_is_valid_empty_metadata(self):
        result = ClaudeInventory(config=self.root/'empty-config').discovery_inventory()
        self.assertEqual([], result['skills'])
        self.assertEqual('metadata_read', result['diagnostics']['code'])
        self.assertFalse((self.root/'empty-config').exists())

    def test_harness_shaped_command_never_discovers_installer(self):
        events = []; bridge = ClaudeSession(['never-start'], {}, self.work, lambda *args: events.append(args))
        with patch('local_app.harness_client.HarnessClient', side_effect=AssertionError('No implicit harness')):
            bridge.handle({'type':'assistant','message':{'content':[{'type':'tool_use','id':'tool','name':'Bash',
                'input':{'command':'python harness_cli.py business html-choices --spec example.json'}}]}})
        self.assertTrue(any(kind == 'activity' for kind, _ in events))
        self.assertFalse(getattr(bridge, '_choice_tools', set()))


if __name__ == '__main__': unittest.main()
