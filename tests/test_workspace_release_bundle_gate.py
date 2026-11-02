"""Run the release entry point and verify its independent checker is mandatory."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = Path(os.environ.get('WINDIR', 'C:/Windows'))
PS = WINDOWS / 'System32/WindowsPowerShell/v1.0/powershell.exe'
CSC = WINDOWS / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


@unittest.skipUnless(os.name == 'nt' and PS.is_file() and CSC.is_file(), 'Windows build tools required')
class ReleaseBundleGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='release-bundle-gate-', dir=ROOT / 'build')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        deploy = self.root / 'deploy'
        deploy.mkdir()
        self.checker = self.root / 'scripts/test-lab/check-workspace-bundle.py'
        self.checker.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/create-workspace-archive.py', self.root / 'scripts/create-workspace-archive.py')
        shutil.copytree(ROOT / 'workspace_publisher', self.root / 'workspace_publisher',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copyfile(ROOT / 'deploy/New-WorkspaceRelease.ps1', deploy / 'New-WorkspaceRelease.ps1')
        self.version = re.search(r'^WORKSPACE_VERSION = "([0-9.]+)"',
            (ROOT / 'local_app/server.py').read_text(encoding='utf-8'), re.M)[1]
        (deploy / 'Workspace.Version.ps1').write_text(
            "function Sync-WorkspaceVersion { param([string]$RepoRoot, [switch]$Check) return '" + self.version + "' }", encoding='utf-8')
        (deploy / 'New-WorkspaceBundle.ps1').write_text('''param($OutputDirectory,$UpdateConfig,$ConfigPython,$UsageConfig)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'payload.zip') -Destination (Join-Path $OutputDirectory 'bundle.zip')
''', encoding='utf-8')
        self.checker.write_text('''import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[2]
(root / 'checker-arguments.json').write_text(json.dumps(sys.argv[1:]))
sys.exit(int((root / 'checker-exit.txt').read_text()))
''', encoding='utf-8')
        with zipfile.ZipFile(self.root / 'payload.zip', 'w') as bundle:
            bundle.writestr('Company-Workspace/fixture.txt', 'matching bundle source')
        source = self.root / 'Fixture.cs'
        source.write_text('using System.Reflection; [assembly: AssemblyVersion("' + self.version
            + '.0")] class Fixture { static void Main() {} }', encoding='utf-8')
        self.exe = self.root / 'Fixture.exe'
        compiled = subprocess.run([str(CSC), '/nologo', '/target:exe', '/out:' + str(self.exe),
            '/resource:' + str(self.root / 'payload.zip') + ',WorkspacePayload.zip', str(source)],
            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(0, compiled.returncode, compiled.stdout + compiled.stderr)

    def run_release(self, code, *, configs=False):
        (self.root / 'checker-exit.txt').write_text(str(code), encoding='ascii')
        args = [str(PS), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(self.root / 'deploy/New-WorkspaceRelease.ps1'), '-StandaloneExe', str(self.exe),
            '-OutputDirectory', str(self.root / 'release'), '-ConfigPython', sys.executable]
        if configs:
            for name in ('update config.json', 'usage config.json'):
                (self.root / name).write_text('{}', encoding='ascii')
            args += ['-UpdateConfig', str(self.root / 'update config.json'),
                     '-UsageConfig', str(self.root / 'usage config.json')]
        env = dict(os.environ)
        env['PSModulePath'] = str(PS.parent / 'Modules')  # Do not load PowerShell 7 modules in 5.1.
        return subprocess.run(args, capture_output=True, timeout=45, env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def test_failed_checker_stops_before_release_archives_or_checksums_are_written(self):
        result = self.run_release(1)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'Bundle validation failed', result.stderr)
        self.assertTrue((self.root / 'checker-arguments.json').exists())
        self.assertEqual([], list((self.root / 'release').iterdir()))

    def test_success_passes_both_optional_settings_and_generates_release(self):
        result = self.run_release(0, configs=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        args = json.loads((self.root / 'checker-arguments.json').read_text())
        self.assertEqual('bundle.zip', Path(args[0]).name)
        self.assertEqual(['--update-config', str(self.root / 'update config.json'),
                          '--usage-config', str(self.root / 'usage config.json')], args[1:])
        self.assertTrue((self.root / 'release' / f'AX-Workspace-{self.version}-exe.zip').is_file())
        self.assertTrue((self.root / 'release/SHA256SUMS.txt').is_file())

    def test_missing_checker_cannot_silently_skip_validation(self):
        self.checker.unlink()
        result = self.run_release(0)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'Bundle validation script is missing', result.stderr)
        self.assertEqual([], list((self.root / 'release').iterdir()))


if __name__ == '__main__':
    unittest.main()
