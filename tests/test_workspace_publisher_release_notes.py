"""Offline release records and explicitly labelled local Git fallback."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workspace_publisher.config import PublisherError
from workspace_publisher.release_notes import (
    MAX_COMMITS, MAX_DATA, MAX_GIT_OUTPUT, MAX_HISTORY, MAX_NOTES, MAX_TITLE,
    RELEASE_FILE, load_notes, record_release,
)


ROOT = Path(__file__).resolve().parents[1]


def entry(version='0.23.2', title='현재 릴리스', notes='- 한국어 변경 내용'):
    return {'version': version, 'title': title, 'notes': notes}


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / RELEASE_FILE
        self.path.parent.mkdir()

    def document(self, **changes):
        return {'schema': 1, 'currentVersion': '0.23.2',
                'releases': [entry(), entry('0.23.1', '이전 릴리스', '- 이전 변경')], **changes}

    def write(self, document=None):
        self.path.write_text(json.dumps(document or self.document(), ensure_ascii=False), encoding='utf-8')

    def test_bundled_notes_load_without_git_or_network_for_zip_and_clone(self):
        self.write()
        with patch('workspace_publisher.release_notes.subprocess.run', side_effect=AssertionError('Git is unnecessary')):
            for with_git in (False, True):
                if with_git:
                    (self.root / '.git').mkdir()
                result = load_notes(self.root, '0.23.2')
                self.assertEqual('bundled', result['source'])
                self.assertEqual(entry(), {key: result[key] for key in ('version', 'title', 'notes')})
                self.assertEqual(['0.23.2', '0.23.1'], [item['version'] for item in result['history']])

    def test_history_sort_is_numeric_and_current_is_always_first(self):
        self.write(self.document(currentVersion='0.23.10', releases=[entry('0.23.2'), entry('0.23.10'), entry('0.23.9')]))
        result = load_notes(self.root, '0.23.10')
        self.assertEqual('0.23.10', result['version'])
        self.assertEqual(['0.23.10', '0.23.9', '0.23.2'], [item['version'] for item in result['history']])

    def test_missing_current_record_or_wrong_version_never_loads_older_defaults(self):
        for document in (self.document(currentVersion='0.23.1'),
                         self.document(releases=[entry('0.23.1')])):
            with self.subTest(document=document), self.assertRaises(PublisherError):
                self.write(document)
                load_notes(self.root, '0.23.2')

    def test_duplicate_fields_and_corrupt_json_are_rejected(self):
        for raw in ('{', '{"schema":1,"schema":1,"currentVersion":"0.23.2","releases":[]}'):
            self.path.write_text(raw, encoding='utf-8')
            with self.subTest(raw=raw), self.assertRaises(PublisherError):
                load_notes(self.root, '0.23.2')

    def test_invalid_records_are_rejected(self):
        cases = [
            self.document(schema=True), self.document(schema=2), self.document(extra='unexpected'),
            self.document(releases=[]), self.document(releases=[entry(), entry()]),
            self.document(releases=[entry(), entry('0.24.0')]),
            self.document(releases=[{**entry(), 'authorEmail': 'private@example.invalid'}]),
            self.document(releases=[entry(notes='')]), self.document(releases=[entry(title='  ')]),
            self.document(releases=[entry(notes='bad\x00text')]),
            self.document(releases=[entry(notes='bad\x1btext')]),
            self.document(releases=[entry('v0.23.2')]),
            self.document(releases=[entry(title=None)]),
        ]
        for document in cases:
            with self.subTest(document=document), self.assertRaises(PublisherError):
                self.write(document)
                load_notes(self.root, '0.23.2')

    def test_input_version_is_validated_before_missing_file_fallback(self):
        for version in ('v0.23.2', '', '../secret', None):
            with self.subTest(version=version), self.assertRaises(PublisherError):
                load_notes(self.root, version)

    def test_utf8_byte_bounds_match_publisher_text_limits(self):
        for item in (entry(title='가' * (MAX_TITLE // 3 + 1)),
                     entry(notes='가' * (MAX_NOTES // 3 + 1))):
            with self.subTest(title_size=len(str(item['title']))), self.assertRaises(PublisherError):
                self.write(self.document(releases=[item]))
                load_notes(self.root, '0.23.2')
        self.write(self.document(releases=[entry(title='a' * MAX_TITLE, notes='a' * MAX_NOTES)]))
        self.assertEqual(MAX_NOTES, len(load_notes(self.root, '0.23.2')['notes']))

    def test_file_and_history_bounds_are_enforced(self):
        self.path.write_bytes(b' ' * (MAX_DATA + 1))
        with self.assertRaises(PublisherError):
            load_notes(self.root, '0.23.2')
        self.write(self.document(releases=[entry()] * (MAX_HISTORY + 1)))
        with self.assertRaises(PublisherError):
            load_notes(self.root, '0.23.2')

    def test_utf8_bom_and_newlines_are_supported(self):
        self.path.write_text(json.dumps(self.document(), ensure_ascii=False), encoding='utf-8-sig')
        self.assertEqual('- 한국어 변경 내용', load_notes(self.root, '0.23.2')['notes'])

    def test_missing_zip_record_does_not_pick_up_parent_git_history(self):
        (self.root / '.git').mkdir()
        extracted = self.root / 'extracted'
        extracted.mkdir()
        with patch('workspace_publisher.release_notes.subprocess.run', side_effect=AssertionError('No Git for ZIP')):
            result = load_notes(extracted, '0.23.2')
        self.assertEqual({'version': '0.23.2', 'title': 'AX Workspace 0.23.2',
                          'notes': '', 'history': [], 'source': 'empty'}, result)

    def test_corrupt_bundle_never_falls_back_to_git(self):
        (self.root / '.git').mkdir()
        self.path.write_text('{', encoding='utf-8')
        with patch('workspace_publisher.release_notes.subprocess.run', side_effect=AssertionError('No fallback')):
            with self.assertRaises(PublisherError):
                load_notes(self.root, '0.23.2')

    def test_git_failure_is_empty_without_exposing_stderr(self):
        (self.root / '.git').mkdir()
        with patch('workspace_publisher.release_notes.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('git', 5, stderr=b'private fixture')):
            result = load_notes(self.root, '0.23.2')
        self.assertEqual('empty', result['source'])
        self.assertEqual('', result['notes'])

    def test_git_fallback_limits_subject_count_and_width(self):
        (self.root / '.git').mkdir()
        def run(args, **kwargs):
            self.assertIn(f'--max-count={MAX_COMMITS}', args)
            self.assertNotIn('--all', args)
            kwargs['stdout'].write(('long subject ' + 'x' * 800 + '\n').encode('utf-8') * (MAX_COMMITS + 2))
            return subprocess.CompletedProcess(args, 0)
        with patch('workspace_publisher.release_notes.subprocess.run', side_effect=run):
            result = load_notes(self.root, '0.23.2')
        self.assertEqual('git', result['source'])
        lines = result['notes'].splitlines()[1:]
        self.assertEqual(MAX_COMMITS, len(lines))
        self.assertTrue(all(len(line) <= 514 for line in lines))

    def test_git_fallback_rejects_oversize_capture(self):
        (self.root / '.git').mkdir()
        def run(args, **kwargs):
            kwargs['stdout'].write(b'x' * (MAX_GIT_OUTPUT + 1))
            return subprocess.CompletedProcess(args, 0)
        with patch('workspace_publisher.release_notes.subprocess.run', side_effect=run):
            self.assertEqual('empty', load_notes(self.root, '0.23.2')['source'])

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    def test_real_local_git_fallback_is_labelled_bounded_and_omits_author(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.root, check=True, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        git('init', '--quiet')
        git('config', 'user.name', 'Private Fixture Author')
        git('config', 'user.email', 'private-fixture@example.invalid')
        git('commit', '--allow-empty', '--no-gpg-sign', '-m', 'Fix release description', '-m', 'Private commit body')
        result = load_notes(self.root, '0.23.2')
        self.assertEqual('git', result['source'])
        self.assertIn('정식 릴리스 설명 아님', result['notes'])
        self.assertIn('Fix release description', result['notes'])
        for private in ('Private Fixture Author', 'private-fixture@example.invalid', 'Private commit body'):
            self.assertNotIn(private, json.dumps(result))

    def test_record_release_preserves_history_and_replaces_current_notes(self):
        self.write()
        record_release(self.root, '0.23.3', '다음 버전', '- 새 변경')
        record_release(self.root, '0.23.3', '수정한 제목', '- 검토한 새 변경')
        result = load_notes(self.root, '0.23.3')
        self.assertEqual('수정한 제목', result['title'])
        self.assertEqual(['0.23.3', '0.23.2', '0.23.1'], [item['version'] for item in result['history']])

    def test_record_cannot_move_current_version_backwards(self):
        self.write()
        original = self.path.read_bytes()
        with self.assertRaises(PublisherError):
            record_release(self.root, '0.23.1', '오래된 버전', '- 변경')
        self.assertEqual(original, self.path.read_bytes())

    def test_record_prunes_oldest_entries_to_history_limit(self):
        self.write(self.document(currentVersion='1.0.29', releases=[entry(f'1.0.{i}') for i in range(MAX_HISTORY)]))
        record_release(self.root, '1.0.30', '현재 버전', '- 새 변경')
        history = load_notes(self.root, '1.0.30')['history']
        self.assertEqual(MAX_HISTORY, len(history))
        self.assertEqual('1.0.30', history[0]['version'])
        self.assertEqual('1.0.1', history[-1]['version'])

    def test_script_writes_reviewed_notes_to_requested_repository(self):
        source = self.root / 'reviewed.txt'
        source.write_text('- 검토한 한국어 릴리스 설명', encoding='utf-8')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/write-workspace-release-notes.py'),
                                 '--repo-root', str(self.root), '--version', '0.23.2',
                                 '--title', '검토한 제목', '--notes-file', str(source)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(0, result.returncode, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual('- 검토한 한국어 릴리스 설명', load_notes(self.root, '0.23.2')['notes'])

    def test_repository_carries_current_and_previous_publisher_releases(self):
        source_version = re.search(r'^WORKSPACE_VERSION = "([^"]+)"',
                                   (ROOT / 'local_app/server.py').read_text(encoding='utf-8-sig'), re.M).group(1)
        result = load_notes(ROOT, source_version)
        self.assertEqual('bundled', result['source'])
        self.assertEqual(source_version, result['history'][0]['version'])
        history = {item['version']: item for item in result['history']}
        self.assertTrue({'0.23.2', '0.23.1', '0.23.0'} <= history.keys())
        self.assertIn('HTTPS 서버 주소, 숫자 프로젝트 ID, 게시 토큰', history['0.23.2']['notes'])
        self.assertIn('glpat-', history['0.23.2']['notes'])
        self.assertNotIn('HTTP 주소', result['notes'])
        self.assertNotIn('프로젝트 경로를 자동', result['notes'])


if __name__ == '__main__':
    unittest.main()
