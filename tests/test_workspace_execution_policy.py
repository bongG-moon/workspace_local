"""Exercise downloaded script startup with process-only policy flags."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from local_app.windows_process import powershell_path


@unittest.skipUnless(os.name == 'nt', 'Windows execution policy')
class ProcessExecutionPolicyTests(unittest.TestCase):
    def run_ps(self, args):
        return subprocess.run([powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive', *args],
                              capture_output=True, timeout=30, encoding='utf-8', errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def policy_snapshot(self):
        result = self.run_ps(['-Command', "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop; "
                             'Get-ExecutionPolicy -List | ForEach-Object { '
                             '[pscustomobject]@{scope=[string]$_.Scope; policy=[string]$_.ExecutionPolicy} '
                             '} | ConvertTo-Json -Compress'])
        self.assertEqual(0, result.returncode)
        return {r['scope']: r['policy'] for r in json.loads(result.stdout)}

    def test_download_marked_fixture_runs_with_bypass_without_persistent_policy_change(self):
        before = self.policy_snapshot()
        if any(before.get(key) != 'Undefined' for key in ('MachinePolicy', 'UserPolicy')):
            self.skipTest('Group Policy is authoritative; do not try to override it')
        with tempfile.TemporaryDirectory(prefix='workspace-policy-') as tmp:
            script = Path(tmp) / 'benign fixture.ps1'
            script.write_text("[Console]::Write('WORKSPACE_POLICY_OK'); exit 0", encoding='ascii')
            zone = Path(str(script) + ':Zone.Identifier')
            try:
                zone.write_text('[ZoneTransfer]\nZoneId=3\n', encoding='ascii')
            except OSError:
                self.skipTest('Alternate data streams unavailable for downloaded-file fixture')
            blocked = self.run_ps(['-ExecutionPolicy', 'Restricted', '-File', str(script)])
            allowed = self.run_ps(['-ExecutionPolicy', 'Bypass', '-File', str(script)])
            self.assertNotEqual(0, blocked.returncode)
            self.assertNotIn('WORKSPACE_POLICY_OK', blocked.stdout)
            self.assertEqual(0, allowed.returncode)
            self.assertEqual('WORKSPACE_POLICY_OK', allowed.stdout)
            self.assertTrue(zone.exists(), 'The launcher must not remove download markers')
        self.assertEqual(before, self.policy_snapshot())
