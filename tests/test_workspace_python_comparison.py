"""Incomplete comparison and source integrity must never imply app success."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ws38_package', ROOT / 'scripts/test-lab/package-ws38-diagnostic.py')
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell comparison diagnostic')
class PythonComparisonTests(unittest.TestCase):
    def run_fixture(self, child_exit=None, child_status='checked', tamper=False, generic_list=False):
        with tempfile.TemporaryDirectory(prefix='workspace-진단 & ') as raw:
            base = Path(raw)
            package = base / 'diagnostic.zip'
            PACKAGER.package(package)
            with zipfile.ZipFile(package) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(9, len(archive.namelist()))
                archive.extractall(base)
            tool = base / 'Company-Workspace-Python-Diagnostic'
            # These stubs exist only inside this disposable test package. They
            # never edit the production guard, token or original source files.
            helper = tool / 'deploy/CompanyWorkspace.Startup.ps1'
            helper.write_text(helper.read_text(encoding='utf-8-sig') + r'''
function Get-WorkspaceVerifiedContext {
    [pscustomobject]@{verified=$true;sid='S-1-5-21-4242001';sessionId=1;isAdministrator=$true}
}
function Get-WorkspaceLaunchAction { param($Context,$Relaunched); 'relaunch' }
function Invoke-WorkspaceNormalTokenRelaunch {
    param($Context,$ScriptPath,$PythonCommand,$Demo,$NoBrowser,$StateRoot)
    CHILD_REPORT
    return CHILD_EXIT
}
'''.replace('CHILD_REPORT', '' if child_exit in (22, None) else (
                "$result=@{diagnosticVersion='ws38-1';runId=(Split-Path $StateRoot -Leaf);status='" + child_status +
                "';result=" + ("@{kind='fixture';status='checked'}" if child_status == 'checked' else '$null') +
                ";failure=$null}; [IO.File]::WriteAllText((Join-Path $StateRoot 'child.json'),($result|ConvertTo-Json -Depth 6))"
            )).replace('CHILD_EXIT', str(1 if child_exit is None else child_exit)), encoding='utf-8-sig')
            launcher = tool / 'deploy/Start-CompanyWorkspace.ps1'
            names = ['Invoke-WorkspacePythonQuery', 'Get-WorkspacePythonRegistryEntries',
                     'Get-WorkspaceRegisteredPythonCandidates', 'Add-WorkspacePythonCheck']
            resolver_body = "throw 'WORKSPACE_STARTUP:37'"
            if generic_list:
                # Match the production resolver's List[object] result boundary.
                # Actual process permission differs in sandbox/current-user runs.
                resolver_body = r'''
                $script:WorkspacePythonChecks=New-Object 'System.Collections.Generic.List[object]'
                $script:WorkspacePythonChecks.Add([pscustomobject]@{source='explicit';path=$Command;status='accepted';version='3.11.5'})
                return $Command
                '''
            launcher.write_text('\n'.join(f'function {name} {{ }}' for name in names) +
                                '\nfunction Get-WorkspacePythonExecutable { param($Command); ' + resolver_body + ' }\n',
                                encoding='utf-8-sig')
            manifest_file = tool / 'manifest.json'
            manifest = json.loads(manifest_file.read_text())
            for name in manifest['sha256']:
                manifest['sha256'][name] = hashlib.sha256((tool / name).read_bytes()).hexdigest()
            manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
            if tamper:
                with helper.open('a', encoding='utf-8') as stream:
                    stream.write("\nthrow 'MUST_NOT_EXECUTE_TAMPERED_SOURCE'\n")
            completed = subprocess.run([
                'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(tool / 'Check-Python.ps1'),
                '-PythonCommand', sys.executable],
                capture_output=True, encoding='utf-8', errors='replace', timeout=40,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            reports = list(tool.glob('Workspace-Python-Diagnostic-*.json'))
            self.assertEqual(1, len(reports), completed.stdout + completed.stderr)
            text = reports[0].read_text(encoding='utf-8')
            self.assertNotIn('S-1-5-21-', text)
            self.assertNotIn('MUST_NOT_EXECUTE', text)
            return completed.returncode, json.loads(text)

    def test_failed_or_missing_child_preserves_current_and_stays_incomplete(self):
        for code, status in ((22, 'checked'), (None, 'checked'), (1, 'failed')):
            with self.subTest(code=code, status=status):
                exit_code, report = self.run_fixture(code, status)
                self.assertEqual(1, exit_code)
                self.assertEqual('incomplete', report['status'])
                self.assertIsNotNone(report['current'])
                self.assertFalse(report['originalAppFailureObserved'])
                self.assertIsNone(report['production'])

    def test_finished_comparison_is_distinct_from_python_or_app_success(self):
        exit_code, report = self.run_fixture(0, 'checked')
        self.assertEqual(0, exit_code)
        self.assertEqual('checked', report['status'])
        self.assertEqual('failed', report['current']['productionQuery']['status'])
        self.assertFalse(report['originalAppFailureObserved'])
        self.assertEqual('python_prerequisite_only', report['scope'])

    def test_changed_source_is_rejected_before_running_it(self):
        exit_code, report = self.run_fixture(tamper=True)
        self.assertEqual(1, exit_code)
        self.assertFalse(report['sourceMatches'])
        self.assertEqual('source_mismatch', report['failure']['detail']['reason'])
        self.assertIsNone(report['current'])

    def test_resolver_generic_list_survives_powershell_serialization(self):
        exit_code, report = self.run_fixture(0, 'checked', generic_list=True)
        self.assertEqual(0, exit_code, report)
        query = report['current']['productionQuery']
        self.assertEqual('accepted', query['status'], report)
        self.assertGreater(len(query['checks']), 0)
        self.assertEqual('accepted', query['checks'][-1]['status'])
        self.assertEqual(Path(sys.executable), Path(query['selectedPython']))


if __name__ == '__main__':
    unittest.main()
