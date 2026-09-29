"""Copied launcher resolution fixtures; no real UI, CLI or server is started."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from tests.test_workspace_startup import HELPER, LAUNCHER, ps_quote


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
class LauncherResolutionTests(unittest.TestCase):
    def launch(self, resolution, *, explicit=False, native=False, unicode_python=False, windowless=False):
        with tempfile.TemporaryDirectory(prefix='workspace-resolution-한글 & ') as raw:
            root = Path(raw)
            deploy = root / 'deploy'
            deploy.mkdir()
            state = root / 'state'
            result_file = root / 'selected.json'
            launcher = deploy / LAUNCHER.name
            launcher.write_bytes(LAUNCHER.read_bytes())
            native_executable = root / '.local' / 'bin' / 'claude.exe'
            if native:
                native_executable.parent.mkdir(parents=True)
                native_executable.touch()
            reported_python = root / '바탕 화면 & 실행 경로' / 'python.exe'
            if unicode_python:
                reported_python.parent.mkdir(parents=True)
                reported_python.touch()
                if windowless:
                    reported_python.with_name('pythonw.exe').touch()
            overrides = r'''
ENCODING_SETUP
function Get-WorkspaceVerifiedContext {
    [pscustomobject]@{verified=$true;sid='S-1-5-21-FIXTURE_SID';sessionId=1;isAdministrator=$false;localAppData=FIXTURE_ROOT;userProfile=FIXTURE_ROOT}
}
function Assert-WorkspaceNormalProcess { param($Context) }
function Show-WorkspaceStartupDialog { param($Message); throw 'A fixture must never show UI' }
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -eq 'claude') { RESOLUTION }
    if ($Name -eq 'fixture-python-with-unicode-report') {
        return [pscustomobject]@{Source='Invoke-FixturePython'}
    }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
function Invoke-FixturePython {
    # A real native Python writes the production probe output through the
    # redirected PS 5.1 byte channel. Only sys.executable's reported value is
    # isolated, so this fixture needs no additional embedded distribution.
    $forwardArgs = @($args)
    $forwardArgs[-1] = PYTHON_PRELUDE + [string]$forwardArgs[-1]
    & REAL_PYTHON @forwardArgs
}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
    $captured = @{
        unavailable=$env:COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE;
        entry=$env:COMPANY_WORKSPACE_CLAUDE_ENTRY;
        profile=$env:COMPANY_WORKSPACE_CLAUDE_PROFILE;
        definitionHash=$env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH;
        shellPresent=(-not [string]::IsNullOrWhiteSpace($env:COMPANY_WORKSPACE_SHELL));
        shellValid=($env:COMPANY_WORKSPACE_SHELL -and (Test-Path -LiteralPath $env:COMPANY_WORKSPACE_SHELL -PathType Leaf));
        nativeSelected=($env:COMPANY_WORKSPACE_CLAUDE_ENTRY -eq NATIVE_FILE);
        path=$env:PATH;
        explicit=$env:COMPANY_AGENT_CLAUDE;
        config=$env:CLAUDE_CONFIG_DIR;
        model=$env:ANTHROPIC_MODEL;
        python=$FilePath;
        pythonExists=(Test-Path -LiteralPath $FilePath -PathType Leaf);
        codePage=[Console]::OutputEncoding.CodePage;
        hidden=($WindowStyle -eq 'Hidden');
        appRequested=($ArgumentList -contains 'local_app.server')
    }
    [IO.File]::WriteAllText(RESULT_FILE, ($captured | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    $null = [IO.Directory]::CreateDirectory(STATE_ROOT)
    [IO.File]::WriteAllText((Join-Path STATE_ROOT 'runtime.json'), '{"pid": 70701}')
    [pscustomobject]@{Id=70701;HasExited=$false}
}
'''
            overrides = overrides.replace('FIXTURE_ROOT', ps_quote(root)).replace('RESOLUTION', resolution)
            overrides = overrides.replace('FIXTURE_SID', str(uuid.uuid4().int & 0x7fffffff))
            overrides = overrides.replace('RESULT_FILE', ps_quote(result_file)).replace('STATE_ROOT', ps_quote(state))
            overrides = overrides.replace('NATIVE_FILE', ps_quote(native_executable))
            overrides = overrides.replace('REAL_PYTHON', ps_quote(sys.executable))
            python_prelude = ("import sys; sys.executable = bytes.fromhex('"
                              + str(reported_python).encode('utf-8').hex() + "').decode('utf-8'); ")
            overrides = overrides.replace('PYTHON_PRELUDE', ps_quote(python_prelude))
            overrides = overrides.replace('ENCODING_SETUP',
                '[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(949)' if unicode_python else '')
            (deploy / HELPER.name).write_text(HELPER.read_text(encoding='utf-8-sig') + overrides,
                                            encoding='utf-8-sig')
            python_command = 'fixture-python-with-unicode-report' if unicode_python else sys.executable
            script = ('& ' + ps_quote(launcher) + ' -NoBrowser -PythonCommand ' + ps_quote(python_command)
                      + ' -StateRoot ' + ps_quote(state) + '; exit $LASTEXITCODE')
            env = os.environ.copy()
            env.update(COMPANY_AGENT_CLAUDE='explicit-fixture-wrapper' if explicit else '',
                       COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE='1',
                       COMPANY_WORKSPACE_CLAUDE_ENTRY='stale-entry', COMPANY_WORKSPACE_CLAUDE_PROFILE='1',
                       COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH='stale-hash', COMPANY_WORKSPACE_SHELL='stale-shell',
                       CLAUDE_CONFIG_DIR='fixture-config-preserved', ANTHROPIC_MODEL='fixture-model-preserved')
            completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                        '-ExecutionPolicy', 'Bypass', '-Command', script],
                                       env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=20,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn('private profile error', completed.stdout + completed.stderr)
            value = json.loads(result_file.read_text(encoding='utf-8'))
            self.assertTrue(value['hidden'])
            self.assertTrue(value['appRequested'])
            self.assertEqual('fixture-config-preserved', value['config'])
            self.assertEqual('fixture-model-preserved', value['model'])
            self.assertEqual(env.get('PATH'), value.pop('path'))
            if unicode_python:
                expected_python = reported_python.with_name('pythonw.exe') if windowless else reported_python
                self.assertEqual(str(expected_python), value['python'])
                self.assertTrue(value['pythonExists'])
                self.assertEqual(949, value['codePage'])
            return value

    def test_missing_claude_still_requests_app_and_blocks_silent_cli_fallback(self):
        value = self.launch("throw 'private profile error'")
        self.assertEqual('1', value['unavailable'])
        for name in ('entry', 'profile', 'definitionHash'):
            self.assertIn(value[name], (None, ''))
        self.assertFalse(value['shellPresent'])

    def test_unsupported_and_unresolved_alias_do_not_block_app_or_reuse_stale_entry(self):
        for resolution in ("return [pscustomobject]@{CommandType='Cmdlet'}",
                           "return [pscustomobject]@{CommandType='Alias';ResolvedCommand=$null}"):
            with self.subTest(resolution=resolution):
                value = self.launch(resolution)
                self.assertEqual('1', value['unavailable'])
                self.assertIn(value['entry'], (None, ''))

    def test_existing_wrapper_remains_selected_and_clears_old_unavailability(self):
        value = self.launch("return [pscustomobject]@{CommandType='ExternalScript';Source='fixture-wrapper.ps1'}")
        self.assertIn(value['unavailable'], (None, ''))
        self.assertEqual('fixture-wrapper.ps1', value['entry'])
        self.assertEqual('0', value['profile'])
        self.assertIn(value['definitionHash'], (None, ''))
        self.assertTrue(value['shellPresent'])

    def test_existing_function_retains_profile_contract_without_exporting_body(self):
        value = self.launch("return [pscustomobject]@{CommandType='Function';Definition='fixture function body'}")
        self.assertIn(value['unavailable'], (None, ''))
        self.assertEqual('claude', value['entry'])
        self.assertEqual('1', value['profile'])
        self.assertRegex(value['definitionHash'], r'^[A-F0-9]{2}(?:-[A-F0-9]{2}){31}$')
        self.assertNotIn('fixture function body', json.dumps(value))

    def test_explicit_cli_selection_is_preserved_without_automatic_resolution(self):
        value = self.launch("throw 'Automatic discovery should not run'", explicit=True)
        self.assertIn(value['unavailable'], (None, ''))
        self.assertEqual('explicit-fixture-wrapper', value['explicit'])
        self.assertTrue(value['shellValid'])
        for name in ('entry', 'profile', 'definitionHash'):
            self.assertIn(value[name], (None, ''))

    def test_native_install_outside_inherited_path_is_found_for_verified_user(self):
        value = self.launch("throw [System.Management.Automation.CommandNotFoundException]::new('fixture absent')", native=True)
        self.assertIn(value['unavailable'], (None, ''))
        self.assertTrue(value['nativeSelected'])
        self.assertEqual('0', value['profile'])
        self.assertTrue(value['shellValid'])
        self.assertIn(value['definitionHash'], (None, ''))

    def test_missing_native_install_remains_an_ai_issue_and_app_still_starts(self):
        value = self.launch("throw [System.Management.Automation.CommandNotFoundException]::new('fixture absent')")
        self.assertEqual('1', value['unavailable'])
        self.assertFalse(value['nativeSelected'])

    def test_native_fallback_does_not_mask_resolution_errors_or_broken_aliases(self):
        for resolution in ("throw 'private profile error'",
                           "return [pscustomobject]@{CommandType='Alias';ResolvedCommand=$null}"):
            with self.subTest(resolution=resolution):
                value = self.launch(resolution, native=True)
                self.assertEqual('1', value['unavailable'])
                self.assertFalse(value['nativeSelected'])

    def test_native_fallback_never_overrides_profile_function(self):
        value = self.launch("return [pscustomobject]@{CommandType='Function';Definition='fixture function body'}", native=True)
        self.assertEqual('claude', value['entry'])
        self.assertEqual('1', value['profile'])
        self.assertFalse(value['nativeSelected'])

    def test_hidden_cp949_launcher_preserves_unicode_python_probe_path(self):
        self.launch("throw 'Automatic discovery should not run'", explicit=True, unicode_python=True)

    def test_hidden_cp949_launcher_finds_unicode_pythonw_path(self):
        self.launch("throw 'Automatic discovery should not run'", explicit=True,
                    unicode_python=True, windowless=True)


if __name__ == '__main__':
    unittest.main()
