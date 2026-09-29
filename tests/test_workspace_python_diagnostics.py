"""Python startup failure evidence without launching a real app or changing settings."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.test_workspace_startup import HELPER, LAUNCHER, ps_quote


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell diagnostic behavior')
class PythonDiagnosticTests(unittest.TestCase):
    def ps(self, body):
        return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-Command',
            '[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); '
            '$ErrorActionPreference="Stop"; Set-StrictMode -Version 2.0; ' + body],
            capture_output=True, encoding='utf-8', errors='replace', timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def test_allowlisted_evidence_preserves_path_but_omits_private_output(self):
        with tempfile.TemporaryDirectory(prefix='workspace-python-진단 & ') as raw:
            body = '. ' + ps_quote(HELPER) + '; $root=' + ps_quote(raw) + r'''
$checks = @([pscustomobject]@{source='path';path=(Join-Path $root '기존 Python\python.exe');
 status='missing_module';version='3.11.5';nativeCode=193;missingModules=@('ssl','PRIVATE_TOKEN');
 stderr='PRIVATE_TOKEN';profileBody='PRIVATE_PROFILE';environment='PRIVATE_ENV'})
$value=Write-WorkspacePythonDiagnostic -StateRoot $root -AppRoot $root -Checks $checks -Code 38 -AttemptId aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
$value | ConvertTo-Json -Depth 6 -Compress
'''
            result = self.ps(body)
            self.assertEqual(0, result.returncode, result.stderr)
            value = json.loads(result.stdout)
            report = Path(value['path']).read_text(encoding='utf-8')
            self.assertNotIn('PRIVATE_', report)
            saved = json.loads(report)
            self.assertEqual('WS-38', saved['code'])
            self.assertEqual('3.11.5', saved['checks'][0]['version'])
            self.assertEqual(['ssl'], saved['checks'][0]['missingModules'])
            self.assertIn('기존 Python', saved['checks'][0]['path'])
            self.assertEqual(193, saved['checks'][0]['nativeCode'])

    def test_recent_child_report_must_match_source_and_start_time(self):
        with tempfile.TemporaryDirectory(prefix='workspace-python-fresh-') as raw:
            body = '. ' + ps_quote(HELPER) + '; $root=' + ps_quote(raw) + r'''
$since=[DateTime]::UtcNow
$checks=@([pscustomobject]@{source='path';path='C:\Python311\python.exe';status='old_version';version='3.10.9'})
$saved=Write-WorkspacePythonDiagnostic $root $root $checks 38 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
$other=Write-WorkspacePythonDiagnostic $root $root $checks 38 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
$found=Read-WorkspaceRecentPythonDiagnostic $root $root $since aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 38
$wrongCode=Read-WorkspaceRecentPythonDiagnostic $root $root $since aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 37
$foreign=Read-WorkspaceRecentPythonDiagnostic $root (Join-Path $root 'another-build') $since aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 38
(Get-Item -LiteralPath $saved.path).LastWriteTimeUtc=$since.AddMinutes(-1)
$old=Read-WorkspaceRecentPythonDiagnostic $root $root $since aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 38
@{found=($found.path -eq $saved.path);foreign=($null -ne $foreign);old=($null -ne $old);wrongCode=($null -ne $wrongCode)} | ConvertTo-Json -Compress
'''
            result = self.ps(body)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({'found': True, 'foreign': False, 'old': False, 'wrongCode': False}, json.loads(result.stdout))

    def test_unwritable_diagnostic_does_not_hide_failure_details(self):
        with tempfile.TemporaryDirectory(prefix='workspace-python-no-log-') as raw:
            blocked = Path(raw) / 'not-a-directory'
            blocked.write_text('preserve', encoding='utf-8')
            body = '. ' + ps_quote(HELPER) + '; $blocked=' + ps_quote(blocked) + r'''
$checks=@([pscustomobject]@{source='path';path='C:\Python311\python.exe';status='timed_out'})
$saved=Write-WorkspacePythonDiagnostic $blocked $blocked $checks 38 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
@{saved=($null -ne $saved);text=(Get-WorkspacePythonDiagnosticText -Diagnostic $saved -Checks $checks)} | ConvertTo-Json -Compress
'''
            result = self.ps(body)
            self.assertEqual(0, result.returncode, result.stderr)
            value = json.loads(result.stdout)
            self.assertFalse(value['saved'])
            self.assertIn('시간 초과', value['text'])
            self.assertEqual('preserve', blocked.read_text(encoding='utf-8'))

    def test_real_launcher_reports_direct_and_relaunched_child_failures(self):
        for child in (False, True):
            with self.subTest(child=child), tempfile.TemporaryDirectory(prefix='workspace-python-flow-') as raw:
                root = Path(raw)
                deploy = root / 'deploy'
                deploy.mkdir()
                shutil.copy2(LAUNCHER, deploy / LAUNCHER.name)
                state = root / 'state'
                checks = "@([pscustomobject]@{source='path';path='C:\\Python311\\python.exe';status='missing_module';version='3.11.5';missingModules=@('ssl')})"
                overrides = '\nSet-StrictMode -Version 2.0\n'
                overrides += 'function Get-WorkspaceVerifiedContext { [pscustomobject]@{verified=$true;sid="S-1-5-21-4242001";sessionId=1;isAdministrator=$' + str(child).lower() + ';localAppData=' + ps_quote(root) + '} }\n'
                overrides += 'function Assert-WorkspaceNormalProcess { param($Context) }\n'
                overrides += 'function Get-WorkspacePythonExecutable { param($Command); $script:WorkspacePythonChecks = New-Object "System.Collections.Generic.List[object]"; foreach($row in ' + checks + '){$script:WorkspacePythonChecks.Add($row)}; throw "WORKSPACE_STARTUP:38" }\n'
                overrides += 'function Invoke-WorkspaceNormalTokenRelaunch { param($Context,$ScriptPath,$PythonCommand,$Demo,$NoBrowser,$StateRoot); $null=Write-WorkspacePythonDiagnostic -StateRoot $StateRoot -AppRoot ' + ps_quote(root) + ' -Checks ' + checks + ' -Code 38 -AttemptId $env:COMPANY_WORKSPACE_PYTHON_ATTEMPT; return 38 }\n'
                overrides += 'function Show-WorkspaceStartupDialog { throw "Unexpected UI" }\n'
                (deploy / HELPER.name).write_text(HELPER.read_text(encoding='utf-8-sig') + overrides, encoding='utf-8-sig')
                result = self.ps('& ' + ps_quote(deploy / LAUNCHER.name) + ' -Demo -NoBrowser -StateRoot ' + ps_quote(state) + '; exit $LASTEXITCODE')
                self.assertEqual(38, result.returncode, result.stderr)
                self.assertIn('WS-38', result.stderr)
                self.assertIn('3.11.5', result.stderr)
                self.assertIn('ssl', result.stderr)
                self.assertIn('python-check-', result.stderr)
                self.assertEqual(1, len(list(state.glob('diagnostics/python-check-*.json'))))


if __name__ == '__main__':
    unittest.main()
