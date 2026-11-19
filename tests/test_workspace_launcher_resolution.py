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
from unittest.mock import patch

from local_app.bridge import launch_marker_error, probe_discovered_cli, resolve_cli
from tests.test_workspace_startup import HELPER, LAUNCHER, ps_quote


SHIM_FIXTURES = Path(__file__).parent / 'fixtures' / 'npm_claude_shims'


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
class LauncherResolutionTests(unittest.TestCase):
    def launch(self, resolution, *, explicit=False, native=False, npm=False, unicode_python=False, windowless=False, startup_failure=False):
        with tempfile.TemporaryDirectory(prefix='workspace-resolution-한글 & ') as raw:
            root = Path(raw)
            deploy = root / 'deploy'
            deploy.mkdir()
            state = root / 'state'
            result_file = root / 'selected.json'
            launcher = deploy / LAUNCHER.name
            launcher.write_bytes(LAUNCHER.read_bytes())
            if startup_failure:
                profile = root / 'synthetic.ps1'
                profile.write_bytes(b'# private profile error\nif (')
                # Mimic an automatic profile error already in this fresh host's
                # error collection before the actual -File launcher's first action.
                prelude = 'try { . ' + ps_quote(profile) + ' } catch {}\n'
                launcher.write_text(launcher.read_text(encoding='utf-8-sig').replace(
                    "$ErrorActionPreference = 'Stop'", prelude + "$ErrorActionPreference = 'Stop'", 1), encoding='utf-8-sig')
                helper = root / 'local_app' / 'ClaudeLaunchDiagnostic.ps1'
                helper.parent.mkdir()
                helper.write_bytes((LAUNCHER.parents[1] / 'local_app' / helper.name).read_bytes())
            native_executable = root / '.local' / 'bin' / 'claude.exe'
            if native:
                native_executable.parent.mkdir(parents=True)
                native_executable.touch()
            npm_shim = root / 'npm' / 'claude.ps1'
            if npm:
                npm_shim.parent.mkdir()
                npm_shim.write_bytes((SHIM_FIXTURES / 'node.ps1.txt').read_bytes())
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
function Get-WorkspaceExecutionMode { param($Context); return 'normal' }
function Show-WorkspaceStartupDialog { param($Message); throw 'A fixture must never show UI' }
function Get-Command {
    param($Name, $CommandType, $ErrorAction, [switch]$All)
    if ($Name -eq 'claude') { RESOLUTION }
    if ($Name -eq 'fixture-python-with-unicode-report') {
        return [pscustomobject]@{Source=REPORTED_PYTHON;CommandType='Application'}
    }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
$originalPythonQuery = ${function:Invoke-WorkspacePythonQuery}
function Invoke-WorkspacePythonQuery {
    param($Executable,$Arguments,$TimeoutMilliseconds)
    # Use the production hidden native-process query, changing only the
    # reported sys.executable. The ASCII JSON path must survive CP949 hosts.
    if ($Executable -eq REPORTED_PYTHON) {
        $Executable = REAL_PYTHON
        $Arguments = $Arguments.Replace('-c "', ('-c "' + PYTHON_PRELUDE))
    }
    & $originalPythonQuery -Executable $Executable -Arguments $Arguments -TimeoutMilliseconds $TimeoutMilliseconds
}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
    $captured = @{
        unavailable=$env:COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE;
        startupDiagnostic=$env:COMPANY_WORKSPACE_CLAUDE_STARTUP_DIAGNOSTIC;
        entry=$env:COMPANY_WORKSPACE_CLAUDE_ENTRY;
        profile=$env:COMPANY_WORKSPACE_CLAUDE_PROFILE;
        definitionHash=$env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH;
        shellPresent=(-not [string]::IsNullOrWhiteSpace($env:COMPANY_WORKSPACE_SHELL));
        shellValid=($env:COMPANY_WORKSPACE_SHELL -and (Test-Path -LiteralPath $env:COMPANY_WORKSPACE_SHELL -PathType Leaf));
        nativeSelected=($env:COMPANY_WORKSPACE_CLAUDE_ENTRY -eq NATIVE_FILE);
        nativeCandidate=$env:COMPANY_WORKSPACE_CLAUDE_NATIVE_CANDIDATE;
        npmSelected=($env:COMPANY_WORKSPACE_CLAUDE_ENTRY -eq NPM_FILE);
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
            overrides = overrides.replace('NATIVE_FILE', ps_quote(native_executable)).replace('NPM_FILE', ps_quote(npm_shim))
            overrides = overrides.replace('REAL_PYTHON', ps_quote(sys.executable))
            overrides = overrides.replace('REPORTED_PYTHON', ps_quote(reported_python))
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
            if npm:
                env['PATH'] = str(npm_shim.parent) + os.pathsep + env['PATH']
            env.update(COMPANY_AGENT_CLAUDE='explicit-fixture-wrapper' if explicit else '',
                       COMPANY_WORKSPACE_CLAUDE_NATIVE_CANDIDATE='stale-candidate',
                       COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE='1',
                       COMPANY_WORKSPACE_CLAUDE_ENTRY='stale-entry', COMPANY_WORKSPACE_CLAUDE_PROFILE='1',
                       COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH='stale-hash', COMPANY_WORKSPACE_SHELL='stale-shell',
                       CLAUDE_CONFIG_DIR='fixture-config-preserved', ANTHROPIC_MODEL='fixture-model-preserved')
            invoke = (['-File', str(launcher), '-NoBrowser', '-PythonCommand', python_command, '-StateRoot', str(state)]
                      if startup_failure else ['-Command', script])
            completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                        '-ExecutionPolicy', 'Bypass', *invoke],
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
            if value['nativeCandidate']:
                self.assertEqual(str(native_executable), value['nativeCandidate'])
            if unicode_python:
                expected_python = reported_python.with_name('pythonw.exe') if windowless else reported_python
                self.assertEqual(str(expected_python), value['python'])
                self.assertTrue(value['pythonExists'])
                self.assertEqual(949, value['codePage'])
            return value

    def test_failed_startup_profile_is_observed_without_blocking_a_working_cli(self):
        result = self.launch("return [pscustomobject]@{CommandType='ExternalScript';Source='fixture-wrapper.ps1'}", startup_failure=True)
        self.assertFalse(result['unavailable'])
        error = launch_marker_error(result['startupDiagnostic'])
        self.assertEqual('launcher_profile', error.diagnostic['stage'])
        self.assertEqual('profile_parse', error.diagnostic['category'])
        self.assertNotIn('encodingHint', error.diagnostic)

    def test_missing_claude_still_requests_app_and_blocks_silent_cli_fallback(self):
        value = self.launch("throw 'private profile error'")
        self.assertEqual('1', value['unavailable'])
        for name in ('entry', 'profile', 'definitionHash'):
            self.assertIn(value[name], (None, ''))
        self.assertFalse(value['shellPresent'])
        self.assertFalse(value['nativeCandidate'])

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
        self.assertFalse(value['nativeCandidate'])
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
        self.assertFalse(value['nativeCandidate'])

    def test_stale_path_finds_stock_npm_and_offers_verified_user_native(self):
        value = self.launch('', native=True, npm=True)
        self.assertTrue(value['npmSelected'])
        self.assertTrue(value['nativeCandidate'])
        self.assertEqual('0', value['profile'])

    def test_alias_explicitly_pointing_at_npm_retains_that_entry(self):
        value = self.launch("return [pscustomobject]@{CommandType='Alias';ResolvedCommand=(Microsoft.PowerShell.Core\\Get-Command NPM_FILE)}",
                            native=True, npm=True)
        self.assertTrue(value['npmSelected'])
        self.assertFalse(value['nativeCandidate'])

    def test_npm_only_uses_the_existing_entry(self):
        value = self.launch('', npm=True)
        self.assertTrue(value['npmSelected'])
        self.assertFalse(value['nativeCandidate'])

    def test_hidden_cp949_launcher_preserves_unicode_python_probe_path(self):
        self.launch("throw 'Automatic discovery should not run'", explicit=True, unicode_python=True)

    def test_hidden_cp949_launcher_finds_unicode_pythonw_path(self):
        self.launch("throw 'Automatic discovery should not run'", explicit=True,
                    unicode_python=True, windowless=True)


@unittest.skipUnless(os.name == 'nt', 'Windows Claude entry selection')
class ClaudeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='workspace-cli-한글 & ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.native = self.root / '.local' / 'bin' / 'claude.exe'
        self.native.parent.mkdir(parents=True)
        self.native.touch()
        self.shim = self.root / 'npm' / 'claude.ps1'
        self.shim.parent.mkdir()
        self.shim.write_bytes((SHIM_FIXTURES / 'node.ps1.txt').read_bytes())
        self.enterContext(patch.dict(os.environ, {
            'COMPANY_WORKSPACE_CLAUDE_ENTRY': str(self.shim),
            'COMPANY_WORKSPACE_CLAUDE_NATIVE_CANDIDATE': str(self.native),
            'COMPANY_WORKSPACE_CLAUDE_PROFILE': '0', 'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '',
            'COMPANY_WORKSPACE_SHELL': sys.executable, 'COMPANY_AGENT_CLAUDE': '',
        }))
        self.info = {'version': '2.1.306 (Claude Code)', 'help': '--input-format --output-format --permission-prompt-tool'}
        self.calls = []
        self.native_compatible = True
        self.enterContext(patch('local_app.bridge.run_owned', side_effect=self.run_probe))

    def run_probe(self, args, **kwargs):
        entry, flag = args[-2:]
        self.assertIn(flag, ('--version', '--help'))
        self.calls.append((entry, flag))
        stdout = self.info['version'] if flag == '--version' else self.info['help']
        if entry == str(self.native) and not self.native_compatible and flag == '--help':
            stdout = 'fixture without stream support'
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    def test_unmodified_npm_templates_select_native_with_one_set_of_probes(self):
        for filename in ('native.ps1', 'native.cmd', 'node.ps1', 'node.cmd'):
            with self.subTest(fixture=filename):
                fixture = SHIM_FIXTURES / (filename + '.txt')
                shim = self.shim.with_suffix(Path(filename).suffix)
                # npm may write LF or CRLF on Windows.
                shim.write_bytes(fixture.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
                with patch.dict(os.environ, {'COMPANY_WORKSPACE_CLAUDE_ENTRY': str(shim)}):
                    self.calls.clear()
                    command, info = probe_discovered_cli(resolve_cli())
                self.assertEqual([str(self.native)], command)
                self.assertEqual(self.info, info)
                self.assertEqual([(str(self.native), '--version'), (str(self.native), '--help')], self.calls)

    def test_custom_wrapper_and_explicit_npm_selection_are_respected(self):
        stock = self.shim.read_bytes()
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                self.shim.write_bytes(stock if explicit else b"$env:FIXTURE_TEAM='test'\n" + stock)
                with patch.dict(os.environ, {'COMPANY_AGENT_CLAUDE': str(self.shim) if explicit else ''}):
                    self.calls.clear()
                    command, info = probe_discovered_cli(resolve_cli())
                self.assertEqual(str(self.shim), command[-1])
                self.assertEqual(self.info, info)
                self.assertEqual([(str(self.shim), '--version'), (str(self.shim), '--help')], self.calls)

    def test_startup_and_recheck_report_the_entry_that_passed_the_existing_probe(self):
        from local_app.server import LocalApp
        self.native_compatible = False
        app = LocalApp(self.root / 'state', managed_workspace_root=self.root / 'workspaces')
        self.addCleanup(app.close)
        self.assertIsNone(app.error)
        self.assertEqual(str(self.shim), app.bootstrap()['runtime']['entry'])
        self.assertEqual([(str(path), flag) for path in (self.native, self.shim)
                          for flag in ('--version', '--help')], self.calls)
        self.native_compatible = True
        self.calls.clear()
        result = app.reconnect()
        self.assertTrue(result['ok'])
        self.assertEqual(str(self.native), result['runtime']['entry'])
        self.assertEqual([(str(self.native), '--version'), (str(self.native), '--help')], self.calls)

    def test_native_removed_after_discovery_uses_the_original_npm_entry(self):
        self.native.unlink()
        command, info = probe_discovered_cli(resolve_cli())
        self.assertEqual(str(self.shim), command[-1])
        self.assertEqual(self.info, info)
        self.assertEqual([(str(self.shim), '--version'), (str(self.shim), '--help')], self.calls)

    def test_failed_native_probe_cannot_execute_a_replaced_shim_as_fallback(self):
        def replace_during_probe(command):
            self.shim.write_text('Write-Output changed', encoding='utf-8')
            raise OSError('fixture native failed')
        with patch('local_app.bridge.probe_cli', side_effect=replace_during_probe) as probe:
            with self.assertRaisesRegex(OSError, 'fixture native failed'):
                probe_discovered_cli(resolve_cli())
        probe.assert_called_once_with([str(self.native)])


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell dependency checks')
class PythonDependencyTests(unittest.TestCase):
    def powershell(self, body, *, env=None):
        source = LAUNCHER.read_text(encoding='utf-8-sig')
        functions = source[source.index('function Invoke-WorkspacePythonQuery'):source.index('\ntry {\n    $startupHelper')]
        return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                               '-ExecutionPolicy', 'Bypass', '-Command',
                               '$ErrorActionPreference="Stop"\nSet-StrictMode -Version 2\n[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)\n' + functions + '\n' + body],
                              env=os.environ.copy() if env is None else env,
                              capture_output=True, encoding='utf-8', errors='replace', timeout=35,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def resolve(self, python_status=None, listed_status=1, *, explicit=False, list_installed=True,
                later_status=None, registry=False, noise=False, command_kind='Application',
                extension='.exe', query_failure=None, alias_target='Application', malformed=False):
        with tempfile.TemporaryDirectory(prefix='workspace-existing-python-한글 & ') as raw:
            installed = Path(raw) / '기존 Python 3.12' / 'python.exe'
            installed.parent.mkdir(); installed.touch()
            path_python = Path(raw) / ('path-python' + extension)
            path_python.touch()
            later_python = Path(raw) / 'later-python.exe'
            later_python.touch()
            py_launcher = Path(raw) / 'registered-py.exe'
            transcript = Path(raw) / 'queries.jsonl'
            diagnosis = Path(raw) / 'diagnosis.json'
            listing = '-V:3.12 * ' + str(installed) if list_installed else 'No installed Pythons found!'
            body = r'''
function Get-Command {
    param($Name,$CommandType,$ErrorAction,[switch]$All)
    if ($Name -eq 'python' -and PYTHON_FOUND) {
        if (-not $CommandType) {
            return [pscustomobject]@{Source=PATH_PYTHON;CommandType=COMMAND_KIND;Definition='PRIVATE_FUNCTION_BODY';ResolvedCommand=[pscustomobject]@{Source=PATH_PYTHON;CommandType=ALIAS_TARGET}}
        }
        [pscustomobject]@{Source=PATH_PYTHON;CommandType='Application'}
        if ($All -and LATER_FOUND) { [pscustomobject]@{Source=LATER_PYTHON;CommandType='Application'} }
        return
    }
    if ($Name -eq 'py') { return [pscustomobject]@{Source=PY_LAUNCHER;CommandType='Application'} }
    throw [System.Management.Automation.CommandNotFoundException]::new('fixture missing command')
}
function Get-WorkspaceRegisteredPythonCandidates {
    param($Deadline)
    if (REGISTRY_FOUND) { [pscustomobject]@{source='registry_current_user';path=INSTALLED;unsupported=$false} }
}
function Invoke-WorkspacePythonQuery {
    param($Executable,$Arguments,$TimeoutMilliseconds)
    [IO.File]::AppendAllText(TRANSCRIPT, (($Executable + '|' + $Arguments | ConvertTo-Json -Compress) + "`n"))
    if ($Executable -eq PY_LAUNCHER) {
        if ($Arguments -ne '-0p') { throw 'Must only list installed runtimes' }
        return LISTING
    }
    if ($Executable -eq PATH_PYTHON -and QUERY_FAILURE) {
        $script:WorkspacePythonQueryStatus=QUERY_FAILURE
        $script:WorkspacePythonQueryNativeCode=5
        return $null
    }
    $status = if ($Executable -eq PATH_PYTHON) { PYTHON_STATUS } elseif ($Executable -eq LATER_PYTHON) { LATER_STATUS } else { LISTED_STATUS }
    $version = if ($status -eq 0) { @(3,10,9) } else { @(3,11,5) }
    $line = 'WORKSPACE_PYTHON_V1:' + (@{executable=INSTALLED;version=$version;modules=($status -ne 2);missingModules=@('ssl','PRIVATE_MODULE')} | ConvertTo-Json -Compress)
    if (MALFORMED) { return $line + "`n" + $line }
    if (WITH_NOISE) { return "Company wrapper notice`n" + $line + "`nCompany wrapper completed" }
    return $line
}
try { Get-WorkspacePythonExecutable -Command SELECTED }
catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
finally {
    @{checks=@($script:WorkspacePythonChecks.ToArray());selected=$script:WorkspacePythonSelected;failure=$script:WorkspacePythonFailureKind} |
        ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath DIAGNOSIS -Encoding UTF8
}
'''
            for key, value in {
                'PYTHON_FOUND': '$true' if python_status is not None else '$false',
                'PYTHON_STATUS': str(python_status or 0), 'LISTED_STATUS': str(listed_status),
                'LATER_FOUND': '$true' if later_status is not None else '$false',
                'LATER_STATUS': str(later_status or 0), 'LATER_PYTHON': ps_quote(later_python),
                'PATH_PYTHON': ps_quote(path_python), 'PY_LAUNCHER': ps_quote(py_launcher),
                'REGISTRY_FOUND': '$true' if registry else '$false', 'INSTALLED': ps_quote(installed),
                'WITH_NOISE': '$true' if noise else '$false', 'MALFORMED': '$true' if malformed else '$false',
                'QUERY_FAILURE': ps_quote(query_failure or ''), 'COMMAND_KIND': ps_quote(command_kind),
                'ALIAS_TARGET': ps_quote(alias_target), 'DIAGNOSIS': ps_quote(diagnosis),
                'TRANSCRIPT': ps_quote(transcript), 'LISTING': ps_quote(listing),
                'SELECTED': ps_quote('python' if explicit else 'auto'),
            }.items():
                body = body.replace(key, value)
            result = self.powershell(body)
            queries = [json.loads(line) for line in transcript.read_text(encoding='utf-8').splitlines()] if transcript.exists() else []
            self.diagnosis = json.loads(diagnosis.read_text(encoding='utf-8-sig'))
            return result, queries, str(installed)

    def test_default_existing_python_is_preferred_without_calling_py(self):
        result, queries, installed = self.resolve(python_status=1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertEqual(1, len(queries))
        self.assertIn('path-python.exe|-B -X utf8 -c', queries[0])
        self.assertEqual('accepted', self.diagnosis['checks'][0]['status'])

    def test_py_only_installation_uses_listed_exact_unicode_path(self):
        result, queries, installed = self.resolve()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertTrue(queries[0].endswith('registered-py.exe|-0p'))
        self.assertTrue(queries[1].startswith(installed + '|-B -X utf8 -c'))

    def test_old_or_incomplete_path_python_can_use_an_existing_compatible_runtime(self):
        for status in (0, 2):
            with self.subTest(status=status):
                result, queries, installed = self.resolve(python_status=status)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(installed, result.stdout.strip())
                self.assertEqual(3, len(queries))
                self.assertTrue(queries[1].endswith('registered-py.exe|-0p'))

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
        self.assertEqual(1, len(queries))
        self.assertTrue(queries[0].endswith('registered-py.exe|-0p'))

    def test_old_path_entry_does_not_hide_valid_later_path_python(self):
        result, queries, installed = self.resolve(python_status=0, later_status=1, list_installed=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertEqual(2, len(queries))
        self.assertIn('later-python.exe|', queries[1])
        self.assertEqual(['old_version', 'accepted'], [row['status'] for row in self.diagnosis['checks']])

    def test_registry_recovers_missing_inherited_path_without_installing(self):
        result, queries, installed = self.resolve(list_installed=False, registry=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(installed, result.stdout.strip())
        self.assertEqual(2, len(queries))
        self.assertEqual('registry_current_user', self.diagnosis['checks'][0]['source'])

    def test_explicit_old_command_does_not_fall_back_to_path_or_registry(self):
        result, queries, _ = self.resolve(python_status=0, later_status=1, registry=True, explicit=True)
        self.assertIn('WORKSPACE_STARTUP:38', result.stderr)
        self.assertEqual(1, len(queries))
        self.assertEqual('old_version', self.diagnosis['failure'])

    def test_wrapper_noise_is_ignored_but_duplicate_probe_response_is_rejected(self):
        result, _, _ = self.resolve(python_status=1, noise=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result, _, _ = self.resolve(python_status=1, malformed=True, explicit=True)
        self.assertIn('WORKSPACE_STARTUP:38', result.stderr)
        self.assertEqual('invalid_response', self.diagnosis['failure'])

    def test_batch_wrapper_is_not_executed_and_later_executable_is_checked(self):
        for extension in ('.cmd', '.bat'):
            with self.subTest(extension=extension):
                result, queries, _ = self.resolve(python_status=1, extension=extension, later_status=1)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(1, len(queries))
                self.assertIn('later-python.exe|', queries[0])
                self.assertEqual('unsupported_command', self.diagnosis['checks'][0]['status'])

    def test_existing_alias_to_executable_is_resolved_without_function_execution(self):
        result, queries, _ = self.resolve(python_status=1, command_kind='Alias', explicit=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len(queries))
        self.assertEqual('alias', self.diagnosis['checks'][0]['source'])
        for kind, target in (('Function', 'Application'), ('Alias', 'Function')):
            with self.subTest(kind=kind):
                result, queries, _ = self.resolve(python_status=1, command_kind=kind, alias_target=target,
                                                registry=True, explicit=True)
                self.assertIn('WORKSPACE_STARTUP:38', result.stderr)
                self.assertEqual([], queries)
                self.assertEqual('unsupported_command', self.diagnosis['failure'])
                self.assertNotIn('PRIVATE_FUNCTION_BODY', json.dumps(self.diagnosis))

    def test_missing_module_details_are_allowlisted_and_not_mislabeled_as_old_version(self):
        result, _, _ = self.resolve(python_status=2, explicit=True)
        self.assertIn('WORKSPACE_STARTUP:38', result.stderr)
        check = self.diagnosis['checks'][0]
        self.assertEqual('missing_module', check['status'])
        self.assertEqual('3.11.5', check['version'])
        self.assertEqual(['ssl'], check['missingModules'])
        self.assertNotIn('PRIVATE_MODULE', json.dumps(self.diagnosis))

    def test_query_failure_preserves_safe_status_and_native_code(self):
        for reason in ('start_failed', 'exit_failed', 'timed_out'):
            with self.subTest(reason=reason):
                result, _, _ = self.resolve(python_status=1, explicit=True, query_failure=reason)
                self.assertIn('WORKSPACE_STARTUP:38', result.stderr)
                self.assertEqual(reason, self.diagnosis['failure'])
                self.assertEqual(5, self.diagnosis['checks'][0]['nativeCode'])
                self.assertEqual({'source','path','status','version','nativeCode'}, set(self.diagnosis['checks'][0]))

    def test_pep514_entries_use_read_only_keys_and_vendor_fallback_rules(self):
        with tempfile.TemporaryDirectory(prefix='workspace-registry-fixture-') as raw:
            root = Path(raw)
            executable = root / 'python.exe'
            executable.touch()
            body = r'''
function New-FixtureKey {
    param($Children=@{},$Values=@{})
    $key = [pscustomobject]@{Children=$Children;Values=$Values;Closed=$false}
    $key | Add-Member ScriptMethod GetSubKeyNames { @($this.Children.Keys | ForEach-Object { ($_ -split '\\')[0] } | Select-Object -Unique) }
    $key | Add-Member ScriptMethod OpenSubKey {
        param($Name,$Writable)
        if ($Writable) { throw 'Registry must be read-only' }
        $this.Children[$Name]
    }
    $key | Add-Member ScriptMethod GetValue {
        param($Name,$Default,$Options)
        if ($Options -ne [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) { throw 'Do not expand environment data' }
        if ($this.Values.ContainsKey($Name)) { $this.Values[$Name] } else { $Default }
    }
    $key | Add-Member ScriptMethod Dispose { $this.Closed=$true }
    $key
}
$coreInstall = New-FixtureKey -Values @{''=DIRECTORY}
$vendorInstall = New-FixtureKey -Values @{ExecutablePath=EXECUTABLE;ExecutableArguments='--private-argument'}
$missingVendor = New-FixtureKey -Values @{''=DIRECTORY}
$core = New-FixtureKey -Children @{'3.11\InstallPath'=$coreInstall}
$vendor = New-FixtureKey -Children @{'opaque-tag\InstallPath'=$vendorInstall;'no-executable\InstallPath'=$missingVendor}
$launcher = New-FixtureKey -Children @{'ignored\InstallPath'=$coreInstall}
$root = New-FixtureKey -Children @{PythonCore=$core;Vendor=$vendor;PyLauncher=$launcher}
$entries = @(Get-WorkspacePythonRegistryEntries -Root $root -Source 'registry_current_user' -Deadline ([DateTime]::UtcNow.AddSeconds(5)))
@{entries=$entries;closed=@($coreInstall.Closed,$vendorInstall.Closed,$missingVendor.Closed,$core.Closed,$vendor.Closed);launcherUntouched=(-not $launcher.Closed)} | ConvertTo-Json -Depth 5 -Compress
'''.replace('DIRECTORY', ps_quote(root)).replace('EXECUTABLE', ps_quote(executable))
            result = self.powershell(body)
            self.assertEqual(0, result.returncode, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(2, len(value['entries']))
            self.assertTrue(all(value['closed']))
            self.assertTrue(value['launcherUntouched'])
            entries = {entry['company']: entry for entry in value['entries']}
            self.assertEqual(str(executable), entries['PythonCore']['path'])
            self.assertFalse(entries['PythonCore']['unsupported'])
            self.assertTrue(entries['Vendor']['unsupported'])
            self.assertNotIn('private-argument', result.stdout)

    def test_real_probe_start_and_exit_failures_do_not_expose_stderr(self):
        with tempfile.TemporaryDirectory(prefix='workspace-probe-failure-') as raw:
            missing = Path(raw) / 'missing.exe'
            body = ('$missing = Invoke-WorkspacePythonQuery -Executable ' + ps_quote(missing)
                    + ' -Arguments ""\n$startStatus=$script:WorkspacePythonQueryStatus\n'
                    + '$startCode=$script:WorkspacePythonQueryNativeCode\n'
                    + '$failed=Invoke-WorkspacePythonQuery -Executable ' + ps_quote(sys.executable)
                    + ' -Arguments \'-B -c "import sys;sys.stderr.write(\\\"PRIVATE_DIAGNOSTIC\\\");sys.exit(7)"\'\n'
                    + '@{startStatus=$startStatus;startCode=$startCode;status=$script:WorkspacePythonQueryStatus;'
                      'nativeCode=$script:WorkspacePythonQueryNativeCode;noOutput=($null -eq $missing -and $null -eq $failed)} | ConvertTo-Json -Compress')
            result = self.powershell(body)
            self.assertEqual(0, result.returncode, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual('start_failed', value['startStatus'])
            self.assertEqual(2, value['startCode'])
            self.assertEqual('exit_failed', value['status'])
            self.assertEqual(7, value['nativeCode'])
            self.assertTrue(value['noOutput'])
            self.assertNotIn('PRIVATE_DIAGNOSTIC', result.stdout + result.stderr)

    def test_environment_initialization_failure_is_distinct_without_path_mutation(self):
        # Duplicate case-insensitive keys caused ArgumentException in the
        # observed Windows host. Inject that getter failure without relying on
        # every test machine retaining a malformed inherited environment block.
        fixture = r'''
function New-Object {
    param($TypeName,[object[]]$ArgumentList)
    $value = Microsoft.PowerShell.Utility\New-Object @PSBoundParameters
    if ($TypeName -eq 'Diagnostics.ProcessStartInfo') {
        $value | Add-Member ScriptMethod get_EnvironmentVariables { throw [ArgumentException]::new('fixture duplicate environment key') } -Force
    }
    $value
}
'''
        body = (fixture + '$before=$env:PATH\n$answer=Invoke-WorkspacePythonQuery -Executable ' + ps_quote(sys.executable)
                + ' -Arguments \'-B -c "print(1)"\'\n'
                + '@{empty=($null -eq $answer);status=$script:WorkspacePythonQueryStatus;pathPreserved=($before -ceq $env:PATH)} | ConvertTo-Json -Compress')
        result = self.powershell(body)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual('environment_failed', value['status'])
        self.assertTrue(value['empty'])
        self.assertTrue(value['pathPreserved'])

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
                + '@{empty=($null -eq $answer);elapsed=$clock.Elapsed.TotalSeconds;status=$script:WorkspacePythonQueryStatus} | ConvertTo-Json -Compress')
        result = self.powershell(body)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['empty'])
        self.assertEqual('timed_out', value['status'])
        self.assertLess(value['elapsed'], 3)


if __name__ == '__main__':
    unittest.main()
