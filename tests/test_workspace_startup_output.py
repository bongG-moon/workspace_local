"""Owned startup responses tolerate outside diagnostics without guessing success."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from local_app import server, upgrade_launcher
from local_app.startup import StartupError

ROOT = Path(__file__).resolve().parents[1]
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
NONCE = 'a' * 32


def frame(value, nonce=NONCE):
    return f'WORKSPACE_PREFLIGHT_V1:{nonce}:{value}:END:{nonce}'


class StartupOutputTests(unittest.TestCase):
    def test_server_preserves_startup_code_without_constructing_app(self):
        for code in (30, 33, 41, 45):
            with self.subTest(code=code), patch.object(sys, 'argv', ['workspace']), \
                    patch('local_app.startup.verify_process', side_effect=StartupError('safe failure', code=code)), \
                    patch.object(server, 'LocalApp') as app:
                with self.assertRaises(SystemExit) as result:
                    server.main()
                self.assertEqual(code, result.exception.code)
                app.assert_not_called()

    def test_preflight_emits_nonce_frame_and_preserves_failure_code(self):
        for fail in (False, True):
            with self.subTest(fail=fail), patch.object(sys, 'argv', [
                    'upgrade', '--preflight', '--no-browser', '--response-marker', NONCE]), \
                    patch.object(upgrade_launcher, 'preflight',
                                 side_effect=OSError('private details') if fail else None) as check:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = upgrade_launcher.main()
                self.assertEqual(47 if fail else 0, result)
                self.assertEqual(frame('{"ok":false}' if fail else '{"ok":true}') + '\n', output.getvalue())
                check.assert_called_once_with(no_browser=True)
                self.assertNotIn('private', output.getvalue())

    def test_preflight_without_marker_keeps_existing_cli_contract(self):
        with patch.object(sys, 'argv', ['upgrade', '--preflight']), \
                patch.object(upgrade_launcher, 'preflight'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, upgrade_launcher.main())
            self.assertEqual({'ok': True}, json.loads(output.getvalue()))

    def test_invalid_or_misplaced_marker_cannot_start_preflight(self):
        for arguments in (['--preflight', '--response-marker', 'wrong'],
                          ['--response-marker', NONCE]):
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['upgrade', *arguments]), \
                    patch.object(upgrade_launcher, 'preflight') as check, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    upgrade_launcher.main()
                self.assertEqual(2, result.exception.code)
                check.assert_not_called()


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell required')
class StartupOutputPowerShellTests(unittest.TestCase):
    def run_ps(self, script):
        helper = str(ROOT / 'deploy/CompanyWorkspace.Startup.ps1').replace("'", "''")
        result = subprocess.run([str(PS), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-Command', "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); . '" + helper + "'; $ErrorActionPreference='Stop'; " + script],
            capture_output=True, encoding='utf-8', errors='replace', timeout=25,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_security_noise_is_outside_unique_bound_preflight_result(self):
        valid = frame('{"ok":true}')
        noise = '[0x610329E0] ANOMALY: meaningless REX prefix used'
        samples = [valid, noise + valid + noise, noise + '\n' + valid + '\r\n' + noise,
                   '{"ok":true}', frame('{"ok":true}', 'b' * 32), valid + valid,
                   frame('{"ok":false}'), frame('{"ok":true,"unexpected":1}'),
                   frame('{"ok":true}' + noise), valid.replace(':END:', ':BROKEN:'),
                   'WORKSPACE_PREFLIGHT_V1:' + NONCE + ':' + valid]
        data = json.dumps(samples).replace("'", "''")
        actual = self.run_ps("$samples='" + data + "' | ConvertFrom-Json; "
            "$values=@($samples | ForEach-Object { Test-WorkspacePreflightResponse -Output $_ -Nonce '" + NONCE + "' }); "
            "$values += Test-WorkspacePreflightResponse -Output (('x' * 65537) + $samples[0]) -Nonce '" + NONCE + "'; "
            "ConvertTo-Json -InputObject $values -Compress")
        self.assertEqual([True, True, True] + [False] * 9, actual)

    def test_launcher_keeps_verified_child_code_and_unknown_failure_is_ws40(self):
        source = (ROOT / 'deploy/Start-CompanyWorkspace.ps1').read_text(encoding='utf-8-sig')
        block = source.split('    if (-not $workspaceReady) {', 1)[1].split('    if ($ExtensionPackage)', 1)[0]
        block = 'if (-not $workspaceReady) {' + block
        script = "$values=@(); foreach($exitCode in @(30,33,41,45,46,47,1)) { "
        script += "$workspaceReady=$false; $workspaceProcess=[pscustomobject]@{HasExited=$true;ExitCode=$exitCode}; "
        script += "$workspaceProcess | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}; "
        script += 'try { ' + block + ' } catch { $values += Get-WorkspaceStartupCode -Message $_.Exception.Message } }; '
        script += 'ConvertTo-Json -InputObject $values -Compress'
        self.assertEqual([30, 33, 41, 45, 46, 47, 40], self.run_ps(script))


if __name__ == '__main__':
    unittest.main()
