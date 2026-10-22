"""App-owned folder approvals and ordinary permission choices; no Claude settings writes."""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading
import uuid
from .history import read, safe
from .windows_paths import workspace_path

MAX_ENTRIES = 10500
MAX_BYTES = 4 * 1024 * 1024
ORDINARY_MODES = {'default', 'manual', 'plan', 'acceptEdits', 'auto'}


def identity(workspace):
    path = Path(workspace).absolute()
    if path.resolve(strict=True) != path or not path.is_dir():
        raise ValueError('업무 폴더의 실제 위치가 바뀌었습니다. 폴더를 다시 확인해 주세요.')
    workspace_path(path)
    stat = path.stat()
    if not stat.st_ino:
        raise ValueError('업무 폴더의 고유 정보를 확인하지 못했습니다.')
    return {'path': os.path.normcase(str(path)), 'device': stat.st_dev, 'inode': stat.st_ino,
            'created': getattr(stat, 'st_birthtime_ns', None)}


class WorkspacePreferences:
    def __init__(self, state):
        self.path = Path(state) / 'workspace-preferences.json'
        self.lock = threading.RLock()
        self.data = {'schema': 1, 'approvals': {}, 'modes': {}}
        try:
            value = read(self.path, MAX_BYTES)
            if (not isinstance(value, dict) or value.get('schema') != 1
                    or any(not isinstance(value.get(k), dict) or len(value[k]) > MAX_ENTRIES for k in ('approvals', 'modes'))):
                return
            self.data = value
        except (OSError, ValueError, TypeError):
            pass  # Missing/corrupt preferences never grant access.

    def approved(self, workspace):
        try:
            key = os.path.normcase(str(Path(workspace).absolute()))
            with self.lock:
                expected = self.data['approvals'].get(key)
            if not isinstance(expected, dict):
                return False
            current = identity(workspace)
            return expected == current
        except (OSError, ValueError, TypeError):
            return False

    def _save(self, value):
        encoded = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ValueError('기억한 업무 설정이 저장 한도에 도달했습니다.')
        temporary = self.path.with_name('.workspace-preferences-' + uuid.uuid4().hex + '.tmp')
        try:
            safe(self.path)
            with safe(temporary).open('xb') as stream:
                stream.write(encoded)
            os.replace(temporary, self.path)
            self.data = value
        finally:
            temporary.unlink(missing_ok=True)

    def approve(self, workspace):
        current = identity(workspace)
        with self.lock:
            if self.data['approvals'].get(current['path']) == current:
                return
            approvals = dict(self.data['approvals'])
            if len(approvals) >= MAX_ENTRIES and current['path'] not in approvals:
                raise ValueError('기억한 폴더가 보관 한도에 도달했습니다.')
            approvals[current['path']] = current
            self._save({**self.data, 'approvals': approvals})

    def mode(self, item):
        try:
            with self.lock:
                row = self.data['modes'].get(item['id'])
            if isinstance(row, dict) and row.get('identity') == identity(item['workspace']):
                if row.get('inherit') is True:
                    return {'inherit': True}
                if row.get('value') is None or row.get('value') in ORDINARY_MODES:
                    return {'permissionMode': row['value']}
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return {}

    def remember_mode(self, item, mode, *, inherit=False):
        if mode is not None and mode not in ORDINARY_MODES:
            # Never persist a Bypass authorization. Returning next time to
            # Manual is deliberate, not an automatic grant to bypass controls.
            mode = 'default'
        current = identity(item['workspace'])
        with self.lock:
            modes = dict(self.data['modes'])
            row = {'identity': current, 'value': mode}
            if inherit:
                row['inherit'] = True
            if modes.get(item['id']) == row:
                return
            if len(modes) >= MAX_ENTRIES and item['id'] not in modes:
                raise ValueError('기억한 업무 설정이 보관 한도에 도달했습니다.')
            modes[item['id']] = row
            self._save({**self.data, 'modes': modes})
