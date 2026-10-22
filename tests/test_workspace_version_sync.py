"""Exercise version generation and build rejection against disposable source trees."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
SOURCES = (
    'local_app/server.py', 'deploy/Start-CompanyWorkspace.ps1',
    'deploy/CompanyWorkspace.Startup.ps1', 'deploy/CompanyWorkspace.NormalToken.cs',
    'deploy/CompanyAgent.UserContext.ps1', 'Check-Workspace.ps1',
    'deploy/workspace-release-notes.json', 'deploy/Workspace.Version.ps1',
    'scripts/Sync-WorkspaceVersion.ps1',
)
BUILDERS = tuple(path.relative_to(ROOT).as_posix() for path in (ROOT / 'deploy').glob('New-Workspace*.ps1'))


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell required')
class WorkspaceVersionSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-version-', dir=ROOT / 'build')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in set(SOURCES + BUILDERS):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def run_script(self, relative='scripts/Sync-WorkspaceVersion.ps1', *arguments, root=None):
        env = dict(os.environ)
        env.pop('PSModulePath', None)
        return subprocess.run(
            [str(PS), '-NoProfile', '-NoLogo', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str((root or self.root) / relative), *arguments],
            capture_output=True, timeout=30, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def assert_ok(self, result):
        self.assertEqual(0, result.returncode, (result.stdout + result.stderr).decode('utf-8', errors='replace'))

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def change_version(self):
        source = self.root / 'local_app/server.py'
        source.write_bytes(re.sub(rb'(?m)^WORKSPACE_VERSION = "[0-9.]+"',
                                 b'WORKSPACE_VERSION = "3.12.7"', source.read_bytes()))

    def test_current_tree_preflight_is_read_only(self):
        before = self.snapshot()
        self.assert_ok(self.run_script('scripts/Sync-WorkspaceVersion.ps1', '-Check'))
        self.assertEqual(before, self.snapshot())

    def test_generation_updates_all_literals_and_hashes_without_touching_history(self):
        self.change_version()
        history = (self.root / 'deploy/workspace-release-notes.json').read_bytes()
        self.assert_ok(self.run_script())
        launcher = (self.root / 'deploy/Start-CompanyWorkspace.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("-CurrentVersion '3.12.7'", launcher)
        self.assertIn("$targetVersion = [version]'3.12.7'", launcher)
        startup = (self.root / 'deploy/CompanyWorkspace.Startup.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("workspaceVersion='3.12.7'", startup)
        self.assertIn("$report.workspaceVersion -ne '3.12.7'", startup)
        bundle = (self.root / 'deploy/New-WorkspaceBundle.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("'ax-workspace-preview-' + $version + '-'", bundle)
        check = (self.root / 'Check-Workspace.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("targetSource = 'workspace-3.12.7'", check)
        for relative, digest in re.findall(r"'(deploy/[^']+)'\s*=\s*'([a-f0-9]{64})'", check):
            data = (self.root / relative).read_text(encoding='utf-8-sig').encode('utf-8')
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertEqual(history, (self.root / 'deploy/workspace-release-notes.json').read_bytes())
        once = self.snapshot()
        self.assert_ok(self.run_script())
        self.assertEqual(once, self.snapshot())
        self.assertNotEqual(0, self.run_script('scripts/Sync-WorkspaceVersion.ps1', '-Check').returncode)
        # The content workflow must explicitly add the new notes before a build.
        notes = json.loads(history)
        notes['currentVersion'] = '3.12.7'
        notes['releases'].insert(0, {'version': '3.12.7', 'title': 'New', 'notes': 'Reviewed changes'})
        (self.root / 'deploy/workspace-release-notes.json').write_text(json.dumps(notes), encoding='utf-8')
        self.assert_ok(self.run_script('scripts/Sync-WorkspaceVersion.ps1', '-Check'))

    def test_check_rejects_stale_values_without_silently_generating(self):
        self.change_version()
        before = self.snapshot()
        result = self.run_script('scripts/Sync-WorkspaceVersion.ps1', '-Check')
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'Generated version or diagnostic metadata is stale', result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_template_change_is_rejected_before_any_write(self):
        self.change_version()
        check = self.root / 'Check-Workspace.ps1'
        check.write_bytes(check.read_bytes().replace(b'targetSource = ', b'changedSource = '))
        before = self.snapshot()
        result = self.run_script()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'Version template changed', result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_invalid_or_duplicate_canonical_version_is_rejected(self):
        path = self.root / 'local_app/server.py'
        original = path.read_bytes()
        for source in (original + b'\nWORKSPACE_VERSION = "1.2.3"\n',
                       re.sub(rb'(?m)^WORKSPACE_VERSION = "[0-9.]+"', b'WORKSPACE_VERSION = "invalid"', original)):
            with self.subTest(source=source[-40:]):
                path.write_bytes(source)
                before = self.snapshot()
                self.assertNotEqual(0, self.run_script().returncode)
                self.assertEqual(before, self.snapshot())

    def test_generation_preserves_bom_crlf_and_unrelated_source(self):
        self.change_version()
        path = self.root / 'deploy/Start-CompanyWorkspace.ps1'
        text = path.read_text(encoding='utf-8-sig') + '\n# Preserve this unrelated edit.\n'
        path.write_bytes(b'\xef\xbb\xbf' + text.replace('\n', '\r\n').encode('utf-8'))
        self.assert_ok(self.run_script())
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertTrue(raw.endswith(b'# Preserve this unrelated edit.\r\n'))

    def test_every_build_entrypoint_rejects_stale_version_before_creating_artifacts(self):
        self.change_version()
        before = self.snapshot()
        self.assertEqual(6, len(BUILDERS))
        for builder in BUILDERS:
            with self.subTest(builder=builder):
                args = ('-UpdateConfig', 'does-not-exist.json') if builder.endswith('OnlineInstaller.ps1') else ()
                result = self.run_script(builder, *args)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(b'Generated version or diagnostic metadata is stale', result.stderr)
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.root / 'build').exists())
                self.assertFalse((self.root / 'dist').exists())


if __name__ == '__main__':
    unittest.main()
