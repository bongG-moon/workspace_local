"""Apply the launcher's read-only identity/token checks to direct Python starts."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
from .windows_process import powershell_path
from .owned_process import run_owned


# Native modules can write diagnostics to a PowerShell child's stdout. Only a
# frame produced for this invocation carries the verified role; other output
# must never be searched for role names or treated as an unframed fallback.
_VERIFY_OUTPUT_LIMIT = 64 * 1024
_VERIFY_BEGIN = '__WORKSPACE_VERIFY_V1_BEGIN__'
_VERIFY_END = '__WORKSPACE_VERIFY_V1_END__'


class StartupError(RuntimeError):
    """A verified startup failure whose code can be preserved by the launcher."""

    def __init__(self, message, *, code=45):
        super().__init__(message)
        self.code = code if type(code) is int and 30 <= code <= 45 else 45


def _verified_mode(output, nonce):
    if not isinstance(output, (bytes, str)) or len(output) > _VERIFY_OUTPUT_LIMIT:
        return None
    if isinstance(output, bytes):
        output = output.decode('ascii', errors='replace')
    # Reject duplicated and incomplete protocol frames, including another
    # invocation's frame. Do not trim or repair anything inside the frame.
    if output.count(_VERIFY_BEGIN) != 1 or output.count(_VERIFY_END) != 1:
        return None
    begin = _VERIFY_BEGIN + nonce + ':'
    end = ':' + nonce + _VERIFY_END
    start = output.find(begin)
    finish = output.find(end)
    if start < 0 or finish < start + len(begin):
        return None
    mode = output[start + len(begin):finish]
    return mode if mode in {'normal', 'administrator'} else None


def verify_process(*, execution_mode='auto', runner=run_owned):
    from .execution_mode import checked_request
    checked_request(execution_mode)
    if os.name != 'nt':
        return 'normal' if execution_mode == 'auto' else execution_mode
    helper = Path(__file__).resolve().parents[1] / 'deploy' / 'CompanyWorkspace.Startup.ps1'
    if not helper.is_file():
        raise StartupError('실행 확인 파일이 없습니다. ZIP 전체를 다시 압축 해제해 주세요. (WS-41)', code=41)
    # The child inherits this token; no elevation, profile changes or policy writes.
    quoted = str(helper).replace("'", "''")
    nonce = secrets.token_hex(16)
    begin = _VERIFY_BEGIN + nonce + ':'
    end = ':' + nonce + _VERIFY_END
    script = ("$ErrorActionPreference='Stop'; try { . '" + quoted +
              "'; $context=Get-WorkspaceVerifiedContext; "
              "$mode=Get-WorkspaceExecutionMode -Context $context; " +
              ("" if execution_mode == 'auto' else
               "if ($mode -ne '" + execution_mode + "') { throw 'WORKSPACE_STARTUP:33' }; ") +
              "[Console]::Write('" + begin + "' + $mode + '" + end + "'); exit 0 "
              "} catch { exit (Get-WorkspaceStartupCode -Message $_.Exception.Message) }")
    try:
        result = runner([powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive',
                         '-ExecutionPolicy', 'Bypass',
                         '-Command', script], capture_output=True, timeout=30,
                        output_limit=_VERIFY_OUTPUT_LIMIT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        raise StartupError('Windows 실행 환경을 확인하지 못했습니다. Company-Workspace.vbs로 실행해 주세요.') from None
    if result.returncode:
        code = result.returncode if result.returncode in range(30, 46) else 45
        raise StartupError('현재 실행 권한과 로그인 환경을 확인하지 못했습니다. '
                           f'Company-Workspace.vbs 실행기에서 다시 열어 주세요. (WS-{code})', code=code)
    mode = _verified_mode(getattr(result, 'stdout', None), nonce)
    if mode not in {'normal', 'administrator'} or execution_mode != 'auto' and mode != execution_mode:
        raise StartupError('Windows 실행 권한을 확인하지 못했습니다. (WS-45)')
    return mode
