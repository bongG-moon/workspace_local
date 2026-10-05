"""Windows notification-area host for the existing Workspace server.

No browser hooks, subprocesses, startup registration or extra dependencies.
The server owns opening its window and graceful shutdown; this module never
terminates a process. Only counts are shown, never task text or credentials.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import unicodedata


def _count(value):
    return max(0, min(value, 9999)) if isinstance(value, int) and not isinstance(value, bool) else 0


def summary(running=0, waiting=0):
    running, waiting = _count(running), _count(waiting)
    if not running and not waiting:
        return '준비됨 · 백그라운드 실행 중'
    return f'진행 중 {running}건 · 승인·질문 대기 {waiting}건'


def _notification_text(value, units):
    if not isinstance(value, str):
        return ''
    value = ''.join(' ' if unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} else char for char in value)
    # Windows arrays count UTF-16 code units, not Python Unicode characters.
    return value.encode('utf-16-le')[:units * 2].decode('utf-16-le', errors='ignore').strip()


class WorkspaceTray:
    """One tray per instance_key, using a dedicated native message-loop thread.

    on_open/on_exit run outside that loop. on_exit must ask the server to close
    gracefully; returning False or raising leaves the icon present. The owner
    calls stop() only once the server has actually finished. start() reports
    whether the icon was added, while available reflects later shell recovery.
    """
    def __init__(self, instance_key, on_open, on_exit, icon_path=None, *, native_factory=None):
        self._key = hashlib.sha256(str(instance_key).encode('utf-8')).hexdigest()[:32]
        self._callbacks = {'open': on_open, 'exit': on_exit}
        self.icon_path = Path(icon_path) if icon_path is not None else Path(__file__).parent / 'web/app-icon.ico'
        self._factory = native_factory or _WindowsTray
        self._supported = os.name == 'nt' or native_factory is not None
        self._lock = threading.RLock()
        self._ready, self._stopping = threading.Event(), threading.Event()
        self._thread = self._backend = None
        self._available = False
        self._error = ''
        self._counts = (0, 0)
        self._pending = set()
        self._notification = None

    @property
    def available(self):
        with self._lock:
            return self._available and not self._stopping.is_set()

    def status(self):
        with self._lock:
            return {'supported': self._supported, 'available': self.available,
                    'running': bool(self._thread and self._thread.is_alive()),
                    'error': self._error}

    def start(self):
        with self._lock:
            if not self._supported:
                self._error = 'unsupported'
                return False
            if self._thread and self._thread.is_alive():
                return self.available
            self._ready.clear()
            self._stopping.clear()
            self._error = ''
            self._thread = threading.Thread(target=self._run, name='WorkspaceTray', daemon=True)
            self._thread.start()
        self._ready.wait(3)
        return self.available

    def _run(self):
        try:
            backend = self._factory(self)
            with self._lock:
                self._backend = backend
            backend.run()
        except Exception:
            with self._lock:
                self._error = 'unavailable'
        finally:
            with self._lock:
                self._available = False
                self._backend = None
                self._notification = None
            self._ready.set()

    def _mark_ready(self, available, error=''):
        with self._lock:
            self._available = bool(available)
            self._error = error
        self._ready.set()

    def update(self, *, running=0, waiting=0):
        counts = (_count(running), _count(waiting))
        with self._lock:
            if counts == self._counts:
                return
            self._counts = counts
            backend = self._backend
        if backend is not None:
            backend.post_update()

    def notify(self, *, title, message, on_click=None):
        """Request one native banner; False means keep it in the app inbox.

        Windows balloon callbacks carry no notification ID. Never overwrite an
        active balloon's target with a newer task, or queue stale banners in
        the Shell. Other events remain accessible in the caller's durable inbox.
        """
        title, message = _notification_text(title, 63), _notification_text(message, 255)
        if not title or not message or (on_click is not None and not callable(on_click)):
            return False
        with self._lock:
            if (not self.available or self._backend is None or self._notification is not None
                    or 'exit' in self._pending):
                return False
            value = {'title': title, 'message': message, 'on_click': on_click,
                     'sent': False, 'shown': False}
            self._notification = value
            backend = self._backend
        try:
            posted = backend.post_notification() is True
        except Exception:
            posted = False
        if not posted:
            self._discard_notification(value)
        return posted

    def _discard_notification(self, expected=None):
        with self._lock:
            if expected is None or self._notification is expected:
                self._notification = None

    def _notification_event(self, event):
        with self._lock:
            value = self._notification
            if value is None or not value['sent']:
                return
            if event == 0x402:  # NIN_BALLOONSHOW
                value['shown'] = True
                return
            if event not in (0x403, 0x404, 0x405):
                return
            self._notification = None
            callback = value['on_click'] if event == 0x405 and value['shown'] else None
            if self._stopping.is_set() or 'exit' in self._pending or callback is None:
                return
        def invoke():
            try:
                callback()
            except Exception:
                # The server may already be closing or the task may be gone.
                pass
        threading.Thread(target=invoke, name='WorkspaceTray-notification', daemon=True).start()

    def _text(self):
        with self._lock:
            if 'exit' in self._pending:
                return '업무를 정리하고 종료하는 중'
            if self._error == 'exit_failed':
                return '종료하지 못했어요 · 앱을 열어 확인해 주세요'
            return summary(*self._counts)

    def _dispatch(self, action):
        with self._lock:
            if action not in self._callbacks or action in self._pending or self._stopping.is_set():
                return
            if 'exit' in self._pending:
                return
            self._pending.add(action)
        def invoke():
            failed = False
            try:
                failed = self._callbacks[action]() is False
            except Exception:
                failed = True
            finally:
                with self._lock:
                    self._pending.discard(action)
                    if failed:
                        self._error = action + '_failed'
                    elif self._error == action + '_failed':
                        self._error = ''
                    backend = self._backend
                if backend is not None:
                    backend.post_update()
        threading.Thread(target=invoke, name='WorkspaceTray-' + action, daemon=True).start()

    def stop(self):
        with self._lock:
            self._stopping.set()
            self._notification = None
            backend, thread = self._backend, self._thread
        if backend is not None:
            backend.request_stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)


@contextmanager
def _tray_dpi_context(user):
    # The hidden owner must be created in this context too: Windows restores
    # a window's creation-time DPI context whenever it calls its window proc.
    change = user.SetThreadDpiAwarenessContext
    change.argtypes, change.restype = [ctypes.c_void_p], ctypes.c_void_p
    previous = change(-4) or change(-3)  # per-monitor v2, then Windows 10 1607
    if not previous:
        raise OSError('Tray DPI context unavailable.')
    try:
        yield
    finally:
        change(previous)  # thread only; never change the process or PC settings


class _WindowsTray:
    CALLBACK = 0x8001
    UPDATE = 0x8002
    NOTIFICATION = 0x8003
    TIMER = 1
    ICON_ID = 1
    OPEN, EXIT = 100, 101

    def __init__(self, owner):
        from ctypes import wintypes as wt
        self.owner, self.wt = owner, wt
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.hwnd = self.icon = self.mutex = None
        self._owns_icon = self._added = self._version4 = self._registered = False
        self.class_name = 'CompanyWorkspace.Tray.' + owner._key + '.' + str(os.getpid())
        self.WndProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wt.UINT), ('procedure', self.WndProc),
                        ('classExtra', ctypes.c_int), ('windowExtra', ctypes.c_int),
                        ('instance', wt.HINSTANCE), ('icon', wt.HICON), ('cursor', wt.HANDLE),
                        ('background', wt.HBRUSH), ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]

        class Guid(ctypes.Structure):
            _fields_ = [('data1', wt.DWORD), ('data2', wt.WORD), ('data3', wt.WORD),
                        ('data4', wt.BYTE * 8)]

        class NotifyIcon(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('window', wt.HWND), ('id', wt.UINT),
                        ('flags', wt.UINT), ('callback', wt.UINT), ('icon', wt.HICON),
                        ('tip', wt.WCHAR * 128), ('state', wt.DWORD), ('stateMask', wt.DWORD),
                        ('info', wt.WCHAR * 256), ('version', wt.UINT), ('title', wt.WCHAR * 64),
                        ('infoFlags', wt.DWORD), ('guid', Guid), ('balloonIcon', wt.HICON)]

        class IconIdentifier(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('window', wt.HWND), ('id', wt.UINT), ('guid', Guid)]

        self.WindowClass, self.NotifyIcon, self.IconIdentifier = WindowClass, NotifyIcon, IconIdentifier
        def signature(library, name, arguments, result):
            fn = getattr(library, name)
            fn.argtypes, fn.restype = arguments, result
        ptr = ctypes.POINTER
        signature(self.kernel, 'GetModuleHandleW', [wt.LPCWSTR], wt.HMODULE)
        signature(self.kernel, 'CreateMutexW', [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR], wt.HANDLE)
        signature(self.kernel, 'CloseHandle', [wt.HANDLE], wt.BOOL)
        signature(self.user, 'RegisterClassW', [ptr(WindowClass)], wt.ATOM)
        signature(self.user, 'UnregisterClassW', [wt.LPCWSTR, wt.HINSTANCE], wt.BOOL)
        signature(self.user, 'CreateWindowExW', [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                  wt.HINSTANCE, ctypes.c_void_p], wt.HWND)
        signature(self.user, 'DefWindowProcW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t)
        signature(self.user, 'DestroyWindow', [wt.HWND], wt.BOOL)
        signature(self.user, 'RegisterWindowMessageW', [wt.LPCWSTR], wt.UINT)
        signature(self.user, 'PostMessageW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL)
        signature(self.user, 'GetMessageW', [ptr(wt.MSG), wt.HWND, wt.UINT, wt.UINT], wt.BOOL)
        signature(self.user, 'TranslateMessage', [ptr(wt.MSG)], wt.BOOL)
        signature(self.user, 'DispatchMessageW', [ptr(wt.MSG)], ctypes.c_ssize_t)
        signature(self.user, 'PostQuitMessage', [ctypes.c_int], None)
        signature(self.user, 'SetTimer', [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p], ctypes.c_size_t)
        signature(self.user, 'KillTimer', [wt.HWND, ctypes.c_size_t], wt.BOOL)
        signature(self.user, 'GetSystemMetrics', [ctypes.c_int], ctypes.c_int)
        signature(self.user, 'LoadImageW', [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT], wt.HANDLE)
        signature(self.user, 'LoadIconW', [wt.HINSTANCE, ctypes.c_void_p], wt.HICON)
        signature(self.user, 'DestroyIcon', [wt.HICON], wt.BOOL)
        signature(self.user, 'CreatePopupMenu', [], wt.HMENU)
        signature(self.user, 'AppendMenuW', [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR], wt.BOOL)
        signature(self.user, 'DestroyMenu', [wt.HMENU], wt.BOOL)
        signature(self.user, 'EndMenu', [], wt.BOOL)
        signature(self.user, 'SetForegroundWindow', [wt.HWND], wt.BOOL)
        signature(self.user, 'GetCursorPos', [ptr(wt.POINT)], wt.BOOL)
        signature(self.user, 'TrackPopupMenu', [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                  ctypes.c_int, wt.HWND, ctypes.c_void_p], wt.UINT)
        signature(self.shell, 'Shell_NotifyIconW', [wt.DWORD, ptr(NotifyIcon)], wt.BOOL)
        signature(self.shell, 'Shell_NotifyIconGetRect', [ptr(IconIdentifier), ptr(wt.RECT)], ctypes.c_long)
        self.instance = self.kernel.GetModuleHandleW(None)
        self.taskbar_created = self.user.RegisterWindowMessageW('TaskbarCreated')
        if not self.instance or not self.taskbar_created:
            raise OSError('Tray initialization failed.')
        self._procedure_ref = self.WndProc(self._window_proc)

    def _data(self):
        data = self.NotifyIcon()
        data.size, data.window, data.id = ctypes.sizeof(data), self.hwnd, self.ICON_ID
        data.flags = 1 | 2 | 4 | 0x80  # MESSAGE | ICON | TIP | SHOWTIP with version 4
        data.callback, data.icon = self.CALLBACK, self.icon
        data.tip = ('Company Workspace\n' + self.owner._text())[:127]
        return data

    def _notify(self, action):
        data = self._data()
        if action == 4:
            data.version = 4
        return bool(self.shell.Shell_NotifyIconW(action, ctypes.byref(data)))

    def _add(self):
        if self.owner._stopping.is_set():
            return
        self._added = self._notify(0)
        if self._added:
            self._version4 = self._notify(4)
        self.owner._mark_ready(self._added, '' if self._added else 'shell_unavailable')

    def run(self):
        with _tray_dpi_context(self.user):
            self._run()

    def _run(self):
        try:
            ctypes.set_last_error(0)
            self.mutex = self.kernel.CreateMutexW(None, False, 'Local\\CompanyWorkspace.Tray.' + self.owner._key)
            if not self.mutex:
                raise OSError('Tray ownership unavailable.')
            if ctypes.get_last_error() == 183:
                self.owner._mark_ready(False, 'already_running')
                return
            self.icon = self.user.LoadImageW(None, str(self.owner.icon_path), 1,
                        self.user.GetSystemMetrics(49), self.user.GetSystemMetrics(50), 0x10)
            self._owns_icon = bool(self.icon)
            if not self.icon:
                self.icon = self.user.LoadIconW(None, ctypes.c_void_p(32512))
            if not self.icon:
                raise OSError('Tray icon unavailable.')
            value = self.WindowClass()
            value.procedure, value.instance, value.name = self._procedure_ref, self.instance, self.class_name
            if not self.user.RegisterClassW(ctypes.byref(value)):
                raise OSError('Tray window class unavailable.')
            self._registered = True
            # Hidden top-level window receives TaskbarCreated broadcasts.
            # A message-only HWND would silently miss Explorer restarts.
            self.hwnd = self.user.CreateWindowExW(0, self.class_name, 'Company Workspace Tray',
                                                 0, 0, 0, 0, 0, None, None, self.instance, None)
            if not self.hwnd:
                raise OSError('Tray window unavailable.')
            if self.owner._stopping.is_set():
                return
            self._add()
            self.user.SetTimer(self.hwnd, self.TIMER, 5000, None)
            message = self.wt.MSG()
            while not self.owner._stopping.is_set():
                result = self.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                self.user.TranslateMessage(ctypes.byref(message))
                self.user.DispatchMessageW(ctypes.byref(message))
        finally:
            if self.hwnd:
                self.user.KillTimer(self.hwnd, self.TIMER)
                if self._added:
                    self._notify(2)
                    self._added = False
                self.user.DestroyWindow(self.hwnd)
                self.hwnd = None
            if self._registered:
                self.user.UnregisterClassW(self.class_name, self.instance)
            if self._owns_icon and self.icon:
                self.user.DestroyIcon(self.icon)
            if self.mutex:
                self.kernel.CloseHandle(self.mutex)
                self.mutex = None

    def post_update(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, self.UPDATE, 0, 0)

    def post_notification(self):
        return bool(self.hwnd and self.user.PostMessageW(self.hwnd, self.NOTIFICATION, 0, 0))

    def _show_notification(self):
        with self.owner._lock:
            value = self.owner._notification
            if value is None or value['sent']:
                return
            if not self._added or self.owner._stopping.is_set():
                self.owner._discard_notification(value)
                return
            value['sent'] = True
            data = self._data()
            data.flags = 0x10 | 0x40  # NIF_INFO | NIF_REALTIME: no delayed stale banner
            data.title, data.info = value['title'], value['message']
            data.infoFlags = 1 | 0x80  # NIIF_INFO | NIIF_RESPECT_QUIET_TIME
        if not self.shell.Shell_NotifyIconW(1, ctypes.byref(data)):
            self.owner._discard_notification(value)

    def request_stop(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, 0x10, 0, 0)  # wakes GetMessage on owner thread

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == self.taskbar_created:
                self.owner._discard_notification()
                self._added = False
                self._add()
                return 0
            if message == self.UPDATE:
                if self._added and not self._notify(1):
                    self._added = False
                    self.owner._mark_ready(False, 'shell_unavailable')
                return 0
            if message == self.NOTIFICATION:
                self._show_notification()
                return 0
            if message == 0x113 and wparam == self.TIMER:
                if not self._added:
                    self._add()
                return 0
            if message == self.CALLBACK:
                event = lparam & 0xFFFF if self._version4 else lparam
                if event in (0x402, 0x403, 0x404, 0x405):
                    self.owner._notification_event(event)
                elif event in (0x400, 0x401) or (not self._version4 and event == 0x202):
                    self.owner._dispatch('open')
                elif event == 0x7B or (not self._version4 and event == 0x205):
                    self._menu()
                return 0
            if message == 0x10:
                self.owner._stopping.set()
                self.user.EndMenu()  # release this thread's own open context menu
                return 0
            if message == 2:
                self.user.PostQuitMessage(0)
                return 0
        except Exception:
            # Never leak callback exceptions/paths into ctypes' stderr printer.
            return 0
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def _menu_anchor(self):
        # Ask the shell for our icon's current location (including overflow).
        # WM_CONTEXTMENU's version-4 wParam is not a guaranteed mouse anchor;
        # using it also mixes Explorer's coordinates with a DPI-unaware owner.
        identity = self.IconIdentifier()
        identity.size, identity.window, identity.id = ctypes.sizeof(identity), self.hwnd, self.ICON_ID
        rect = self.wt.RECT()
        if (self.shell.Shell_NotifyIconGetRect(ctypes.byref(identity), ctypes.byref(rect)) == 0
                and rect.right > rect.left and rect.bottom > rect.top):
            return self.wt.POINT(rect.left, rect.bottom)
        point = self.wt.POINT()
        if self.user.GetCursorPos(ctypes.byref(point)):
            return point
        return None

    def _menu(self):
        point = self._menu_anchor()
        if point is None:
            return
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user.AppendMenuW(menu, 0, self.OPEN, '앱 열기')
            self.user.AppendMenuW(menu, 1, 0, self.owner._text())
            self.user.AppendMenuW(menu, 0x800, 0, None)
            with self.owner._lock:
                closing = 'exit' in self.owner._pending
            self.user.AppendMenuW(menu, 1 if closing else 0, self.EXIT, '완전 종료')
            self.user.SetForegroundWindow(self.hwnd)
            command = self.user.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
            self._notify(3)
            if command == self.OPEN:
                self.owner._dispatch('open')
            elif command == self.EXIT:
                self.owner._dispatch('exit')
        finally:
            self.user.DestroyMenu(menu)
