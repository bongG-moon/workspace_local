"""GitLab token headers and actionable errors, with an offline package registry."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.update_source import source_from_config
from tests.test_workspace_publisher_core import CONFIG, Registry, release_files
from workspace_publisher.config import DEFAULTS, PublisherError, normalize, runtime_config
from workspace_publisher.publish import HTTPSClient, Response, connection, publish, resolve_token_kind


class TokenConfigTests(unittest.TestCase):
    def test_auto_default_and_all_explicit_token_types_preserve_runtime_identity(self):
        self.assertEqual('auto', DEFAULTS['tokenKind'])
        baseline = runtime_config(CONFIG)
        for kind in ('auto', 'access', 'deploy', 'job'):
            with self.subTest(kind=kind):
                value = normalize({**CONFIG, 'tokenKind': kind, 'notesVersion': '0.23.3'})
                self.assertEqual(kind, value['tokenKind'])
                self.assertEqual(baseline, runtime_config(value))
                self.assertNotIn('notesVersion', runtime_config(value))
        with self.assertRaises(PublisherError):
            normalize({**CONFIG, 'tokenKind': 'bearer'})
        with self.assertRaises(PublisherError):
            normalize({**CONFIG, 'notesVersion': 'latest'})

    def test_target_stays_https_numeric_id_and_never_accepts_http_opt_in(self):
        for changes in ({'baseUrl': 'http://gitlab.corp'}, {'projectId': 'group/repo'},
                        {'repositoryUrl': 'https://gitlab.corp/group/repo.git'}, {'allowInsecureHttp': True}):
            with self.subTest(changes=changes), self.assertRaises(PublisherError):
                normalize({**CONFIG, **changes})
        source = runtime_config(CONFIG)
        for changes in ({'baseUrl': 'http://gitlab.corp'}, {'projectId': 'group/repo'}, {'allowInsecureHttp': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                source_from_config({**source, **changes})

    def test_invalid_credentials_never_echo_the_input(self):
        for token in ('', 'glpat-SECRET\n', 'glpat-SECRET token', 'glpat-SECRET\x00', 'x' * 4097, None):
            with self.subTest(token_type=type(token).__name__), self.assertRaises(PublisherError) as raised:
                resolve_token_kind(token)
            self.assertNotIn('SECRET', str(raised.exception))


class PublishTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='publisher tokens ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = release_files(self.directory)
        self.events = []

    def test_access_deploy_job_headers_match_token_kind_and_all_reads_remain_anonymous(self):
        cases = [('auto', 'glpat-fixture-secret', 'PRIVATE-TOKEN'),
                 ('deploy', 'glpat-fixture-secret', 'PRIVATE-TOKEN'),
                 ('access', 'custom-prefix-fixture-secret', 'PRIVATE-TOKEN'),
                 ('auto', 'gldt-fixture-secret', 'DEPLOY-TOKEN'),
                 ('deploy', 'gldt-fixture-secret', 'DEPLOY-TOKEN'),
                 ('job', 'ci-fixture-secret', 'JOB-TOKEN')]
        for kind, token, expected_header in cases:
            registry = Registry()
            with self.subTest(kind=kind, expected_header=expected_header):
                result = publish(CONFIG, self.result, token, token_kind=kind,
                                 transport=registry, emit=self.events.append)
                self.assertTrue(result['verified'])
                writes = [kwargs for method, _, kwargs in registry.calls if method == 'PUT']
                self.assertEqual(4, len(writes))
                for values in writes:
                    self.assertEqual({expected_header: token, 'Content-Type': 'application/octet-stream'}, values['headers'])
                for method, _, values in registry.calls:
                    if method == 'GET':
                        self.assertFalse(values.get('headers'))
                self.assertNotIn(token, json.dumps(self.events))
                self.assertFalse(any(token.encode() in path.read_bytes() for path in self.directory.iterdir()))

    def test_default_publish_kind_detects_access_token_without_configuration_change(self):
        registry = Registry()
        publish(CONFIG, self.result, 'glpat-fixture-secret', transport=registry)
        self.assertTrue(all('PRIVATE-TOKEN' in values['headers'] for method, _, values in registry.calls if method == 'PUT'))

    def failing_registry(self, status, *, channel=False):
        class FailureRegistry(Registry):
            def __call__(self, method, url, **kwargs):
                if method == 'PUT' and (not channel or url.endswith('/latest.json')):
                    self.calls.append((method, url, kwargs))
                    return Response(status, b'PRIVATE SERVER BODY glpat-fixture-secret')
                return super().__call__(method, url, **kwargs)
        return FailureRegistry()

    def test_upload_http_errors_identify_status_scope_and_project_without_secrets(self):
        expected_reasons = {401: '인증이 거절', 403: '게시 권한이 거절', 404: '숫자 프로젝트 ID'}
        for channel in (False, True):
            for status, reason in expected_reasons.items():
                for kind, token, scope in [('access', 'glpat-fixture-secret', 'api 범위'),
                                           ('deploy', 'gldt-fixture-secret', 'write_package_registry'),
                                           ('job', 'ci-fixture-secret', 'Job Token 허용 목록')]:
                    registry = self.failing_registry(status, channel=channel)
                    with self.subTest(channel=channel, status=status, kind=kind), self.assertRaises(PublisherError) as raised:
                        publish(CONFIG, self.result, token, token_kind=kind, transport=registry, emit=self.events.append)
                    message = str(raised.exception)
                    for expected in (f'HTTP {status}', reason, scope, 'write_registry는 컨테이너'):
                        self.assertIn(expected, message)
                    self.assertNotIn(token, message)
                    self.assertNotIn('PRIVATE SERVER BODY', message)
                    self.assertNotIn('만 정리', message)
                    if not channel:
                        self.assertFalse(any(url.endswith('/latest.json') for _, url, _ in registry.calls))

    def test_read_failures_include_status_and_explain_anonymous_access(self):
        for status in (401, 403):
            registry = lambda *args, **kwargs: Response(status, b'PRIVATE SERVER BODY glpat-fixture-secret')
            with self.subTest(status=status), self.assertRaises(PublisherError) as raised:
                connection(CONFIG, transport=registry)
            self.assertIn(f'HTTP {status}', str(raised.exception))
            self.assertIn('읽기', str(raised.exception))
            self.assertNotIn('PRIVATE SERVER BODY', str(raised.exception))
        value = connection(CONFIG, transport=lambda *args, **kwargs: Response(404, b'private'))
        self.assertFalse(value['published'])
        self.assertIn('HTTP 404', value['message'])
        self.assertIn('숫자 프로젝트 ID', value['message'])

    def test_access_tokens_never_follow_redirects_even_to_the_same_https_origin(self):
        class Body(io.BytesIO):
            status, headers = 200, {}
        for method in ('GET', 'PUT'):
            for target in ('https://gitlab.corp/other', 'https://storage.corp/object?signature=x'):
                def make_opener(handler):
                    class Opener:
                        def open(self, request, **kwargs):
                            handler.redirect_request(request, None, 302, 'Moved', {}, target)
                            return Body(b'ok')
                    return Opener()
                with self.subTest(method=method, target=target), patch('workspace_publisher.publish.build_opener', side_effect=make_opener):
                    with self.assertRaises(PublisherError):
                        HTTPSClient(['https://gitlab.corp', 'https://storage.corp'])(method,
                            'https://gitlab.corp/api/v4/projects/42/packages/generic/a',
                            headers={'PRIVATE-TOKEN': 'glpat-fixture-secret'})


if __name__ == '__main__':
    unittest.main()
