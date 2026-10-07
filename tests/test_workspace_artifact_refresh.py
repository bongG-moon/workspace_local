"""Refresh current file availability without deleting historical evidence."""
import json
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from local_app.artifacts import available_artifacts
from local_app.server import LocalApp, Server


class ArtifactAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / 'work'
        self.work.mkdir()

    def row(self, name='report.txt'):
        return {'path': str(self.work / name), 'name': name, 'change': 'created',
                'runId': 'run', 'observedAt': 1.0, 'size': 5}

    def test_deleted_and_recreated_file_refresh_without_mutating_history(self):
        row = self.row()
        path = Path(row['path'])
        path.write_text('hello')
        self.assertEqual([row], available_artifacts(self.work, [row])[0])
        path.unlink()
        visible, status = available_artifacts(self.work, [row])
        self.assertEqual([], visible)
        self.assertEqual({'missing': 1, 'errors': 0, 'limited': False}, status)
        path.write_text('restored')
        self.assertEqual([row], available_artifacts(self.work, [row])[0])
        self.assertEqual(5, row['size'])

    def test_missing_parent_or_nonfile_hides_only_affected_entry(self):
        parent = self.work / 'reports'
        parent.mkdir()
        child = self.row('reports/report.txt')
        Path(child['path']).write_text('result')
        kept = self.row('keep.txt')
        Path(kept['path']).write_text('keep')
        Path(child['path']).unlink()
        parent.rmdir()
        self.assertEqual([kept], available_artifacts(self.work, [child, kept])[0])
        parent.write_text('folder replaced by a file')
        self.assertEqual([kept], available_artifacts(self.work, [child, kept])[0])
        Path(kept['path']).unlink()
        Path(kept['path']).mkdir()
        self.assertEqual([], available_artifacts(self.work, [kept])[0])

    def test_access_and_io_errors_preserve_uncertain_rows(self):
        row = self.row()
        target = Path(row['path'])
        original = Path.lstat
        for error in (PermissionError('locked'), OSError('volume not ready')):
            with self.subTest(error=type(error).__name__):
                def checked(path):
                    if path == target:
                        raise error
                    return original(path)
                with patch.object(Path, 'lstat', checked):
                    visible, status = available_artifacts(self.work, [row])
                self.assertEqual([row], visible)
                self.assertEqual(1, status['errors'])
                self.assertEqual(0, status['missing'])

    def test_missing_workspace_is_not_mass_file_deletion(self):
        row = self.row()
        self.work.rmdir()
        visible, status = available_artifacts(self.work, [row])
        self.assertEqual([row], visible)
        self.assertEqual(1, status['errors'])
        self.assertEqual(0, status['missing'])

    def test_deadline_retains_unchecked_rows_without_disk_access(self):
        with patch.object(Path, 'lstat', side_effect=AssertionError('budget exhausted')):
            visible, status = available_artifacts(self.work, [self.row()], seconds=0)
        self.assertEqual([self.row()], visible)
        self.assertTrue(status['limited'])

    def test_parent_link_is_rejected_before_descendant_metadata_is_read(self):
        parent = self.work / 'reports'
        row = self.row('reports/report.txt')
        original = Path.lstat
        seen = []
        def checked(path):
            seen.append(path)
            if path == parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
            if path == Path(row['path']):
                raise AssertionError('followed replaced directory')
            return original(path)
        with patch.object(Path, 'lstat', checked):
            self.assertEqual([], available_artifacts(self.work, [row])[0])
        self.assertNotIn(Path(row['path']), seen)

    def test_replaced_workspace_link_does_not_probe_any_child(self):
        original = Path.lstat
        row = self.row()
        def checked(path):
            if path == self.work:
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            if path == Path(row['path']):
                raise AssertionError('followed workspace link')
            return original(path)
        with patch.object(Path, 'lstat', checked):
            visible, status = available_artifacts(self.work, [row])
        self.assertEqual([row], visible)
        self.assertEqual(1, status['errors'])

    def test_file_link_hides_but_supported_cloud_placeholder_remains(self):
        original = Path.lstat
        row = self.row()
        for mode, tag, expected in ((stat.S_IFLNK, 0, []), (stat.S_IFREG, 0x9000001A, [row])):
            with self.subTest(tag=tag):
                def checked(path):
                    if path == Path(row['path']):
                        return SimpleNamespace(st_mode=mode, st_file_attributes=0x400, st_reparse_tag=tag)
                    return original(path)
                with patch.object(Path, 'lstat', checked):
                    self.assertEqual(expected, available_artifacts(self.work, [row])[0])

    def test_outside_traversal_and_private_paths_are_not_probed(self):
        rows = [self.row('../outside.txt'), self.row('.private/secret.txt'),
                {**self.row(), 'path': str(self.root / 'outside.txt')}, self.row('bad\x00.txt')]
        original = Path.lstat
        def checked(path):
            if path not in (self.work, *self.work.parents):
                raise AssertionError('unsafe metadata probe')
            return original(path)
        with patch.object(Path, 'lstat', checked):
            self.assertEqual([], available_artifacts(self.work, rows)[0])

    def test_presence_does_not_read_content_or_scan_directory_and_deduplicates_stat(self):
        row = self.row()
        target = Path(row['path'])
        target.write_text('hello')
        original = Path.lstat
        seen = []
        def checked(path):
            seen.append(path)
            return original(path)
        with patch.object(Path, 'lstat', checked), \
             patch.object(Path, 'open', side_effect=AssertionError('no content reads')), \
             patch('local_app.artifacts.os.scandir', side_effect=AssertionError('no rescans')):
            visible, _ = available_artifacts(self.work, [row, {**row, 'runId': 'older'}])
        self.assertEqual(2, len(visible))
        self.assertEqual(1, seen.count(target))


class ResultsRefreshRoutesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['fixture-not-started'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.item = self.app.get(self.sid)
        self.file = self.work / 'result.txt'
        self.file.write_text('result')
        self.row = {'path': str(self.file), 'name': self.file.name, 'change': 'created',
                    'runId': 'fixture-run', 'observedAt': 1.0, 'size': 6}
        self.item.update(artifacts=[self.row], lastRunId='fixture-run', state='done')
        self.app.save(self.sid)

    def test_result_refresh_preserves_history_and_handles_reload(self):
        before = {path: path.read_bytes() for path in self.app.state.rglob('*.json')}
        self.file.unlink()
        result = self.app.results(self.sid)
        self.assertEqual([], result['artifacts'])
        self.assertEqual(1, result['availability']['missing'])
        self.assertEqual([self.row], self.item['artifacts'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.app.state.rglob('*.json')})
        restored = LocalApp(self.app.state, command=['fixture-not-started'])
        self.addCleanup(restored.close)
        self.assertEqual([], restored.results(self.sid)['artifacts'])
        self.assertEqual([self.row], restored.get(self.sid)['artifacts'])

    def test_metadata_io_is_outside_the_application_lock(self):
        from local_app.artifacts import available_artifacts as actual
        def checked(*args):
            self.assertFalse(self.app.lock._is_owned())
            return actual(*args)
        with patch('local_app.server.available_artifacts', side_effect=checked):
            self.assertEqual([self.row], self.app.results(self.sid)['artifacts'])

    def test_result_availability_is_independent_of_material_scan_limits(self):
        with patch('local_app.server.snapshot', side_effect=AssertionError('not the source file scan')):
            self.assertEqual([self.row], self.app.results(self.sid)['artifacts'])

    def test_http_refresh_removes_deleted_file_from_both_lists(self):
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), worker.join(2)))
        def request(route):
            with urlopen(Request(server.origin + route + '?id=' + self.sid,
                                 headers={'Authorization': 'Bearer ' + self.app.token}), timeout=3) as response:
                return json.load(response)
        self.assertEqual(1, len(request('/api/files')['files']))
        self.assertEqual(1, len(request('/api/results')['artifacts']))
        self.file.unlink()
        self.assertEqual([], request('/api/files')['files'])
        self.assertEqual([], request('/api/results')['artifacts'])


if __name__ == '__main__':
    unittest.main()
