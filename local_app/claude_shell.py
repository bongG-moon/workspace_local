"""An optional, user-selected Windows shell for newly started Claude processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .history import read, safe
from .owned_process import CancelledError, run_owned
from .windows_process import powershell_path

CHOICES = ('default', 'windows', 'pwsh')
WRAPPER = Path(__file__).with_name('Invoke-SelectedClaude.ps1')


def installed_shell(choice):
    if os.name != 'nt' or choice not in ('windows', 'pwsh'):
        return None
    if choice == 'windows':
        try:
            return powershell_path()
        except OSError:
            return None
    # If AX itself was started from an explicitly verified PowerShell host,
    # retain that user's working portable/MSIX installation.
    current = Path(os.environ.get('COMPANY_WORKSPACE_SHELL', ''))
    if current.is_absolute() and current.name.lower() == 'pwsh.exe' and current.is_file():
        return str(current)
    # Do not resolve pwsh.exe relative to a task folder or an editable PATH.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            base, kind = winreg.QueryValueEx(key, 'ProgramFilesDir')
        if kind != winreg.REG_SZ or not Path(base).is_absolute():
            return None
        path = Path(base) / 'PowerShell/7/pwsh.exe'
        return str(path) if path.is_file() else None
    except OSError:
        return None


class ShellPreference:
    def __init__(self, state):
        self.path = state / 'claude-shell.json'
        self.choice = 'default'
        self.config_dir = self.executable = ''
        self.configured = False
        try:
            value = read(self.path, 512 * 1024)
            if isinstance(value, str):
                value = {'choice': value}  # Preferences saved before optional paths.
            if isinstance(value, dict) and value.get('choice') in CHOICES:
                self.config_dir, self.executable = _paths(value.get('configDir', ''), value.get('executable', ''))
                self.choice = value['choice']
                self.configured = True
        except (OSError, ValueError):
            pass

    def save(self, choice, *, config_dir='', executable=''):
        if choice not in CHOICES:
            raise ValueError('Claude를 실행할 셸을 목록에서 선택해 주세요.')
        config_dir, executable = _paths(config_dir, executable)
        if (choice, config_dir, executable) != (self.choice, self.config_dir, self.executable):
            path = safe(self.path)
            temporary = safe(path.with_name('.claude-shell-' + uuid.uuid4().hex + '.tmp'))
            try:
                value = {'choice': choice, 'configDir': config_dir, 'executable': executable} if config_dir or executable else choice
                with temporary.open('x', encoding='utf-8') as output:
                    json.dump(value, output, ensure_ascii=False)
                os.replace(safe(temporary), safe(path))
            finally:
                temporary.unlink(missing_ok=True)
            self.choice, self.config_dir, self.executable = choice, config_dir, executable
            self.configured = True


def _absolute_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 32767
            and all(ord(char) >= 32 and char != '"' for char in value) and Path(value).is_absolute())


def _path_text(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label}의 전체 경로를 입력해 주세요.')
    value = value.strip()
    if not value:
        return ''
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]  # Windows Explorer's “Copy as path”.
    path = Path(value)
    if (not _absolute_path(value) or '..' in path.parts
            or value.replace('/', '\\').startswith(('\\\\?\\', '\\\\.\\'))
            or any(':' in part or '?' in part or '*' in part for part in path.parts[1:])):
        raise ValueError(f'{label}의 전체 경로를 입력해 주세요.')
    return str(path)


def _paths(config_dir, executable):
    root = _path_text(config_dir, 'Claude 설정 폴더')
    entry = _path_text(executable, 'Claude 실행 파일')
    if entry and Path(entry).suffix.lower() not in {'.exe', '.cmd', '.bat', '.ps1'}:
        raise ValueError('Claude를 실행하는 .exe, .cmd, .bat 또는 .ps1 파일을 선택해 주세요.')
    return root, entry


def checked_paths(config_dir, executable):
    """Check only the locations the user supplied; never open credentials or walk a folder."""
    root, entry = _paths(config_dir, executable)
    if root and not Path(root).is_dir():
        raise ValueError('Claude 설정 폴더를 찾지 못했어요. settings.json이 들어 있는 폴더의 위치를 확인해 주세요.')
    if entry and not Path(entry).is_file():
        raise ValueError('Claude 실행 파일을 찾지 못했어요. 터미널에서 확인한 파일의 위치를 확인해 주세요.')
    return root, entry


def selected_command(choice, *, config_dir='', executable='', cancel_event=None):
    from .bridge import HIDDEN, LaunchDiagnosticError, bind_config_dir, launch_marker_error, resolve_cli
    # The bounded child checks the settings folder after restoring its profile.
    # The settings form already verifies file types before requesting a probe.
    config_dir, executable = _paths(config_dir, executable)
    if choice == 'default':
        command = resolve_cli(executable or None)
        profiles = '-LoadProfiles' in command
        entry = command[8] if '-Entry' in command else command[0]
        kind = 'Function' if profiles else ('ExternalScript' if Path(entry).suffix.lower() == '.ps1' else 'Application')
        return bind_config_dir(command, config_dir), {'stage': 'help', 'commandType': kind, 'loadProfiles': profiles}
    shell = installed_shell(choice)
    if not shell:
        raise ValueError('선택한 PowerShell 설치를 찾을 수 없습니다. 사용 가능한 셸을 선택해 주세요.')
    prefix = [shell, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WRAPPER)]
    # Only the private wrapper receives these switches. Paths stay out of diagnostics.
    overrides = []
    if config_dir:
        overrides += ['-AxConfigDir', config_dir]
    if executable:
        overrides += ['-AxExecutable', executable]
    try:
        result = run_owned(prefix + ['-Discover'] + overrides, capture_output=True, encoding='utf-8', errors='strict',
                           timeout=12, output_limit=192 * 1024, creationflags=HIDDEN, cancel_event=cancel_event)
        if result.returncode == 78:
            error = launch_marker_error(result.stderr)
            if error:
                raise error
        if result.returncode:
            raise LaunchDiagnosticError('cli_start', 'command_resolution')
        evidence = json.loads(result.stdout)
    except CancelledError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchDiagnosticError('cli_start', 'command_resolution') from exc
    if not isinstance(evidence, dict):
        raise LaunchDiagnosticError('cli_start', 'command_resolution')
    entry, kind = evidence.get('entry'), evidence.get('commandType')
    identity, root = evidence.get('identity'), evidence.get('configRoot')
    version = evidence.get('shellVersion')
    file_command = kind in ('Application', 'ExternalScript') and _absolute_path(entry) and identity in ('file', 'coworkFile')
    function = (kind == 'Function' and entry == 'claude' and isinstance(identity, str)
                and re.fullmatch(r'[A-F0-9]{64}', identity))
    if (not (file_command or function) or not _absolute_path(root) or not isinstance(version, str)
            or not re.fullmatch(r'[0-9]{1,4}(?:\.[0-9]{1,6}){1,3}', version)):
        raise LaunchDiagnosticError('cli_start', 'command_resolution')
    if executable and (not file_command or Path(executable) != Path(entry)):
        raise LaunchDiagnosticError('command_not_found', 'command_resolution')
    if config_dir and Path(config_dir) != Path(root):
        raise LaunchDiagnosticError('config_root', 'command_resolution')
    return prefix + ['-Entry', entry, kind, identity, root] + overrides, {
        'stage': 'help', 'shellVersion': version, 'commandType': kind, 'loadProfiles': True}
