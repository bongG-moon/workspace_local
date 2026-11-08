import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from local_app.file_diff import FileDiffStore, MAX_RUN_BYTES


class FileDiffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.store = FileDiffStore(self.base / 'state')

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
        return path

    def changes(self, run='run1'):
        return self.store.list(self.workspace, run)['runs'][0]['files']

    def compare(self, row, run='run1'):
        return self.store.get(self.workspace, run, row['id'])

    def test_captured_text_survives_later_edits_without_reading_original(self):
        path = self.write('report.html', 'first\nold\n')
        self.assertEqual(self.store.start(self.workspace, 'run1')['status'], 'captured')
        path.write_text('first\nnew\n', encoding='utf-8')
        self.store.finish(self.workspace, 'run1')
        path.write_text('third version\n', encoding='utf-8')
        row = self.changes()[0]
        with mock.patch.object(self.store, '_read_source', side_effect=AssertionError('read original')):
            result = self.compare(row)
        self.assertEqual(result['status'], 'text')
        self.assertIn('-old\n+new\n', result['diff'].replace('\r\n', '\n'))
        self.assertNotIn('third version', result['diff'])
        self.assertEqual(result['version'], 'captured')
        self.assertFalse(result['currentFileRead'])
        self.assertEqual(path.read_text(encoding='utf-8'), 'third version\n')

    def test_scoped_markdown_and_source_previews_support_captured_review(self):
        names = ['guide.md', 'script.go']
        for name in names:
            self.write(name, 'one\ntwo\n')
        self.store.start(self.workspace, 'docs-code', session_id='task')
        for name in names:
            self.write(name, 'one\nupdated\nthree\n')
        result = self.store.finish(self.workspace, 'docs-code', allowed_paths=names)
        self.assertEqual(result['lineCounts'], {name: {'added': 2, 'removed': 1} for name in names})
        for row in self.changes('docs-code'):
            self.assertEqual(self.compare(row, 'docs-code')['status'], 'text')

    def test_scoped_run_without_write_evidence_drops_unrelated_text(self):
        self.write('changed-by-other-task.md', 'private baseline')
        self.store.start(self.workspace, 'scoped', session_id='task-a')
        self.write('changed-by-other-task.md', 'other task result')
        with mock.patch.object(self.store, '_read_source', side_effect=AssertionError('unattributed read')):
            result = self.store.finish(self.workspace, 'scoped')
        self.assertEqual(0, result['changeCount'])
        self.assertEqual([], self.changes('scoped'))
        stored = self.store._read(self.store._target(self.workspace, 'scoped'))
        for phase in ('before', 'after'):
            self.assertEqual({}, stored[phase]['files'])

    def test_inline_edits_align_related_lines_across_insertions_and_preserve_unicode_offsets(self):
        path = self.workspace / 'report.md'
        original = ['# Report', '', '🗂 수율 목표 90 퍼센트', '최종 점검 기간 3일 이내', '',
                    'fixed', 'x' * 520 + ' old']
        updated = ['# Report', '', '새 섹션', '🗂 수율 목표 95 퍼센트', '담당자 참고',
                   '최종 점검 기간 7일 이내', '추가 안내', '끝', '', 'fixed', 'x' * 520 + ' new']
        path.write_bytes('\n'.join(original).encode())
        self.store.start(self.workspace, 'report')
        path.write_bytes('\n'.join(updated).encode())
        self.store.finish(self.workspace, 'report')
        rows = [row for hunk in self.compare(self.changes('report')[0], 'report')['hunks'] for row in hunk['rows']]
        for row in rows:
            if row['beforeLine'] is not None:
                self.assertEqual(row['text'], original[row['beforeLine']-1])
            if row['afterLine'] is not None:
                self.assertEqual(row['text'], updated[row['afterLine']-1])
        self.assertEqual([(r['pairedBeforeLine'], r['afterLine']) for r in rows if 'pairedBeforeLine' in r],
                         [(3, 4), (4, 6)])
        self.assertEqual([r['text'][start:end] for r in rows for start, end in r.get('highlights', ())],
                         ['90', '3일', '95', '7일'])

    def test_numbered_hunks_follow_stored_versions_and_preserve_source_whitespace(self):
        path = self.workspace / 'code.py'
        original = [f'unchanged {i}\r\n' for i in range(1, 20)]
        original[0] = '\t--- old  \r\n'
        original[-1] = 'last\u2028line\fhere'
        updated = ['\t+++ new  \n', 'extra\n', *original[1:-1], 'final\n']
        path.write_bytes(''.join(original).encode())
        self.store.start(self.workspace, 'scoped', session_id='A')
        path.write_bytes(''.join(updated).encode())
        self.store.finish(self.workspace, 'scoped', allowed_paths=['code.py'])
        path.write_bytes(b'unrelated current content')
        files = self.store.list(self.workspace, 'scoped', session_id='A')['runs'][0]['files']
        result = self.store.get(self.workspace, 'scoped', files[0]['id'], session_id='A')
        first, last = result['hunks']
        self.assertEqual((1, 4, 1, 5), (first['beforeStart'], first['beforeCount'],
                                         first['afterStart'], first['afterCount']))
        rows = [r for h in (first, last) for r in h['rows']]
        for row in rows:
            line = row['text'] + row['ending']
            if row['beforeLine'] is not None:
                self.assertEqual(line, original[row['beforeLine'] - 1])
            if row['afterLine'] is not None:
                self.assertEqual(line, updated[row['afterLine'] - 1])
        self.assertEqual([(r['kind'], r['beforeLine'], r['afterLine']) for r in rows
                          if r['kind'] != 'context'], [('remove', 1, None), ('add', None, 1),
                          ('add', None, 2), ('remove', 19, None), ('add', None, 20)])
        self.assertEqual(first['rows'][1]['text'], '\t+++ new  ')
        self.assertEqual(last['rows'][-2]['ending'], '')
        self.assertNotIn('unrelated current content', str(result))
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'scoped', files[0]['id'], session_id='B')

    def test_create_and_delete_including_new_and_removed_child_folders(self):
        path = self.write('old/report.txt', 'deleted\n')
        self.store.start(self.workspace, 'run1')
        path.unlink()
        path.parent.rmdir()
        self.write('new/result.txt', 'created\n')
        self.store.finish(self.workspace, 'run1')
        rows = {row['name']: row for row in self.changes()}
        self.assertEqual(rows['old/report.txt']['change'], 'deleted')
        self.assertEqual(rows['new/result.txt']['change'], 'created')
        self.assertIn('-deleted', self.compare(rows['old/report.txt'])['diff'])
        self.assertIn('+created', self.compare(rows['new/result.txt'])['diff'])
        deleted = self.compare(rows['old/report.txt'])['hunks'][0]
        created = self.compare(rows['new/result.txt'])['hunks'][0]
        self.assertEqual((deleted['afterStart'], deleted['afterCount']), (0, 0))
        self.assertEqual((created['beforeStart'], created['beforeCount']), (0, 0))
        self.assertIsNone(deleted['rows'][0]['afterLine'])
        self.assertIsNone(created['rows'][0]['beforeLine'])

    def test_incomplete_baseline_never_invents_creation(self):
        with mock.patch('local_app.file_diff.os.scandir', side_effect=PermissionError()):
            # Store setup needs no directory enumeration until retention checks.
            capture = self.store._capture(self.workspace)
        self.store.start(self.workspace, 'run1')
        target = self.store._target(self.workspace, 'run1')
        data = json.loads(target.read_text(encoding='utf-8'))
        data['before'] = capture
        self.store._write(target, data)
        self.write('result.txt', 'result')
        self.store.finish(self.workspace, 'run1')
        row = self.changes()[0]
        self.assertEqual(row['change'], 'unconfirmed')
        self.assertEqual(self.compare(row)['reason'], 'observation_incomplete')

    def test_failed_after_scan_never_invents_deletion(self):
        self.write('kept.txt', 'still here')
        self.store.start(self.workspace, 'run1')
        with mock.patch.object(self.store, '_capture', return_value={
                'files': {}, 'directories': [], 'complete': [], 'errors': 1}):
            self.store.finish(self.workspace, 'run1')
        self.assertEqual(self.changes()[0]['change'], 'unconfirmed')

    def test_binary_office_files_only_have_metadata(self):
        path = self.workspace / 'report.xlsx'
        path.write_bytes(b'PK\x00old')
        self.store.start(self.workspace, 'run1')
        path.write_bytes(b'PK\x00new-size')
        self.store.finish(self.workspace, 'run1')
        result = self.compare(self.changes()[0])
        self.assertEqual(result['status'], 'metadata_only')
        self.assertEqual(result['diff'], '')
        self.assertNotIn('PK', self.store._target(self.workspace, 'run1').read_text(encoding='utf-8'))

    def test_utf8_bom_and_utf16_are_captured(self):
        for name, encoding in [('utf8.txt', 'utf-8-sig'), ('utf16.txt', 'utf-16')]:
            (self.workspace / name).write_text('이전\n', encoding=encoding)
        self.store.start(self.workspace, 'run1')
        for name, encoding in [('utf8.txt', 'utf-8-sig'), ('utf16.txt', 'utf-16')]:
            (self.workspace / name).write_text('수정\n', encoding=encoding)
        self.store.finish(self.workspace, 'run1')
        for row in self.changes():
            result = self.compare(row)
            self.assertEqual(result['status'], 'text')
            self.assertIn('-이전', result['diff'])
            self.assertIn('+수정', result['diff'])

    def test_limits_prevent_large_file_and_total_text_capture(self):
        self.store = FileDiffStore(self.base / 'state', file_bytes=16, capture_bytes=20)
        self.write('huge.txt', 'x' * 17)
        self.write('small1.txt', 'first12345')
        self.write('small2.txt', 'second12345')
        self.write('small3.txt', 'third12345')
        self.store.start(self.workspace, 'run1')
        data = self.store._read(self.store._target(self.workspace, 'run1'))['before']
        self.assertLessEqual(data['textBytes'], 20)
        self.assertEqual(data['files']['huge.txt']['textStatus'], 'file_limit')
        self.assertIn('capture_limit', [row['textStatus'] for row in data['files'].values()])
        self.assertLessEqual(sum(len(row.get('text', '')) for row in data['files'].values()), 20)

    def test_scan_depth_hidden_secrets_and_build_are_excluded(self):
        for name in ['safe.txt', 'child/safe.py', 'child/deeper/ignored.txt', '.env',
                     '.git/config.txt', 'secrets.json', 'api-token.txt', 'credentials.yaml',
                     'private.key', 'node_modules/result.txt', 'dist/result.txt']:
            self.write(name, 'sensitive marker')
        self.store.start(self.workspace, 'run1')
        files = self.store._read(self.store._target(self.workspace, 'run1'))['before']['files']
        self.assertEqual(set(files), {'safe.txt', 'child/safe.py'})

    def test_symlink_and_hardlink_do_not_capture_outside_content(self):
        outside = self.base / 'outside.txt'
        outside.write_text('outside secret', encoding='utf-8')
        hardlink = self.workspace / 'hardlink.txt'
        try:
            os.link(outside, hardlink)
        except OSError:
            pass
        try:
            (self.workspace / 'linked.txt').symlink_to(outside)
        except OSError:
            pass  # Windows may deny creating symlinks without developer mode.
        self.store.start(self.workspace, 'run1')
        raw = self.store._target(self.workspace, 'run1').read_text(encoding='utf-8')
        self.assertNotIn('outside secret', raw)
        self.assertEqual(self.store._read(self.store._target(self.workspace, 'run1'))['before']['files'], {})

    def test_reparse_point_metadata_is_excluded(self):
        path = self.write('redirect.txt', 'outside secret')
        original = path.stat()
        redirected = mock.Mock(st_mode=original.st_mode, st_file_attributes=0x400)
        # Check exclusion through the real scanner by replacing only entry stat.
        item = mock.Mock(path=str(path), stat=lambda **_: redirected)
        item.name = path.name
        with mock.patch('local_app.file_diff.os.scandir', return_value=mock.MagicMock(
                __enter__=lambda *_: iter([item]), __exit__=lambda *_: False)):
            result = self.store._capture(self.workspace)
        self.assertEqual(result['files'], {})

    def test_read_failure_has_no_empty_baseline_diff(self):
        self.write('report.txt', 'before')
        with mock.patch.object(self.store, '_read_source', side_effect=PermissionError()):
            self.store.start(self.workspace, 'run1')
        self.write('report.txt', 'after-longer')
        self.store.finish(self.workspace, 'run1')
        row = self.changes()[0]
        self.assertEqual(row['change'], 'modified')
        self.assertEqual(row['beforeStatus'], 'read_failed')
        self.assertEqual(self.compare(row)['status'], 'metadata_only')

    def test_file_changed_during_read_is_rejected(self):
        path = self.write('report.txt', 'before')
        expected = path.stat()
        self.write('report.txt', 'changed-longer')
        with self.assertRaises(ValueError):
            self.store._read_source(path, expected)

    def test_scan_limit_marks_absent_files_uncertain(self):
        self.write('a.txt', 'before')
        self.store.start(self.workspace, 'run1')
        self.write('b.txt', 'created')
        self.store.max_files = 1
        self.store.finish(self.workspace, 'run1')
        result = self.store.list(self.workspace)
        self.assertTrue(result['runs'][0]['after']['limited'])
        self.assertLessEqual(result['runs'][0]['after']['scanned'], 1)

    def test_retention_is_global_and_restart_can_read_without_caches(self):
        self.store = FileDiffStore(self.base / 'state', max_runs=2)
        self.write('report.txt', 'one')
        self.store.start(self.workspace, 'one')
        self.store.finish(self.workspace, 'one')
        other = self.base / 'other'
        other.mkdir()
        self.store.start(other, 'two')
        self.store.finish(other, 'two')
        self.store.start(self.workspace, 'three')
        self.write('report.txt', 'three')
        self.store.finish(self.workspace, 'three')
        self.assertEqual(len(list(self.store.directory.glob('*.json'))), 2)
        restarted = FileDiffStore(self.store.directory, max_runs=2)
        self.assertEqual([row['runId'] for row in restarted.list(self.workspace)['runs']], ['three'])
        self.assertEqual(self.store.finish(self.workspace, 'one')['reason'], 'baseline_not_available')
        self.assertFalse(any('text' in key.lower() for key in self.store.__dict__))

    def test_simultaneous_jobs_are_atomic_and_finite(self):
        self.write('report.txt', 'before')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda i: self.store.start(self.workspace, f'run{i}'), range(10)))
        self.assertTrue(all(row['status'] == 'captured' for row in results))
        self.assertEqual(len(list(self.store.directory.glob('*.json'))), 8)
        self.write('report.txt', 'after')
        runs = [row['runId'] for row in self.store.list(self.workspace)['runs']]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda run: self.store.finish(self.workspace, run), runs))
        self.assertTrue(all(row['status'] == 'captured' for row in results))
        self.assertFalse(list(self.store.directory.glob('*.tmp')))
        self.assertLessEqual(sum(path.stat().st_size for path in self.store.directory.iterdir()), 8 * MAX_RUN_BYTES)

    def test_traversal_cross_workspace_and_unknown_ids_rejected(self):
        self.write('report.txt', 'before')
        self.store.start(self.workspace, 'run1')
        self.write('report.txt', 'after')
        self.store.finish(self.workspace, 'run1')
        row = self.changes()[0]
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, '../run1', row['id'])
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'run1', '../report.txt')
        other = self.base / 'other'
        other.mkdir()
        with self.assertRaises(FileNotFoundError):
            self.store.get(other, 'run1', row['id'])
        with self.assertRaises(ValueError):
            self.store.get(self.workspace, 'run1', '0' * 24)

    def test_diff_work_and_output_limits(self):
        self.store = FileDiffStore(self.base / 'state', diff_work=10)
        self.write('report.txt', 'a\nb\nc\nd\n')
        self.store.start(self.workspace, 'run1')
        self.write('report.txt', 'e\nf\ng\nh\n')
        self.store.finish(self.workspace, 'run1')
        self.assertEqual(self.compare(self.changes()[0])['reason'], 'diff_work_limit')
        self.store.diff_work = 100
        self.store.diff_lines = 3
        result = self.compare(self.changes()[0])
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['diff'].splitlines()), 3)
        self.assertFalse(any(h['rows'] for h in result['hunks']))
        self.store.diff_lines = 5
        result = self.compare(self.changes()[0])
        self.assertTrue(result['truncated'])
        self.assertEqual([r['beforeLine'] for r in result['hunks'][0]['rows']], [1, 2])

    def test_capture_storage_failure_does_not_escape_start_or_finish(self):
        self.write('report.txt', 'before')
        with mock.patch.object(self.store, '_write', side_effect=OSError('full disk')):
            self.assertEqual(self.store.start(self.workspace, 'run1')['status'], 'unavailable')
        self.store.start(self.workspace, 'run1')
        with mock.patch.object(self.store, '_write', side_effect=OSError('full disk')):
            self.assertEqual(self.store.finish(self.workspace, 'run1')['status'], 'unavailable')
        self.assertEqual(self.store._read(self.store._target(self.workspace, 'run1'))['after'], None)

    def test_atomic_replace_failure_keeps_previous_baseline_and_cleans_temp(self):
        self.write('report.txt', 'before')
        self.store.start(self.workspace, 'run1')
        target = self.store._target(self.workspace, 'run1')
        before = target.read_bytes()
        with mock.patch('local_app.file_diff.os.replace', side_effect=OSError('denied')):
            self.assertEqual(self.store.finish(self.workspace, 'run1')['status'], 'unavailable')
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(list(self.store.directory.glob('*.tmp')))

    def test_same_run_is_idempotent_and_list_has_no_text(self):
        self.write('report.txt', 'PRIVATE CAPTURE PAYLOAD')
        self.store.start(self.workspace, 'run1')
        self.assertEqual(self.store.start(self.workspace, 'run1')['status'], 'already_captured')
        self.write('report.txt', 'updated')
        self.store.finish(self.workspace, 'run1')
        self.assertEqual(self.store.finish(self.workspace, 'run1')['status'], 'already_captured')
        self.assertNotIn('PRIVATE CAPTURE PAYLOAD', json.dumps(self.store.list(self.workspace)))

    def test_malformed_capture_is_unavailable_and_not_silently_overwritten(self):
        self.store.start(self.workspace, 'run1')
        path = self.store._target(self.workspace, 'run1')
        path.write_text('{"version":1,"before":[]}', encoding='utf-8')
        original = path.read_bytes()
        self.assertEqual(self.store.finish(self.workspace, 'run1')['status'], 'unavailable')
        result = self.store.list(self.workspace)
        self.assertEqual(result['runs'], [])
        self.assertEqual(result['errors'], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_interrupted_temporary_copy_cleanup_is_narrow(self):
        self.store.directory.mkdir()
        temporary = self.store.directory / ('.capture-' + 'a' * 32 + '.tmp')
        temporary.write_bytes(b'old atomic copy')
        unrelated = self.store.directory / 'user-notes.tmp'
        unrelated.write_bytes(b'keep me')
        self.store.start(self.workspace, 'run1')
        self.assertFalse(temporary.exists())
        self.assertEqual(unrelated.read_bytes(), b'keep me')

    def test_oversize_or_excess_state_fails_closed_without_adding_data(self):
        self.store.start(self.workspace, 'run1')
        target = self.store._target(self.workspace, 'run1')
        with target.open('wb') as handle:
            handle.truncate(MAX_RUN_BYTES + 1)
        self.assertEqual(self.store.finish(self.workspace, 'run1')['status'], 'unavailable')
        self.assertEqual(self.store.list(self.workspace)['errors'], 1)
        for index in range(256):
            (self.store.directory / f'foreign-{index}').touch()
        self.assertEqual(self.store.start(self.workspace, 'run2')['status'], 'unavailable')
        self.assertFalse(self.store._target(self.workspace, 'run2').exists())


if __name__ == '__main__':
    unittest.main()
