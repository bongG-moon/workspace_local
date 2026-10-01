"""Read one explicitly requested directory for the app's file/folder chooser.

Only bounded metadata is returned. No contents, recursive scans, file creation,
Claude connection, trust acknowledgement, or Windows preferences are involved.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from .completions import REFERENCE_FILE_TYPES
from .windows_paths import _known_folder_path, redirects_path, workspace_path

MAX_ENTRIES = 250
MAX_SCANNED = 2000
_KNOWN_FOLDERS = (
    ('바탕화면', 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641'),
    ('문서', 'FDD39AD0-238F-46AF-ADB4-6C85480369C7'),
    ('다운로드', '374DE290-123F-4565-9164-39C4925E467B'),
)


def _path(value, *, existing=True):
    if (not isinstance(value, str) or not value or len(value) > 32767
            or any(ord(char) < 32 for char in value)):
        raise ValueError('폴더의 전체 경로를 입력해 주세요.')
    # Shell namespaces, UNC servers, device paths and ADS are deliberately not
    # emulated. The user may explicitly open the native picker for those places.
    if value.startswith(('\\\\', '//')):
        raise ValueError('네트워크 위치는 아래의 ‘Windows 탐색기로 선택’을 사용해 주세요.')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('폴더의 전체 경로를 입력해 주세요.')
    if os.name == 'nt' and any(':' in part for part in path.parts[1:]):
        raise ValueError('일반 폴더 경로를 입력해 주세요.')
    workspace_path(path)
    if not existing:
        return path
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError('파일 대신 폴더를 선택해 주세요.')
    return resolved


def _shortcuts():
    rows, seen = [], set()
    if os.name == 'nt':
        for name, identifier in _KNOWN_FOLDERS:
            try:
                path = _path(_known_folder_path(identifier))
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    rows.append({'name': name, 'path': str(path)})
            except (OSError, ValueError, RuntimeError, AttributeError):
                continue
    try:
        home = _path(str(Path.home()))
        if os.path.normcase(str(home)) not in seen:
            rows.append({'name': '내 폴더', 'path': str(home)})
    except (OSError, ValueError, RuntimeError):
        pass
    return rows


def validate_folder_selection(paths):
    """Recheck an explicit selection, including native-picker network shares."""
    if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
        raise ValueError('업무 폴더 하나를 선택해 주세요.')
    value = paths[0]
    if (not value or len(value) > 32767 or any(ord(char) < 32 for char in value)
            or value.startswith(('\\\\?\\', '\\\\.\\'))):
        raise ValueError('폴더의 전체 경로를 확인해 주세요.')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('폴더의 전체 경로를 확인해 주세요.')
    if os.name == 'nt' and any(':' in part for part in path.parts[1:]):
        raise ValueError('일반 폴더 경로를 선택해 주세요.')
    try:
        resolved = workspace_path(path).resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError('파일 대신 폴더를 선택해 주세요.')
        return [str(resolved)]
    except OSError as exc:
        raise ValueError('선택한 폴더를 다시 확인해 주세요. 위치나 접근 권한이 바뀌었을 수 있어요.') from exc


def browse_paths(data, *, max_entries=MAX_ENTRIES, max_scanned=MAX_SCANNED, seconds=.6):
    if not isinstance(data, dict) or data.get('kind') not in {'files', 'folder'}:
        raise ValueError('선택할 항목의 종류를 확인해 주세요.')
    query = data.get('query', '')
    if not isinstance(query, str) or len(query) > 160 or any(ord(c) < 32 for c in query):
        raise ValueError('검색어는 160자 이내로 입력해 주세요.')
    shortcuts = _shortcuts()
    requested = data.get('path')
    if requested is None or requested == '':
        if not shortcuts:
            raise ValueError('기본 폴더를 찾지 못했어요. 전체 경로를 입력하거나 Windows 탐색기로 선택해 주세요.')
        requested = shortcuts[0]['path']
    try:
        candidate = _path(requested, existing=False)
        if data.get('initial') is True:
            # The default managed workspace may not have been created yet.
            # Start from its existing parent without creating or trusting it.
            for _ in range(32):
                if candidate.exists() or candidate.parent == candidate:
                    break
                candidate = candidate.parent
        directory = _path(str(candidate))
    except FileNotFoundError as exc:
        raise ValueError('폴더를 찾지 못했어요. 위치가 바뀌었는지 확인해 주세요.') from exc
    except PermissionError as exc:
        raise ValueError('이 폴더를 볼 권한이 없어요. 다른 위치를 선택해 주세요.') from exc
    entries, scanned, limited = [], 0, False
    deadline = time.monotonic() + seconds
    needle = query.casefold().strip()
    try:
        with os.scandir(directory) as children:
            for child in children:
                if scanned >= max_scanned or time.monotonic() >= deadline or len(entries) >= max_entries:
                    limited = True
                    break
                scanned += 1
                if child.name.startswith('.') or (needle and needle not in child.name.casefold()):
                    continue
                try:
                    info = child.stat(follow_symlinks=False)
                    if redirects_path(info) or getattr(info, 'st_file_attributes', 0) & 2:
                        continue
                    is_directory = stat.S_ISDIR(info.st_mode)
                    if not is_directory and (data['kind'] == 'folder' or not stat.S_ISREG(info.st_mode)
                                             or Path(child.name).suffix.lower() not in REFERENCE_FILE_TYPES):
                        continue
                    entries.append({'name': child.name, 'path': str(directory / child.name),
                                    'kind': 'folder' if is_directory else 'file',
                                    'size': None if is_directory else info.st_size})
                except (OSError, ValueError):
                    limited = True
    except PermissionError as exc:
        raise ValueError('이 폴더를 볼 권한이 없어요. 다른 위치를 선택해 주세요.') from exc
    except OSError as exc:
        raise ValueError('폴더를 열지 못했어요. 연결 상태를 확인하거나 Windows 탐색기로 선택해 주세요.') from exc
    entries.sort(key=lambda item: (item['kind'] != 'folder', item['name'].casefold()))
    ancestors = list(reversed(directory.parents)) + [directory]
    breadcrumbs = [{'name': path.name or str(path), 'path': str(path)} for path in ancestors]
    return {'path': str(directory), 'parent': str(directory.parent) if directory.parent != directory else None,
            'breadcrumbs': breadcrumbs[-32:], 'shortcuts': shortcuts, 'entries': entries, 'limited': limited}
