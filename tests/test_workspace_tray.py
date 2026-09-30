from __future__ import annotations

import ctypes
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from local_app.tray import WorkspaceTray, summary


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


@unittest.skipUnless(os.name == 'nt' and os.environ.get('COMPANY_WORKSPACE_TEST_TRAY') == '1',
                     'Opt in on the interactive Windows desktop: one temporary owned tray icon')
class WorkspaceTrayNativeTests(unittest.TestCase):
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
