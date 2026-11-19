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
        self.configured = False
        try:
            value = read(self.path, 256)
            if value in CHOICES:
                self.choice = value
                self.configured = True
        except (OSError, ValueError):
            pass

    def save(self, choice):
        if choice not in CHOICES:
            raise ValueError('Claude를 실행할 셸을 목록에서 선택해 주세요.')
        if choice != self.choice:
            path = safe(self.path)
            temporary = safe(path.with_name('.claude-shell-' + uuid.uuid4().hex + '.tmp'))
            try:
                with temporary.open('x', encoding='ascii') as output:
                    json.dump(choice, output)
                os.replace(safe(temporary), safe(path))
            finally:
                temporary.unlink(missing_ok=True)
            self.choice = choice
            self.configured = True


def _absolute_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 32767
            and all(ord(char) >= 32 and char != '"' for char in value) and Path(value).is_absolute())


def selected_command(choice, *, cancel_event=None):
    from .bridge import HIDDEN, LaunchDiagnosticError, launch_marker_error
    shell = installed_shell(choice)
    if not shell:
        raise ValueError('선택한 PowerShell 설치를 찾을 수 없습니다. 사용 가능한 셸을 선택해 주세요.')
    prefix = [shell, '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WRAPPER)]
    try:
        result = run_owned(prefix + ['-Discover'], capture_output=True, encoding='utf-8', errors='strict',
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
    return prefix + ['-Entry', entry, kind, identity, root], {
        'stage': 'help', 'shellVersion': version, 'commandType': kind, 'loadProfiles': True}
