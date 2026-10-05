from __future__ import annotations

import ctypes
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from local_app.tray import WorkspaceTray, _WindowsTray, summary


def eventually(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


class FakeNative:
    def __init__(self, owner):
        self.owner = owner
        self.stopped = threading.Event()
        self.updates = 0

    def run(self):
        self.owner._mark_ready(True)
        self.stopped.wait(5)

    def request_stop(self):
        self.stopped.set()

    def post_update(self):
        self.updates += 1


class WorkspaceTrayTests(unittest.TestCase):
    def make_tray(self, on_open=lambda: None, on_exit=lambda: None, native_factory=FakeNative):
        tray = WorkspaceTray('isolated-test', on_open, on_exit, native_factory=native_factory)
        self.addCleanup(tray.stop)
        self.assertTrue(tray.start())
        return tray

    def test_lifecycle_is_idempotent_and_does_not_request_server_exit(self):
        exits = []
        tray = self.make_tray(on_exit=lambda: exits.append(True))
        thread = tray._thread
        self.assertTrue(tray.start())
        self.assertIs(thread, tray._thread)
        tray.stop()
        tray.stop()
        self.assertFalse(tray.available)
        self.assertFalse(tray.status()['running'])
        self.assertEqual([], exits)

    def test_failed_server_exit_keeps_icon_and_can_be_retried(self):
        calls = []
        def close():
            calls.append(True)
            return False
        tray = self.make_tray(on_exit=close)
        tray._dispatch('exit')
        self.assertTrue(eventually(lambda: tray.status()['error'] == 'exit_failed'))
        self.assertTrue(tray.available)
        self.assertIn('종료하지 못했어요', tray._text())
        tray._dispatch('exit')
        self.assertTrue(eventually(lambda: len(calls) == 2))

    def test_callback_exception_is_bounded_and_not_exposed(self):
        def fail():
            raise RuntimeError('secret path and private task text')
        tray = self.make_tray(on_open=fail)
        tray._dispatch('open')
        self.assertTrue(eventually(lambda: tray.status()['error'] == 'open_failed'))
        self.assertNotIn('secret', str(tray.status()))
        self.assertTrue(tray.available)

    def test_displayed_confirmation_is_neutral_and_clears_previous_exit_failure(self):
        exits, opens = [], []
        def request_exit():
            exits.append(True)
            # Displaying the confirmation has no shutdown result yet. Cancel
            # is handled by the API, so the callback must leave the tray alive.
            return False if len(exits) == 1 else None
        tray = self.make_tray(on_open=lambda: opens.append(True), on_exit=request_exit)
        tray.update(running=1, waiting=0)
        tray._dispatch('exit')
        self.assertTrue(eventually(lambda: tray.status()['error'] == 'exit_failed'))
        tray._dispatch('exit')
        self.assertTrue(eventually(lambda: len(exits) == 2 and not tray.status()['error']))
        self.assertTrue(tray.available)
        self.assertFalse(tray._stopping.is_set())
        self.assertEqual('진행 중 1건 · 승인·질문 대기 0건', tray._text())
        tray._dispatch('open')
        self.assertTrue(eventually(lambda: len(opens) == 1))

    def test_pending_actions_do_not_block_message_thread_or_duplicate_exit(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def close():
            calls.append(threading.current_thread().name)
            entered.set()
            release.wait(3)
            return False
        self.addCleanup(release.set)
        opened = []
        tray = self.make_tray(on_open=lambda: opened.append(True), on_exit=close)
        tray._dispatch('exit')
        self.assertTrue(entered.wait(1))
        tray._dispatch('exit')
        tray._dispatch('open')
        tray.update(running=2, waiting=1)
        self.assertIn('종료하는 중', tray._text())
        self.assertEqual(['WorkspaceTray-exit'], calls)
        self.assertEqual([], opened)
        self.assertEqual(1, tray._backend.updates)
        release.set()

    def test_counts_are_bounded_and_only_changes_update_native_icon(self):
        tray = self.make_tray()
        tray.update(running=2, waiting=3)
        tray.update(running=2, waiting=3)
        self.assertEqual(1, tray._backend.updates)
        self.assertEqual('진행 중 2건 · 승인·질문 대기 3건', tray._text())
        tray.update(running=-1, waiting='private text')
        self.assertEqual('준비됨 · 백그라운드 실행 중', tray._text())
        self.assertNotIn('private', tray._text())
        self.assertIn('9999건', summary(999999, True))

    def test_instance_identity_is_hashed_and_default_icon_is_bundled(self):
        tray = WorkspaceTray(r'C:\user-private\workspace', lambda: None, lambda: None)
        self.assertEqual(32, len(tray._key))
        self.assertNotIn('private', tray._key)
        self.assertTrue(tray.icon_path.is_file())

    def test_unsupported_platform_is_safe_noop(self):
        with patch('local_app.tray.os') as platform:
            platform.name = 'posix'
            tray = WorkspaceTray('test', lambda: self.fail('no callbacks'), lambda: self.fail('no callbacks'),
                                 icon_path=str(Path(__file__).parent / 'unused.ico'))
        self.assertFalse(tray.start())
        self.assertFalse(tray.available)
        self.assertEqual('unsupported', tray.status()['error'])
        tray.stop()

    def test_native_initialization_failure_preserves_server(self):
        def broken(owner):
            raise OSError('private native error')
        tray = WorkspaceTray('test', lambda: None, lambda: None, native_factory=broken)
        self.addCleanup(tray.stop)
        self.assertFalse(tray.start())
        self.assertEqual('unavailable', tray.status()['error'])

    def test_late_start_during_stop_cleans_up(self):
        entered, release = threading.Event(), threading.Event()
        class DelayedNative(FakeNative):
            def run(self):
                entered.set()
                release.wait(2)
                if not self.owner._stopping.is_set():
                    super().run()
        tray = WorkspaceTray('test', lambda: None, lambda: None, native_factory=DelayedNative)
        starter = threading.Thread(target=tray.start)
        starter.start()
        self.assertTrue(entered.wait(1))
        tray._stopping.set()
        release.set()
        tray.stop()
        starter.join(2)
        self.assertFalse(tray.status()['running'])
        self.assertFalse(tray.available)


class WorkspaceTrayMenuTests(unittest.TestCase):
    def native(self, bounds=(140, 100, 180, 140), result=0, cursor=(600, 400)):
        from ctypes import wintypes as wt
        native = object.__new__(_WindowsTray)
        native.wt = wt
        class Identity(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('window', wt.HWND), ('id', wt.UINT)]
        native.IconIdentifier, native.hwnd = Identity, 123
        native.owner = WorkspaceTray('menu-test', lambda: None, lambda: None)
        def rectangle(identity, output):
            self.assertEqual(123, identity._obj.window)
            self.assertEqual(native.ICON_ID, identity._obj.id)
            output._obj.left, output._obj.top, output._obj.right, output._obj.bottom = bounds
            return result
        def position(output):
            if cursor is None:
                return False
            output._obj.x, output._obj.y = cursor
            return True
        native.shell = SimpleNamespace(Shell_NotifyIconGetRect=Mock(side_effect=rectangle))
        native.user = SimpleNamespace(GetCursorPos=Mock(side_effect=position), CreatePopupMenu=Mock(return_value=321),
            AppendMenuW=Mock(), DestroyMenu=Mock(), SetForegroundWindow=Mock(),
            TrackPopupMenu=Mock(return_value=0), PostMessageW=Mock())
        native._notify = Mock()
        native.taskbar_created = 0xC001
        return native

    def test_shell_icon_anchor_is_used_for_mouse_and_keyboard_not_callback_payload(self):
        # Physical-pixel icon rectangles for different scales and a left monitor.
        for bounds in ((100, 100, 120, 120), (200, 200, 240, 240), (-1820, 700, -1780, 740)):
            for payload in (0, 0x7FFFFFFF, -1):
                with self.subTest(bounds=bounds, payload=payload):
                    native = self.native(bounds=bounds)
                    native._version4 = True
                    native._window_proc(native.hwnd, native.CALLBACK, payload, 0x7B | (native.ICON_ID << 16))
                    self.assertEqual((bounds[0], bounds[3]), native.user.TrackPopupMenu.call_args.args[2:4])
                    native.user.GetCursorPos.assert_not_called()
                    native.user.DestroyMenu.assert_called_once_with(321)

    def test_legacy_right_click_uses_the_same_icon_anchor(self):
        native = self.native()
        native._version4 = False
        native._window_proc(native.hwnd, native.CALLBACK, native.ICON_ID, 0x205)
        self.assertEqual((140, 140), native.user.TrackPopupMenu.call_args.args[2:4])

    def test_shell_failure_or_empty_rectangle_falls_back_to_cursor_in_same_context(self):
        for result, bounds in ((-2147467259, (0, 0, 0, 0)), (0, (0, 0, 0, 0))):
            native = self.native(result=result, bounds=bounds, cursor=(-600, 720))
            native._menu()
            self.assertEqual((-600, 720), native.user.TrackPopupMenu.call_args.args[2:4])

    def test_no_location_does_not_show_menu_at_arbitrary_origin(self):
        native = self.native(result=-1, cursor=None)
        native._menu()
        native.user.CreatePopupMenu.assert_not_called()

    def test_dpi_scope_covers_window_lifetime_and_restores_on_failure(self):
        native = self.native()
        calls = []
        previous = 0xFFFFFFFFFFFFFFF1
        def change(value):
            calls.append(value)
            return previous
        native.user.SetThreadDpiAwarenessContext = Mock(side_effect=change)
        def fail():
            self.assertEqual([-4], calls)
            raise OSError('window initialization failed')
        native._run = fail
        with self.assertRaises(OSError):
            native.run()
        self.assertEqual([-4, previous], calls)
        self.assertIs(ctypes.c_void_p, native.user.SetThreadDpiAwarenessContext.restype)

    def test_dpi_v1_fallback_and_unavailable_context(self):
        for results, expected, started in (([None, 17, 1], [-4, -3, 17], True),
                                          ([None, None], [-4, -3], False)):
            native = self.native()
            native.user.SetThreadDpiAwarenessContext = Mock(side_effect=results)
            native._run = Mock()
            if started:
                native.run()
                native._run.assert_called_once()
            else:
                with self.assertRaises(OSError):
                    native.run()
                native._run.assert_not_called()
            self.assertEqual(expected, [call.args[0] for call in native.user.SetThreadDpiAwarenessContext.call_args_list])


@unittest.skipUnless(os.name == 'nt' and os.environ.get('COMPANY_WORKSPACE_TEST_TRAY') == '1',
                     'Opt in on the interactive Windows desktop: one temporary owned tray icon')
class WorkspaceTrayNativeTests(unittest.TestCase):
    def test_native_window_and_menu_callback_are_per_monitor_without_changing_caller(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user.GetWindowDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
        user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user.AreDpiAwarenessContextsEqual.restype = ctypes.c_bool
        before = user.GetThreadDpiAwarenessContext()
        tray = WorkspaceTray('native-dpi-' + uuid.uuid4().hex, lambda: None, lambda: None)
        self.addCleanup(tray.stop)
        self.assertTrue(tray.start(), tray.status())
        native = tray._backend
        self.assertTrue(user.AreDpiAwarenessContextsEqual(user.GetWindowDpiAwarenessContext(native.hwnd), -4))
        seen, result = threading.Event(), {}
        # Observe the real callback on our HWND, but suppress the blocking menu.
        def track(menu, flags, x, y, reserved, hwnd, rectangle):
            result.update(context=user.GetThreadDpiAwarenessContext(), point=(x, y), hwnd=hwnd)
            seen.set()
            return 0
        with patch.object(native.user, 'TrackPopupMenu', side_effect=track):
            self.assertTrue(native.user.PostMessageW(native.hwnd, native.CALLBACK, 0, 0x7B | (native.ICON_ID << 16)))
            self.assertTrue(seen.wait(3))
        self.assertTrue(user.AreDpiAwarenessContextsEqual(result['context'], -4))
        self.assertEqual(native.hwnd, result['hwnd'])
        self.assertTrue(user.AreDpiAwarenessContextsEqual(before, user.GetThreadDpiAwarenessContext()))
        tray.stop()
        self.assertTrue(user.AreDpiAwarenessContextsEqual(before, user.GetThreadDpiAwarenessContext()))

    def test_native_icon_lifecycle_duplicate_guard_and_shell_recovery(self):
        key = 'native-tray-test-' + uuid.uuid4().hex
        opened, closed = threading.Event(), threading.Event()
        tray = WorkspaceTray(key, opened.set, closed.set)
        duplicate = WorkspaceTray(key, lambda: None, lambda: None)
        self.addCleanup(tray.stop)
        self.addCleanup(duplicate.stop)
        self.assertTrue(tray.start(), tray.status())
        self.assertFalse(duplicate.start())
        self.assertEqual('already_running', duplicate.status()['error'])
        native = tray._backend
        self.assertIsNotNone(native.hwnd)
        # Exercise native delivery without opening any real application.
        native.user.PostMessageW(native.hwnd, native.CALLBACK, 0, 0x401 | (native.ICON_ID << 16))
        self.assertTrue(opened.wait(2))
        self.assertFalse(closed.is_set())
        tray.update(running=2, waiting=1)
        # Remove only our icon, then deliver the registered shell-restart message
        # to only our HWND. Explorer and other applications are never restarted.
        self.assertTrue(native._notify(2))
        tray._mark_ready(False)
        native.user.PostMessageW(native.hwnd, native.taskbar_created, 0, 0)
        self.assertTrue(eventually(lambda: tray.available))
        tray.stop()
        self.assertFalse(tray.status()['running'])
        self.assertIsNone(native.hwnd)
        self.assertIsNone(native.mutex)
        self.assertFalse(closed.is_set())
        # All ownership handles/classes were released, so the same key can start.
        self.assertTrue(duplicate.start(), duplicate.status())
        duplicate.stop()


if __name__ == '__main__':
    unittest.main()
