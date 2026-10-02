"""Atomic Repository Commits API publishing from a manifest-verified Code ZIP."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
import zipfile

from workspace_publisher import source_publish as module
from workspace_publisher.config import PublisherError
from workspace_publisher.publish import Response
from workspace_publisher.source_archive import MANIFEST, manifest_bytes


CONFIG = {'baseUrl': 'https://gitlab.corp:8443/company', 'projectId': '42'}
TOKEN = 'glpat-fixture-memory-only'


def source_files(version='0.23.3', **extra):
    return {'.gitignore': b'build/\ndist/\n', 'README.md': b'Company Workspace source\n',
            'local_app/server.py': f'WORKSPACE_VERSION = "{version}"\n'.encode(),
            'workspace_publisher/config.py': b'CONFIG_SCHEMA = 1\n',
            'assets/font.woff': b'font\x00\xffbinary', **extra}


def write_source(root, files):
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    raw = manifest_bytes(files)
    (root / MANIFEST).write_bytes(raw)
    return {**files, MANIFEST: raw}


class GitLabRepository:
    """Pinned commits, tree pages, last-file guards, and archive readback."""
    def __init__(self, files=None, *, branch='main', empty=False):
        self.branch, self.empty, self.calls, self.posts = branch, empty, [], []
        self.head = None if empty else 'a' * 40
        self.snapshots = {} if empty else {self.head: dict(files or {'README.md': b'Initial GitLab README\n'})}
        self.last = {name: self.head for name in (files or {})}
        self.fail_status, self.bad_archive, self.change_before_write = None, False, False
        self.head_reads = 0

    def response(self, data, status=200, headers=None):
        return Response(status, json.dumps(data).encode() if not isinstance(data, bytes) else data, headers or {})

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        parsed, query = urlsplit(url), parse_qs(urlsplit(url).query)
        prefix = '/company/api/v4/projects/42'
        assert parsed.scheme == 'https' and parsed.netloc == 'gitlab.corp:8443' and parsed.path.startswith(prefix)
        assert kwargs['headers']['PRIVATE-TOKEN'] == TOKEN
        path = parsed.path[len(prefix):]
        if not path:
            return self.response({'id': 42, 'default_branch': None if self.empty else self.branch,
                                  'empty_repo': self.empty, 'archived': False})
        if path.startswith('/repository/branches/'):
            assert unquote(path.removeprefix('/repository/branches/')) == self.branch
            self.head_reads += 1
            if self.change_before_write and self.head_reads == 2:
                self.snapshots['c' * 40] = dict(self.snapshots[self.head])
                self.head = 'c' * 40
            return self.response({'name': self.branch, 'commit': {'id': self.head}}) if self.head else self.response({}, 404)
        if path == '/repository/tree':
            snapshot = self.snapshots[query['ref'][0]]
            rows = {}
            for name, data in snapshot.items():
                rows[name] = {'id': module._blob(data), 'path': name, 'mode': '100644', 'type': 'blob'}
                parts = name.split('/')[:-1]
                for end in range(1, len(parts) + 1):
                    directory = '/'.join(parts[:end])
                    rows[directory] = {'id': 'd' * 40, 'path': directory, 'mode': '040000', 'type': 'tree'}
            page = int(query['page'][0])
            values = sorted(rows.values(), key=lambda row: row['path'])
            return self.response(values[(page-1)*100:page*100], headers={'X-Next-Page': str(page + 1) if len(values) > page*100 else ''})
        if path.startswith('/repository/files/'):
            raw = path.endswith('/raw')
            name = unquote(path[len('/repository/files/'):-4] if raw else path[len('/repository/files/'):])
            snapshot = self.snapshots[query['ref'][0]]
            assert name in snapshot
            if raw:
                return self.response(snapshot[name])
            assert method == 'HEAD'
            return self.response(b'', headers={'X-Gitlab-Last-Commit-Id': self.last.get(name, 'a' * 40)})
        if path == '/repository/commits':
            assert method == 'POST'
            payload = json.loads(kwargs['data'])
            self.posts.append(payload)
            if self.fail_status:
                return self.response(b'SECRET SERVER BODY ' + TOKEN.encode(), self.fail_status)
            assert payload['branch'] == self.branch and payload['force'] is False
            assert payload.get('start_sha') == self.head
            files = dict(self.snapshots[self.head]) if self.head else {}
            commit = hashlib.sha1(str(len(self.snapshots)).encode()).hexdigest()
            for action in payload['actions']:
                name = action['file_path']
                assert action['action'] in {'create', 'update'} and action['encoding'] == 'base64'
                if action['action'] == 'create':
                    assert name not in files
                else:
                    assert name in files
                    assert action['last_commit_id'] == self.last.get(name, 'a' * 40)
                files[name] = base64.b64decode(action['content'], validate=True)
                self.last[name] = commit
            self.snapshots[commit], self.head, self.empty = files, commit, False
            return self.response({'id': commit}, 201)
        if path == '/repository/archive.zip':
            snapshot = self.snapshots[query['sha'][0]]
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in snapshot.items():
                    archive.writestr('project-' + query['sha'][0] + '/' + name,
                                     b'corrupted' if self.bad_archive and name == MANIFEST else data)
            return self.response(buffer.getvalue())
        raise AssertionError((method, path))


class SourcePublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='source publish ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = write_source(self.root, source_files())
        self.events = []

    def publish(self, api, **kwargs):
        return module.publish_source(self.root, CONFIG, TOKEN, transport=api, emit=self.events.append, **kwargs)

    def test_readme_only_project_receives_exact_allowlisted_source_in_one_atomic_commit(self):
        api = GitLabRepository()
        result = self.publish(api)
        self.assertTrue(result['verified'])
        self.assertTrue(result['sourceDownloadReady'])
        self.assertEqual(self.files, api.snapshots[result['commit']])
        self.assertEqual(1, result['updated'])
        self.assertEqual(len(self.files)-1, result['created'])
        self.assertEqual(1, len(api.posts))
        self.assertEqual('a' * 40, api.posts[0]['start_sha'])
        self.assertFalse(any('TOKEN' in str(event) or TOKEN in str(event) for event in self.events))
        self.assertEqual(1, sum('/repository/archive.zip?' in url for _, url, _ in api.calls))
        self.assertFalse(any('/raw?' in url for _, url, _ in api.calls))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_empty_project_starts_main_and_nonstandard_default_branch_is_used(self):
        for api in (GitLabRepository(empty=True), GitLabRepository(branch='releases/internal')):
            with self.subTest(branch=api.branch):
                result = self.publish(api)
                self.assertEqual(api.branch, result['branch'])
                self.assertEqual(self.files, api.snapshots[result['commit']])

    def test_unchanged_source_is_verified_without_a_second_commit(self):
        api = GitLabRepository()
        first = self.publish(api)
        second = self.publish(api)
        self.assertTrue(second['unchanged'])
        self.assertEqual(first['commit'], second['commit'])
        self.assertEqual(1, len(api.posts))

    def test_owned_source_updates_use_last_file_commit_guards_and_preserve_other_files(self):
        previous = source_files('0.23.2')
        old = {**previous, MANIFEST: manifest_bytes(previous), 'internal-notes.txt': b'Keep this private project note'}
        api = GitLabRepository(old)
        result = self.publish(api)
        actions = api.posts[0]['actions']
        self.assertEqual({'local_app/server.py', MANIFEST}, {row['file_path'] for row in actions})
        self.assertTrue(all(row['action'] == 'update' and row['last_commit_id'] == 'a' * 40 for row in actions))
        self.assertEqual(old['internal-notes.txt'], api.snapshots[result['commit']]['internal-notes.txt'])
        self.assertEqual(1, result['preservedRemoteFiles'])
        self.assertFalse(result['sourceDownloadReady'])

    def test_unowned_collision_or_remote_edits_or_deleted_owned_files_prevent_any_commit(self):
        previous = source_files('0.23.2')
        owned = {**previous, MANIFEST: manifest_bytes(previous)}
        cases = [dict(owned, **{'local_app/server.py': b'custom remote edit'}),
                 {name: data for name, data in owned.items() if name != 'local_app/server.py'},
                 {'README.md': b'initial', 'workspace_publisher/config.py': b'unrelated config'},
                 {'README.md': b'initial', 'company-notes.txt': b'unrelated project content'},
                 {'README.md': self.files['README.md'], 'Local_App/company.txt': b'case-sensitive parent'},
                 {'readme.md': b'different case'}]
        for files in cases:
            api = GitLabRepository(files)
            with self.subTest(files=list(files)), self.assertRaises(PublisherError):
                self.publish(api)
            self.assertEqual([], api.posts)

    def test_remote_newer_manifest_or_malformed_manifest_is_never_overwritten(self):
        for raw in (manifest_bytes(source_files('0.24.0')), b'{}', b'{"token":"secret"}'):
            api = GitLabRepository({MANIFEST: raw})
            with self.subTest(raw=raw[:20]), self.assertRaises(PublisherError):
                self.publish(api)
            self.assertEqual([], api.posts)

    def test_changed_branch_before_write_and_guard_rejection_do_not_retry_or_force(self):
        api = GitLabRepository()
        api.change_before_write = True
        with self.assertRaises(PublisherError):
            self.publish(api)
        self.assertEqual([], api.posts)
        for status in (400, 401, 403, 404, 409, 413, 429):
            api = GitLabRepository()
            api.fail_status = status
            with self.subTest(status=status), self.assertRaises(PublisherError) as raised:
                self.publish(api)
            self.assertIn(f'HTTP {status}', str(raised.exception))
            self.assertNotIn(TOKEN, str(raised.exception))
            self.assertNotIn('SECRET SERVER BODY', str(raised.exception))
            self.assertEqual(1, len(api.posts))
            self.assertEqual('a' * 40, api.head)

    def test_archive_readback_must_match_every_source_hash(self):
        api = GitLabRepository()
        api.bad_archive = True
        with self.assertRaisesRegex(PublisherError, 'SHA256'):
            self.publish(api)
        self.assertEqual(1, len(api.posts))

    def test_manifest_identity_cancellation_and_private_files_fail_before_network(self):
        api = GitLabRepository()
        with self.assertRaises(PublisherError):
            self.publish(api, expected_source_id='0' * 64)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(PublisherError):
            self.publish(api, cancel=cancelled)
        self.assertEqual([], api.calls)
        files = source_files(**{'.env': b'API_TOKEN=secret'})
        write_source(self.root, files)
        with self.assertRaises(PublisherError):
            self.publish(api)
        self.assertEqual([], api.calls)

    def test_build_and_publisher_config_remain_local_even_when_next_to_code_zip(self):
        (self.root / 'build/publisher').mkdir(parents=True)
        (self.root / 'build/publisher/config.json').write_text('{"token":"never upload"}')
        api = GitLabRepository()
        result = self.publish(api)
        self.assertEqual(self.files, api.snapshots[result['commit']])

    def test_transient_token_accidentally_embedded_in_allowlisted_source_is_never_uploaded(self):
        write_source(self.root, source_files(**{'example.txt': TOKEN.encode()}))
        api = GitLabRepository()
        with self.assertRaises(PublisherError) as raised:
            self.publish(api)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual([], api.calls)

    def test_package_only_token_kinds_are_rejected_before_network(self):
        api = GitLabRepository()
        for kind, token in (('deploy', 'gldt-fixture'), ('job', 'job-fixture')):
            with self.subTest(kind=kind), self.assertRaisesRegex(PublisherError, 'Access Token'):
                module.publish_source(self.root, CONFIG, token, token_kind=kind, transport=api)
        self.assertEqual([], api.calls)

    def test_tree_pagination_preserves_remote_files_after_the_first_page(self):
        files = {f'company-notes/{i:03}.txt': str(i).encode() for i in range(205)}
        api = GitLabRepository({'README.md': self.files['README.md'], **files})
        result = self.publish(api)
        self.assertEqual(205, result['preservedRemoteFiles'])
        self.assertTrue(any('page=3' in url for _, url, _ in api.calls))
        self.assertTrue(all(api.snapshots[result['commit']][name] == data for name, data in files.items()))


class RepositoryTransportTests(unittest.TestCase):
    def test_queries_are_supported_but_https_project_scope_and_credentials_are_enforced(self):
        client = module.RepositoryClient(CONFIG)
        for url in ('http://gitlab.corp:8443/company/api/v4/projects/42',
                    'https://other.corp/company/api/v4/projects/42',
                    'https://gitlab.corp:8443/company/api/v4/projects/420',
                    'https://gitlab.corp:8443/company/api/v4/projects/42/../43',
                    'https://gitlab.corp:8443/company/api/v4/projects/42/%2e%2e/43',
                    'https://user:secret@gitlab.corp:8443/company/api/v4/projects/42'):
            with self.subTest(url=url), patch.object(module, 'build_opener') as opener, self.assertRaises(ValueError):
                client('GET', url, headers={'PRIVATE-TOKEN': TOKEN})
            opener.assert_not_called()

    def test_authenticated_requests_never_follow_same_or_other_origin_redirects(self):
        client = module.RepositoryClient(CONFIG)
        for target in ('https://gitlab.corp:8443/company/api/v4/projects/42/other', 'https://other.corp/object'):
            def make_opener(handler):
                class Opener:
                    def open(self, request, **kwargs):
                        return handler.redirect_request(request, None, 302, 'Moved', {}, target)
                return Opener()
            with self.subTest(target=target), patch.object(module, 'build_opener', side_effect=make_opener), self.assertRaises(PublisherError):
                client('GET', client.root + '/repository/tree?ref=main', headers={'PRIVATE-TOKEN': TOKEN})


if __name__ == '__main__':
    unittest.main()
