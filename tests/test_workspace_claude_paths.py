"""Optional configuration paths, with temporary profiles and offline commands."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from local_app.bridge import _launched_user_settings, probe_discovered_cli, runtime_context
from local_app.claude_shell import ShellPreference, WRAPPER, checked_paths, installed_shell, selected_command


class ClaudePathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / '선택한 설정'
        self.config.mkdir()
        self.entry = self.root / '회사 claude [2].ps1'
        self.entry.write_text("""
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::WriteLine((@{root=$env:CLAUDE_CONFIG_DIR;received=@($args);profile=$env:AX_FIX_PROFILE_LOADED} | ConvertTo-Json -Compress))
""", encoding='utf-8-sig')

    def test_normalize_and_validate_without_reading_settings_or_creating_folders(self):
        self.assertEqual((str(self.config), str(self.entry)),
                         checked_paths(f'  "{self.config}" ', f'"{self.entry}"'))
        self.assertEqual(('', ''), checked_paths('  ', ''))
        self.assertFalse((self.config / 'settings.json').exists())
        for folder, entry in [(str(self.entry), ''), ('', str(self.config)), ('relative', ''),
                              (None, ''), ('', 12), ('', str(self.root / 'wrong.txt')),
                              (str(self.root / 'missing'), ''), ('', str(self.entry) + '" --version'),
                              (str(self.config / '..' / 'other'), '')]:
            with self.subTest(folder=folder, entry=entry), self.assertRaises(ValueError):
                checked_paths(folder, entry)
        self.assertFalse((self.root / 'missing').exists())

    def test_legacy_settings_atomic_save_and_clear_each_field(self):
        path = self.root / 'claude-shell.json'
        path.write_text('"windows"')
        pref = ShellPreference(self.root)
        self.assertEqual(('windows', '', ''), (pref.choice, pref.config_dir, pref.executable))
        pref.save('default', config_dir=str(self.config), executable=str(self.entry))
        original = path.read_bytes()
        with patch('local_app.claude_shell.os.replace', side_effect=OSError), self.assertRaises(OSError):
            pref.save('pwsh')
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(str(self.entry), pref.executable)
        loaded = ShellPreference(self.root)
        self.assertEqual(('default', str(self.config), str(self.entry)),
                         (loaded.choice, loaded.config_dir, loaded.executable))
        loaded.save('default', executable=loaded.executable)
        self.assertEqual('', ShellPreference(self.root).config_dir)
        loaded.save('default')
        self.assertEqual('default', json.loads(path.read_text()))

    @unittest.skipUnless(os.name == 'nt', 'Windows shell required')
    def test_default_wrapper_only_changes_its_child_environment(self):
        shell = installed_shell('windows')
        env = {'COMPANY_WORKSPACE_SHELL': shell, 'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '1',
               'CLAUDE_CONFIG_DIR': str(self.root / 'inherited'), 'COMPANY_AGENT_CLAUDE': ''}
        with patch.dict(os.environ, env):
            for folder in ('', str(self.config)):
                command, _ = selected_command('default', config_dir=folder, executable=str(self.entry))
                expected = folder or env['CLAUDE_CONFIG_DIR']
                self.assertEqual(expected, runtime_context(command)['configRoot'])
                result = subprocess.run(command + ['--sample', 'argument with spaces'], capture_output=True,
                                        encoding='utf-8', timeout=10, cwd=self.root, check=True)
                self.assertEqual({'root': expected, 'received': ['--sample', 'argument with spaces'], 'profile': None},
                                 json.loads(result.stdout))
                self.assertEqual(env['CLAUDE_CONFIG_DIR'], os.environ['CLAUDE_CONFIG_DIR'])
                self.assertIsNone(_launched_user_settings(command, env, self.root))  # Custom script.

    @unittest.skipUnless(os.name == 'nt', 'Windows shell required')
    def test_config_only_preserves_native_preference_and_editor_path_even_on_fallback(self):
        native = self.root / 'claude.exe'
        native.touch()
        shim = self.root / 'claude.cmd'
        stock = Path(__file__).parent / 'fixtures/npm_claude_shims/native.cmd.txt'
        shutil.copyfile(stock, shim)
        env = {'COMPANY_WORKSPACE_SHELL': installed_shell('windows'), 'COMPANY_AGENT_CLAUDE': '',
               'COMPANY_WORKSPACE_CLAUDE_PROFILE': '', 'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '',
               'COMPANY_WORKSPACE_CLAUDE_ENTRY': str(shim), 'COMPANY_WORKSPACE_CLAUDE_NATIVE_CANDIDATE': str(native)}
        with patch.dict(os.environ, env):
            initial, _ = selected_command('default', config_dir=str(self.config))
            for results, expected in [([{}], native), ([ValueError('unavailable'), {}], shim)]:
                with self.subTest(expected=expected), patch('local_app.bridge.probe_cli', side_effect=results) as probe:
                    command, _ = probe_discovered_cli(initial)
                    for check in probe.call_args_list:
                        self.assertEqual(str(self.config), runtime_context(check.args[0])['configRoot'])
                    self.assertEqual(expected, Path(runtime_context(command)['entry']))
                    self.assertEqual(self.config / 'settings.json', _launched_user_settings(command, {}, self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows shell required')
    def test_pinned_selected_profile_and_file_can_be_overridden_independently(self):
        profile = self.root / 'profile.ps1'
        profile.write_text("""
$env:AX_FIX_PROFILE_LOADED='yes'
$env:CLAUDE_CONFIG_DIR=$env:AX_FIX_PROFILE_ROOT
function claude { [Console]::WriteLine($env:CLAUDE_CONFIG_DIR) }
""", encoding='utf-8-sig')
        harness = self.root / 'host.ps1'
        harness.write_text("""
$PROFILE=[pscustomobject]@{CurrentUserAllHosts=$env:AX_FIX_PROFILE;CurrentUserCurrentHost=$env:AX_FIX_PROFILE;
 AllUsersCurrentHost=$env:AX_FIX_PROFILE;AllUsersAllHosts=$env:AX_FIX_PROFILE}
& $env:AX_FIX_WRAPPER @args
exit $LASTEXITCODE
""", encoding='utf-8-sig')
        env = dict(os.environ, AX_FIX_PROFILE=str(profile), AX_FIX_WRAPPER=str(WRAPPER),
                   AX_FIX_PROFILE_ROOT=str(self.root / 'profile settings'), COMPANY_AGENT_CLAUDE='')
        for shell in {installed_shell('windows'), shutil.which('pwsh.exe')} - {None}:
            def invoke(command, **unused):
                # Isolate all PowerShell profile paths; never read the runner's real profile.
                args = [*command[:6], str(harness), *command[7:]]
                return subprocess.run(args, env=env, cwd=self.root, capture_output=True, encoding='utf-8', timeout=15)

            with patch('local_app.claude_shell.installed_shell', return_value=shell), \
                 patch('local_app.claude_shell.run_owned', side_effect=invoke):
                for folder, entry in [(str(self.config), ''), ('', str(self.entry)), (str(self.config), str(self.entry))]:
                    with self.subTest(shell=shell, folder=folder, entry=entry):
                        command, _ = selected_command('pwsh', config_dir=folder, executable=entry)
                        expected = folder or env['AX_FIX_PROFILE_ROOT']
                        self.assertEqual(expected, runtime_context(command)['configRoot'])
                        run = invoke([*command, '--flag'])
                        self.assertEqual(0, run.returncode, run.stderr)
                        if entry:
                            self.assertEqual({'root': expected, 'received': ['--flag'], 'profile': 'yes'},
                                             json.loads(run.stdout))
                        else:
                            self.assertEqual(expected, run.stdout.strip())
