"""Native update-card typography, lifecycle and optional Windows resource checks."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from local_app import update_progress as progress


class FakeNative:
    def __init__(self, owner):
        self.owner = owner
        self.stopped = threading.Event()
        self.updates = []
        self.hidden = 0

    def run(self):
        self.owner._ready.set()
        self.stopped.wait(5)

    def update(self, phase):
        self.updates.append(phase)

    def hide(self):
        self.hidden += 1

    def close(self):
        self.stopped.set()


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


class UpdateProgressTests(unittest.TestCase):
    def test_phases_share_one_thread_and_close_is_terminal(self):
        with patch.object(progress.os, 'name', 'nt'):
            card = progress.UpdateProgress(factory=FakeNative)
        self.addCleanup(card.close)
        card.phase('preparing')
        thread, native = card._thread, card._native
        card.phase('waiting')
        card.phase('restarting')
        card.phase('opening')
        self.assertIs(thread, card._thread)
        self.assertEqual(['preparing', 'restarting', 'opening'], native.updates)
        self.assertEqual(1, native.hidden)
        card.close()
        card.close()
        card.phase('preparing')
        self.assertFalse(thread.is_alive())
        self.assertEqual(3, len(native.updates))

    def test_disabled_card_allocates_no_thread(self):
        card = progress.UpdateProgress(enabled=False, factory=FakeNative)
        card.phase('preparing')
        card.phase('waiting')
        card.close()
        self.assertIsNone(card._thread)

    def test_creation_failure_aborts_progress_before_commit(self):
        def fail(owner):
            raise OSError('A local resource is unavailable')
        with patch.object(progress.os, 'name', 'nt'):
            card = progress.UpdateProgress(factory=fail)
        self.addCleanup(card.close)
        with self.assertRaisesRegex(OSError, '진행 안내'):
            card.phase('preparing')
        self.assertIsInstance(card._error, OSError)

    def test_unknown_phase_does_not_start_a_window(self):
        card = progress.UpdateProgress(factory=FakeNative)
        with self.assertRaises(ValueError):
            card.phase('completed-100-percent')
        self.assertIsNone(card._thread)

    def test_embedded_font_hashes_license_and_copy_coverage(self):
        directory = Path(progress.__file__).with_name('native_fonts')
        manifest = json.loads((directory / 'SOURCE.json').read_text(encoding='utf-8'))
        labels = progress.BRAND + progress.LABEL + ''.join(
            text for phase in progress.PHASES.values() for text in phase)
        self.assertTrue(set(map(ord, labels)).issubset(manifest['codepoints']),
                        'Regenerate the font subsets after changing fixed update labels.')
        for item in manifest['fonts']:
            data = (directory / item['file']).read_bytes()
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
            self.assertLess(len(data), 32 * 1024)
            self.assertEqual(progress.NATIVE_FONT_FACES[item['weight']], (item['file'], item['family']))
            self.assertLess(len(item['family']), 32, 'Win32 LOGFONT family names are limited to 31 characters.')
        self.assertEqual(manifest['licenseSha256'], hashlib.sha256((directory / 'OFL.txt').read_bytes()).hexdigest())

    def test_font_cmaps_contain_every_fixed_label_and_real_weights(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest('fontTools is a build-time validation dependency only')
        directory = Path(progress.__file__).with_name('native_fonts')
        labels = progress.BRAND + progress.LABEL + ''.join(
            text for phase in progress.PHASES.values() for text in phase)
        for weight, (filename, family) in progress.NATIVE_FONT_FACES.items():
            with TTFont(directory / filename) as font:
                self.assertTrue(set(map(ord, labels)).issubset(font.getBestCmap()))
                self.assertEqual(weight, font['OS/2'].usWeightClass)
                self.assertEqual(family, font['name'].getDebugName(1))
                self.assertNotIn('fvar', font)
                self.assertIn('SIL', font['name'].getDebugName(13))


@unittest.skipUnless(os.name == 'nt' and os.environ.get('WORKSPACE_NATIVE_UI_TESTS') == '1',
                     'Explicit isolated native UI check only')
class NativeUpdateProgressTests(unittest.TestCase):
    observations = []

    def setUp(self):
        from ctypes import wintypes as wt
        self.wt = wt
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        from local_app.tray import _tray_dpi_context
        context = _tray_dpi_context(self.user)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.user.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.user.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.user.IsWindowVisible.argtypes = [wt.HWND]
        self.user.IsWindow.argtypes = [wt.HWND]
        self.user.GetDC.argtypes, self.user.GetDC.restype = [wt.HWND], wt.HDC
        self.user.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        self.user.GetGuiResources.argtypes, self.user.GetGuiResources.restype = [wt.HANDLE, wt.DWORD], wt.DWORD
        self.kernel.GetCurrentProcess.restype = wt.HANDLE
        self.gdi.SelectObject.argtypes, self.gdi.SelectObject.restype = [wt.HDC, wt.HANDLE], wt.HANDLE
        self.gdi.GetTextFaceW.argtypes = [wt.HDC, ctypes.c_int, wt.LPWSTR]
        self.gdi.GetTextExtentPoint32W.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.SIZE)]

    def make_card(self, theme='light'):
        card = progress.UpdateProgress(theme=theme)
        self.addCleanup(card.close)
        card.phase('opening')
        self.assertTrue(eventually(lambda: self.user.IsWindowVisible(card._native.hwnd)))
        return card

    def measure_labels(self, native):
        for index, texts in ((0, (progress.BRAND,)), (1, (progress.LABEL,)),
                             (2, tuple(value[0] for value in progress.PHASES.values())),
                             (3, tuple(value[1] for value in progress.PHASES.values()))):
            dc = self.user.GetDC(native.children[index])
            old_font = self.gdi.SelectObject(dc, native.fonts[index])
            try:
                face = ctypes.create_unicode_buffer(64)
                self.gdi.GetTextFaceW(dc, len(face), face)
                weight = progress.FONT_SPECS[index][1]
                if weight in native.font_faces:
                    self.assertEqual(native.font_faces[weight], face.value)
                else:
                    self.assertIn(face.value, ('Malgun Gothic', '맑은 고딕'))
                rect = self.wt.RECT()
                self.assertTrue(self.user.GetClientRect(native.children[index], ctypes.byref(rect)))
                for text in texts:
                    size = self.wt.SIZE()
                    self.assertTrue(self.gdi.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size)))
                    self.assertLessEqual(size.cx, rect.right, (native.dpi, text, 'width'))
                    self.assertLessEqual(size.cy, rect.bottom, (native.dpi, text, 'height'))
            finally:
                self.gdi.SelectObject(dc, old_font)
                self.user.ReleaseDC(native.children[index], dc)

    def test_actual_fonts_labels_and_layout_after_dpi_changes(self):
        for theme in ('light', 'dark'):
            card = self.make_card(theme)
            native = card._native
            self.assertEqual(2, len(native.font_resources))
            for dpi in (96, 120, 144, 192, 96):
                # Exercise the same owned-window message used by Windows on a
                # monitor move. Does not change the user's display settings.
                current = self.wt.RECT()
                self.user.GetWindowRect(native.hwnd, ctypes.byref(current))
                native.user.SendMessageW(native.hwnd, 0x02E0, dpi | dpi << 16,
                                         ctypes.addressof(current))
                self.assertEqual(dpi, native.dpi)
                rect = self.wt.RECT()
                self.user.GetClientRect(native.hwnd, ctypes.byref(rect))
                self.assertEqual(progress._scaled(progress.CARD_WIDTH, dpi), rect.right)
                self.assertEqual(progress._scaled(progress.CARD_HEIGHT, dpi), rect.bottom)
                self.measure_labels(native)
                self.observations.append({'check': 'dpi-and-text-fit', 'theme': theme, 'dpi': dpi,
                    'physicalWidth': rect.right, 'physicalHeight': rect.bottom,
                    'fontFaces': dict(native.font_faces), 'fontResources': len(native.font_resources)})
            card.close()

    def test_missing_optional_font_uses_korean_fallback(self):
        with patch.object(progress._NativeCard, '_load_private_fonts'):
            card = self.make_card()
        self.assertEqual([], card._native.font_resources)
        self.measure_labels(card._native)
        self.observations.append({'check': 'font-fallback', 'family': 'Malgun Gothic', 'passed': True})
        card.close()

    def test_hide_resumes_and_repeated_close_releases_gdi_user_resources(self):
        process = self.kernel.GetCurrentProcess()
        samples = []
        for number in range(6):
            card = self.make_card('dark' if number % 2 else 'light')
            native, hwnd = card._native, card._native.hwnd
            card.phase('waiting')
            self.assertTrue(eventually(lambda: not self.user.IsWindowVisible(hwnd)))
            frame = native._frame
            time.sleep(.12)
            self.assertEqual(frame, native._frame, 'Hidden cards must not keep animating.')
            card.phase('restarting')
            self.assertTrue(eventually(lambda: self.user.IsWindowVisible(hwnd)))
            card.close()
            self.assertFalse(self.user.IsWindow(hwnd))
            self.assertFalse(card._thread.is_alive())
            self.assertEqual([], native.font_resources)
            self.assertEqual([], native.font_buffers)
            samples.append(tuple(self.user.GetGuiResources(process, kind) for kind in (0, 1)))
        self.assertEqual(samples[-3], samples[-2])
        self.assertEqual(samples[-2], samples[-1], samples)
        self.assertFalse(any(thread.name == 'workspace-update-progress' for thread in threading.enumerate()))
        self.observations.append({'check': 'six-window-resource-cleanup', 'resourcesAfterCloseGdiUser': samples,
                                  'allWindowsAndThreadsClosed': True, 'hiddenAnimationStopped': True})


if __name__ == '__main__':
    unittest.main()
