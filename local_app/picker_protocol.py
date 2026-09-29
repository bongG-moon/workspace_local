"""Bounded native-picker result channel; console output is never data."""
from __future__ import annotations

import json
from pathlib import Path
import stat

MAX_RESULT_BYTES = 256 * 1024
MAX_PATHS = 12
INVALID_RESULT = '선택 결과를 확인하지 못했습니다. 선택 창을 다시 열어 주세요.'


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(INVALID_RESULT)
        result[key] = value
    return result


def read_result(path: Path, kind: str) -> dict:
    """Accept exactly one versioned response and only existing selected items."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(INVALID_RESULT)
        with path.open('rb') as stream:
            raw = stream.read(MAX_RESULT_BYTES + 1)
        if not raw or len(raw) > MAX_RESULT_BYTES:
            raise ValueError(INVALID_RESULT)
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object)
        if (not isinstance(value, dict) or set(value) - {'version', 'status', 'paths', 'errorCode'}
                or type(value.get('version')) is not int or value['version'] != 1
                or value.get('status') not in {'success', 'cancel', 'error'}
                or not isinstance(value.get('paths'), list)):
            raise ValueError(INVALID_RESULT)
        status, paths = value['status'], value['paths']
        if status == 'error':
            if paths or value.get('errorCode') != 'picker_failed':
                raise ValueError(INVALID_RESULT)
            raise ValueError('파일 선택 창을 열지 못했습니다. 경로를 직접 입력하거나 다시 선택해 주세요.')
        if 'errorCode' in value or (status == 'cancel' and paths):
            raise ValueError(INVALID_RESULT)
        if status == 'success' and not (1 <= len(paths) <= (1 if kind == 'folder' else MAX_PATHS)):
            raise ValueError('폴더는 1개, 첨부 파일은 한 번에 12개까지 선택해 주세요.')
        seen = set()
        for selected in paths:
            if (not isinstance(selected, str) or not selected or len(selected) > 8192
                    or any(ord(char) < 32 for char in selected)):
                raise ValueError(INVALID_RESULT)
            item = Path(selected)
            if not item.is_absolute() or '..' in item.parts:
                raise ValueError(INVALID_RESULT)
            resolved = item.resolve(strict=True)
            if not (resolved.is_dir() if kind == 'folder' else resolved.is_file()):
                raise ValueError(INVALID_RESULT)
            if resolved in seen:
                raise ValueError(INVALID_RESULT)
            seen.add(resolved)
        return {'version': 1, 'status': status, 'paths': paths}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(INVALID_RESULT) from exc
