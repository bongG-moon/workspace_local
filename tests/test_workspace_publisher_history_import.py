"""Historical internal GitLab records are imported from bounded mock APIs only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tests.test_workspace_publisher_core import CONFIG, VERSION, release_files
from tests.test_workspace_publisher_release_history import GitLab, encoded, entry
from workspace_publisher.config import PublisherError
from workspace_publisher.history_import import Importer, import_history
from workspace_publisher.publish import Response, endpoint, publish
from workspace_publisher.release_history import HistoryError, merge_history, merge_imported, parse_history

HEADERS = {'PRIVATE-TOKEN': 'glpat-secret', 'Content-Type': 'application/octet-stream'}


def package(identifier, version, name='company-workspace', **changes):
    return {'id': identifier, 'name': name, 'version': version, 'package_type': 'generic',
            'created_at': '2024-01-02T03:04:05Z', **changes}


def native(version, **changes):
    return {'tag_name': 'v' + version, 'name': '사내 제목 ' + version, 'description': '사내에서 수정한 설명',
            'released_at': '2024-02-03T04:05:06.1234567Z', **changes}


def manifest(version, **changes):
    return {'schema': 1, 'channel': 'stable', 'version': version, 'title': '당시 사내 배포',
            'notes': '보존된 당시 설명', 'publishedAt': '2024-03-04T05:06:07.1234567Z',
            'files': [{'name': name, 'size': 1, 'sha256': 'a' * 64} for name in
                      (f'Company-Workspace-{version}-vbs.zip', 'SHA256SUMS.txt', f'Company-Workspace-{version}-exe.zip')],
            **changes}


class HistoricalGitLab(GitLab):
    def __init__(self):
        super().__init__()
        self.packages = []
        self.native = []
        self.package_files = {}
        self.retained = {}
        self.failures = {}

    def keep(self, file_id, version, *, package_id=100, **changes):
        raw = encoded(manifest(version, **changes))
        value = {'id': file_id, 'package_id': package_id, 'file_name': 'latest.json',
                 'size': len(raw), 'file_sha256': hashlib.sha256(raw).hexdigest()}
        self.package_files.setdefault(package_id, []).append(value)
        self.retained[(package_id, file_id)] = raw
        return value

    def __call__(self, method, url, **kwargs):
        path = url.split('?', 1)[0]
        if method == 'GET' and path in self.failures:
            self.calls.append((method, url, kwargs))
            return Response(self.failures[path], b'PRIVATE SERVER BODY glpat-secret')
        if method == 'GET' and '?' in url:
            self.calls.append((method, url, kwargs))
            query = parse_qs(urlsplit(url).query)
            page = int(query['page'][0])
            if path == self.api + '/packages':
                name = query['package_name'][0]
                rows = [value for value in self.packages if name in str(value.get('name', ''))]
            elif path == self.api + '/releases':
                rows = self.native
            elif path.startswith(self.api + '/packages/') and path.endswith('/package_files'):
                package_id = int(path.split('/')[-2])
                rows = self.package_files.get(package_id, [])
            else:
                raise AssertionError(url)
            start = (page - 1) * 30
            return Response(200, encoded(rows[start:start + 30]),
                            {'X-Next-Page': str(page + 1) if start + 30 < len(rows) else ''})
        if method == 'GET' and path.endswith('/download'):
            self.calls.append((method, url, kwargs))
            parts = path.split('/')
            body = self.retained.get((int(parts[-4]), int(parts[-2])))
            return Response(200, body) if body is not None else Response(404, b'not retained')
        return super().__call__(method, url, **kwargs)


class SchemaTests(unittest.TestCase):
    def test_schema1_normalizes_and_schema2_enforces_exact_sources_and_package_shape(self):
        legacy = parse_history(encoded({'schema': 1, 'releases': [entry()]}))
        self.assertEqual(2, legacy['schema'])
        self.assertEqual('deployment', legacy['releases'][0]['noteSource'])
        for source in ('deployment', 'gitlab-release', 'package'):
            row = {**entry(), 'noteSource': source}
            if source == 'package':
                row.update(title='AX Workspace ' + VERSION, notes='')
            value = {'schema': 2, 'releases': [row]}
            self.assertEqual(value, parse_history(encoded(value)))
        valid = {**entry(), 'noteSource': 'deployment'}
        for row in ({**valid, 'noteSource': 'bundled'}, {**valid, 'noteSource': False},
                    {**valid, 'noteSource': 'package'}, {**valid, 'noteSource': 'package', 'notes': ''},
                    {**valid, 'url': 'https://untrusted'}, entry()):
            with self.subTest(row=str(row)), self.assertRaises(HistoryError):
                parse_history(encoded({'schema': 2, 'releases': [row]}))
        with self.assertRaises(HistoryError):
            parse_history(encoded({'schema': 1, 'releases': [valid]}))

    def test_precedence_current_previous_and_native_refresh(self):
        deployed = {**entry('0.20.0', notes='저장된 실제 배포'), 'noteSource': 'deployment'}
        native_old = {**entry('0.19.0', notes='이전 GitLab 설명'), 'noteSource': 'gitlab-release'}
        only_package = {**entry('0.18.0', title='AX Workspace 0.18.0', notes=''), 'noteSource': 'package'}
        history = {'schema': 2, 'releases': [deployed, native_old, only_package]}
        changes = [{**deployed, 'notes': '다른 보존 파일'},
                   {**native_old, 'version': '0.20.0', 'notes': '더 낮은 릴리스 설명'},
                   {**native_old, 'notes': '관리자가 수정한 실제 설명'},
                   {**only_package, 'notes': '검증된 채널 설명', 'noteSource': 'deployment',
                    'publishedAt': '2025-01-01T01:00:00Z'}]
        merged, warnings = merge_imported(history, changes)
        rows = {row['version']: row for row in merged['releases']}
        self.assertEqual('더 낮은 릴리스 설명', rows['0.20.0']['notes'])
        self.assertEqual('관리자가 수정한 실제 설명', rows['0.19.0']['notes'])
        self.assertEqual('검증된 채널 설명', rows['0.18.0']['notes'])
        self.assertEqual('2025-01-01T01:00:00Z', rows['0.18.0']['publishedAt'])
        self.assertTrue(warnings)
        current = merge_history(merged, entry('0.20.0', notes='방금 검증한 최신 채널'))
        self.assertEqual('더 낮은 릴리스 설명', current['releases'][0]['notes'])
        self.assertEqual(deployed['publishedAt'], current['releases'][0]['publishedAt'])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.gitlab = HistoricalGitLab()

    def read(self, **kwargs):
        return import_history(CONFIG, transport=self.gitlab, headers=HEADERS, token_kind='access', **kwargs)

    def channel(self):
        self.gitlab.packages.append(package(100, '0.0.0', 'company-workspace-channel'))

    def test_actual_native_and_retained_notes_override_package_without_bundle_defaults(self):
        self.gitlab.packages = [package(1, '0.10.0'), package(2, '0.11.0'), package(3, '0.12.0')]
        self.gitlab.native = [native('0.11.0'), native('0.12.0', description='릴리스보다 강한 채널 기록 있음')]
        self.channel()
        self.gitlab.keep(501, '0.12.0')
        result = self.read()
        rows = {row['version']: row for row in result['releases']}
        self.assertEqual('릴리스보다 강한 채널 기록 있음', rows['0.12.0']['notes'])
        self.assertEqual('gitlab-release', rows['0.12.0']['noteSource'])
        self.assertEqual(self.gitlab.native[1]['released_at'], rows['0.12.0']['publishedAt'])
        self.assertEqual('사내에서 수정한 설명', rows['0.11.0']['notes'])
        self.assertEqual('gitlab-release', rows['0.11.0']['noteSource'])
        self.assertEqual('', rows['0.10.0']['notes'])
        self.assertEqual('AX Workspace 0.10.0', rows['0.10.0']['title'])
        self.assertEqual('package', rows['0.10.0']['noteSource'])
        self.assertIn('당시 배포 설명을 확인할 수 없습니다', ' '.join(result['warnings']))
        self.assertFalse(any('/packages/generic/' in url for _, url, _ in self.gitlab.calls))

    def test_duplicate_latest_filenames_download_by_exact_file_id(self):
        self.channel()
        self.gitlab.keep(501, '0.12.0')
        self.gitlab.keep(502, '0.11.0', notes='더 오래된 사내 설명')
        result = self.read()
        self.assertEqual(['0.12.0', '0.11.0'], [row['version'] for row in result['releases']])
        urls = [url for _, url, _ in self.gitlab.calls if url.endswith('/download')]
        self.assertEqual([self.gitlab.api + f'/packages/100/package_files/{fid}/download' for fid in (501, 502)], urls)
        self.assertEqual([], result['warnings'])

    def test_unsupported_file_download_keeps_native_and_package_rows_with_warning(self):
        self.gitlab.packages = [package(1, '0.10.0'), package(2, '0.11.0')]
        self.gitlab.native = [native('0.11.0')]
        self.channel()
        self.gitlab.keep(501, '0.10.0')
        self.gitlab.failures[self.gitlab.api + '/packages/100/package_files/501/download'] = 404
        result = self.read()
        self.assertEqual(['gitlab-release', 'package'], [row['noteSource'] for row in result['releases']])
        self.assertIn('HTTP 404', ' '.join(result['warnings']))
        self.assertNotIn('PRIVATE SERVER BODY', json.dumps(result))
        self.assertNotIn('glpat-secret', json.dumps(result))

    def test_hash_size_parent_project_and_manifest_mismatches_are_rejected(self):
        changes = ({'file_sha256': 'b' * 64}, {'size': 1}, {'package_id': 999}, {'project_id': 99})
        for changeset in changes:
            self.gitlab = HistoricalGitLab()
            self.channel()
            self.gitlab.keep(501, '0.12.0').update(changeset)
            value = self.read()
            self.assertEqual([], value['releases'])
            self.assertTrue(value['warnings'])
        self.gitlab = HistoricalGitLab()
        self.channel()
        self.gitlab.keep(501, '0.12.0', projectId=99)
        self.assertEqual([], self.read()['releases'])
        self.gitlab = HistoricalGitLab()
        self.gitlab.project['id'] = 99
        self.gitlab.packages = [package(1, '0.10.0')]
        self.assertEqual([], self.read()['releases'])
        self.assertEqual(1, len(self.gitlab.calls))

    def test_exact_package_names_stable_tags_and_bounded_actual_descriptions(self):
        self.gitlab.packages = [package(1, '0.10.0'), package(2, '0.11.0', 'company-workspace-unrelated'),
                                package(3, '0.12.0', package_type='npm'), package(4, '0.13.0', project_id=99)]
        self.gitlab.native = [native('0.14.0'), native('0.15.0', tag_name='0.15.0'),
                              native('0.16.0', tag_name='v0.16.0-rc1'), native('0.17.0', upcoming_release=True),
                              native('0.18.0', description='x' * 32769), native('0.19.0', project_id=99)]
        result = self.read()
        self.assertEqual(['0.15.0', '0.14.0', '0.10.0'], [row['version'] for row in result['releases']])
        self.assertTrue(result['warnings'])

    def test_ambiguous_native_tags_keep_first_and_deployment_conflicts_do_not_replace_existing(self):
        self.gitlab.native = [native('0.11.0', description='첫 설명'), native('0.11.0', tag_name='0.11.0', description='다른 설명')]
        result = self.read()
        self.assertEqual('첫 설명', result['releases'][0]['notes'])
        self.assertTrue(result['warnings'])
        self.channel()
        self.gitlab.keep(501, '0.11.0', notes='서로 다른 보존 설명1')
        self.gitlab.keep(502, '0.11.0', notes='서로 다른 보존 설명2')
        result = self.read()
        existing = {'schema': 2, 'releases': [{**entry('0.11.0', notes='저장된 실제 설명'), 'noteSource': 'deployment'}]}
        merged, _ = merge_imported(existing, result['releases'])
        self.assertEqual('첫 설명', merged['releases'][0]['notes'])

    def test_same_description_different_dates_is_not_a_conflict(self):
        self.channel()
        self.gitlab.keep(501, '0.12.0', publishedAt='2024-03-04T05:06:07Z')
        self.gitlab.keep(502, '0.12.0', publishedAt='2024-03-05T05:06:07Z')
        result = self.read()
        self.assertEqual(1, len(result['releases']))
        self.assertEqual([], result['warnings'])

    def test_edited_gitlab_description_wins_over_saved_deployment(self):
        old = {'schema': 2, 'releases': [{**entry('0.11.0', notes='old'), 'noteSource': 'deployment'}]}
        self.gitlab.native = [native('0.11.0', description='운영자가 고친 설명')]
        merged, warnings = merge_imported(old, self.read()['releases'])
        self.assertEqual('운영자가 고친 설명', merged['releases'][0]['notes'])
        from workspace_publisher.release_history import merge_history
        merged = merge_history(merged, entry('0.11.0', notes='old bundled default'))
        self.assertEqual('운영자가 고친 설명', merged['releases'][0]['notes'])
        self.assertEqual([], warnings)

    def test_excluded_record_warning_identifies_version_without_echoing_secrets(self):
        self.gitlab.native = [native('0.11.0', description='x' * 32769)]
        warnings = ' '.join(self.read()['warnings'])
        self.assertIn('0.11.0', warnings)
        self.assertIn('크기', warnings)
        self.assertNotIn('x' * 100, warnings)

    def test_deploy_token_skips_metadata_and_access_job_use_only_expected_header(self):
        result = import_history(CONFIG, transport=self.gitlab, headers={'DEPLOY-TOKEN': 'gldt-secret'}, token_kind='deploy')
        self.assertEqual([], self.gitlab.calls)
        self.assertTrue(result['warnings'])
        for kind, header in (('access', 'PRIVATE-TOKEN'), ('job', 'JOB-TOKEN')):
            self.gitlab = HistoricalGitLab()
            import_history(CONFIG, transport=self.gitlab, headers={header: 'credential'}, token_kind=kind)
            self.assertTrue(self.gitlab.calls)
            for method, url, kwargs in self.gitlab.calls:
                self.assertEqual('GET', method)
                self.assertTrue(url.startswith(self.gitlab.api))
                self.assertEqual({header: 'credential'}, kwargs['headers'])

    def test_reflected_token_is_not_persisted_from_native_or_retained_descriptions(self):
        self.gitlab.native = [native('0.11.0', description='error contains glpat-secret')]
        self.channel()
        self.gitlab.keep(501, '0.12.0', notes='error contains glpat-secret')
        result = self.read()
        self.assertEqual([], result['releases'])
        self.assertNotIn('glpat-secret', json.dumps(result))
        self.assertTrue(result['warnings'])

    def test_pagination_and_download_limits_keep_partial_rows(self):
        self.gitlab.packages = [package(i + 1, f'0.10.{i}') for i in range(95)]
        result = self.read()
        self.assertEqual(90, len(result['releases']))
        queries = [url for _, url, _ in self.gitlab.calls if '/packages?' in url and 'package_name=company-workspace&' in url]
        self.assertEqual(3, len(queries))
        self.assertIn('조회 한도', ' '.join(result['warnings']))
        self.gitlab = HistoricalGitLab()
        self.channel()
        for i in range(40):
            self.gitlab.keep(500 + i, f'0.12.{i}')
        result = self.read()
        self.assertEqual(30, len(result['releases']))
        self.assertEqual(30, sum(url.endswith('/download') for _, url, _ in self.gitlab.calls))
        self.assertIn('조회 한도', ' '.join(result['warnings']))

    def test_import_time_limit_keeps_package_rows_and_does_not_consume_outer_deadline(self):
        self.gitlab.packages = [package(1, '0.10.0')]
        clock = [0]
        registry = self.gitlab
        def transport(method, url, **kwargs):
            response = registry(method, url, **kwargs)
            if '/releases?' in url:
                clock[0] = 21
            return response
        with patch('workspace_publisher.history_import.time.monotonic', side_effect=lambda: clock[0]):
            result = import_history(CONFIG, transport=transport, headers=HEADERS, token_kind='access')
        self.assertEqual(['0.10.0'], [row['version'] for row in result['releases']])
        self.assertIn('조회 한도', ' '.join(result['warnings']))
        self.assertFalse(any('package_name=company-workspace-channel' in url for _, url, _ in registry.calls))

    def test_byte_budget_caps_requests_and_keeps_partial_evidence(self):
        self.gitlab.packages = [package(1, '0.10.0')]
        self.gitlab.native = [native('0.11.0', description='x' * 2000)]
        with patch('workspace_publisher.history_import.MAX_TOTAL_BYTES', 1000):
            result = self.read()
        self.assertEqual(['0.10.0'], [row['version'] for row in result['releases']])
        self.assertTrue(result['warnings'])
        self.assertTrue(all(kwargs['max_bytes'] <= 1000 for _, _, kwargs in self.gitlab.calls))

    def test_failed_oversized_downloads_consume_aggregate_budget(self):
        self.channel()
        for index in range(10):
            value = self.gitlab.keep(500 + index, f'0.12.{index}')
            value['size'] = 256 * 1024
        registry, requested = self.gitlab, []
        def oversized(method, url, **kwargs):
            if url.endswith('/download'):
                requested.append(kwargs['max_bytes'] + 1)
                raise PublisherError('PRIVATE SERVER BODY glpat-secret')
            return registry(method, url, **kwargs)
        reader = Importer(CONFIG, oversized, HEADERS, None)
        reader.bytes = 6 * 1024 * 1024
        reader.phase(lambda: reader.project())
        reader.phase(reader.retained_rows)
        self.assertLessEqual(reader.bytes, 8 * 1024 * 1024)
        self.assertLessEqual(sum(requested), 2 * 1024 * 1024)
        self.assertLess(len(requested), 10)
        self.assertIn('조회 한도', ' '.join(reader.result()['warnings']))
        self.assertNotIn('glpat-secret', json.dumps(reader.result()))

    def test_full_publish_uses_internal_notes_and_repairs_schema1_without_inventing_package_notes(self):
        with tempfile.TemporaryDirectory(prefix='history import publication ') as temporary:
            result = release_files(Path(temporary))
            history_url = endpoint(CONFIG, VERSION, 'release-history.json', channel=True)
            self.gitlab.files[history_url] = encoded({'schema': 1, 'releases': [entry('0.9.0', notes='원래 실제 이력')]})
            self.gitlab.packages = [package(1, '0.10.0'), package(2, '0.11.0')]
            self.gitlab.native = [native('0.11.0')]
            bundled = [{'version': '0.10.0', 'title': '공개 기본 제목', 'notes': '공개 기본 설명'},
                       {'version': '0.11.0', 'title': '공개 기본 제목', 'notes': '공개 기본 설명'}]
            value = publish(CONFIG, result, 'glpat-secret', transport=self.gitlab, bundled_history=bundled)
            self.assertTrue(value['verified'])
            self.assertTrue(value['releaseHistory']['verified'])
            stored = json.loads(self.gitlab.files[history_url])
            self.assertEqual(2, stored['schema'])
            rows = {row['version']: row for row in stored['releases']}
            self.assertEqual('사내에서 수정한 설명', rows['0.11.0']['notes'])
            self.assertEqual('', rows['0.10.0']['notes'])
            self.assertEqual('원래 실제 이력', rows['0.9.0']['notes'])
            self.assertNotIn('공개 기본 설명', self.gitlab.files[history_url].decode())
            self.gitlab.native[0]['description'] = '관리자가 다시 수정한 사내 설명'
            self.gitlab.calls.clear()
            again = publish(CONFIG, result, 'glpat-secret', transport=self.gitlab, bundled_history=bundled)
            self.assertTrue(again['verified'])
            self.assertEqual([history_url], [url for method, url, _ in self.gitlab.calls if method == 'PUT'])
            self.assertFalse(any(method == 'POST' for method, _, _ in self.gitlab.calls))
            self.assertEqual('관리자가 다시 수정한 사내 설명', parse_history(self.gitlab.files[history_url])['releases'][1]['notes'])

    def test_malformed_existing_history_blocks_import_and_write_but_main_still_succeeds(self):
        with tempfile.TemporaryDirectory(prefix='history corrupt publication ') as temporary:
            result = release_files(Path(temporary))
            history_url = endpoint(CONFIG, VERSION, 'release-history.json', channel=True)
            self.gitlab.files[history_url] = b'{bad existing history}'
            value = publish(CONFIG, result, 'glpat-secret', transport=self.gitlab)
            self.assertTrue(value['verified'])
            self.assertFalse(value['releaseHistory']['verified'])
            self.assertEqual(b'{bad existing history}', self.gitlab.files[history_url])
            self.assertFalse(any('?' in url for _, url, _ in self.gitlab.calls))


if __name__ == '__main__':
    unittest.main()
