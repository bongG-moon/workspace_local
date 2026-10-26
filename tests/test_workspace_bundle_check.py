"""Exercise the bundle checker against the packaging script's actual file list.

Only desktop build outputs are synthetic; no SDK, NSIS or network is required.
The checker itself runs unchanged in a separate process against a source snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CHECKER = Path('scripts/test-lab/check-workspace-bundle.py')
PREFIX = 'Company-Workspace/'


class WorkspaceBundleCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='workspace-bundle-check-', dir=ROOT / 'build')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        builder = (ROOT / 'deploy/New-WorkspaceBundle.ps1').read_text(encoding='utf-8-sig')
        source_block = re.search(r'\$files\s*=\s*@\((.*?)\n\)', builder, re.S)
        desktop_block = re.search(r'foreach \(\$name in @\((.*?)\)\)', builder, re.S)
        if source_block is None or desktop_block is None:
            raise AssertionError('The builder file lists changed; update this fixture extractor.')
        source_files = [name.replace('\\', '/') for name in re.findall(r"'([^']+)'", source_block[1])]
        desktop_files = re.findall(r"'([^']+)'", desktop_block[1])
        if len(source_files) < 100 or len(source_files) != len(set(source_files)):
            raise AssertionError('Expected the complete, unique bundle source file list.')
        if 'desktop-build.json' not in desktop_files:
            raise AssertionError('Expected the desktop build report in the builder file list.')

        cls.entries = {}
        for relative in source_files:
            data = (ROOT / relative).read_bytes()
            target = cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            cls.entries[relative] = data
        checker = cls.root / CHECKER
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_bytes((ROOT / CHECKER).read_bytes())

        desktop = cls.root / 'build/desktop-host'
        desktop.mkdir(parents=True)
        lock = json.loads((cls.root / 'deploy/WebView2.lock.json').read_text(encoding='utf-8-sig'))
        report = {'sdkSha256': lock['sha256'], 'sdkVersion': lock['version'],
                  'sources': {}, 'files': {}}
        for relative in ('deploy/Workspace.Desktop.cs', 'deploy/CompanyWorkspace.Standalone.manifest'):
            report['sources'][relative] = hashlib.sha256(cls.entries[relative]).hexdigest()
        for name in desktop_files:
            if name == 'desktop-build.json':
                continue
            data = ('Synthetic desktop fixture: ' + name).encode('utf-8')
            (desktop / name).write_bytes(data)
            cls.entries['desktop/' + name] = data
            report['files'][name] = hashlib.sha256(data).hexdigest()
        data = json.dumps(report).encode('utf-8')
        (desktop / 'desktop-build.json').write_bytes(data)
        cls.entries['desktop/desktop-build.json'] = data

    def check(self, *, omit=(), replace=None, extra=()):
        archive = self.root / 'bundle.zip'
        replacements = replace or {}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)  # Deliberate duplicate-member fixtures.
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
                for relative, content in sorted(self.entries.items()):
                    if relative not in omit:
                        bundle.writestr(PREFIX + relative, replacements.get(relative, content))
                for relative, content in extra:
                    bundle.writestr(PREFIX + relative, content)
        result = subprocess.run([sys.executable, '-X', 'utf8', str(self.root / CHECKER), str(archive)],
                                capture_output=True, text=True, encoding='utf-8', timeout=120)
        return result.returncode, result.stdout + result.stderr

    def test_current_builder_file_list_is_accepted(self):
        self.assertIn('docs/WORKSPACE_NSIS_INSTALLER.md', self.entries)
        self.assertIn('docs/WORKSPACE_ONLINE_INSTALLER.md', self.entries)
        self.assertIn('deploy/workspace-release-notes.json', self.entries)
        self.assertIn('local_app/release_history.py', self.entries)
        self.assertIn('local_app/event_recovery.py', self.entries)
        self.assertIn('local_app/web/result-receipts.js', self.entries)
        code, output = self.check()
        self.assertEqual(0, code, output)
        self.assertIn('no runtime state or credentials bundled', output)

    def test_missing_required_source_files_are_rejected(self):
        for relative in ('docs/WORKSPACE_NSIS_INSTALLER.md', 'docs/WORKSPACE_ONLINE_INSTALLER.md',
                         'local_app/server.py', 'deploy/workspace-release-notes.json',
                         'local_app/release_history.py', 'local_app/event_recovery.py', 'local_app/web/result-receipts.js', 'local_app/web/release-history.js',
                         'local_app/web/release-history.css'):
            with self.subTest(relative=relative):
                code, output = self.check(omit=(relative,))
                self.assertNotEqual(0, code, output)
                self.assertIn(relative, output)

    def test_duplicate_member_is_rejected(self):
        relative = 'docs/WORKSPACE_NSIS_INSTALLER.md'
        code, output = self.check(extra=((relative, self.entries[relative]),))
        self.assertNotEqual(0, code, output)
        self.assertIn(relative, output)

    def test_extra_or_private_files_are_rejected(self):
        for relative in ('docs/unreviewed.md', '.env', 'local_app/runtime.json', '../outside.txt'):
            with self.subTest(relative=relative):
                code, output = self.check(extra=((relative, b'fixture'),))
                self.assertNotEqual(0, code, output)
                self.assertIn(relative, output)

    def test_stale_source_or_desktop_content_is_rejected(self):
        for relative in ('docs/WORKSPACE_ONLINE_INSTALLER.md', 'local_app/server.py',
                         'desktop/Workspace.Desktop.exe'):
            with self.subTest(relative=relative):
                code, output = self.check(replace={relative: b'previous build content'})
                self.assertNotEqual(0, code, output)
                self.assertIn('Stale bundle content: ' + PREFIX + relative, output)


if __name__ == '__main__':
    unittest.main()
