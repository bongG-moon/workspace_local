"""GitLab update contracts, using local fixtures only (no company network)."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request
import zipfile

from local_app import app_updates as updates
from local_app import update_source as sources
from tests.test_workspace_app_updates import PREFIX, VERSION, package, release


def config(**changes):
    value = {'schema': 1, 'provider': 'gitlab', 'baseUrl': 'https://gitlab.example:8443/company',
             'projectId': '123', 'packageName': 'company-workspace',
             'channelPackage': 'company-workspace-channel', 'channelVersion': '0.0.0',
             'allowedDownloadOrigins': []}
    value.update(changes)
    return value


def gitlab_package(source, version=VERSION):
    return package(version, extra=[(PREFIX + sources.CONFIG_NAME, json.dumps(source.config).encode())])


class GitLabTransport:
    def __init__(self, source, *, data=None, version=VERSION):
        self.source, self.calls, self.failure = source, [], None
        self.data = gitlab_package(source, version) if data is None else data
        name = f'Company-Workspace-{version}-vbs.zip'
        sums = f'{hashlib.sha256(self.data).hexdigest()}  {name}\n'.encode()
        self.assets = {source.asset_url(version, name): self.data,
                       source.asset_url(version, 'SHA256SUMS.txt'): sums}
        self.metadata = {'schema': 1, 'channel': 'stable', 'version': version,
                         'publishedAt': '2026-10-02T10:00:00Z', 'title': '사내 업데이트',
                         'notes': '변경 내용\n<script>never execute</script>',
                         'files': [{'name': url.rsplit('/', 1)[-1], 'size': len(value),
                                    'sha256': hashlib.sha256(value).hexdigest()}
                                   for url, value in self.assets.items()]}

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if self.failure:
            raise self.failure
        if url == self.source.latest_url:
            return json.dumps(self.metadata).encode()
        data = self.assets[url]
        if kwargs.get('progress'):
            kwargs['progress'](len(data), len(data))
        return data


class SourceSettingsTests(unittest.TestCase):
    def test_absent_settings_use_github_without_writing_any_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = sources.load_source(directory)
            self.assertEqual('github', source.provider)
            self.assertEqual(updates.API_URL, source.latest_url)
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_custom_port_subpath_and_origins_are_canonical_and_return_copies(self):
        source = sources.source_from_config(config(baseUrl='https://GitLab.Example:8443/company/',
            allowedDownloadOrigins=['https://STORE.example:443/', 'https://store.example']))
        self.assertEqual('https://gitlab.example:8443/company', source.base_url)
        self.assertEqual(['https://store.example'], source.config['allowedDownloadOrigins'])
        self.assertEqual('https://gitlab.example:8443/company/api/v4/projects/123/packages/generic/'
                         'company-workspace-channel/0.0.0/latest.json', source.latest_url)
        self.assertEqual(source.identity, sources.source_from_config(source.config).identity)
        exported = source.config
        exported['projectId'] = '9'
        exported['allowedDownloadOrigins'].append('https://evil.example')
        self.assertEqual('123', source.project_id)
        self.assertEqual(('https://store.example',), source.allowed_download_origins)

    def test_unknown_fields_credentials_plain_http_and_path_injection_are_rejected(self):
        changes = [{'schema': True}, {'schema': 2}, {'provider': 'github'}, {'projectId': 1},
                   {'projectId': '1/../../2'}, {'projectId': '001'}, {'token': 'secret'},
                   {'headers': {'Authorization': 'secret'}}, {'packageName': 'other'},
                   {'channelPackage': 'other'}, {'channelVersion': 'latest'},
                   {'baseUrl': 'http://gitlab.example'}, {'baseUrl': 'https://user:secret@gitlab.example'},
                   {'baseUrl': 'https://gitlab.example/path?token=secret'},
                   {'baseUrl': 'https://gitlab.example/#fragment'}, {'baseUrl': 'https://gitlab.example/a/../b'},
                   {'baseUrl': 'https://gitlab.example/%2e%2e'}, {'baseUrl': 'https://gitlab.example:0'},
                   {'baseUrl': 'https://gitlab.example:65536'}, {'baseUrl': 'https://gitlab.example/\\evil'},
                   {'allowedDownloadOrigins': ['https://store.example/path']},
                   {'allowedDownloadOrigins': ['https://user@store.example']},
                   {'allowedDownloadOrigins': ['https://store.example?token=secret']},
                   {'allowedDownloadOrigins': ['http://store.example']}, {'allowedDownloadOrigins': 'https://store.example'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                sources.source_from_config(config(**change))

    def test_settings_file_errors_fail_closed_and_do_not_contact_github(self):
        samples = [b'{', b'{}', b'[]', b'\xff', b' ' * (sources.MAX_CONFIG + 1),
                   json.dumps(config(token='secret')).encode(),
                   json.dumps(config()).replace('"schema": 1', '"schema": 2, "schema": 1').encode()]
        for data in samples:
            with self.subTest(data=data[:30]), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / sources.CONFIG_NAME
                path.write_bytes(data)
                source = sources.load_source(directory)
                transport = Mock()
                manager = updates.UpdateManager(Path(directory) / 'state', '0.21.10', source=source, transport=transport)
                try:
                    manager.start()
                    manager.check(manual=True)
                    self.assertEqual('error', manager.snapshot()['status'])
                    self.assertEqual(sources.SOURCE_ERROR, manager.snapshot()['error'])
                    self.assertFalse(manager.snapshot()['canInstall'])
                    self.assertEqual({'provider': 'invalid', 'label': '업데이트 설정 오류'}, manager.snapshot()['source'])
                    self.assertIsNone(manager._scheduler)
                    transport.assert_not_called()
                    self.assertEqual(data, path.read_bytes())
                finally:
                    manager.close()

    def test_valid_utf8_bom_settings_load_and_missing_optional_origins_is_equivalent(self):
        value = config()
        del value['allowedDownloadOrigins']
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / sources.CONFIG_NAME).write_bytes(b'\xef\xbb\xbf' + json.dumps(value).encode())
            self.assertEqual(sources.source_from_config(config()).identity, sources.load_source(directory).identity)

    def test_source_identity_changes_when_project_base_or_allowed_origin_changes(self):
        original = sources.source_from_config(config())
        for change in [{'projectId': '124'}, {'baseUrl': 'https://other.example/company'},
                       {'allowedDownloadOrigins': ['https://store.example']}]:
            self.assertNotEqual(original.identity, sources.source_from_config(config(**change)).identity)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.source = sources.source_from_config(config())
        self.manifest = GitLabTransport(self.source).metadata

    def test_manifest_is_normalized_without_exposing_foreign_links_or_url_fields(self):
        result = updates.parse_manifest(self.manifest, self.source)
        self.assertEqual('v' + VERSION, result['tag_name'])
        self.assertEqual('', result['html_url'])
        self.assertIn('<script>', result['body'])  # The UI renders this as plain text.
        self.assertEqual(self.source.asset_url(VERSION, f'Company-Workspace-{VERSION}-vbs.zip'),
                         result['assets'][0]['browser_download_url'])

    def test_optional_exe_is_validated_and_does_not_change_the_common_install_package(self):
        self.manifest['files'].append({'name': f'Company-Workspace-{VERSION}-exe.zip', 'size': 5, 'sha256': 'a' * 64})
        parsed = updates.parse_manifest(self.manifest, self.source)
        self.assertEqual(2, len(parsed['assets']))
        self.assertTrue(parsed['assets'][0]['name'].endswith('-vbs.zip'))

    def test_rejects_nonstable_versions_unknown_fields_sources_and_invalid_timestamps(self):
        mutations = [('schema', True), ('schema', 2), ('channel', 'beta'), ('version', 'v0.22.0'),
                     ('version', '0.22.0-rc.1'), ('version', '../0.22.0'), ('title', None), ('notes', {}),
                     ('publishedAt', '2026-02-31T10:00:00Z'), ('publishedAt', '2026-10-02T10:00:00+09:00'),
                     ('url', 'https://evil.example'), ('source', config(projectId='9'))]
        for key, value in mutations:
            payload = copy.deepcopy(self.manifest)
            payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(updates.UpdateError):
                updates.parse_manifest(payload, self.source)
        github, _ = release()
        with self.assertRaises(updates.UpdateError):
            updates.parse_manifest(github, self.source)

    def test_rejects_file_url_hash_size_names_missing_sums_and_duplicates(self):
        for key, value in [('name', '../payload.zip'), ('name', 'Company-Workspace-0.22.1-vbs.zip'),
                           ('size', True), ('size', 0), ('size', updates.MAX_ARCHIVE + 1),
                           ('sha256', 'A' * 64), ('sha256', 'a' * 63), ('url', 'https://evil.example')]:
            payload = copy.deepcopy(self.manifest)
            payload['files'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(updates.UpdateError):
                updates.parse_manifest(payload, self.source)
        for files in [self.manifest['files'][:1], self.manifest['files'] * 2,
                      [self.manifest['files'][0], self.manifest['files'][0]]]:
            with self.subTest(files=files), self.assertRaises(updates.UpdateError):
                updates.parse_manifest(dict(self.manifest, files=files), self.source)

    def test_plain_text_is_bounded_and_utc_fractional_date_is_accepted(self):
        self.manifest.update(title='가' * 500, notes='\x00가' * 40000, publishedAt='2026-10-02T10:00:00.123+00:00')
        parsed = updates.parse_manifest(self.manifest, self.source)
        self.assertLessEqual(len(parsed['name'].encode()), 300)
        self.assertLessEqual(len(parsed['body'].encode()), updates.MAX_NOTES)
        self.assertNotIn('\x00', parsed['body'])


class GitLabDownloadTests(unittest.TestCase):
    def setUp(self):
        self.source = sources.source_from_config(config(allowedDownloadOrigins=['https://store.example:9443']))

    def test_redirects_require_explicit_exact_https_origins_for_metadata_and_assets(self):
        for initial in [self.source.latest_url, self.source.asset_url(VERSION, 'SHA256SUMS.txt')]:
            self.assertEqual(initial, self.source.validate_download(initial, initial))
            signed = 'https://store.example:9443/object?signature=temporary'
            self.assertEqual(signed, self.source.validate_download(signed, initial))
            for url in ['http://store.example:9443/object', 'https://store.example/object',
                        'https://store.example.evil:9443/object', 'https://user@store.example:9443/object',
                        'https://store.example:9443/object#part', 'https://gitlab.example:8443/users/sign_in',
                        'https://github.com/bongG-moon/workspace_local/releases/latest']:
                with self.subTest(initial=initial, url=url), self.assertRaises(ValueError):
                    self.source.validate_download(url, initial)

    def test_initial_requests_only_use_derived_configured_package_urls(self):
        for url in [self.source.latest_url + '?token=abc', self.source.latest_url.replace('/123/', '/124/'),
                    self.source.latest_url.replace('latest.json', 'other.json'),
                    self.source.asset_url(VERSION, 'SHA256SUMS.txt').replace('/0.22.0/', '/../../')]:
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                updates.source_download(self.source, url, limit=100, deadline=time.monotonic() + 10,
                                        cancel=threading.Event())

    def test_download_is_anonymous_and_final_redirect_is_checked_before_reading(self):
        for final, good in [('https://store.example:9443/object?signed=value', True),
                            ('https://evil.example/object', False)]:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.status, response.headers = 200, {'Content-Length': '3'}
            response.geturl.return_value = final
            response.read1 = Mock(side_effect=io.BytesIO(b'abc').read)
            opener = Mock()
            opener.open.return_value = response
            with patch.object(updates, 'build_opener', return_value=opener) as build:
                call = lambda: updates.source_download(self.source, self.source.latest_url, limit=3,
                    deadline=time.monotonic() + 10, cancel=threading.Event())
                if good:
                    self.assertEqual(b'abc', call())
                else:
                    with self.assertRaises(updates.UpdateError):
                        call()
                    response.read1.assert_not_called()
                request = opener.open.call_args.args[0]
                self.assertFalse(any(key.lower() in ('authorization', 'private-token', 'deploy-token', 'job-token',
                                                     'cookie', 'x-github-api-version') for key, _ in request.header_items()))
                handler = build.call_args.args[0]
                with self.assertRaises(updates.UpdateError):
                    handler.redirect_request(Request(self.source.latest_url), None, 302, 'Found', {}, 'https://evil.example/')


class GitLabManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = sources.source_from_config(config())
        self.transport = GitLabTransport(self.source)
        self.installer = Mock(return_value={'status': 'launching'})
        self.clock = [1800000000.0]

    def manager(self, **kwargs):
        manager = updates.UpdateManager(self.root, kwargs.pop('current_version', '0.21.10'),
            source=kwargs.pop('source', self.source), transport=kwargs.pop('transport', self.transport),
            installer=self.installer, clock=lambda: self.clock[0], **kwargs)
        self.addCleanup(manager.close)
        return manager

    def finish(self, manager):
        if manager._thread:
            manager._thread.join(3)
            self.assertFalse(manager._thread.is_alive())
        return manager.snapshot()

    def test_discovery_only_installs_after_explicit_request_and_preserves_source(self):
        manager = self.manager()
        self.assertEqual([], self.transport.calls)
        manager.check()
        checked = self.finish(manager)
        self.assertEqual('available', checked['status'])
        self.assertEqual({'provider': 'gitlab', 'label': '사내 GitLab'}, checked['source'])
        self.assertEqual('', checked['release']['url'])
        self.installer.assert_not_called()
        manager.install(VERSION)
        self.assertEqual('launching', self.finish(manager)['status'])
        self.assertEqual(self.transport.data, self.installer.call_args.args[1])
        self.assertEqual(hashlib.sha256(self.transport.data).hexdigest(), self.installer.call_args.args[2])
        self.assertEqual([self.source.latest_url, self.source.asset_url(VERSION, 'SHA256SUMS.txt'),
                          self.source.asset_url(VERSION, f'Company-Workspace-{VERSION}-vbs.zip')], self.transport.calls)

    def test_package_missing_invalid_or_different_bundled_source_never_reaches_installer(self):
        samples = [package(), gitlab_package(sources.source_from_config(config(projectId='124'))),
                   gitlab_package(sources.source_from_config(config(allowedDownloadOrigins=['https://store.example']))),
                   package(extra=[(PREFIX + sources.CONFIG_NAME, b'{}')]),
                   package(extra=[(PREFIX + sources.CONFIG_NAME, b' ' * (sources.MAX_CONFIG + 1))])]
        old_file = self.root / 'existing-app-marker'
        old_file.write_bytes(b'unchanged')
        for data in samples:
            with self.subTest(size=len(data)):
                transport = GitLabTransport(self.source, data=data)
                manager = self.manager(transport=transport)
                self.clock[0] += 61
                manager.check(manual=True)
                self.finish(manager)
                manager.install(VERSION)
                self.assertEqual('error', self.finish(manager)['status'])
                self.installer.assert_not_called()
                self.assertEqual(b'unchanged', old_file.read_bytes())

    def test_equal_canonical_config_identity_is_accepted_inside_archive(self):
        value = config(baseUrl='https://GITLAB.example:8443/company/')
        data = package(extra=[(PREFIX + sources.CONFIG_NAME, json.dumps(value).encode())])
        self.assertEqual(data, updates.verify_archive(data, VERSION, self.source)[0])

    def test_publisher_default_archive_validation_accepts_source_config_and_both_editions(self):
        from workspace_publisher.publish import verify_files
        vbs = self.transport.data
        self.assertEqual(vbs, updates.verify_archive(vbs, VERSION)[0])
        executable = b'MZpublisher-fixture'
        exe_name = f'Company-Workspace-{VERSION}.exe'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in [(exe_name, executable), ('README.txt', b'Fixture'),
                                (exe_name + '.sha256', (hashlib.sha256(executable).hexdigest() + '  ' + exe_name).encode())]:
                archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), value)
        files = {f'Company-Workspace-{VERSION}-vbs.zip': vbs,
                 f'Company-Workspace-{VERSION}-exe.zip': buffer.getvalue()}
        sums = ''.join(hashlib.sha256(value).hexdigest() + '  ' + name + '\n' for name, value in files.items()).encode()
        files['SHA256SUMS.txt'] = sums
        for name, value in files.items():
            (self.root / name).write_bytes(value)
        verified = verify_files(self.root, VERSION, self.source.config)
        self.assertEqual(set(files), {item['name'] for item in verified})
        for item in verified:
            self.assertEqual(hashlib.sha256(files[item['name']]).hexdigest(), item['sha256'])

    def test_same_source_cache_reuses_metadata_and_source_switch_discards_release_but_keeps_preference(self):
        manager = self.manager()
        manager.check()
        self.finish(manager)
        manager.configure(False)
        same = self.manager()
        self.assertEqual('available', same.snapshot()['status'])
        self.assertTrue(same.snapshot()['canInstall'])
        same.check()
        self.assertEqual(1, len(self.transport.calls))
        saved = json.loads((self.root / 'app-updates.json').read_text())
        self.assertEqual(2, saved['schemaVersion'])
        self.assertEqual(self.source.identity, saved['sourceIdentity'])
        self.assertEqual('stable', saved['release']['channel'])
        for source in [sources.GITHUB_SOURCE, sources.source_from_config(config(projectId='124'))]:
            switched = self.manager(source=source)
            self.assertFalse(switched.snapshot()['autoCheck'])
            self.assertIsNone(switched.snapshot()['release'])
            self.assertIsNone(switched.snapshot()['lastChecked'])
            self.assertFalse(switched.snapshot()['canInstall'])

    def test_legacy_github_cache_is_preserved_only_for_github_not_gitlab(self):
        metadata, _ = release()
        (self.root / 'app-updates.json').write_text(json.dumps({'schemaVersion': 1, 'autoCheck': False,
            'lastChecked': self.clock[0], 'release': metadata, 'verified': True}))
        github = self.manager(source=sources.GITHUB_SOURCE)
        self.assertEqual('available', github.snapshot()['status'])
        gitlab = self.manager()
        self.assertIsNone(gitlab.snapshot()['release'])
        self.assertFalse(gitlab.snapshot()['autoCheck'])
        self.assertIsNone(gitlab.snapshot()['lastChecked'])

    def test_anonymous_access_errors_are_safe_and_do_not_fallback_or_enable_install(self):
        for code in (401, 403, 404, 429):
            with self.subTest(code=code):
                manager = self.manager()
                self.clock[0] += 61
                self.transport.failure = HTTPError(self.source.latest_url, code, 'server secret', {}, None)
                manager.check(manual=True)
                result = self.finish(manager)
                self.assertEqual('error', result['status'])
                self.assertFalse(result['canInstall'])
                self.assertIn('사내', result['error'])
                self.assertNotIn('server secret', result['error'])
                self.assertNotIn(updates.API_URL, self.transport.calls)

    def test_failed_refresh_retains_notes_but_disables_install_and_current_versions_are_not_downgraded(self):
        manager = self.manager()
        manager.check()
        before = self.finish(manager)['release']
        self.clock[0] += 61
        self.transport.failure = OSError('internal details')
        manager.check(manual=True)
        after = self.finish(manager)
        self.assertEqual(before, after['release'])
        self.assertFalse(after['canInstall'])
        self.transport.failure = None
        for current in (VERSION, '0.23.0'):
            self.clock[0] += 61
            manager = self.manager(current_version=current)
            manager.check(manual=True)
            self.assertEqual('current', self.finish(manager)['status'])
            self.assertFalse(manager.snapshot()['canInstall'])

    def test_sums_or_package_digest_mismatch_blocks_install_even_with_matching_source(self):
        for index in (0, 1):
            transport = GitLabTransport(self.source)
            transport.metadata['files'][index]['sha256'] = 'a' * 64
            manager = self.manager(transport=transport)
            self.clock[0] += 61
            manager.check(manual=True)
            self.finish(manager)
            manager.install(VERSION)
            self.assertEqual('error', self.finish(manager)['status'])
            self.installer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
