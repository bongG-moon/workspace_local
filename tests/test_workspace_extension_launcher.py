"""Run the real launcher with disposable native/server boundaries.

No real app, Claude, registry association or personal state is changed.
"""
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
class ExtensionLauncherTests(unittest.TestCase):
    def launch(self, *, live=True, capability=1, closing=False, client_code=0,
               demo=False, package_override=None, contend=False, show_window=False):
        with tempfile.TemporaryDirectory(prefix="확장 launcher & key's ") as temporary:
            root = Path(temporary)
            deploy = root / 'deploy'
            deploy.mkdir()
            launcher = deploy / LAUNCHER.name
            launcher.write_bytes(LAUNCHER.read_bytes())
            state = root / '개인 상태'
            state.mkdir()
            (state / 'draft.txt').write_text('unsent text and queued requests', encoding='utf-8')
            runtime_state = state / 'demo' if demo else state
            runtime_state.mkdir(exist_ok=True)
            runtime = runtime_state / 'runtime.json'
            runtime_value = {'url': 'http://127.0.0.1:47123/#token=' + 'a' * 48, 'pid': 70701}
            if live:
                runtime.write_text(json.dumps(runtime_value), encoding='utf-8')
            package = root / '조직 도구 $(literal) & key.zip'
            package.write_bytes(b'untouched package')
            capture = root / 'handoff.json'
            calls = root / 'calls.txt'
            python_package = root / 'local_app'
            python_package.mkdir()
            (python_package / '__init__.py').touch()
            (python_package / 'extension_open_client.py').write_text(
                'import json, pathlib, sys\n'
                f'pathlib.Path({str(capture)!r}).write_text(json.dumps({{"argv":sys.argv[1:],"cwd":str(pathlib.Path.cwd())}}, ensure_ascii=False), encoding="utf-8")\n'
                f'print("확장 패키지를 확인할 수 없습니다. 파일을 다시 다운로드해 주세요." if {client_code} else "", file=sys.stderr)\n'
                f'raise SystemExit({client_code})\n', encoding='utf-8')
            health = {'application': 'company-workspace', 'demo': demo, 'closing': closing,
                      'workspaceVersion': '0.1.0', 'buildIdentity': 'different-build',
                      'extensionImportProtocol': capability, 'executionMode': 'normal',
                      'window': {'reopenSupported': True}, 'shutdownState': 'failed'}
            sid = 'S-1-5-21-' + str(uuid.uuid4().int & 0x7fffffff)
            overrides = r'''
function Get-WorkspaceVerifiedContext { [pscustomobject]@{verified=$true;sid=SID;sessionId=1;isAdministrator=$false;localAppData=ROOT;userProfile=ROOT} }
function Get-WorkspaceLaunchAction { param($Context,$Relaunched,$ExecutionMode); 'run' }
function Get-WorkspaceExecutionMode { param($Context); 'normal' }
function Get-WorkspacePythonExecutable { param($Command); PYTHON }
function Invoke-RestMethod {
  param($Uri,$Headers,$TimeoutSec,$MaximumRedirection,$Method,$ContentType,$Body)
  if ($Uri -like '*/api/bootstrap') { HEALTH | ConvertFrom-Json; return }
  Add-Content -LiteralPath CALLS ('unexpected-request:' + $Uri)
  throw 'A fixture must never stop the current app'
}
function Open-WorkspaceWindow { param($Uri,$ReuseSupported); Add-Content -LiteralPath CALLS 'focus' }
function Start-Process {
  param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
  Add-Content -LiteralPath CALLS 'new-server'
  [IO.File]::WriteAllText(RUNTIME, RUNTIME_JSON)
  [pscustomobject]@{Id=70701;HasExited=$false}
}
function Show-WorkspaceStartupDialog { param($Message); throw 'A fixture must never show UI' }
'''
            replacements = {'SID': ps_quote(sid), 'ROOT': ps_quote(root), 'PYTHON': ps_quote(sys.executable),
                            'CALLS': ps_quote(calls), 'HEALTH': ps_quote(json.dumps(health)),
                            'RUNTIME_JSON': ps_quote(json.dumps(runtime_value)), 'RUNTIME': ps_quote(runtime)}
            for key in sorted(replacements, key=len, reverse=True):
                overrides = overrides.replace(key, replacements[key])
            (deploy / HELPER.name).write_text(HELPER.read_text(encoding='utf-8-sig') + overrides,
                                             encoding='utf-8-sig')
            args = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                    'Bypass', '-File', str(launcher), '-StateRoot', str(state),
                    '-ExtensionPackage', package_override if package_override is not None else str(package)]
            if not show_window:
                args.append('-NoBrowser')
            if demo:
                args.append('-Demo')
            env = {**os.environ, 'COMPANY_AGENT_CLAUDE': 'fixture-not-launched'}
            holder = None
            if contend:
                signal = root / 'lock-ready'
                mutex_name = 'Local\\CompanyWorkspace-' + sid + ('-demo' if demo else '')
                script = (f'$m=New-Object Threading.Mutex($false,{ps_quote(mutex_name)}); '
                          '$null=$m.WaitOne(); '
                          f'[IO.File]::WriteAllText({ps_quote(signal)},"ready"); '
                          'Start-Sleep -Milliseconds 2300; $m.ReleaseMutex(); $m.Dispose()')
                holder = subprocess.Popen(['powershell.exe', '-NoProfile', '-Command', script],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=subprocess.CREATE_NO_WINDOW)
                import time
                deadline = time.monotonic() + 10
                while not signal.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(signal.exists())
            try:
                result = subprocess.run(args, env=env, capture_output=True, timeout=45,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
            finally:
                if holder:
                    holder.wait(timeout=10)
            value = json.loads(capture.read_text(encoding='utf-8')) if capture.exists() else None
            events = calls.read_text(encoding='utf-8-sig').splitlines() if calls.exists() else []
            self.assertEqual('unsent text and queued requests', (state / 'draft.txt').read_text(encoding='utf-8'))
            self.assertEqual(b'untouched package', package.read_bytes())
            if value:
                self.assertEqual(['--state', str(runtime_state), '--package', str(package)], value['argv'])
                self.assertEqual(str(root), value['cwd'])
            return result, value, events

    def test_running_capable_app_reuses_even_different_build_without_quit(self):
        result, value, events = self.launch()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(value)
        self.assertEqual([], events)

    def test_running_app_window_is_focused_without_restarting_it(self):
        result, value, events = self.launch(show_window=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(value)
        self.assertEqual(['focus'], events)

    def test_old_running_app_gives_actionable_error_without_restart_or_lost_work(self):
        result, value, events = self.launch(capability=None)
        self.assertEqual(49, result.returncode, result.stderr)
        self.assertIn(b'WS-49', result.stderr)
        self.assertIsNone(value)
        self.assertEqual([], events)

    def test_closing_app_is_not_forced_to_quit_again(self):
        result, value, events = self.launch(closing=True)
        self.assertEqual(49, result.returncode, result.stderr)
        self.assertIsNone(value)
        self.assertEqual([], events)

    def test_new_app_receives_package_after_startup(self):
        result, value, events = self.launch(live=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(value)
        self.assertEqual(['new-server'], events)

    def test_demo_uses_actual_demo_runtime_state(self):
        result, value, events = self.launch(demo=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(value)

    def test_invalid_source_argument_does_not_start_app(self):
        result, value, events = self.launch(live=False, package_override='relative.zip')
        self.assertEqual(49, result.returncode, result.stderr)
        self.assertIsNone(value)
        self.assertEqual([], events)

    def test_rejected_client_never_installs_or_restarts_app(self):
        result, value, events = self.launch(client_code=1)
        self.assertEqual(49, result.returncode, result.stderr)
        self.assertIsNotNone(value)
        self.assertEqual([], events)

    def test_import_waits_for_other_startup_instead_of_silently_dropping(self):
        result, value, events = self.launch(contend=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNotNone(value)
        self.assertEqual([], events)


class ExtensionAssociationContractTests(unittest.TestCase):
    def test_custom_type_only_regular_app_and_owned_command_uninstall(self):
        source = (LAUNCHER.parent / 'AX-Workspace.nsi').read_text(encoding='utf-8-sig')
        register = source.split('Function RegisterExtensionPackage', 1)[1].split('FunctionEnd', 1)[0]
        remove = source.split('Function un.UnregisterExtensionPackage', 1)[1].split('FunctionEnd', 1)[0]
        for function in (register, remove):
            self.assertIn('!if "${PRODUCT_ID}" == "AX.Workspace.Desktop"', function)
            self.assertIn('--import-extension $\\"%1$\\"', function)
            self.assertNotIn('HKLM', function)
            self.assertNotIn('Classes\\.zip', function)
            self.assertNotIn('Explorer\\FileExts', function)
        self.assertLess(register.index('${OrIf} $0 == $2'), register.index('WriteRegStr'))
        self.assertIn('${OrIf} $0 == "${EXTENSION_PROGID}"', register)
        self.assertLess(remove.index('${If} $0 == $2'), remove.index('DeleteRegValue'))
        self.assertIn('${If} $1 == "${EXTENSION_PROGID}"', remove)
        self.assertNotIn('DeleteRegKey HKCU "Software\\Classes\\.axextension"', remove)
        self.assertIn('DeleteRegKey /ifempty HKCU "Software\\Classes\\.axextension"', remove)


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
class ManagedExtensionForwardTests(unittest.TestCase):
    def run_forward(self, *, capable=True, child_code=0):
        import hashlib
        with tempfile.TemporaryDirectory(prefix='managed-extension-', dir=LAUNCHER.parent.parent / 'build') as raw:
            root = Path(raw)
            state = root / 'state'
            application = state / 'updates/versions/9.0.0/Company-Workspace'
            (application / 'deploy').mkdir(parents=True)
            (application / 'local_app').mkdir()
            package = root / '일회 파일 & data.zip'
            package.write_bytes(b'package survives original shell caller')
            receipt = root / 'received.txt'
            launcher = application / 'deploy/Start-CompanyWorkspace.ps1'
            launcher.write_text(
                'param($StateRoot,$PythonCommand,$ExecutionMode,$ExtensionPackage,[switch]$NoBrowser,[switch]$Demo)\n'
                'Start-Sleep -Milliseconds 350\n'
                f'[IO.File]::Copy($ExtensionPackage,{ps_quote(receipt)})\n'
                f'exit {child_code}\n', encoding='utf-8-sig')
            (application / 'local_app/server.py').write_text('# fixture\n')
            if capable:
                (application / 'local_app/extension_open_client.py').write_text('# capability fixture\n')
            manifest = {p.relative_to(application).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in application.rglob('*') if p.is_file()}
            (state / 'updates/current.json').write_text(json.dumps({
                'schema': 1, 'version': '9.0.0', 'root': 'versions/9.0.0/Company-Workspace',
                'sha256': 'a' * 64, 'files': manifest}), encoding='utf-8')
            source = LAUNCHER.read_text(encoding='utf-8-sig')
            function = source[source.index('function Invoke-WorkspaceManagedUpdate {'):source.index('\ntry {\n    $startupHelper')]
            runner = root / 'run.ps1'
            output = root / 'result.json'
            runner.write_text('$ErrorActionPreference="Stop"\n' + function + '\ntry {\n'
                f'$forwarded = Invoke-WorkspaceManagedUpdate -State {ps_quote(state)} -CurrentVersion "0.23.32" -Headless $true -Mode "normal" -Package {ps_quote(package)}\n'
                f'@{{forwarded=$forwarded;received=(Test-Path -LiteralPath {ps_quote(receipt)})}} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 -LiteralPath {ps_quote(output)}\n'
                '} catch {\n'
                f'@{{error=$_.Exception.Message}} | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 -LiteralPath {ps_quote(output)}\n'
                '}\n', encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(runner)],
                                    capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(output.read_text(encoding='utf-8-sig'))

    def test_capable_managed_launcher_has_received_package_before_caller_returns(self):
        self.assertEqual({'forwarded': True, 'received': True}, self.run_forward())

    def test_old_managed_payload_is_not_forwarded_a_command_it_cannot_handle(self):
        self.assertEqual({'forwarded': False, 'received': False}, self.run_forward(capable=False))

    def test_managed_import_failure_does_not_fall_back_to_second_startup(self):
        result = self.run_forward(child_code=49)
        self.assertTrue(result['error'].startswith('WORKSPACE_EXTENSION:'))
