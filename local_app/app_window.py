"""Open a supported installed browser as an app; reuse the verified owned HWND.

No browser installation, default-browser change, profile mutation or process kill.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
import webbrowser


def browser_candidates():
    """Prefer Edge, then Chrome, including registered nonstandard installations."""
    rows, seen = [], set()
    for name, relative, executable in (
        ('Edge', 'Microsoft/Edge/Application/msedge.exe', 'msedge.exe'),
        ('Chrome', 'Google/Chrome/Application/chrome.exe', 'chrome.exe'),
    ):
        candidates = [Path(root) / relative for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')
                      if (root := os.environ.get(key))]
        if os.name == 'nt':
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, 'Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + executable,
                                            0, winreg.KEY_READ | view) as key:
                            value, kind = winreg.QueryValueEx(key, '')
                        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                            candidates.append(Path(os.path.expandvars(value).strip('"')))
                    except OSError:
                        pass
        for path in candidates:
            try:
                if not path.is_absolute() or path.name.casefold() != executable or not path.is_file():
                    continue
                path = path.resolve(strict=True)
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    rows.append((name, path))
            except OSError:
                continue
    return rows


def launch(url):
    if os.name == 'nt':
        for name, path in browser_candidates():
            try:
                subprocess.Popen([str(path), '--app=' + url], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return {'mode': 'app', 'browser': name}
            except OSError:
                continue
    if not webbrowser.open(url):
        raise OSError('앱 화면을 열지 못했습니다. 설치된 Edge 또는 Chrome 실행 상태를 확인해 주세요.')
    return {'mode': 'browser', 'browser': 'default',
            'message': '앱 창으로 실행할 Edge·Chrome을 확인하지 못해 기본 브라우저로 열었어요.'}


class AppWindow:
    """Serialize icon/tray/notification opens, including the first page load gap."""
    def __init__(self, notifier, url, *, opener=launch, clock=time.monotonic):
        self.notifier, self.url, self.opener, self.clock = notifier, url, opener, clock
        self.lock = threading.RLock()
        self.launched_at = None
        self.presentation = {}

    def open(self):
        with self.lock:
            if self.notifier.set_visible(True):
                self.launched_at = None
                return {'ok': True, 'action': 'activated', **self.presentation}
            now = self.clock()
            if self.launched_at is not None and now - self.launched_at < 15:
                return {'ok': True, 'action': 'opening', **self.presentation}
            self.presentation = self.opener(self.url) or {}
            self.launched_at = now
            return {'ok': True, 'action': 'opened', **self.presentation}
