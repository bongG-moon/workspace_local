"""Run opt-in shell checks with synthetic profiles and an offline CLI."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app.bridge import LaunchDiagnosticError, _launched_user_settings, runtime_context
from local_app.claude_shell import WRAPPER, ShellPreference, installed_shell
from local_app.server import AppClosing, LocalApp
from local_app.owned_process import CancelledError


class ShellSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / 'state'

    def app(self):
        app = LocalApp(self.state, managed_workspace_root=self.state.parent / 'tasks')
        self.addCleanup(app.close)
        return app

    def test_check_apply_restart_and_failure_preserve_work(self):
        default = ['fixture.exe']
        selected = ['pwsh.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WRAPPER),
                    '-Entry', 'claude', 'Function', 'A' * 64, str(self.state.parent / 'selected-config')]
        diagnostic = {'stage': 'help', 'commandType': 'Function', 'shellVersion': '7.5', 'loadProfiles': True}
        with patch('local_app.server.resolve_cli', return_value=default), \
             patch('local_app.server.probe_discovered_cli', return_value=(default, {})) as default_probe, \
             patch('local_app.server.selected_command', return_value=(selected, diagnostic)) as discovery, \
             patch('local_app.server.probe_cli', return_value={'version': 'fixture'}):
            app = self.app()
            self.assertEqual(1, default_probe.call_count)
            app.shell_snapshot()
            app.bootstrap()
            discovery.assert_not_called()
            checked = app.check_shell({'choice': 'pwsh', 'checkOnly': True})
            self.assertEqual('default', checked['choice'])
            self.assertIs(app.command, default)
            self.assertFalse(app.shell_preference.path.exists())
            item = app.create(str(self.state.parent), True)
            bridge = Mock(command=default, closed=False, _control_active=False, _effort_baselines={}, session_id=None)
            bridge.ready = threading.Event()
            bridge.ready.set()
            bridge.model_state.return_value = {}
            task = app.get(item['id'])
            task.update(bridge=bridge, state='running', importedConfigRoot=runtime_context(default)['configRoot'])
            self.assertEqual('pwsh', app.check_shell({'choice': 'pwsh'})['choice'])
            self.assertEqual('pwsh', json.loads(app.shell_preference.path.read_text()))
            self.assertEqual(selected[-1], runtime_context(app.command)['configRoot'])
            self.assertEqual(Path(selected[-1]), app.inventory_client.config)
            self.assertEqual(selected[-1], str(app.customizations.config))
            self.assertIs(bridge, app.get(item['id'])['bridge'])
            self.assertIs(bridge.command, default)
            bridge.close.assert_not_called()
            bridge.send.assert_not_called()
            task['state'] = 'idle'
            with self.assertRaisesRegex(ValueError, '설정 위치'):
                app.restart_connection(item['id'])
            bridge.close.assert_not_called()
            app.send(item['id'], '격리 시험', [])
            bridge.send.assert_called_once()
            for _ in range(4):
                app.shell_snapshot()
            self.assertEqual(2, discovery.call_count)
            app.get(item['id'])['bridge'] = None
            restored = self.app()
            self.assertEqual(3, discovery.call_count)
            self.assertEqual('pwsh', restored.shell_preference.choice)
            self.assertEqual(selected, restored.command)
            inventory, writer, completions = restored.inventory_client, restored.customizations, restored.completion_discovery
            restored.reconnect()
            self.assertIs(restored.inventory_client, inventory)
            self.assertIs(restored.customizations, writer)
            self.assertIs(restored.completion_discovery, completions)
            discovery.side_effect = LaunchDiagnosticError('profile_parse', 'profile_restore')
            with self.assertRaises(LaunchDiagnosticError):
                restored.check_shell({'choice': 'windows'})
            self.assertIs(restored.command, selected)
            self.assertEqual('pwsh', ShellPreference(self.state).choice)
            for bad in ({'choice': '../pwsh.exe'}, {'choice': 'pwsh', 'path': 'another.exe'},
                        {'choice': 'default', 'checkOnly': 'true'}):
                with self.assertRaises(ValueError):
                    restored.check_shell(bad)
            self.assertEqual('default', restored.check_shell({'choice': 'default'})['choice'])

    def test_selected_native_reports_only_bound_settings_path(self):
        root = self.state.parent / 'config'
        command = ['pwsh.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WRAPPER),
                   '-Entry', str(self.state.parent / 'claude.exe'), 'Application', 'file', str(root)]
        elsewhere = {'CLAUDE_CONFIG_DIR': str(self.state.parent / 'wrong')}
        self.assertEqual(root / 'settings.json', _launched_user_settings(command, elsewhere, self.state.parent))
        command[10] = 'coworkFile'
        self.assertIsNone(_launched_user_settings(command, elsewhere, self.state.parent))

    def test_shutdown_cancels_check_and_rejects_even_a_late_success(self):
        for check_only in (True, False):
            with self.subTest(check_only=check_only), patch('local_app.server.resolve_cli', return_value=['fixture']), \
                 patch('local_app.server.probe_discovered_cli', return_value=(['fixture'], {})):
                app = self.app()
                entered = threading.Event()

                def pending(choice, *, cancel_event):
                    self.assertIs(cancel_event, app.customizations.cancel)
                    entered.set()
                    if not cancel_event.wait(3):
                        self.fail('Shutdown did not cancel the pending check')
                    if check_only:
                        raise CancelledError()
                    # Simulate a successful subprocess finishing just as quit wins.
                    return ['fixture-after-close'], {}, {'stage': 'help'}

                with patch.object(app, '_probe_shell', side_effect=pending), ThreadPoolExecutor(1) as pool:
                    future = pool.submit(app.check_shell, {'choice': 'pwsh', 'checkOnly': check_only})
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(0, app._active_operations)
                    self.assertTrue(app.request_quit()['closed'])
                    with self.assertRaises(AppClosing):
                        future.result(timeout=1)
                self.assertEqual('default', app.shell_preference.choice)
                self.assertFalse(app.shell_preference.path.exists())
                self.assertFalse(app._shell_check_running)

    def test_quit_retires_sleeping_probe_subprocess(self):
        with patch('local_app.server.resolve_cli', return_value=['fixture']), \
             patch('local_app.server.probe_discovered_cli', return_value=(['fixture'], {})):
            app = self.app()
        fixture = self.state.parent / 'slow_cli.py'
        fixture.write_text("from pathlib import Path\nimport time\nPath(__file__).with_suffix('.started').touch()\ntime.sleep(30)\n")
        start = fixture.with_suffix('.started')
        with patch('local_app.server.selected_command', return_value=([sys.executable, str(fixture)], {})), \
             ThreadPoolExecutor(1) as pool:
            future = pool.submit(app.check_shell, {'choice': 'pwsh', 'checkOnly': True})
            deadline = time.monotonic() + 5
            while not start.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(start.exists())
            self.assertTrue(app.request_quit()['closed'])
            with self.assertRaises(AppClosing):
                future.result(timeout=1)

    @unittest.skipUnless(os.name == 'nt', 'PowerShell host required')
    def test_real_shell_restores_private_profile_and_pins_function_identity(self):
        # Override the four PowerShell test profiles before invoking the real
        # wrapper. No installed profile, personal Claude or network is used.
        root = self.state.parent
        profile = root / 'profile.ps1'
        harness = root / 'host.ps1'
        harness.write_text("""
$PROFILE = [pscustomobject]@{AllUsersAllHosts=$env:AX_FIX_PROFILE;AllUsersCurrentHost=$env:AX_FIX_PROFILE;
    CurrentUserAllHosts=$env:AX_FIX_PROFILE;CurrentUserCurrentHost=$env:AX_FIX_PROFILE}
& $env:AX_FIX_WRAPPER @args
exit $LASTEXITCODE
""", encoding='utf-8-sig')
        fixture = """
[Console]::WriteLine('private-profile-banner')
Write-Warning 'private-profile-warning'
$env:AX_FIX_BOUND = 'present'
$env:CLAUDE_CONFIG_DIR = $env:AX_FIX_ROOT
function claude {
    if ($env:AX_FIX_BOUND -ne 'present') { throw 'configuration lost' }
    if ($args[0] -eq '--version') { '2.1.326 (Claude Code)' }
    elseif ($args[0] -eq '--help') { '--input-format --output-format --permission-prompt-tool' }
    else { 'restored' }
}
"""
        env = dict(os.environ, AX_FIX_PROFILE=str(profile), AX_FIX_WRAPPER=str(WRAPPER),
                   AX_FIX_ROOT=str(root / '구성'), COMPANY_AGENT_CLAUDE='', CLAUDE_CODE_USE_COWORK_PLUGINS='')
        shells = {installed_shell('windows'), shutil.which('pwsh.exe')}
        for shell in sorted(shells - {None}):
            with self.subTest(shell=Path(shell).name):
                def invoke(args, cwd=root):
                    return subprocess.run([shell, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                           str(harness), *args], env=env, cwd=cwd, capture_output=True,
                                          encoding='utf8', errors='replace', timeout=15)
                profile.write_text(fixture, encoding='utf-8-sig')
                result = invoke(['-Discover'])
                self.assertEqual(0, result.returncode, result.stderr)
                meta = json.loads(result.stdout)
                self.assertEqual('Function', meta['commandType'])
                self.assertNotIn('private-profile', result.stdout + result.stderr)
                args = ['-Entry', meta['entry'], meta['commandType'], meta['identity'], meta['configRoot']]
                self.assertEqual('2.1.326 (Claude Code)', invoke(args + ['--version']).stdout.strip())
                self.assertIn('--permission-prompt-tool', invoke(args + ['--help']).stdout)
                self.assertEqual('restored', invoke(args + ['fixture']).stdout.strip())
                task_folder = root / 'another task'
                task_folder.mkdir(exist_ok=True)
                self.assertEqual('restored', invoke(args + ['fixture'], task_folder).stdout.strip())
                env['AX_FIX_ROOT'] = 'relative-config'
                try:
                    failed = invoke(['-Discover'], task_folder)
                    self.assertEqual(78, failed.returncode)
                    self.assertIn('config_root', failed.stderr)
                finally:
                    env['AX_FIX_ROOT'] = str(root / '구성')
                profile.write_text(fixture.replace("'restored'", "'changed'"), encoding='utf-8-sig')
                result = invoke(args + ['fixture'])
                self.assertEqual(78, result.returncode)
                self.assertIn('function_changed', result.stderr)
                self.assertNotIn('private-profile', result.stdout + result.stderr)
                shim = root / 'fixture claude.ps1'
                shim.write_text("if ($env:AX_FIX_BOUND -eq 'present') { 'profile-before-shim' }", encoding='utf-8-sig')
                profile.write_text(fixture + "\nSet-Alias claude -Value $env:AX_FIX_SHIM\n", encoding='utf-8-sig')
                env['AX_FIX_SHIM'] = str(shim)
                result = invoke(['-Discover'])
                self.assertEqual(0, result.returncode, result.stderr)
                meta = json.loads(result.stdout)
                self.assertEqual(('ExternalScript', str(shim), 'file'), (meta['commandType'], meta['entry'], meta['identity']))
                args = ['-Entry', meta['entry'], meta['commandType'], meta['identity'], meta['configRoot']]
                result = invoke(args)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual('profile-before-shim', result.stdout.strip())
                self.assertEqual(78, invoke([*args[:-1], str(root / 'changed'), '--version']).returncode)
                batch = root / 'fixture.cmd'
                batch.write_text('@echo off\nif "%AX_FIX_BOUND%"=="present" echo cli:%1\n', encoding='ascii')
                env['COMPANY_AGENT_CLAUDE'] = str(batch)
                try:
                    meta = json.loads(invoke(['-Discover']).stdout)
                    self.assertEqual(('Application', str(batch)), (meta['commandType'], meta['entry']))
                    result = invoke(['-Entry', meta['entry'], 'Application', meta['identity'], meta['configRoot'], '--version'])
                    self.assertEqual('cli:--version', result.stdout.strip(), result.stderr)
                finally:
                    env['COMPANY_AGENT_CLAUDE'] = ''
