"""Small app-owned theme preference. No polling or Windows setting changes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid
from .history import read, safe

CHOICES = {'light', 'dark', 'system'}


def system_theme():
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return 'dark' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0 else 'light'
        except OSError:
            pass
    return 'light'


class Appearance:
    def __init__(self, state):
        self.path = Path(state) / 'appearance.json'
        self.lock = threading.Lock()
        self.preference = 'light'
        try:
            value = read(self.path, 1024)
            if isinstance(value, dict) and value.get('theme') in CHOICES:
                self.preference = value['theme']
        except (OSError, ValueError, TypeError):
            pass

    def snapshot(self):
        with self.lock:
            theme = self.preference
        return {'theme': theme, 'effective': system_theme() if theme == 'system' else theme}

    def configure(self, theme):
        if not isinstance(theme, str) or theme not in CHOICES:
            raise ValueError('화면 모드를 다시 선택해 주세요.')
        with self.lock:
            if theme != self.preference:
                temporary = self.path.with_name('.appearance-' + uuid.uuid4().hex + '.tmp')
                try:
                    safe(self.path)
                    with safe(temporary).open('x', encoding='utf-8') as stream:
                        json.dump({'theme': theme}, stream)
                    os.replace(temporary, self.path)
                    self.preference = theme
                finally:
                    temporary.unlink(missing_ok=True)
        return self.snapshot()
