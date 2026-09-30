"""Set-only color contracts; native probe owns and destroys its hidden HWND."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local_app import window_theme
from local_app.attention import AttentionNotifier
from tests.test_workspace_window_lifecycle import native_window


class ColorApi:
    def __init__(self):
        self.colors = {35: 0x123456, 36: 0xABCDEF, 34: 0xFFFFFFFF}
        self.original = dict(self.colors)
        self.calls = []
        self.contrast = False
        self.spi_ok = True
        self.write_failure = self.write_exception = None
        self.reset_failure = False
        self.user = SimpleNamespace(SystemParametersInfoW=Mock(side_effect=self.spi))
        self.dwm = SimpleNamespace(DwmGetWindowAttribute=Mock(side_effect=self.get),
                                   DwmSetWindowAttribute=Mock(side_effect=self.set))

    def library(self, name, **kwargs):
        if name == 'user32':
            return self.user
        if name == 'dwmapi':
            return self.dwm
        raise AssertionError('Only user32/dwmapi are used')

    def spi(self, action, size, value, flags):
        if action != 0x42 or flags != 0 or size != value._obj.cbSize:
            raise AssertionError('Only SPI_GETHIGHCONTRAST is allowed')
        value._obj.dwFlags = int(self.contrast)
        return self.spi_ok

    def get(self, hwnd, attr, value, size):
        self.calls.append(('get', hwnd, attr))
        # Windows 11 build 26200 returns E_INVALIDARG for these Set-only attrs,
        # even after a successful Set. Never gate application on this getter.
        return -2147024809

    def set(self, hwnd, attr, value, size):
        self.calls.append(('set', hwnd, attr, value._obj.value))
        if value._obj.value == window_theme.DEFAULT_COLOR and self.reset_failure:
            return -1
        if attr == self.write_failure:
            return -1
        if attr == self.write_exception:
            raise OSError('Private failure detail')
        self.colors[attr] = value._obj.value
        return 0


class WindowThemeTests(unittest.TestCase):
    def setUp(self):
        self.api = ColorApi()
        platform = patch.object(window_theme, 'os', SimpleNamespace(name='nt'))
        library = patch.object(window_theme.ctypes, 'WinDLL', side_effect=self.api.library, create=True)
        platform.start()
        library.start()
        self.addCleanup(platform.stop)
        self.addCleanup(library.stop)
        self.hwnd = 0x100000002

    def test_missing_window_or_unsupported_platform_never_loads_native_libraries(self):
        self.assertEqual('unsupported', window_theme.apply(0)['reason'])
        window_theme.ctypes.WinDLL.assert_not_called()
        with patch.object(window_theme, 'os', SimpleNamespace(name='posix')):
            self.assertEqual('unsupported', window_theme.apply(self.hwnd)['reason'])
        window_theme.ctypes.WinDLL.assert_not_called()

    def test_high_contrast_is_read_only_and_prevents_all_color_access(self):
        self.api.contrast = True
        self.assertEqual({'applied': False, 'reason': 'high_contrast'}, window_theme.apply(self.hwnd))
        self.assertEqual([], self.api.calls)
        self.assertEqual(self.api.original, self.api.colors)
        self.assertEqual(1, self.api.user.SystemParametersInfoW.call_count)

    def test_unavailable_high_contrast_check_prevents_all_color_access(self):
        self.api.spi_ok = False
        self.assertEqual({'applied': False, 'reason': 'unavailable'}, window_theme.apply(self.hwnd))
        self.assertEqual([], self.api.calls)

    def test_accepted_requests_do_not_use_unsupported_getter_or_claim_visible_rendering(self):
        self.assertEqual({'applied': True, 'reason': 'native_colors_requested'}, window_theme.apply(self.hwnd))
        self.assertEqual(window_theme.PALETTE, self.api.colors)
        self.api.dwm.DwmGetWindowAttribute.assert_not_called()
        self.assertEqual(3, self.api.dwm.DwmSetWindowAttribute.call_count)
        self.assertEqual([('set', self.hwnd, attr, color) for attr, color in window_theme.PALETTE.items()], self.api.calls)

    def test_setter_preserves_pointer_sized_window_handle(self):
        self.assertTrue(window_theme.apply(self.hwnd)['applied'])
        self.assertTrue(all(row[1] == self.hwnd for row in self.api.calls))
        self.assertEqual(wintypes.HWND, self.api.dwm.DwmSetWindowAttribute.argtypes[0])

    def test_failed_write_resets_only_prior_accepted_attributes_to_default(self):
        attrs = list(window_theme.PALETTE)
        for index, attr in enumerate(attrs):
            with self.subTest(attribute=attr):
                self.api.calls.clear()
                self.api.colors = dict(self.api.original)
                self.api.write_failure = attr
                self.assertEqual({'applied': False, 'reason': 'unavailable'}, window_theme.apply(self.hwnd))
                expected = {**self.api.original, **{key: window_theme.DEFAULT_COLOR for key in attrs[:index]}}
                self.assertEqual(expected, self.api.colors)
                reset = [row[2] for row in self.api.calls if row[3] == window_theme.DEFAULT_COLOR]
                self.assertEqual(attrs[:index], reset)
                self.api.dwm.DwmGetWindowAttribute.assert_not_called()

    def test_native_exception_still_resets_only_prior_accepted_attributes(self):
        attrs = list(window_theme.PALETTE)
        for index, attr in enumerate(attrs):
            with self.subTest(attribute=attr):
                self.api.calls.clear()
                self.api.colors = dict(self.api.original)
                self.api.write_exception = attr
                self.assertEqual({'applied': False, 'reason': 'unavailable'}, window_theme.apply(self.hwnd))
                reset = [row[2] for row in self.api.calls if row[3] == window_theme.DEFAULT_COLOR]
                self.assertEqual(attrs[:index], reset)

    def test_failed_reset_is_best_effort_and_never_reported_as_success(self):
        attrs = list(window_theme.PALETTE)
        self.api.write_failure, self.api.reset_failure = attrs[-1], True
        self.assertEqual({'applied': False, 'reason': 'unavailable'}, window_theme.apply(self.hwnd))
        reset = [row[2] for row in self.api.calls if row[3] == window_theme.DEFAULT_COLOR]
        self.assertEqual(attrs[:-1], reset)

    def test_unavailable_native_library_does_not_report_success(self):
        with patch.object(window_theme.ctypes, 'WinDLL', side_effect=OSError('private native detail')):
            self.assertEqual({'applied': False, 'reason': 'unavailable'}, window_theme.apply(self.hwnd))


class WindowThemeIdentityTests(unittest.TestCase):
    def test_same_verified_hidden_window_can_receive_colors(self):
        native = native_window()
        title = native.user.title
        binding = native.bind(title)
        native.user.visible = False
        with patch.object(window_theme, 'apply', return_value={'applied': True, 'reason': 'native_colors_requested'}) as apply:
            self.assertTrue(native.theme(binding, title)['applied'])
            apply.assert_called_once_with(binding.hwnd)

    def test_recycled_identity_or_wrong_window_never_reaches_color_api(self):
        mutations = {
            'pid': lambda n: setattr(n.user, 'pid', 78),
            'created': lambda n: setattr(n, 'identity', (9999999, *n.identity[1:])),
            'session': lambda n: setattr(n, 'identity', (1234567, 3, b'this-user', 'verified-edge.exe')),
            'user': lambda n: setattr(n, 'identity', (1234567, 2, b'other-user', 'verified-edge.exe')),
            'executable': lambda n: setattr(n, 'identity', (1234567, 2, b'this-user', 'other.exe')),
            'title': lambda n: setattr(n.user, 'title', 'Other application'),
            'child': lambda n: setattr(n.user, 'ancestor', 123),
            'destroyed': lambda n: setattr(n.user, 'exists', False),
            'unqueryable': lambda n: setattr(n, 'identity', None),
        }
        for name, mutate in mutations.items():
            with self.subTest(identity=name):
                native = native_window()
                title = native.user.title
                binding = native.bind(title)
                mutate(native)
                with patch.object(window_theme, 'apply') as apply:
                    self.assertEqual({'applied': False, 'reason': 'invalid_window'}, native.theme(binding, title))
                    apply.assert_not_called()

    def test_theme_failure_does_not_disable_verified_attention_binding(self):
        native = native_window()
        notifier = AttentionNotifier(native=native)
        notifier.window_title = native.user.title
        with patch.object(window_theme, 'apply', return_value={'applied': False, 'reason': 'unsupported'}):
            self.assertTrue(notifier.bind()['bound'])
        self.assertEqual({'applied': False, 'reason': 'unsupported'}, notifier.theme_state)
        self.assertTrue(notifier.is_foreground())
        native.user.visible = False
        self.assertFalse(notifier.is_foreground())
        notifier.close()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('COMPANY_WORKSPACE_TEST_WINDOW_THEME') == '1',
                     'Opt in to own hidden HWND color requests')
class NativeWindowThemeTests(unittest.TestCase):
    def test_own_hidden_window_color_requests_accepted_and_destroy(self):
        if sys.getwindowsversion().build < 22000:
            self.skipTest('Window color attributes require Windows 11 build 22000')
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
        user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = wintypes.HWND
        for name in ('DestroyWindow', 'IsWindow', 'IsWindowVisible'):
            fn = getattr(user, name)
            fn.argtypes, fn.restype = [wintypes.HWND], wintypes.BOOL
        # Built-in class, hidden WS_OVERLAPPEDWINDOW: never takes focus or shows UI.
        hwnd = user.CreateWindowExW(0, 'STATIC', 'Workspace own hidden theme probe', 0x00CF0000,
                                    0, 0, 80, 50, None, None, kernel.GetModuleHandleW(None), None)
        self.assertTrue(hwnd, 'Could not create the test-owned hidden HWND')
        try:
            self.assertFalse(user.IsWindowVisible(hwnd))
            result = window_theme.apply(hwnd)
            if result['reason'] == 'high_contrast':
                self.skipTest('High-contrast colors are preserved')
            # This checks accepted HRESULTs, not a visible browser screenshot or
            # a color readback: the color attributes have no supported getter.
            self.assertEqual({'applied': True, 'reason': 'native_colors_requested'}, result)
            self.assertFalse(user.IsWindowVisible(hwnd))
        finally:
            self.assertTrue(user.DestroyWindow(hwnd))
        self.assertFalse(user.IsWindow(hwnd))


if __name__ == '__main__':
    unittest.main()
