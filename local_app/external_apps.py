"""Explicit document opening through existing Windows associations, never a shell."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess

from .artifacts import DOCUMENT_TYPES
from .windows_paths import workspace_path

TEXT_TYPES = {'.md', '.txt', '.csv', '.tsv', '.html', '.htm'}


def windows_program(name):
    if name not in {'notepad.exe', 'explorer.exe'}:
        raise ValueError('지원하지 않는 연결 프로그램입니다.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    lookup = kernel.GetWindowsDirectoryW if name == 'explorer.exe' else kernel.GetSystemDirectoryW
    lookup.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    lookup.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = lookup(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError('Windows program directory is unavailable.')
    executable = Path(buffer.value) / name
    if not executable.is_absolute() or not executable.is_file():
        raise OSError('Windows program is unavailable.')
    return str(executable)


def open_document(path, action='open'):
    """The caller must authorize task scope first. A launch is not a view receipt."""
    if os.name != 'nt':
        raise ValueError('외부 앱 열기는 Windows에서 지원합니다.')
    if action not in {'open', 'reveal', 'text'}:
        raise ValueError('파일을 열 방식을 확인해 주세요.')
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path.suffix.lower() not in DOCUMENT_TYPES:
        raise ValueError('지원하는 문서 또는 이미지 파일을 선택해 주세요.')
    workspace_path(path)
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError('파일의 실제 위치를 다시 확인해 주세요.')
    try:
        if action == 'open':
            # HTML follows the user's existing association. Scripts/networking
            # run only after the user explicitly requests opening the original.
            os.startfile(str(path), 'open')
        elif action == 'reveal':
            subprocess.Popen([windows_program('explorer.exe'), '/select,', str(path)],
                             cwd=str(path.parent), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            if path.suffix.lower() not in TEXT_TYPES:
                raise ValueError('텍스트 문서와 HTML 소스만 메모장으로 열 수 있습니다.')
            subprocess.Popen([windows_program('notepad.exe'), str(path)], cwd=str(path.parent),
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as exc:
        if getattr(exc, 'winerror', None) in {31, 1155}:
            raise ValueError('이 파일의 연결 프로그램이 없습니다. 파일 위치에서 사용할 앱을 선택해 주세요.') from exc
        raise ValueError('외부 앱을 열도록 요청하지 못했습니다. 파일 위치와 연결 프로그램을 확인해 주세요.') from exc
    return {'ok': True, 'action': action, 'requested': True}
