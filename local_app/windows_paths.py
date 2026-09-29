"""Read Windows Known Folders without changing registry or creating folders."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import uuid

DESKTOP_UNAVAILABLE = '바탕화면 위치를 확인하지 못했습니다. 새 업무의 저장 위치를 선택해 주세요.'
# MS-FSCC Reparse Tags: CLOUD and CLOUD_1..F identify Cloud Files storage,
# not a name-surrogate link. Unknown tags remain blocked for workspace paths.
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c8e77b37-3909-4fe6-a4ea-2b9d423b1ee4
_CLOUD_TAGS = frozenset(0x9000001A | (index << 12) for index in range(16))


def redirects_path(info) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, 'st_file_attributes', 0) & 0x400
                and getattr(info, 'st_reparse_tag', 0) not in _CLOUD_TAGS)


def workspace_path(path: Path) -> Path:
    """Check user workspace ancestry; history/config keep their stricter checks."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if redirects_path(info):
            raise ValueError('연결된 경로는 업무 저장 위치로 사용할 수 없습니다. 실제 폴더를 선택해 주세요.')
    return path


def _known_folder_path(identifier: str) -> str:
    class GUID(ctypes.Structure):
        _fields_ = [('data1', ctypes.c_uint32), ('data2', ctypes.c_uint16),
                    ('data3', ctypes.c_uint16), ('data4', ctypes.c_ubyte * 8)]

    folder_id = GUID.from_buffer_copy(uuid.UUID(identifier).bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    get_path = shell.SHGetKnownFolderPath
    get_path.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)]
    get_path.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    pointer = ctypes.c_void_p()
    try:
        # flags=0 queries the current user's redirected location; no KF_FLAG_CREATE.
        result = get_path(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer))
        if result < 0 or not pointer.value:
            raise OSError('Known-folder lookup failed.')
        return ctypes.wstring_at(pointer.value)
    finally:
        if pointer.value:
            free(pointer)


def _known_desktop_path() -> str:
    return _known_folder_path('B4BFCC3A-DB2C-424C-B029-7FE99A87C641')


def local_app_data_folder() -> Path:
    # The operating system's current-user location, never an inherited TEMP or
    # LOCALAPPDATA environment override pointing at a shared directory.
    path = Path(_known_folder_path('F1B32785-6FBA-4FCF-9D55-7B8E7F157091'))
    if not path.is_absolute() or not path.is_dir():
        raise OSError('The user application-data directory is unavailable.')
    return path.resolve(strict=True)


def desktop_folder() -> Path:
    try:
        if os.name != 'nt':
            raise OSError('Windows Known Folders are required.')
        path = Path(_known_desktop_path())
        if not path.is_absolute() or not path.is_dir():
            raise OSError('Desktop is unavailable.')
        return path.resolve(strict=True)
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(DESKTOP_UNAVAILABLE) from exc
