"""Metadata-only picker boundaries; no installed Claude or user state is used."""
from pathlib import Path
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.path_browser import browse_paths, validate_folder_selection, _shortcuts, _KNOWN_FOLDERS
from local_app.server import LocalApp, Server


class PathBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.shortcut = patch('local_app.path_browser._shortcuts', return_value=[{'name': '바탕화면', 'path': str(self.root)}])
        self.shortcut.start()
        self.addCleanup(self.shortcut.stop)

    def browse(self, **data):
        return browse_paths({'kind': 'files', 'path': str(self.root), **data})

    def test_metadata_korean_multiselect_candidates_no_content_reads(self):
        (self.root / '업무 자료').mkdir()
        (self.root / '보고서 😀.CSV').write_text('private contents', encoding='utf-8')
        (self.root / 'app.py').write_text('print(1)', encoding='utf-8')
        (self.root / 'run.exe').write_bytes(b'not supported')
        with patch.object(Path, 'open', side_effect=AssertionError('must not read contents')):
            result = self.browse()
        self.assertEqual(['folder', 'file', 'file'], [row['kind'] for row in result['entries']])
        self.assertEqual({'업무 자료', '보고서 😀.CSV', 'app.py'}, {r['name'] for r in result['entries']})
        self.assertNotIn('private contents', str(result))
        self.assertEqual(str(self.root), result['path'])
        self.assertEqual(str(self.root.parent), result['parent'])
        self.assertEqual(str(self.root), result['breadcrumbs'][-1]['path'])

    def test_folder_only_excludes_files_and_does_not_recurse(self):
        sub = self.root / 'sub'
        sub.mkdir()
        (sub / 'nested.txt').write_text('nested')
        (self.root / 'parent.txt').write_text('parent')
        result = self.browse(kind='folder')
        self.assertEqual(['sub'], [r['name'] for r in result['entries']])
        self.assertNotIn('nested', str(result))

    def test_default_uses_real_known_folder_shortcut(self):
        result = browse_paths({'kind': 'folder'})
        self.assertEqual(str(self.root), result['path'])

    def test_initial_not_yet_created_workspace_uses_existing_parent_without_creating(self):
        path = self.root / 'Company Workspace' / 'new task'
        result = self.browse(path=str(path), initial=True)
        self.assertEqual(str(self.root), result['path'])
        self.assertFalse(path.parent.exists())
        with self.assertRaisesRegex(ValueError, '찾지 못했'):
            self.browse(path=str(path))

    def test_file_path_not_treated_as_directory(self):
        path = self.root / 'report.txt'
        path.write_text('x')
        with self.assertRaisesRegex(ValueError, '파일 대신 폴더'):
            self.browse(path=str(path))

    def test_apply_revalidates_explicit_folder_without_creating_or_trusting(self):
        self.assertEqual([str(self.root)], validate_folder_selection([str(self.root)]))
        for paths in [[], [str(self.root)]*2, str(self.root), [None], ['relative'], ['\\\\?\\C:\\Windows']]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                validate_folder_selection(paths)
        candidate = self.root / 'deleted'
        candidate.mkdir()
        self.browse(path=str(candidate))
        candidate.rmdir()
        with self.assertRaisesRegex(ValueError, '다시 확인'):
            validate_folder_selection([str(candidate)])

    def test_relative_traversal_device_network_and_control_input_rejected(self):
        for value in ['relative', str(self.root / '..' / 'other'), '\\\\server\\share', '//server/share', '\\\\?\\C:\\Windows', str(self.root)+'\x00']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.browse(path=value)
        if os.name == 'nt':
            with self.assertRaises(ValueError):
                self.browse(path=str(self.root / 'folder:stream'))

    def test_wrong_kinds_queries_and_payload_rejected(self):
        for data in [None, [], {}, {'kind':'execute'}, {'kind':'files','query':[]}, {'kind':'files','query':'a'*161}, {'kind':'files','query':'\n'}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                browse_paths(data)

    def test_cap_marks_incomplete_and_search_is_only_current_directory(self):
        for index in range(8):
            (self.root / f'file{index}.txt').write_text('')
        result = browse_paths({'kind':'files','path':str(self.root)}, max_entries=3)
        self.assertEqual(3, len(result['entries']))
        self.assertTrue(result['limited'])
        self.assertEqual(['file7.txt'], [r['name'] for r in self.browse(query='FILE7')['entries']])
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'secret7.txt').write_text('')
        self.assertEqual([], self.browse(query='secret7')['entries'])

    def test_scan_and_time_budgets_apply_to_nonmatching_entries(self):
        for index in range(8):
            (self.root / f'file{index}.txt').write_text('')
        result = browse_paths({'kind':'files','path':str(self.root),'query':'no match'}, max_scanned=2)
        self.assertTrue(result['limited'])
        self.assertEqual([], result['entries'])
        result = browse_paths({'kind':'files','path':str(self.root)}, seconds=-1)
        self.assertTrue(result['limited'])
        self.assertEqual([], result['entries'])

    def test_hidden_files_not_returned(self):
        (self.root / '.private.txt').write_text('')
        self.assertEqual([], self.browse()['entries'])

    def test_permission_and_disconnected_errors_are_actionable(self):
        for error, phrase in [(PermissionError('raw private path'), '권한'), (OSError('raw error'), '연결 상태')]:
            with patch('local_app.path_browser.os.scandir', side_effect=error):
                with self.assertRaisesRegex(ValueError, phrase) as caught:
                    self.browse()
                self.assertNotIn('raw', str(caught.exception))

    def test_symlink_directory_and_file_not_followed(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'value.txt').write_text('')
        try:
            (self.root / 'link').symlink_to(target, target_is_directory=True)
            (self.root / 'file-link.txt').symlink_to(target / 'value.txt')
        except OSError:
            self.skipTest('Creating symlinks requires unavailable Windows rights')
        self.assertEqual(['target'], [row['name'] for row in self.browse()['entries']])
        with self.assertRaises(ValueError):
            self.browse(path=str(self.root / 'link'))

    def test_shortcuts_query_redirected_windows_folders_without_guessing_profile_names(self):
        self.shortcut.stop()
        if os.name != 'nt':
            self.skipTest('Windows Known Folder paths')
        targets=[]
        for index in range(3):
            folder=self.root / f'redirected-{index}'
            folder.mkdir()
            targets.append(str(folder))
        with patch('local_app.path_browser._known_folder_path', side_effect=targets) as query:
            rows=_shortcuts()
        self.assertEqual(targets, [row['path'] for row in rows[:3]])
        self.assertEqual([item[1] for item in _KNOWN_FOLDERS], [call.args[0] for call in query.call_args_list])

    def test_unknown_shortcut_is_skipped_without_creating_folders(self):
        self.shortcut.stop()
        if os.name != 'nt':
            self.skipTest('Windows Known Folder paths')
        with patch('local_app.path_browser._known_folder_path', side_effect=OSError('unavailable')):
            rows=_shortcuts()
        self.assertTrue(all(row['name']=='내 폴더' for row in rows))
        self.assertEqual([], list(self.root.iterdir()))


class PathBrowserRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['never-started'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.app.get(self.sid)['trusted'] = False  # An existing task awaiting reconfirmation.
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, payload, *, authorized=True, origin=None):
        headers = {'Content-Type':'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.app.token
        if origin:
            headers['Origin'] = origin
        req = Request(self.server.origin+'/api/browse-paths', data=json.dumps(payload).encode(), headers=headers)
        try:
            response = urlopen(req, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_unauthorized_and_other_origin_are_rejected_before_enumeration(self):
        with patch('local_app.path_browser.browse_paths', side_effect=AssertionError('must not browse')):
            self.assertEqual(403, self.request({'kind':'files'}, authorized=False)[0])
            self.assertEqual(403, self.request({'kind':'files'}, origin='https://other.example')[0])

    def test_listing_and_apply_do_not_connect_trust_or_persist_attachments(self):
        path = self.work / '보고서.csv'
        path.write_text('private,csv')
        code, response = self.request({'kind':'files','path':str(self.work)})
        self.assertEqual(200, code, response)
        self.assertEqual(['보고서.csv'], [row['name'] for row in response['entries']])
        code, response = self.request({'action':'select','kind':'files','paths':[str(path)]})
        self.assertEqual(200, code, response)
        self.assertEqual([str(path)], response['paths'])
        code, response = self.request({'action':'select','kind':'folder','paths':[str(self.work)]})
        self.assertEqual(200, code, response)
        self.assertEqual([str(self.work)], response['paths'])
        item = self.app.get(self.sid)
        self.assertFalse(item['trusted'])
        self.assertIsNone(item['bridge'])
        self.assertEqual([], item['attachments'])
        self.assertEqual([], item['messages'])

    def test_apply_deletion_unsupported_file_and_invalid_payloads_do_not_mutate_task(self):
        path = self.work / 'to-delete.csv'
        path.write_text('')
        self.request({'kind':'files','path':str(self.work)})
        path.unlink()
        executable = self.work / 'app.exe'
        executable.write_bytes(b'')
        for payload in [
                {'action':'select','kind':'files','paths':[str(path)]},
                {'action':'select','kind':'files','paths':[str(executable)]},
                {'action':'select','kind':'files','paths':[str(path)]*13},
                {'action':'select','kind':'folder','paths':[str(executable)]},
                {'action':'select','kind':'bad','paths':[str(self.work)]},
                {'action':'write','kind':'folder','paths':[str(self.work)]}]:
            with self.subTest(payload=payload):
                self.assertEqual(400, self.request(payload)[0])
        self.assertEqual([], self.app.get(self.sid)['attachments'])
        self.assertFalse(self.app.get(self.sid)['trusted'])

    def test_new_script_and_style_are_served_with_expected_types(self):
        for path, content_type in [('/path-picker.js','text/javascript'),('/path-picker.css','text/css')]:
            with urlopen(self.server.origin+path, timeout=4) as response:
                self.assertEqual(200, response.status)
                self.assertIn(content_type, response.headers['Content-Type'])
                self.assertGreater(len(response.read()), 100)


if __name__ == '__main__':
    unittest.main()
