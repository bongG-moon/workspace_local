"""Compile the real bootstrap without running it or contacting a server."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from local_app.server import WORKSPACE_VERSION
from local_app.update_source import source_from_config

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
COMPILER = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


@unittest.skipUnless(os.name == 'nt' and COMPILER.is_file() and POWERSHELL.is_file(), 'Windows .NET compiler required')
class OnlineInstallerBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='online-build-', dir=ROOT / 'build')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'source.json'
        self.output = self.root / 'output'
        self.value = {'schema': 1, 'provider': 'gitlab', 'baseUrl': 'https://gitlab.example.invalid/team',
                      'projectId': '42', 'packageName': 'company-workspace',
                      'channelPackage': 'company-workspace-channel', 'channelVersion': '0.0.0',
                      'allowedDownloadOrigins': ['https://gitlab.example.invalid']}

    def build(self):
        self.config.write_text(json.dumps(self.value), encoding='utf-8')
        env = os.environ.copy()
        env.pop('PSModulePath', None)
        return subprocess.run([str(POWERSHELL), '-NoProfile', '-NoLogo', '-ExecutionPolicy', 'Bypass',
                               '-File', str(ROOT / 'deploy/New-WorkspaceOnlineInstaller.ps1'),
                               '-UpdateConfig', str(self.config), '-ConfigPython', sys.executable,
                               '-OutputDirectory', str(self.output)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def test_compiled_exe_binds_server_identity_and_refuses_to_overwrite(self):
        built = self.build()
        self.assertEqual(0, built.returncode, (built.stdout + built.stderr).decode('utf-8', errors='replace')[-3000:])
        exe = self.output / 'AX-Workspace-Install.exe'
        raw = exe.read_bytes()
        self.assertTrue(raw.startswith(b'MZ'))
        self.assertLess(len(raw), 2 * 1024 * 1024)
        report = json.loads((ROOT / f'build/workspace-online-installer-{WORKSPACE_VERSION}.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(str(exe), report['installer'])
        self.assertEqual(WORKSPACE_VERSION, report['version'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), report['sha256'])
        self.assertEqual(len(raw), report['bytes'])
        self.assertEqual(source_from_config(self.value).config, report['updateSource'])
        self.assertEqual(source_from_config(self.value).identity, report['sourceIdentity'])
        self.assertTrue(report['embeddedConfigVerified'])
        self.assertFalse(report['appPayloadBundled'])
        self.assertFalse(report['pythonRequiredToInstall'])
        self.assertNotEqual(0, self.build().returncode)
        self.assertEqual(raw, exe.read_bytes())

    def test_embedded_credentials_and_http_are_rejected_before_build(self):
        for changes in ({'token': 'private-fixture-value'}, {'baseUrl': 'http://gitlab.example.invalid'},
                        {'baseUrl': 'https://user:password@gitlab.example.invalid'}):
            with self.subTest(changes=changes):
                previous = dict(self.value)
                self.value.update(changes)
                result = self.build()
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.output / 'AX-Workspace-Install.exe').exists())
                self.assertNotIn(b'private-fixture-value', result.stdout + result.stderr)
                self.value = previous


if __name__ == '__main__':
    unittest.main()
