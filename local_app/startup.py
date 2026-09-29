"""Apply the launcher's read-only identity/token checks to direct Python starts."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from .windows_process import powershell_path


def verify_process(*, runner=subprocess.run):
    if os.name != 'nt':
        return
    helper = Path(__file__).resolve().parents[1] / 'deploy' / 'CompanyWorkspace.Startup.ps1'
    if not helper.is_file():
        raise RuntimeError('실행 확인 파일이 없습니다. ZIP 전체를 다시 압축 해제해 주세요. (WS-41)')
    # The child inherits this token; no elevation, profile changes or policy writes.
    quoted = str(helper).replace("'", "''")
    script = ("$ErrorActionPreference='Stop'; try { . '" + quoted +
              "'; $context=Get-WorkspaceVerifiedContext; "
              "Assert-WorkspaceNormalProcess -Context $context; exit 0 "
              "} catch { exit (Get-WorkspaceStartupCode -Message $_.Exception.Message) }")
    try:
        result = runner([powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive',
                         '-ExecutionPolicy', 'Bypass',
                         '-Command', script], capture_output=True, timeout=30,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Windows 실행 환경을 확인하지 못했습니다. Company-Workspace.vbs로 실행해 주세요.') from None
    if result.returncode:
        code = result.returncode if result.returncode in range(30, 46) else 45
        raise RuntimeError('현재 실행 권한과 로그인 환경을 확인하지 못했습니다. '
                           f'Company-Workspace.vbs 실행기에서 다시 열어 주세요. (WS-{code})')
