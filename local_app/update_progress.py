"""A short-lived native update card owned by the detached upgrade coordinator.

No browser, child process, filesystem polling or third-party GUI runtime is
needed. Win32 controls expose real text to accessibility tools and fonts are
rendered at the monitor's DPI, rather than scaling a screenshot of the card.
"""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import threading


PHASES = {
    'preparing': ('업데이트를 적용하고 있어요', '작성 중인 내용을 보관하고 있어요.'),
    'restarting': ('앱을 다시 시작하고 있어요', '잠시 후 새 버전이 열립니다.'),
    'opening': ('새 화면을 준비하고 있어요', '준비가 끝나면 이 안내는 닫힙니다.'),
}
BRAND = 'AX Workspace'
LABEL = '업데이트'
CARD_WIDTH, CARD_HEIGHT = 392, 172
TEXT_LAYOUT = ((66, 21, 302, 20), (66, 42, 302, 17),
               (24, 77, 344, 28), (24, 111, 344, 21))
FONT_SPECS = ((12, 600), (11, 400), (17, 600), (12, 400))
NATIVE_FONT_FACES = {
    400: ('AXWorkspaceUpdate-Regular.ttf', 'AX Workspace Update'),
    600: ('AXWorkspaceUpdate-SemiBold.ttf', 'AX Workspace Update SemiBold'),
}


def _scaled(value, dpi):
    return round(value * dpi / 96)


def _color(value):
    """Convert a CSS-style RGB hex color into a Win32 COLORREF."""
    value = value.lstrip('#')
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return red | green << 8 | blue << 16


class UpdateProgress:
    def __init__(self, *, enabled=True, theme='light', factory=None):
        self.enabled = enabled and os.name == 'nt'
        self.theme = 'dark' if theme == 'dark' else 'light'
        self._factory = factory or _NativeCard
        self._thread = self._native = self._error = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._phase = 'preparing'

    def phase(self, phase):
        if phase == 'waiting':
            self.hide()
            return
        if phase not in PHASES:
            raise ValueError('Unknown update phase')
        if not self.enabled or self._closed.is_set():
            return
        self._phase = phase
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='workspace-update-progress', daemon=True)
            self._thread.start()
        # Before commit, confirm the card has actually been painted. Failure
        # leaves the old app running and follows its existing error notice.
        if not self._ready.wait(3) or self._error or not self._native:
            raise OSError('업데이트 진행 안내를 준비하지 못했습니다.')
        self._native.update(phase)

    def _run(self):
        try:
            self._native = self._factory(self)
            self._native.run()
        except Exception as error:
            self._error = error
        finally:
            self._ready.set()

    def hide(self):
        if self._native:
            self._native.hide()

    def close(self):
        self._closed.set()
        if self._native:
            self._native.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(3)


class _NativeCard:
    UPDATE, HIDE, CLOSE = 0x8001, 0x8002, 0x8003

    def __init__(self, owner):
        from ctypes import wintypes as wt
        self.owner, self.wt = owner, wt
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.hwnd = None
        self.children, self.fonts, self.brushes = [], [], []
        self.font_resources, self.font_buffers, self.font_faces = [], [], {}
        self.dpi = 96
        self._shown, self._frame, self._registered = False, 0, False
        self.class_name = 'AXWorkspace.Update.' + str(os.getpid())
        self.WndProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wt.UINT), ('proc', self.WndProc), ('classExtra', ctypes.c_int),
                        ('windowExtra', ctypes.c_int), ('instance', wt.HINSTANCE), ('icon', wt.HICON),
                        ('cursor', wt.HANDLE), ('background', wt.HBRUSH), ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]

        class Paint(ctypes.Structure):
            _fields_ = [('dc', wt.HDC), ('erase', wt.BOOL), ('rect', wt.RECT), ('restore', wt.BOOL),
                        ('incUpdate', wt.BOOL), ('reserved', wt.BYTE * 32)]

        self.WindowClass, self.Paint = WindowClass, Paint
        ptr = ctypes.POINTER
        def sig(library, name, arguments, result):
            fn = getattr(library, name)
            fn.argtypes, fn.restype = arguments, result
        sig(self.kernel, 'GetModuleHandleW', [wt.LPCWSTR], wt.HMODULE)
        sig(self.user, 'RegisterClassW', [ptr(WindowClass)], wt.ATOM)
        sig(self.user, 'UnregisterClassW', [wt.LPCWSTR, wt.HINSTANCE], wt.BOOL)
        sig(self.user, 'CreateWindowExW', [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p], wt.HWND)
        sig(self.user, 'DefWindowProcW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t)
        sig(self.user, 'DestroyWindow', [wt.HWND], wt.BOOL)
        sig(self.user, 'ShowWindow', [wt.HWND, ctypes.c_int], wt.BOOL)
        sig(self.user, 'UpdateWindow', [wt.HWND], wt.BOOL)
        sig(self.user, 'PostMessageW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL)
        sig(self.user, 'SendMessageW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], ctypes.c_ssize_t)
        sig(self.user, 'GetMessageW', [ptr(wt.MSG), wt.HWND, wt.UINT, wt.UINT], wt.BOOL)
        sig(self.user, 'TranslateMessage', [ptr(wt.MSG)], wt.BOOL)
        sig(self.user, 'DispatchMessageW', [ptr(wt.MSG)], ctypes.c_ssize_t)
        sig(self.user, 'PostQuitMessage', [ctypes.c_int], None)
        sig(self.user, 'SetTimer', [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p], ctypes.c_size_t)
        sig(self.user, 'KillTimer', [wt.HWND, ctypes.c_size_t], wt.BOOL)
        sig(self.user, 'GetSystemMetrics', [ctypes.c_int], ctypes.c_int)
        sig(self.user, 'GetCursorPos', [ptr(wt.POINT)], wt.BOOL)
        sig(self.user, 'MonitorFromPoint', [wt.POINT, wt.DWORD], wt.HANDLE)
        sig(self.user, 'GetMonitorInfoW', [wt.HANDLE, ctypes.c_void_p], wt.BOOL)
        sig(self.user, 'SetWindowPos', [wt.HWND, wt.HWND] + [ctypes.c_int] * 4 + [wt.UINT], wt.BOOL)
        sig(self.user, 'SetWindowRgn', [wt.HWND, wt.HANDLE, wt.BOOL], ctypes.c_int)
        sig(self.user, 'SetWindowTextW', [wt.HWND, wt.LPCWSTR], wt.BOOL)
        sig(self.user, 'InvalidateRect', [wt.HWND, ptr(wt.RECT), wt.BOOL], wt.BOOL)
        sig(self.user, 'BeginPaint', [wt.HWND, ptr(Paint)], wt.HDC)
        sig(self.user, 'EndPaint', [wt.HWND, ptr(Paint)], wt.BOOL)
        sig(self.user, 'FillRect', [wt.HDC, ptr(wt.RECT), wt.HBRUSH], ctypes.c_int)
        sig(self.gdi, 'CreateSolidBrush', [wt.DWORD], wt.HBRUSH)
        sig(self.gdi, 'CreatePen', [ctypes.c_int, ctypes.c_int, wt.DWORD], wt.HANDLE)
        sig(self.gdi, 'GetStockObject', [ctypes.c_int], wt.HANDLE)
        sig(self.gdi, 'SelectObject', [wt.HDC, wt.HANDLE], wt.HANDLE)
        sig(self.gdi, 'RoundRect', [wt.HDC] + [ctypes.c_int] * 6, wt.BOOL)
        sig(self.gdi, 'CreateRoundRectRgn', [ctypes.c_int] * 6, wt.HANDLE)
        sig(self.gdi, 'Polyline', [wt.HDC, ptr(wt.POINT), ctypes.c_int], wt.BOOL)
        sig(self.gdi, 'AddFontMemResourceEx', [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ptr(wt.DWORD)], wt.HANDLE)
        sig(self.gdi, 'RemoveFontMemResourceEx', [wt.HANDLE], wt.BOOL)
        sig(self.gdi, 'SetBkMode', [wt.HDC, ctypes.c_int], ctypes.c_int)
        sig(self.gdi, 'SetTextColor', [wt.HDC, wt.DWORD], wt.DWORD)
        sig(self.gdi, 'DeleteObject', [wt.HANDLE], wt.BOOL)
        sig(self.gdi, 'CreateFontW', [ctypes.c_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR], wt.HANDLE)
        self.instance = self.kernel.GetModuleHandleW(None)
        self.procedure = self.WndProc(self._procedure)

    def update(self, phase):
        self.owner._phase = phase
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, self.UPDATE, 0, 0)

    def hide(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, self.HIDE, 0, 0)

    def close(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, self.CLOSE, 0, 0)

    def run(self):
        # Thread-local DPI context does not alter the server or other apps.
        from .tray import _tray_dpi_context
        with _tray_dpi_context(self.user):
            try:
                self._create()
                if self.owner._closed.is_set():
                    return
                self._display()
                self.owner._ready.set()
                message = self.wt.MSG()
                while not self.owner._closed.is_set():
                    result = self.user.GetMessageW(ctypes.byref(message), None, 0, 0)
                    if result <= 0:
                        break
                    self.user.TranslateMessage(ctypes.byref(message))
                    self.user.DispatchMessageW(ctypes.byref(message))
            finally:
                if self.hwnd:
                    self.user.KillTimer(self.hwnd, 1)
                    self.user.DestroyWindow(self.hwnd)
                    self.hwnd = None
                for handle in self.fonts + self.brushes:
                    self.gdi.DeleteObject(handle)
                for resource in self.font_resources:
                    self.gdi.RemoveFontMemResourceEx(resource)
                self.font_resources.clear()
                self.font_buffers.clear()
                if self._registered:
                    self.user.UnregisterClassW(self.class_name, self.instance)

    def _create(self):
        wt = self.wt
        # Position on the user's current monitor; create an initial hidden
        # window there so GetDpiForWindow resolves the correct monitor DPI.
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('monitor', wt.RECT), ('work', wt.RECT), ('flags', wt.DWORD)]
        point, info = wt.POINT(), MonitorInfo()
        self.user.GetCursorPos(ctypes.byref(point))
        info.size = ctypes.sizeof(info)
        monitor = self.user.MonitorFromPoint(point, 2)
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError('Monitor work area unavailable')
        dark = self.owner.theme == 'dark'
        palette = ('#252630', '#3c3e4d', '#a499eb', '#30303e', '#454757') if dark else (
                   '#fbfbfd', '#e9e9f1', '#7068cc', '#eeedf9', '#dedfe8')
        self.background = _color(palette[0])
        self.ink = _color('#f0eff6' if dark else '#292c37')
        self.muted = _color('#a8aabb' if dark else '#747b8e')
        for color in map(_color, palette):
            brush = self.gdi.CreateSolidBrush(color)
            if not brush:
                raise OSError('Progress brush unavailable')
            self.brushes.append(brush)
        value = self.WindowClass()
        value.proc, value.instance, value.name = self.procedure, self.instance, self.class_name
        value.background = self.brushes[0]
        if not self.user.RegisterClassW(ctypes.byref(value)):
            raise OSError('Progress window class unavailable')
        self._registered = True
        # A temporary tool window: no extra pinned/taskbar app entry, no close
        # affordance that could imply the already-committed update is cancelled.
        self.hwnd = self.user.CreateWindowExW(0x00000080 | 0x00000008, self.class_name,
            'AX Workspace 업데이트', 0x80000000 | 0x02000000,
            info.work.left + 20, info.work.top + 20, CARD_WIDTH, CARD_HEIGHT, None, None, self.instance, None)
        if not self.hwnd:
            raise OSError('Progress window unavailable')
        dpi = 96
        if hasattr(self.user, 'GetDpiForWindow'):
            self.user.GetDpiForWindow.argtypes, self.user.GetDpiForWindow.restype = [wt.HWND], wt.UINT
            dpi = self.user.GetDpiForWindow(self.hwnd) or 96
        self._load_private_fonts()
        for text in (BRAND, LABEL, '', ''):
            child = self.user.CreateWindowExW(0, 'STATIC', text, 0x40000000 | 0x10000000 | 0x0080,
                0, 0, 1, 1, self.hwnd, None, self.instance, None)
            if not child:
                raise OSError('Progress text unavailable')
            self.children.append(child)
        width, height = _scaled(CARD_WIDTH, dpi), _scaled(CARD_HEIGHT, dpi)
        self._layout(dpi, info.work.left + (info.work.right-info.work.left-width)//2,
                     info.work.top + (info.work.bottom-info.work.top-height)//2)
        try:
            dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
            dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
            rounded = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(rounded), 4)
            border = wt.DWORD(_color(palette[4]))
            dwm.DwmSetWindowAttribute(self.hwnd, 34, ctypes.byref(border), 4)
        except (OSError, AttributeError):
            pass  # Windows 10 still provides a standard clean rectangular card.

    def _load_private_fonts(self):
        # Tiny subsets of the same Noto Sans KR used by the app. Process-private
        # GDI resources never install a font or change another application's UI.
        # The buffers stay alive until all HFONTs/windows have been destroyed.
        for weight, (filename, family) in NATIVE_FONT_FACES.items():
            try:
                data = (Path(__file__).with_name('native_fonts') / filename).read_bytes()
                if not 0 < len(data) <= 512 * 1024:
                    continue
                buffer = ctypes.create_string_buffer(data)
                count = self.wt.DWORD()
                resource = self.gdi.AddFontMemResourceEx(buffer, len(data), None, ctypes.byref(count))
                if not resource:
                    continue
                self.font_resources.append(resource)
                self.font_buffers.append(buffer)
                if count.value:
                    self.font_faces[weight] = family
            except OSError:
                pass  # A missing optional font must not block a safe update.

    def scale(self, value):
        return _scaled(value, self.dpi)

    def _layout(self, dpi, left, top):
        # Recreate real fonts at the new monitor's DPI, not a bitmap resize.
        # Child controls switch to the new handles before old handles are freed.
        fonts = []
        try:
            for size, weight in FONT_SPECS:
                family = self.font_faces.get(weight, 'Malgun Gothic')
                font = self.gdi.CreateFontW(-_scaled(size, dpi), 0, 0, 0, weight,
                    0, 0, 0, 1, 0, 0, 5, 0, family)
                if not font:
                    raise OSError('Progress font unavailable')
                fonts.append(font)
        except Exception:
            for font in fonts:
                self.gdi.DeleteObject(font)
            raise
        previous, self.fonts = self.fonts, fonts
        self.dpi = dpi
        width, height = self.scale(CARD_WIDTH), self.scale(CARD_HEIGHT)
        self.user.SetWindowPos(self.hwnd, None, left, top, width, height, 0x0014)
        for child, font, bounds in zip(self.children, self.fonts, TEXT_LAYOUT):
            self.user.SendMessageW(child, 0x0030, font, 0)
            self.user.SetWindowPos(child, None, *(self.scale(value) for value in bounds), 0x0014)
        for font in previous:
            self.gdi.DeleteObject(font)
        # A native region also keeps Windows 10 corners clean. Ownership passes
        # to Windows only when SetWindowRgn succeeds; no region leaks on DPI moves.
        region = self.gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                            self.scale(26), self.scale(26))
        if region and not self.user.SetWindowRgn(self.hwnd, region, True):
            self.gdi.DeleteObject(region)
        self.user.InvalidateRect(self.hwnd, None, True)

    def _display(self):
        title, detail = PHASES[self.owner._phase]
        for child, text in zip(self.children[2:], (title, detail)):
            self.user.SetWindowTextW(child, text)
        self.user.SetWindowTextW(self.hwnd, title + ' — AX Workspace')
        if not self._shown:
            self.user.ShowWindow(self.hwnd, 5)
            self.user.SetTimer(self.hwnd, 1, 80, None)
            self._shown = True
        self.user.UpdateWindow(self.hwnd)

    def _procedure(self, hwnd, message, wparam, lparam):
        if message == self.UPDATE:
            self._display()
            return 0
        if message == self.HIDE:
            self.user.KillTimer(hwnd, 1)
            self.user.ShowWindow(hwnd, 0)
            self._shown = False
            return 0
        if message == self.CLOSE:
            self.user.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:  # WM_DESTROY
            self.hwnd = None
            self.user.PostQuitMessage(0)
            return 0
        if message == 0x0010:  # Alt+F4 is not an implicit update cancellation.
            return 0
        if message == 0x0084:  # Draggable empty background.
            return 2
        if message == 0x02E0 and self.children:  # WM_DPICHANGED
            bounds = ctypes.cast(lparam, ctypes.POINTER(self.wt.RECT)).contents
            dpi = int(wparam) & 0xFFFF
            if dpi:
                try:
                    self._layout(dpi, bounds.left, bounds.top)
                except OSError:
                    # Preserve the previous readable card if new font handles
                    # cannot be allocated; never raise through a Win32 callback.
                    pass
            return 0
        if message == 0x0138:  # WM_CTLCOLORSTATIC
            self.gdi.SetBkMode(wparam, 1)
            primary = len(self.children) > 2 and lparam in (self.children[0], self.children[2])
            self.gdi.SetTextColor(wparam, self.ink if primary else self.muted)
            return self.brushes[0]
        if message == 0x0113:  # Only the tiny progress strip repaints.
            if not self._shown:  # KillTimer may leave an already queued tick.
                return 0
            self._frame = (self._frame + 1) % 64
            rect = self.wt.RECT(self.scale(24), self.scale(147), self.scale(368), self.scale(152))
            self.user.InvalidateRect(hwnd, ctypes.byref(rect), False)
            return 0
        if message == 0x000F and self.brushes:
            paint = self.Paint()
            dc = self.user.BeginPaint(hwnd, ctypes.byref(paint))
            try:
                self._rounded(dc, (0, 0, CARD_WIDTH, CARD_HEIGHT), 13, self.brushes[4])
                self._rounded(dc, (1, 1, CARD_WIDTH - 1, CARD_HEIGHT - 1), 12, self.brushes[0])
                if paint.rect.top < self.scale(64):
                    self._paint_mark(dc)
                self._rounded(dc, (24, 147, 368, 151), 2, self.brushes[1])
                # Indeterminate motion, never an invented percentage.
                offset = (1 - math.cos(self._frame * math.tau / 64)) * 132
                self._rounded(dc, (24 + offset, 147, 104 + offset, 151), 2, self.brushes[2])
            finally:
                self.user.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        return self.user.DefWindowProcW(hwnd, message, wparam, lparam)

    def _rounded(self, dc, bounds, radius, brush):
        old_brush = self.gdi.SelectObject(dc, brush)
        old_pen = self.gdi.SelectObject(dc, self.gdi.GetStockObject(8))  # NULL_PEN
        try:
            self.gdi.RoundRect(dc, *(self.scale(value) for value in bounds),
                               self.scale(radius * 2), self.scale(radius * 2))
        finally:
            self.gdi.SelectObject(dc, old_pen)
            self.gdi.SelectObject(dc, old_brush)

    def _paint_mark(self, dc):
        self._rounded(dc, (24, 24, 54, 54), 9, self.brushes[3])
        pen = self.gdi.CreatePen(0, max(1, self.scale(1.5)),
                               _color('#a499eb' if self.owner.theme == 'dark' else '#7068cc'))
        if not pen:
            return
        old_pen = self.gdi.SelectObject(dc, pen)
        try:
            # The app's four-point spark, with a single clean outline. It does
            # not animate or allocate resources on progress-strip ticks.
            path = ((39, 30), (41.5, 36.5), (48, 39), (41.5, 41.5),
                    (39, 48), (36.5, 41.5), (30, 39), (36.5, 36.5), (39, 30))
            points = (self.wt.POINT * len(path))(
                *(self.wt.POINT(self.scale(x), self.scale(y)) for x, y in path))
            self.gdi.Polyline(dc, points, len(path))
        finally:
            self.gdi.SelectObject(dc, old_pen)
            self.gdi.DeleteObject(pen)
