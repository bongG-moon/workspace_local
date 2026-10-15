"""Local NSIS compiler discovery and binding of installer bytes to the build."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

from .config import PublisherError, checked_version, safe_path


def compiler_path(repo_root, configured=''):
    """Never download a compiler or copy it into the source snapshot."""
    selected = configured or os.environ.get('AX_WORKSPACE_NSIS', '')
    candidates = [selected] if selected else []
    if not selected:
        on_path = shutil.which('makensis.exe')
        if on_path:
            candidates.append(on_path)
        for key in ('ProgramFiles(x86)', 'ProgramFiles'):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / 'NSIS/makensis.exe')
        local = safe_path(Path(repo_root) / 'build/nsis')
        if local.is_dir():
            # Portable distributions may have a download and version directory. Avoid
            # an unbounded walk through unrelated generated files.
            candidates.extend(sorted(local.glob('makensis.exe')))
            candidates.extend(sorted(local.glob('*/makensis.exe')))
            candidates.extend(sorted(local.glob('*/*/makensis.exe')))
    for candidate in candidates:
        try:
            path = safe_path(Path(candidate).absolute())
            if path.name.casefold() == 'makensis.exe' and path.is_file():
                return path
        except (OSError, ValueError):
            continue
    raise PublisherError('NSIS 설치 파일을 만들 컴파일러가 없습니다. NSIS 3를 준비한 뒤 고급 설정의 '
                         'NSIS 컴파일러에 makensis.exe 경로를 넣어 주세요. PATH 또는 AX_WORKSPACE_NSIS도 사용할 수 있습니다. '
                         '기존 ZIP만 만들려면 고급 설정에서 NSIS 설치 파일 만들기를 해제하세요. 자동 다운로드는 하지 않았습니다.')


def verify_build_report(source_root, directory, version, standalone_digest):
    """A valid checksum alone must not accept a stale or fixture installer."""
    version = checked_version(version)
    target = safe_path(Path(directory) / f'AX-Workspace-{version}-Setup.exe')
    report_path = safe_path(Path(source_root) / f'build/workspace-installer-{version}.json')
    try:
        if not report_path.is_file() or report_path.stat().st_size > 65536:
            raise ValueError()
        report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        if (report['version'] != version or Path(report['installer']) != target
                or report['standaloneSha256'] != standalone_digest
                or report['testMode'] is not False or report['productId'] != 'AX.Workspace.Desktop'
                or type(report['bytes']) is not int or report['bytes'] != target.stat().st_size
                or report['sha256'] != hashlib.sha256(target.read_bytes()).hexdigest()):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PublisherError('NSIS 설치 파일의 버전·원본 EXE·체크섬 검증 기록이 일치하지 않습니다. '
                             '같은 설정으로 다시 빌드해 주세요. 게시하지 않았습니다.') from exc
