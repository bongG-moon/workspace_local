"""Copied launcher resolution fixtures; no real UI, CLI or server is started."""
import json
import base64
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
$originalPythonQuery = ${function:Invoke-WorkspacePythonQuery}
function Invoke-WorkspacePythonQuery {
    param($Executable,$Arguments,$TimeoutMilliseconds)
    # Use the production hidden native-process query, changing only the
    # reported sys.executable. The ASCII JSON path must survive CP949 hosts.
    if ($Executable -eq 'Invoke-FixturePython') {
        $Executable = REAL_PYTHON
        $Arguments = $Arguments.Replace('-c "', ('-c "' + PYTHON_PRELUDE))
    }
    & $originalPythonQuery -Executable $Executable -Arguments $Arguments -TimeoutMilliseconds $TimeoutMilliseconds
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


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell dependency checks')
class PythonDependencyTests(unittest.TestCase):
    def powershell(self, body, *, env=None):
        source = LAUNCHER.read_text(encoding='utf-8-sig')
        functions = source[source.index('function Invoke-WorkspacePythonQuery'):source.index('\ntry {\n    $startupHelper')]
        return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                               '-ExecutionPolicy', 'Bypass', '-Command',
                               '[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)\n' + functions + '\n' + body],
                              env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=25,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def resolve(self, python_status=None, listed_status=1, *, explicit=False, list_installed=True):
        with tempfile.TemporaryDirectory(prefix='workspace-existing-python-한글 & ') as raw:
            installed = Path(raw) / '기존 Python 3.12' / 'python.exe'
            installed.parent.mkdir(); installed.touch()
            transcript = Path(raw) / 'queries.jsonl'
            listing = '-V:3.12 * ' + str(installed) if list_installed else 'No installed Pythons found!'
            body = r'''
function Get-Command {
    param($Name,$CommandType,$ErrorAction)
    if ($Name -eq 'python' -and PYTHON_FOUND) { return [pscustomobject]@{Source='path-python'} }
    if ($Name -eq 'py') { return [pscustomobject]@{Source='registered-py'} }
    throw [System.Management.Automation.CommandNotFoundException]::new('fixture missing command')
}
function Invoke-WorkspacePythonQuery {
    param($Executable,$Arguments,$TimeoutMilliseconds)
    [IO.File]::AppendAllText(TRANSCRIPT, (($Executable + '|' + $Arguments | ConvertTo-Json -Compress) + "`n"))
    if ($Executable -eq 'registered-py') {
        if ($Arguments -ne '-0p') { throw 'Must only list installed runtimes' }
        return LISTING
    }
    $status = if ($Executable -eq 'path-python') { PYTHON_STATUS } else { LISTED_STATUS }
    return PATH_JSON + "`n" + $status
}
try { Get-WorkspacePythonExecutable -Command SELECTED }
catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
'''
            for key, value in {
                'PYTHON_FOUND': '$true' if python_status is not None else '$false',
                'PYTHON_STATUS': str(python_status or 0), 'LISTED_STATUS': str(listed_status),
                'TRANSCRIPT': ps_quote(transcript), 'LISTING': ps_quote(listing),
                'PATH_JSON': ps_quote(json.dumps(str(installed))),
                'SELECTED': ps_quote('python' if explicit else 'auto'),
            }.items():
                body = body.replace(key, value)
            result = self.powershell(body)
            queries = [json.loads(line) for line in transcript.read_text().splitlines()] if transcript.exists() else []
            return result, queries, str(installed)

    def test_default_existing_python_is_preferred_without_calling_py(self):
        result, queries, installed = self.resolve(python_status=1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertEqual(1, len(queries))
        self.assertTrue(queries[0].startswith('path-python|'))

    def test_py_only_installation_uses_listed_exact_unicode_path(self):
        result, queries, installed = self.resolve()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertEqual('registered-py|-0p', queries[0])
        self.assertTrue(queries[1].startswith(installed + '|-B -X utf8 -c'))

    def test_old_or_incomplete_path_python_can_use_an_existing_compatible_runtime(self):
        for status in (0, 2):
            with self.subTest(status=status):
                result, queries, installed = self.resolve(python_status=status)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(installed, result.stdout.strip())
                self.assertEqual(3, len(queries))
                self.assertEqual('registered-py|-0p', queries[1])

    def test_explicit_missing_old_or_incomplete_python_never_uses_another_runtime(self):
        for status, code in ((None, '37'), (0, '38'), (2, '38')):
            with self.subTest(status=status):
                result, queries, _ = self.resolve(python_status=status, explicit=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('WORKSPACE_STARTUP:' + code, result.stderr)
                self.assertFalse(any('registered-py' in query for query in queries))

    def test_missing_installed_runtime_stops_after_listing_without_launch_or_install(self):
        result, queries, _ = self.resolve(list_installed=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('WORKSPACE_STARTUP:37', result.stderr)
        self.assertEqual(['registered-py|-0p'], queries)

    def test_probe_child_disables_auto_install_and_parent_environment_is_preserved(self):
        names = ('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'PYLAUNCHER_ALLOW_INSTALL',
                 'PYLAUNCHER_ALWAYS_INSTALL', 'CLAUDE_CONFIG_DIR', 'ANTHROPIC_MODEL')
        payload = ('import os,json; print(json.dumps({name:os.environ.get(name) for name in ' + repr(names) + '}))')
        code = base64.b64encode(payload.encode()).decode()
        args = '-B -c "import base64;exec(base64.b64decode(\'' + code + '\'))"'
        body = ('$raw = Invoke-WorkspacePythonQuery -Executable ' + ps_quote(sys.executable)
                + ' -Arguments ' + ps_quote(args) + '\n'
                + '$parent = @{}\n'
                + 'foreach ($name in @(' + ','.join(ps_quote(name) for name in names) + ')) '
                  '{ $parent[$name] = [Environment]::GetEnvironmentVariable($name) }\n'
                + '@{child=($raw | ConvertFrom-Json);parent=$parent} | ConvertTo-Json -Compress')
        injected = {name: 'fixture-parent-preserved' for name in names}
        result = self.powershell(body, env={**os.environ, **injected})
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(injected, value['parent'])
        self.assertEqual('false', value['child']['PYTHON_MANAGER_AUTOMATIC_INSTALL'])
        self.assertIsNone(value['child']['PYLAUNCHER_ALLOW_INSTALL'])
        self.assertIsNone(value['child']['PYLAUNCHER_ALWAYS_INSTALL'])
        for name in ('CLAUDE_CONFIG_DIR', 'ANTHROPIC_MODEL'):
            self.assertEqual(injected[name], value['child'][name])

    def test_probe_timeout_is_bounded_and_returns_no_valid_interpreter(self):
        body = ('$clock = [Diagnostics.Stopwatch]::StartNew(); $answer = Invoke-WorkspacePythonQuery -Executable '
                + ps_quote(sys.executable) + ' -Arguments \'-B -c "import time;time.sleep(5)"\' -TimeoutMilliseconds 100\n'
                + '@{empty=($null -eq $answer);elapsed=$clock.Elapsed.TotalSeconds} | ConvertTo-Json -Compress')
        result = self.powershell(body)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['empty'])
        self.assertLess(value['elapsed'], 3)


if __name__ == '__main__':
    unittest.main()
