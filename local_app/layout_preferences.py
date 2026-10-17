"""App-owned panel preferences, independent of the local server's port."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

from .history import read, safe


KEYS = ('sidebarCollapsed', 'inspectorCollapsed', 'inspectorWidth', 'miniMode')


def validated(value):
    if not isinstance(value, dict) or set(value) != set(KEYS):
        raise ValueError('화면 배치 설정의 항목을 확인해 주세요.')
    if any(type(value[key]) is not bool for key in ('sidebarCollapsed', 'inspectorCollapsed', 'miniMode')):
        raise ValueError('화면 배치 설정을 다시 선택해 주세요.')
    if type(value['inspectorWidth']) is not int or not 260 <= value['inspectorWidth'] <= 560:
        raise ValueError('자료와 결과 너비는 260~560픽셀이어야 합니다.')
    return {key: value[key] for key in KEYS}


class LayoutPreferences:
    def __init__(self, state):
        self.path = Path(state) / 'layout.json'
        self.lock = threading.Lock()
        self.preference = None
        try:
            self.preference = validated(read(self.path, 1024))
        except (OSError, ValueError, TypeError):
            pass

    def snapshot(self):
        with self.lock:
            return dict(self.preference) if self.preference is not None else None

    def configure(self, value):
        preference = validated(value)
        with self.lock:
            if preference != self.preference:
                temporary = self.path.with_name('.layout-' + uuid.uuid4().hex + '.tmp')
                try:
                    safe(self.path)
                    with safe(temporary).open('x', encoding='utf-8') as stream:
                        json.dump(preference, stream)
                    # Recheck before replacing, including parent reparse points.
                    safe(self.path)
                    os.replace(safe(temporary), self.path)
                    self.preference = preference
                finally:
                    safe(temporary).unlink(missing_ok=True)
            return dict(self.preference)
