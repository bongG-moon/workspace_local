"""Native-picker ownership and process boundaries, without opening user dialogs."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_app.server import LocalApp, workspace_window_handle
from local_app.picker_protocol import read_result, MAX_RESULT_BYTES


class PickerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-picker-protocol-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.response = self.root / 'result.json'
        self.work = self.root / '업무 자료 🗂'
        self.work.mkdir()

    def read(self, payload, kind='folder'):
        self.response.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False), encoding='utf-8-sig')
        return read_result(self.response, kind)

    def test_single_response_preserves_unicode_and_cancel_is_explicit(self):
        success = {'version': 1, 'status': 'success', 'paths': [str(self.work)]}
        self.assertEqual(success, self.read(success))
        cancel = {'version': 1, 'status': 'cancel', 'paths': []}
        self.assertEqual(cancel, self.read(cancel))

    def test_invalid_framing_schema_and_selection_never_become_success(self):
        success = {'version': 1, 'status': 'success', 'paths': [str(self.work)]}
        invalid = [json.dumps(success) + '\n{}', '{}\n' + json.dumps(success),
                   '{"version":1,"version":1,"status":"cancel","paths":[]}',
                   {**success, 'version': True}, {**success, 'version': 2},
                   {**success, 'status': 'cancel'}, {**success, 'extra': 'private'},
                   {**success, 'paths': []}, {**success, 'paths': [str(self.work)] * 2},
                   {**success, 'paths': ['relative']}, {**success, 'paths': [str(self.work / 'missing')]},
                   {**success, 'paths': [False]}, {**success, 'paths': str(self.work)},
                   {**success, 'status': 'error'}, {**success, 'status': []}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(value)

    def test_wrong_kind_files_count_and_bounded_results(self):
        file = self.work / '보고서.txt'
        file.write_text('fixture', encoding='utf-8')
        value = {'version': 1, 'status': 'success', 'paths': [str(file)]}
        self.assertEqual(value, self.read(value, 'files'))
        with self.assertRaises(ValueError):
            self.read(value)
        with self.assertRaises(ValueError):
            self.read({**value, 'paths': [str(self.work)]}, 'files')
        with self.assertRaises(ValueError):
            self.read({**value, 'paths': [str(file)] * 13}, 'files')
        with self.assertRaises(ValueError):
            self.read(' ' * (MAX_RESULT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, '파일 선택 창'):
            self.read({'version': 1, 'status': 'error', 'paths': [], 'errorCode': 'picker_failed'})


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
        self.root = Path(directory.name)
        owner_root = patch('local_app.picker_channel.local_app_data_folder', return_value=self.root)
        owner_root.start()
        self.addCleanup(owner_root.stop)

    def responder(self, response, returncode=0, stdout='noise\n{}\n'):
        def run(args, **kwargs):
            self.result_path = Path(args[args.index('-ResultPath') + 1])
            self.result_path.write_text(json.dumps(response, ensure_ascii=False), encoding='utf-8')
            return Mock(returncode=returncode, stdout=stdout)
        return run

    def test_parent_handle_and_sta_are_passed_and_unicode_path_is_retained(self):
        work = self.root / '업무 자료'
        work.mkdir()
        response = {'version': 1, 'status': 'success', 'paths': [str(work)]}
        with patch('local_app.server.workspace_window_handle', return_value=0x100000001), \
                patch('local_app.server.subprocess.run', side_effect=self.responder(response)) as run:
            self.assertEqual(response, self.app.pick('folder', str(work)))
        args = run.call_args.args[0]
        self.assertIn('-STA', args)
        self.assertEqual('4294967297', args[args.index('-OwnerHandle') + 1])
        self.assertEqual(str(work), args[args.index('-InitialDirectory') + 1])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs['stdout'])
        self.assertFalse(self.result_path.parent.exists())
        self.assertFalse(self.app.dialog_lock.locked())

    def test_cancel_failure_and_timeout_release_picker_lock(self):
        outcomes = [('cancel', 0), ('error', 1), ('timeout', 0), ('missing', 0), ('success', 1)]
        for status, code in outcomes:
            with self.subTest(status=status), \
                    patch('local_app.server.workspace_window_handle', return_value=0), \
                    patch('local_app.server.subprocess.run') as run:
                if status == 'timeout':
                    def timeout(args, **kwargs):
                        self.result_path = Path(args[args.index('-ResultPath') + 1])
                        raise subprocess.TimeoutExpired('picker', 300)
                    run.side_effect = timeout
                elif status == 'missing':
                    def missing(args, **kwargs):
                        self.result_path = Path(args[args.index('-ResultPath') + 1])
                        return Mock(returncode=0)
                    run.side_effect = missing
                else:
                    response = {'version': 1, 'status': status, 'paths': [str(self.root)] if status == 'success' else []}
                    if status == 'error':
                        response['errorCode'] = 'picker_failed'
                    run.side_effect = self.responder(response, code)
                if status == 'cancel':
                    self.assertEqual({'version': 1, 'status': 'cancel', 'paths': []}, self.app.pick('folder'))
                else:
                    with self.assertRaises(ValueError):
                        self.app.pick('folder')
                self.assertFalse(self.app.dialog_lock.locked())
                self.assertFalse(self.result_path.parent.exists())

    def test_invalid_initial_directory_never_opens_picker(self):
        with patch('local_app.server.subprocess.run') as run:
            for initial in ['', 'relative', False, str(self.root / 'missing')]:
                with self.subTest(initial=initial), self.assertRaises((ValueError, OSError)):
                    self.app.pick('folder', initial)
            run.assert_not_called()

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
