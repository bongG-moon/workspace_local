"""Publication history and native Releases use an offline GitLab transport."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.test_workspace_publisher_core import CONFIG, VERSION, Registry, release_files
from workspace_publisher.config import PublisherError
from workspace_publisher.core import Publisher
from workspace_publisher.publish import HTTPSClient, Response, endpoint, publish
from workspace_publisher.release_history import MAX_HISTORY_BYTES, HistoryError, _description, merge_history, parse_history


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def entry(version=VERSION, **changes):
    return {'version': version, 'title': '배포 제목', 'notes': '배포 설명',
            'publishedAt': '2026-10-18T01:02:03Z', **changes}


class GitLab(Registry):
    def __init__(self):
        super().__init__()
        self.api = CONFIG['baseUrl'] + '/api/v4/projects/' + CONFIG['projectId']
        self.project = {'id': 42, 'web_url': CONFIG['baseUrl'] + '/team/workspace', 'default_branch': 'main'}
        self.sha = 'b' * 40
        self.release = None
        self.api_status = None
        self.history_status = None
        self.native_create_status = None
        self.cancel_after_channel = None

    def __call__(self, method, url, **kwargs):
        if '/packages/generic/' in url:
            if method == 'PUT' and url.endswith('/release-history.json') and self.history_status:
                self.calls.append((method, url, kwargs))
                return Response(self.history_status, b'PRIVATE SERVER BODY glpat-secret')
            response = super().__call__(method, url, **kwargs)
            if method == 'GET' and url.endswith('/latest.json') and response.status == 200 and self.cancel_after_channel:
                self.cancel_after_channel.set()
            return response
        self.calls.append((method, url, kwargs))
        if self.api_status:
            return Response(self.api_status, b'PRIVATE SERVER BODY glpat-secret')
        if method == 'GET' and (url.startswith(self.api + '/packages?') or url.startswith(self.api + '/releases?')):
            return Response(200, b'[]')
        if method == 'GET' and url == self.api:
            return Response(200, encoded(self.project))
        if method == 'GET' and '/repository/branches/' in url:
            return Response(200, encoded({'commit': {'id': self.sha}}))
        if method == 'GET' and url == self.api + '/releases/v' + VERSION:
            return Response(200, encoded(self.release)) if self.release is not None else Response(404, b'missing')
        if method == 'POST' and url == self.api + '/releases':
            if self.native_create_status:
                return Response(self.native_create_status, b'PRIVATE SERVER BODY glpat-secret')
            self.release = json.loads(kwargs['data'])
            return Response(201, encoded(self.release))
        raise AssertionError((method, url))


class ParserTests(unittest.TestCase):
    def test_official_notes_are_bounded_and_invalid_optional_history_is_omitted(self):
        current = entry('2.0.0')
        rows = [{'version': f'1.0.{i}', 'title': '공식 안내', 'notes': '한' * 32768} for i in range(30)]
        description = _description(current, '배포 패키지', 'https://gitlab.corp/history', rows)
        self.assertLessEqual(len(description.encode('utf-8')), 256 * 1024)
        self.assertIn('1.0.29', description)
        self.assertNotIn('1.0.0 ·', description)
        self.assertIn('사내 배포 날짜 아님', description)
        self.assertNotIn('<details>', _description(current, '배포 패키지', 'https://gitlab.corp/history',
                                                 [{'version': '1.0.0', 'title': 'bad', 'notes': '\x7f'}]))

    def test_client_checks_cancellation_after_each_single_socket_read(self):
        cancel = threading.Event()
        class Body(io.BytesIO):
            status, headers = 200, {}
            def read(self, *_args):
                raise AssertionError('Unbounded buffered read must not be used')
            def read1(self, size):
                cancel.set()
                return io.BytesIO.read(self, min(size, 1))
        class Opener:
            def open(self, *_args, **_kwargs):
                return Body(b'partial slow response')
        with patch('workspace_publisher.publish.build_opener', return_value=Opener()):
            with self.assertRaises(PublisherError):
                HTTPSClient(['https://gitlab.corp'])('GET', 'https://gitlab.corp/file', cancel=cancel)

    def test_strict_schema_duplicate_keys_versions_dates_and_bounds(self):
        good = {'schema': 1, 'releases': [entry()]}
        self.assertEqual({'schema': 2, 'releases': [{**entry(), 'noteSource': 'deployment'}]}, parse_history(encoded(good)))
        for value in ({**good, 'extra': True}, {**good, 'schema': True},
                      {'schema': 1, 'releases': [entry(), entry()]},
                      {'schema': 1, 'releases': [entry(str(i) + '.0.0') for i in range(31)]}):
            with self.subTest(value=str(value)[:70]), self.assertRaises(HistoryError):
                parse_history(encoded(value))
        for changes in ({'version': '01.2.3'}, {'title': 'a' * 301}, {'notes': 'a' * 32769},
                        {'notes': '\x00'}, {'notes': '\x7f'}, {'publishedAt': '2026-10-18T01:02:03.1234567890Z'},
                        {'publishedAt': '2026-02-31T00:00:00Z'},
                        {'publishedAt': '2026-10-18T00:00:00+09:00'}, {'unexpected': True}):
            with self.subTest(changes=str(changes)[:70]), self.assertRaises(HistoryError):
                parse_history(encoded({'schema': 1, 'releases': [entry(**changes)]}))
        for raw in (b'{"schema":1,"schema":1,"releases":[]}', b' ' * (MAX_HISTORY_BYTES + 1),
                    b'{"schema":NaN,"releases":[]}', b'{"schema":1,"releases":[{"notes":"\\ud800"}]}'):
            with self.assertRaises(HistoryError):
                parse_history(raw)
        for date in ('2026-10-18T01:02:03.123456789Z', '2026-10-18T01:02:03+00:00'):
            self.assertEqual(date, parse_history(encoded({'schema': 1, 'releases': [entry(publishedAt=date)]}))['releases'][0]['publishedAt'])

    def test_merge_orders_versions_preserves_dates_and_bounds_unicode_bytes(self):
        history = {'schema': 1, 'releases': [entry('0.9.0'), entry('0.10.0')]}
        merged = merge_history(history, entry('0.10.0', notes='수정', publishedAt='2026-10-19T00:00:00Z'))
        self.assertEqual(['0.10.0', '0.9.0'], [row['version'] for row in merged['releases']])
        self.assertEqual('2026-10-18T01:02:03Z', merged['releases'][0]['publishedAt'])
        self.assertEqual('수정', merged['releases'][0]['notes'])
        self.assertEqual('배포 설명', history['releases'][1]['notes'])
        for i in range(40):
            merged = merge_history(merged, entry(f'1.0.{i}', title='한' * 300, notes='한' * 32768))
        self.assertLessEqual(len(merged['releases']), 30)
        self.assertLessEqual(len(encoded(merged)), MAX_HISTORY_BYTES)
        self.assertEqual('1.0.39', merged['releases'][0]['version'])


class PublicationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publisher history ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = release_files(self.directory)
        self.gitlab = GitLab()
        self.events = []
        self.history_url = endpoint(CONFIG, VERSION, 'release-history.json', channel=True)
        self.channel_url = endpoint(CONFIG, VERSION, 'latest.json', channel=True)

    def publish(self, **kwargs):
        return publish(CONFIG, self.result, 'glpat-secret', transport=self.gitlab,
                       emit=self.events.append, **kwargs)

    def test_new_release_is_created_after_verified_latest_and_retry_has_no_writes(self):
        official = [entry('0.22.0'), entry()]
        official = [{key: row[key] for key in ('version', 'title', 'notes')} for row in official]
        value = self.publish(bundled_history=official)
        self.assertTrue(value['verified'])
        self.assertTrue(value['releaseHistory']['verified'])
        self.assertTrue(value['gitlabRelease']['verified'])
        self.assertEqual([], value['warnings'])
        latest = json.loads(self.gitlab.files[self.channel_url])
        self.assertEqual({'schema', 'channel', 'version', 'publishedAt', 'title', 'notes', 'files'}, set(latest))
        self.assertEqual(3, len(latest['files']))
        history = parse_history(self.gitlab.files[self.history_url])
        self.assertEqual([VERSION], [row['version'] for row in history['releases']])
        release = self.gitlab.release
        self.assertEqual('v' + VERSION, release['tag_name'])
        self.assertEqual('b' * 40, release['ref'])
        self.assertIn('배포 패키지', release['tag_message'])
        self.assertIn('이전 앱 변경 이력 (사내 배포 날짜 아님)', release['description'])
        self.assertIn('### 0.22.0', release['description'])
        self.assertNotIn('### ' + VERSION, release['description'])
        self.assertEqual(4, len(release['assets']['links']))
        calls = self.gitlab.calls
        channel_write = next(i for i, (method, url, _) in enumerate(calls) if method == 'PUT' and url == self.channel_url)
        history_write = next(i for i, (method, url, _) in enumerate(calls) if method == 'PUT' and url == self.history_url)
        self.assertEqual(('GET', self.channel_url), calls[channel_write + 1][:2])
        self.assertLess(channel_write + 1, history_write)
        self.assertEqual(1, sum(method == 'POST' for method, _, _ in calls))
        self.gitlab.calls.clear()
        again = self.publish(bundled_history=official)
        self.assertTrue(again['verified'])
        self.assertEqual([], again['warnings'])
        self.assertFalse(any(method in {'PUT', 'POST', 'DELETE', 'PATCH'} for method, _, _ in self.gitlab.calls))

    def test_missing_history_seeds_only_previous_verified_latest(self):
        self.publish()
        old = json.loads(self.gitlab.files[self.channel_url])
        old.update(version='0.22.0', publishedAt='2026-09-01T02:03:04Z')
        for file in old['files']:
            file['name'] = file['name'].replace(VERSION, '0.22.0')
        self.gitlab.files[self.channel_url] = encoded(old)
        del self.gitlab.files[self.history_url]
        self.publish()
        rows = parse_history(self.gitlab.files[self.history_url])['releases']
        self.assertEqual([VERSION, '0.22.0'], [row['version'] for row in rows])
        self.assertEqual('2026-09-01T02:03:04Z', rows[1]['publishedAt'])

    def test_history_failure_and_same_version_retry_repair_keep_main_success(self):
        self.gitlab.history_status = 403
        value = self.publish()
        self.assertTrue(value['verified'])
        self.assertFalse(value['releaseHistory']['verified'])
        self.assertIn('HTTP 403', ' '.join(value['warnings']))
        published = json.loads(self.gitlab.files[self.channel_url])['publishedAt']
        self.gitlab.history_status = None
        self.gitlab.calls.clear()
        value = self.publish()
        self.assertTrue(value['releaseHistory']['verified'])
        self.assertEqual([self.history_url], [url for method, url, _ in self.gitlab.calls if method == 'PUT'])
        self.assertEqual(published, parse_history(self.gitlab.files[self.history_url])['releases'][0]['publishedAt'])
        self.assertNotIn('glpat-secret', json.dumps(value) + json.dumps(self.events))
        self.assertNotIn('PRIVATE SERVER BODY', json.dumps(value) + json.dumps(self.events))

    def test_existing_history_recovers_previous_deployment_whose_history_write_failed(self):
        self.publish()
        previous = json.loads(self.gitlab.files[self.channel_url])
        previous.update(version='0.22.0', publishedAt='2026-09-01T02:03:04Z')
        for file in previous['files']:
            file['name'] = file['name'].replace(VERSION, '0.22.0')
        self.gitlab.files[self.channel_url] = encoded(previous)
        self.gitlab.files[self.history_url] = encoded({'schema': 1, 'releases': [entry('0.21.0')]})
        value = self.publish()
        self.assertTrue(value['releaseHistory']['verified'])
        rows = parse_history(self.gitlab.files[self.history_url])['releases']
        self.assertEqual([VERSION, '0.22.0', '0.21.0'], [row['version'] for row in rows])
        self.assertEqual('2026-09-01T02:03:04Z', rows[1]['publishedAt'])

    def test_corrupt_history_is_not_overwritten_and_future_entries_are_retained(self):
        self.gitlab.files[self.history_url] = b'private malformed glpat-secret'
        value = self.publish()
        self.assertTrue(value['verified'])
        self.assertFalse(value['releaseHistory']['verified'])
        self.assertFalse(any(method == 'PUT' and url == self.history_url for method, url, _ in self.gitlab.calls))
        self.gitlab.files[self.history_url] = encoded({'schema': 1, 'releases': [entry('0.24.0')]})
        value = self.publish()
        self.assertTrue(value['releaseHistory']['verified'])
        self.assertEqual(['0.24.0', VERSION], [row['version'] for row in parse_history(self.gitlab.files[self.history_url])['releases']])

    def test_concurrent_history_change_aborts_auxiliary_write(self):
        registry = self.gitlab
        count = 0
        def racing(method, url, **kwargs):
            nonlocal count
            if method == 'GET' and url == self.history_url:
                count += 1
                if count == 2:
                    registry.files[url] = encoded({'schema': 1, 'releases': [entry('0.24.0')]})
            return registry(method, url, **kwargs)
        value = publish(CONFIG, self.result, 'glpat-secret', transport=racing)
        self.assertTrue(value['verified'])
        self.assertFalse(value['releaseHistory']['verified'])
        self.assertIn('다른 게시 작업', ' '.join(value['warnings']))
        self.assertFalse(any(method == 'PUT' and url == self.history_url for method, url, _ in registry.calls))

    def test_cancellation_or_auxiliary_deadline_after_channel_retains_success(self):
        cancel = threading.Event()
        self.gitlab.cancel_after_channel = cancel
        value = self.publish(cancel=cancel)
        self.assertTrue(value['verified'])
        self.assertEqual(2, len(value['warnings']))
        self.assertFalse(value['releaseHistory']['verified'])
        self.gitlab.cancel_after_channel = None
        self.gitlab.calls.clear()
        with patch('workspace_publisher.release_history.time.monotonic', side_effect=[0] + [61] * 10):
            value = self.publish()
        self.assertTrue(value['verified'])
        self.assertEqual(2, len(value['warnings']))
        self.assertFalse(any(url == self.history_url for _, url, _ in self.gitlab.calls))

    def test_native_api_token_modes_keep_packages_anonymous(self):
        for kind, header in (('access', 'PRIVATE-TOKEN'), ('job', 'JOB-TOKEN')):
            self.gitlab = GitLab()
            value = self.publish(token_kind=kind)
            self.assertTrue(value['gitlabRelease']['verified'])
            for method, url, kwargs in self.gitlab.calls:
                if method == 'GET' and '/packages/generic/' in url:
                    self.assertFalse(kwargs.get('headers'))
                elif '/packages/generic/' not in url:
                    self.assertEqual('glpat-secret', kwargs['headers'][header])
        self.gitlab = GitLab()
        value = publish(CONFIG, self.result, 'gldt-secret', token_kind='deploy', transport=self.gitlab)
        self.assertTrue(value['verified'])
        self.assertFalse(value['gitlabRelease']['verified'])
        self.assertIn('Deploy Token', ' '.join(value['warnings']))
        self.assertTrue(all('/packages/generic/' in url for _, url, _ in self.gitlab.calls))

    def test_existing_release_difference_is_reported_and_never_overwritten(self):
        self.publish()
        original = deepcopy(self.gitlab.release)
        self.gitlab.release['description'] = '이미 게시한 설명'
        self.gitlab.calls.clear()
        value = self.publish()
        self.assertTrue(value['gitlabRelease']['verified'])
        self.assertTrue(value['gitlabRelease']['existing'])
        self.assertIn('다릅니다', ' '.join(value['warnings']))
        self.assertFalse(any(method != 'GET' for method, _, _ in self.gitlab.calls))
        self.assertEqual(original['ref'], self.gitlab.release['ref'])
        self.assertEqual('이미 게시한 설명', self.gitlab.release['description'])

    def test_native_failure_is_sanitized_and_retry_creates_only_missing_release(self):
        self.gitlab.native_create_status = 403
        value = self.publish()
        self.assertTrue(value['verified'])
        self.assertFalse(value['gitlabRelease']['verified'])
        self.assertIn('HTTP 403', ' '.join(value['warnings']))
        self.assertNotIn('glpat-secret', json.dumps(value))
        self.assertNotIn('PRIVATE SERVER BODY', json.dumps(value))
        self.gitlab.native_create_status = None
        self.gitlab.calls.clear()
        value = self.publish()
        self.assertTrue(value['gitlabRelease']['verified'])
        self.assertFalse(any(method == 'PUT' for method, _, _ in self.gitlab.calls))
        self.assertEqual(1, sum(method == 'POST' for method, _, _ in self.gitlab.calls))

    def test_untrusted_project_url_branch_and_transport_error_never_leak_or_create(self):
        for changes in ({'web_url': 'https://attacker.invalid/team'}, {'web_url': 'https://token:secret@gitlab.corp/team'},
                        {'web_url': CONFIG['baseUrl'] + '/team?token=secret'}, {'id': 99}, {'default_branch': None}):
            self.gitlab = GitLab()
            self.gitlab.project.update(changes)
            value = self.publish()
            self.assertTrue(value['verified'])
            self.assertFalse(value['gitlabRelease']['verified'])
            self.assertFalse(any(method == 'POST' for method, _, _ in self.gitlab.calls))
        self.gitlab = GitLab()
        original = self.gitlab
        def failed(method, url, **kwargs):
            if '/packages/generic/' not in url:
                raise PublisherError('PRIVATE SERVER BODY glpat-secret')
            return original(method, url, **kwargs)
        value = publish(CONFIG, self.result, 'glpat-secret', transport=failed)
        self.assertTrue(value['verified'])
        self.assertNotIn('glpat-secret', json.dumps(value))
        self.assertNotIn('PRIVATE SERVER BODY', json.dumps(value))

    def test_core_passes_only_reviewed_bundled_history_and_missing_notes_do_not_block(self):
        publisher = Publisher(self.directory)
        history = [{'version': '0.22.0', 'title': '정식 안내', 'notes': '이전 설명'}]
        for source, expected in (('bundled', history), ('git', None), ('empty', None)):
            with patch.object(publisher, '_validated_build'), patch.object(publisher, 'release_notes', return_value={'source': source, 'history': history}), patch('workspace_publisher.core.publishing.publish', return_value={'verified': True}) as called:
                publisher.publish(CONFIG, self.result, 'glpat-secret')
                self.assertEqual(expected, called.call_args.kwargs['bundled_history'])
        with patch.object(publisher, '_validated_build'), patch.object(publisher, 'release_notes', side_effect=ValueError()), patch('workspace_publisher.core.publishing.publish', return_value={'verified': True}) as called:
            self.assertTrue(publisher.publish(CONFIG, self.result, 'glpat-secret')['verified'])
            self.assertIsNone(called.call_args.kwargs['bundled_history'])


if __name__ == '__main__':
    unittest.main()
