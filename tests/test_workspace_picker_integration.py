"""Native-picker ownership and process boundaries, without opening user dialogs."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_app.server import LocalApp, workspace_window_handle


class OwnerTests(unittest.TestCase):
    def native(self, title, visible=True, handle=0x100000001):
        native = Mock()
        native.GetForegroundWindow.return_value = handle
        native.IsWindowVisible.return_value = visible

        def copy_title(hwnd, buffer, length):
            buffer.value = title
            return len(title)

        native.GetWindowTextW.side_effect = copy_title
        return native

    def test_workspace_foreground_handle_preserves_64_bits(self):
        native = self.native('Company Workspace · 나의 업무 공간 - Microsoft Edge')
        with patch('local_app.server.os.name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=native, create=True):
            self.assertEqual(0x100000001, workspace_window_handle())

    def test_unrelated_or_hidden_foreground_is_never_used(self):
        for title, visible in [('Other Browser', True), ('Company Workspace', False), ('', True)]:
            with self.subTest(title=title, visible=visible), patch('local_app.server.os.name', 'nt'), \
                    patch.object(ctypes, 'WinDLL', return_value=self.native(title, visible), create=True):
                self.assertEqual(0, workspace_window_handle())


@unittest.skipUnless(os.name == 'nt', 'Windows picker subprocess boundary')
class PickerIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = LocalApp(Path(directory.name), demo=True)
        self.addCleanup(self.app.close)

    def test_parent_handle_and_sta_are_passed_and_unicode_path_is_retained(self):
        process = Mock(returncode=0, stdout='["C:\\\\업무 자료"]')
        with patch('local_app.server.workspace_window_handle', return_value=0x100000001), \
                patch('local_app.server.subprocess.run', return_value=process) as run:
            self.assertEqual(['C:\\업무 자료'], self.app.pick('folder'))
        args = run.call_args.args[0]
        self.assertIn('-STA', args)
        self.assertEqual(['-OwnerHandle', '4294967297'], args[-2:])
        self.assertFalse(self.app.dialog_lock.locked())

    def test_cancel_failure_and_timeout_release_picker_lock(self):
        outcomes = [Mock(returncode=0, stdout='[]'), Mock(returncode=1), subprocess.TimeoutExpired('picker', 300)]
        for result in outcomes:
            with self.subTest(result=type(result).__name__), \
                    patch('local_app.server.workspace_window_handle', return_value=0), \
                    patch('local_app.server.subprocess.run') as run:
                if isinstance(result, Exception):
                    run.side_effect = result
                    with self.assertRaises(subprocess.TimeoutExpired):
                        self.app.pick('files')
                else:
                    run.return_value = result
                    if result.returncode:
                        with self.assertRaises(ValueError):
                            self.app.pick('files')
                    else:
                        self.assertEqual([], self.app.pick('files'))
                self.assertFalse(self.app.dialog_lock.locked())

    def test_second_click_does_not_spawn_another_dialog(self):
        self.app.dialog_lock.acquire()
        try:
            with patch('local_app.server.subprocess.run') as run, self.assertRaises(ValueError):
                self.app.pick('folder')
            run.assert_not_called()
        finally:
            self.app.dialog_lock.release()


if __name__ == '__main__':
    unittest.main()
