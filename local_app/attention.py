"""Pending-work summaries and optional attention for one verified app window.

Only short, sanitized question/description metadata is projected, never command
or document bodies. Native flashing never activates a window or changes settings.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import unicodedata

from .progress_log import clean_text


MAX_SUMMARY = 100
DEFAULT_REQUEST_SUMMARY = '승인 또는 답변 내용을 확인해 주세요'


def clean_summary(value, fallback=''):
    """Bound a public request label and redact credentials before truncation."""
    if not isinstance(value, str) or len(value) > 4096:
        return fallback
    value = clean_text(value, 16384)[0].replace('[인증 정보 숨김]', '[인증정보숨김]')
    value = ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in value)
    value = ' '.join(value.split()).strip()
    # A banner is not the place to display commands embedded in a description.
    if not value or '```' in value or re.match(
            r'(?i)^(?:\$ |& |(?:powershell|pwsh|cmd|bash|sh|python(?:\d+(?:\.\d+)?)?|curl|wget)\s)', value):
        return fallback
    return value if len(value) <= MAX_SUMMARY else value[:MAX_SUMMARY - 1].rstrip() + '…'


def request_summary(request, kind):
    """Use known description fields only; arbitrary tool inputs remain private."""
    if kind == 'choice':
        return clean_summary(request.get('question'), '선택 항목을 골라 주세요')
    values = request.get('input')
    values = values if isinstance(values, dict) else {}
    if kind == 'question':
        questions = values.get('questions')
        if isinstance(questions, list):
            for question in questions[:4]:
                if isinstance(question, dict):
                    value = clean_summary(question.get('question'))
                    if value:
                        return value
        return clean_summary(request.get('title'), '질문에 답변해 주세요')
    tool = request.get('tool')
    fallback = ({'Edit': '파일 수정 승인이 필요해요', 'Write': '파일 저장 승인이 필요해요',
                 'MultiEdit': '파일 수정 승인이 필요해요', 'NotebookEdit': '노트북 수정 승인이 필요해요',
                 'Read': '자료 읽기 승인이 필요해요', 'Bash': '명령 실행 승인이 필요해요',
                 'PowerShell': '명령 실행 승인이 필요해요', 'WebFetch': '웹 자료 조회 승인이 필요해요',
                 'WebSearch': '웹 검색 승인이 필요해요'}.get(tool if isinstance(tool, str) else '',
                                                            '도구 사용 승인이 필요해요'))
    # Some CLI versions provide a title instead of a description. Skip blank
    # or unsafe metadata rather than letting it hide another usable label.
    command = values.get('command')
    for description in (request.get('description'), values.get('description'), request.get('title')):
        if isinstance(description, str) and isinstance(command, str) and description.strip() == command.strip():
            continue
        label = clean_summary(description)
        if label:
            return label
    return fallback


def snapshot(sessions):
    """Project actual pending requests, not saved status labels or chat text.

    The caller holds its session lock. A newly observed native request gets a
    fresh _attentionId in the server, even when a CLI reuses a request ID later.
    Duplicate deliveries of that outstanding request retain its _attentionId.
    """
    items, seen = [], set()
    source = sessions.values() if isinstance(sessions, dict) else sessions
    for session in source:
        if not isinstance(session, dict) or not isinstance(session.get('id'), str):
            continue
        sid = session['id']
        if not sid or session.get('state') in {'error', 'stopped'}:
            continue
        bridge = session.get('bridge')
        connected = bridge is None or not getattr(bridge, 'closed', False)
        title = session.get('title')
        title = title[:200] if isinstance(title, str) and title.strip() else '새 업무'
        requests = session.get('requests')
        candidates = []
        if connected and isinstance(requests, dict):
            for request in requests.values():
                if not isinstance(request, dict):
                    continue
                identity = request.get('_attentionId') or request.get('id')
                if not isinstance(identity, str) or not identity or len(identity) > 256:
                    continue
                kind = 'question' if request.get('tool') == 'AskUserQuestion' else 'approval'
                candidates.append((kind, identity, request_summary(request, kind)))
        choice = session.get('choice')
        if (session.get('state') in {'idle', 'done'} and isinstance(choice, dict)
                and isinstance(choice.get('id'), str) and 0 < len(choice['id']) <= 256):
            candidates.append(('choice', choice['id'], request_summary(choice, 'choice')))
        for kind, identity, summary in candidates:
            key = hashlib.sha256(json.dumps([sid, kind, identity], ensure_ascii=True).encode('ascii')).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            items.append({'id': key, 'sessionId': sid, 'title': title, 'kind': kind, 'summary': summary})
    items.sort(key=lambda row: row['id'])
    revision = hashlib.sha256(json.dumps(items, ensure_ascii=True, sort_keys=True,
                                        separators=(',', ':')).encode('ascii')).hexdigest()
    return {'revision': revision, 'total': len(items),
            'approvalCount': sum(row['kind'] == 'approval' for row in items),
            'questionCount': sum(row['kind'] == 'question' for row in items),
            'choiceCount': sum(row['kind'] == 'choice' for row in items), 'items': items}


@dataclass(frozen=True)
class WindowBinding:
    hwnd: int
    pid: int
    created: int
    session: int
    sid: bytes
    image: str


class WindowsAttention:
    """Capture foreground for attention; find/focus only on explicit reopen.

    Edge may reuse its existing process, so the launch PID is not a window
    identity. An authenticated page sets a per-run random, non-auth title. Its
    foreground capture is then bound to exact executable, SID/session and
    process creation time. Every flash rechecks all of these facts.
    """
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Native attention is unavailable.')
        from ctypes import wintypes as wt
        self.wt = wt
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        def signature(library, name, arguments, result):
            fn = getattr(library, name)
            fn.argtypes, fn.restype = arguments, result
            return fn
        ptr = ctypes.POINTER
        signature(self.user, 'GetForegroundWindow', [], wt.HWND)
        signature(self.user, 'IsWindow', [wt.HWND], wt.BOOL)
        signature(self.user, 'IsWindowVisible', [wt.HWND], wt.BOOL)
        signature(self.user, 'ShowWindow', [wt.HWND, ctypes.c_int], wt.BOOL)
        signature(self.user, 'SetForegroundWindow', [wt.HWND], wt.BOOL)
        signature(self.user, 'GetAncestor', [wt.HWND, wt.UINT], wt.HWND)
        signature(self.user, 'GetWindowTextLengthW', [wt.HWND], ctypes.c_int)
        signature(self.user, 'GetWindowTextW', [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int)
        signature(self.user, 'GetWindowThreadProcessId', [wt.HWND, ptr(wt.DWORD)], wt.DWORD)
        signature(self.kernel, 'OpenProcess', [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)
        signature(self.kernel, 'CloseHandle', [wt.HANDLE], wt.BOOL)
        signature(self.kernel, 'QueryFullProcessImageNameW', [wt.HANDLE, wt.DWORD, wt.LPWSTR, ptr(wt.DWORD)], wt.BOOL)
        signature(self.kernel, 'ProcessIdToSessionId', [wt.DWORD, ptr(wt.DWORD)], wt.BOOL)
        signature(self.kernel, 'GetProcessTimes', [wt.HANDLE, ptr(wt.FILETIME), ptr(wt.FILETIME), ptr(wt.FILETIME), ptr(wt.FILETIME)], wt.BOOL)
        signature(self.advapi, 'OpenProcessToken', [wt.HANDLE, wt.DWORD, ptr(wt.HANDLE)], wt.BOOL)
        signature(self.advapi, 'GetTokenInformation', [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ptr(wt.DWORD)], wt.BOOL)
        signature(self.advapi, 'IsValidSid', [ctypes.c_void_p], wt.BOOL)
        signature(self.advapi, 'GetLengthSid', [ctypes.c_void_p], wt.DWORD)
        class FlashInfo(ctypes.Structure):
            _fields_ = [('cbSize', wt.UINT), ('hwnd', wt.HWND), ('dwFlags', wt.DWORD),
                        ('uCount', wt.UINT), ('dwTimeout', wt.DWORD)]
        self.FlashInfo = FlashInfo
        signature(self.user, 'FlashWindowEx', [ptr(FlashInfo)], wt.BOOL)
        from .app_window import browser_candidates
        self.allowed_images = {os.path.normcase(str(path)) for _, path in browser_candidates()}
        from .native_window import desktop_executable
        self.allowed_images.add(os.path.normcase(str(desktop_executable())))
        self.EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        signature(self.user, 'EnumWindows', [self.EnumProc, wt.LPARAM], wt.BOOL)
        own = self._process(os.getpid())
        if own is None or not self.allowed_images:
            raise OSError('An eligible app browser could not be verified.')
        self.own_session, self.own_sid = own[1], own[2]

    def _process(self, pid):
        wt = self.wt
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        token = wt.HANDLE()
        try:
            name, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            session = wt.DWORD()
            times = [wt.FILETIME() for _ in range(4)]
            if (not self.kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))
                    or not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session))
                    or not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))
                    or not self.advapi.OpenProcessToken(handle, 0x0008, ctypes.byref(token))):
                return None
            needed = wt.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))  # TokenUser
            if not 0 < needed.value <= 65536:
                return None
            buffer = ctypes.create_string_buffer(needed.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, needed.value, ctypes.byref(needed)):
                return None
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
            if not sid or not self.advapi.IsValidSid(sid):
                return None
            length = self.advapi.GetLengthSid(sid)
            if not 8 <= length <= 68:
                return None
            image = os.path.normcase(str(Path(name.value).resolve(strict=True)))
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return created, session.value, ctypes.string_at(sid, length), image
        finally:
            if token:
                self.kernel.CloseHandle(token)
            self.kernel.CloseHandle(handle)

    def _window(self, hwnd, title, *, allow_hidden=False):
        if (not hwnd or not self.user.IsWindow(hwnd) or (not allow_hidden and not self.user.IsWindowVisible(hwnd))
                or self.user.GetAncestor(hwnd, 2) != hwnd):  # GA_ROOT
            return None
        length = self.user.GetWindowTextLengthW(hwnd)
        if not 0 < length < 2048:
            return None
        text = ctypes.create_unicode_buffer(length + 1)
        if not self.user.GetWindowTextW(hwnd, text, len(text)) or text.value != title:
            return None
        pid = self.wt.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return None
        identity = self._process(pid.value)
        if identity is None:
            return None
        created, session, sid, image = identity
        if session != self.own_session or sid != self.own_sid or image not in self.allowed_images:
            return None
        return WindowBinding(int(hwnd), pid.value, created, session, sid, image)

    def bind(self, title):
        return self._window(self.user.GetForegroundWindow(), title)

    def owned(self, hwnd, pid):
        from .native_window import desktop_executable
        binding = self._window(hwnd, 'Workspace', allow_hidden=True)
        if (binding is not None and binding.pid == pid
                and binding.image == os.path.normcase(str(desktop_executable()))):
            return binding
        return None

    def find(self, title):
        # Used only for an explicit reopen click. The random per-run title plus
        # SID/session/image/creation checks exclude unrelated browser windows.
        found = []
        def visit(hwnd, _):
            binding = self._window(hwnd, title, allow_hidden=True)
            if binding is not None:
                found.append(binding)
                return False
            return True
        self.user.EnumWindows(self.EnumProc(visit), 0)
        return found[0] if found else None

    def flash(self, binding, title, *, stop=False):
        if self._window(binding.hwnd, title, allow_hidden=True) != binding:
            return 'invalid'
        if not self.user.IsWindowVisible(binding.hwnd):
            return 'hidden'
        foreground = self.user.GetForegroundWindow() == binding.hwnd
        flags = 0 if stop or foreground else 2  # FLASHW_STOP / FLASHW_TRAY
        info = self.FlashInfo(ctypes.sizeof(self.FlashInfo), binding.hwnd, flags, 0 if flags == 0 else 3, 0)
        # The BOOL reports the previous active state, NOT whether it succeeded.
        self.user.FlashWindowEx(ctypes.byref(info))
        return 'stopped' if stop else 'foreground' if foreground else 'requested'

    def visibility(self, binding, title, *, show):
        # Only an authenticated UI click or the owned tray's menu calls this.
        # Recheck identity on every use, including a recycled native HWND.
        if self._window(binding.hwnd, title, allow_hidden=True) != binding:
            return False
        self.user.ShowWindow(binding.hwnd, 9 if show else 0)
        if show:
            self.user.SetForegroundWindow(binding.hwnd)
        return bool(self.user.IsWindowVisible(binding.hwnd)) == show

    def theme(self, binding, title):
        if self._window(binding.hwnd, title, allow_hidden=True) != binding:
            return {'applied': False, 'reason': 'invalid_window'}
        from .window_theme import apply
        return apply(binding.hwnd)

    def foreground(self, binding, title):
        return (self._window(binding.hwnd, title) == binding
                and self.user.GetForegroundWindow() == binding.hwnd)


_AUTO = object()


class AttentionNotifier:
    def __init__(self, *, enabled=True, native=_AUTO):
        self.window_title = 'Company Workspace · ' + secrets.token_urlsafe(16)
        self._lock = threading.RLock()
        self._binding = None
        self._owned = False
        self._revision = None
        self._pending = set()
        self._closed = False
        self.theme_state = {'applied': False, 'reason': 'not_bound'}
        if not enabled:
            native = None
        elif native is _AUTO and os.name != 'nt':
            native = None
        # Loading Windows libraries and checking the current process is deferred
        # until the authenticated page asks to bind. Ordinary fixture/bootstrap
        # construction never queries a live browser or process.
        self._native = native

    @property
    def native_state(self):
        with self._lock:
            return {'supported': self._native is not None, 'bound': self._binding is not None and not self._closed}

    @property
    def binding_title(self):
        return 'Workspace' if self._owned else self.window_title

    def bind_owned(self, hwnd, pid, *, apply_theme=True):
        with self._lock:
            if self._closed or self._native is None:
                return False
            try:
                if self._native is _AUTO:
                    self._native = WindowsAttention()
                binding = self._native.owned(hwnd, pid)
                if binding is None:
                    return False
                self._binding, self._owned = binding, True
                self.theme_state = (self._native.theme(binding, self.binding_title) if apply_theme
                                    else {'applied': False, 'reason': 'owned_host_theme'})
                return True
            except (OSError, AttributeError, ValueError):
                return False

    def _flash(self, *, stop=False):
        if self._native is None or self._binding is None:
            return
        try:
            result = self._native.flash(self._binding, self.binding_title, stop=stop)
            if result == 'invalid':
                self._binding = None
        except (OSError, AttributeError, ValueError):
            self._binding = None

    def bind(self):
        with self._lock:
            if self._owned:
                return self.native_state
            if not self._closed and self._native is _AUTO:
                try:
                    self._native = WindowsAttention()
                except (OSError, AttributeError, ValueError):
                    self._native = None
            if not self._closed and self._native is not None:
                try:
                    binding = self._native.bind(self.window_title)
                except (OSError, AttributeError, ValueError):
                    binding = None
                if binding is not None:
                    if self._binding is not None and self._binding != binding:
                        self._flash(stop=True)
                    self._binding = binding
                    self._flash(stop=True)
                    if hasattr(self._native, 'theme'):
                        self.theme_state = self._native.theme(binding, self.window_title)
            return self.native_state

    def is_foreground(self):
        with self._lock:
            if self._closed or self._binding is None or self._native in (None, _AUTO):
                return False
            try:
                return self._native.foreground(self._binding, self.binding_title) is True
            except (OSError, AttributeError, ValueError):
                return False

    def update(self, value):
        with self._lock:
            if self._closed or value.get('revision') == self._revision:
                return self.native_state
            self._revision = value.get('revision')
            pending = {row['id'] for row in value.get('items', []) if isinstance(row, dict) and isinstance(row.get('id'), str)}
            added, previous = pending - self._pending, self._pending
            self._pending = pending
            if added:
                self._flash()
            elif previous and not pending:
                self._flash(stop=True)
            return self.native_state

    def set_visible(self, show):
        with self._lock:
            if self._closed:
                return False
            if show and self._native is _AUTO:
                try:
                    self._native = WindowsAttention()
                except (OSError, AttributeError, ValueError):
                    self._native = None
            if self._native in (None, _AUTO):
                return False
            try:
                if self._binding is not None and self._native.visibility(self._binding, self.binding_title, show=show) is True:
                    return True
                if show and not self._owned and callable(getattr(self._native, 'find', None)):
                    binding = self._native.find(self.window_title)
                    if binding is not None:
                        self._binding = binding
                        return self._native.visibility(binding, self.window_title, show=True) is True
                return False
            except (OSError, AttributeError, ValueError):
                return False

    def close(self):
        with self._lock:
            if not self._closed:
                self._flash(stop=True)
            self._closed = True
            self._binding = None
            self._pending.clear()
