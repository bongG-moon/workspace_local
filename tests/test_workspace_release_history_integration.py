"""Publisher wire records remain readable by the separately packaged app."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from local_app.app_updates import parse_manifest
from local_app.release_history import HistoryManager, parse_bundled, parse_gitlab
from local_app.update_source import from_config
from workspace_publisher.config import runtime_config
from workspace_publisher.publish import Response, endpoint, publish
from workspace_publisher.release_history import parse_history
from tests.test_workspace_publisher_core import CONFIG, VERSION, Registry, release_files


ROOT = Path(__file__).resolve().parents[1]


class ReleaseHistoryIntegrationTests(unittest.TestCase):
    def test_internal_deployment_notes_and_package_only_records_survive_publisher_to_app(self):
        def encoded(value):
            return json.dumps(value, ensure_ascii=False).encode('utf-8')

        old_version = '0.22.3'
        retained = encoded({'schema': 1, 'channel': 'stable', 'version': old_version,
            'publishedAt': '2026-10-01T03:00:00.1234567Z', 'title': '사내 담당자가 바꾼 제목',
            'notes': '이 내용은 개발 기본 문구와 다릅니다. 사내 운영 안내입니다.',
            'files': [{'name': name, 'size': 100, 'sha256': 'a' * 64} for name in (
                f'Company-Workspace-{old_version}-vbs.zip', 'SHA256SUMS.txt')]})

        class InternalGitLab(Registry):
            def __init__(self):
                super().__init__()
                self.current_release = None
                self.native_notes = 'GitLab에 직접 남긴 사내 설명입니다.'

            def __call__(self, method, url, **kwargs):
                if '/packages/generic/' in url:
                    return super().__call__(method, url, **kwargs)
                self.calls.append((method, url, kwargs))
                self_token = kwargs.get('headers', {}).get('PRIVATE-TOKEN')
                if self_token != 'glpat-integration-secret':
                    raise AssertionError('Metadata retrieval requires the publisher token')
                api_path = urlsplit(CONFIG['baseUrl']).path + '/api/v4/projects/42'
                parsed = urlsplit(url)
                suffix = parsed.path.removeprefix(api_path)
                query = parse_qs(parsed.query)
                if method == 'GET' and suffix == '':
                    return Response(200, encoded({'id': 42, 'web_url': CONFIG['baseUrl'] + '/team/workspace', 'default_branch': 'main'}))
                if method == 'GET' and suffix == '/packages':
                    if query.get('page', ['1'])[0] != '1':
                        return Response(200, b'[]')
                    packages = [{'id': 100, 'name': 'company-workspace-channel', 'version': '0.0.0',
                        'package_type': 'generic', 'status': 'default', 'created_at': '2026-09-01T00:00:00Z'}]
                    packages += [{'id': 30 + i, 'name': 'company-workspace', 'version': '0.22.' + str(i),
                        'package_type': 'generic', 'status': 'default', 'created_at': f'2026-09-{10+i:02d}T00:00:00Z'}
                        for i in (3, 2, 1)]
                    return Response(200, encoded(packages))
                if method == 'GET' and suffix.endswith('/package_files'):
                    if suffix != '/packages/100/package_files' or query.get('page', ['1'])[0] != '1':
                        return Response(200, b'[]')
                    return Response(200, encoded([{'id': 333, 'package_id': 100, 'file_name': 'latest.json',
                        'size': len(retained), 'file_sha256': hashlib.sha256(retained).hexdigest(),
                        'created_at': '2026-10-01T03:00:01Z'}]))
                if method == 'GET' and suffix == '/packages/100/package_files/333/download':
                    return Response(200, retained)
                if method == 'GET' and suffix == '/releases':
                    if query.get('page', ['1'])[0] != '1':
                        return Response(200, b'[]')
                    return Response(200, encoded([{'tag_name': 'v0.22.2', 'name': '별도 사내 릴리스 제목',
                        'description': self.native_notes, 'released_at': '2026-09-25T00:00:00Z',
                        'upcoming_release': False}]))
                if method == 'GET' and suffix == '/releases/v' + VERSION:
                    return Response(200, encoded(self.current_release)) if self.current_release else Response(404, b'')
                if method == 'GET' and suffix == '/repository/branches/main':
                    return Response(200, encoded({'commit': {'id': 'b' * 40}}))
                if method == 'POST' and suffix == '/releases':
                    self.current_release = json.loads(kwargs['data'])
                    return Response(201, encoded(self.current_release))
                raise AssertionError((method, suffix, query))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = InternalGitLab()
            build = release_files(root / 'release')
            bundled = {'schema': 1, 'currentVersion': VERSION, 'releases': [
                {'version': version, 'title': '개발 기본 제목', 'notes': '개발자가 만든 기본 설명'}
                for version in ('0.22.3', '0.22.2', '0.22.1')]}
            (root / 'deploy').mkdir()
            (root / 'deploy/workspace-release-notes.json').write_bytes(encoded(bundled))
            result = publish(CONFIG, build, 'glpat-integration-secret', token_kind='access',
                             transport=registry, bundled_history=bundled['releases'])
            self.assertTrue(result['verified'])
            self.assertTrue(result['releaseHistory']['verified'])
            source = from_config(runtime_config(CONFIG))
            raw = registry.files[source.history_url]
            self.assertEqual(json.loads(raw)['schema'], 2)
            calls = []

            def anonymous_download(url, **kwargs):
                calls.append(url)
                self.assertEqual(url, source.history_url)
                self.assertNotIn('headers', kwargs)
                return raw

            manager = HistoryManager(root / 'state', VERSION, bundle_root=root, source=source,
                                     transport=anonymous_download)
            self.addCleanup(manager.close)
            self.assertEqual(calls, [])
            manager.check()
            manager._thread.join(3)
            snapshot = manager.snapshot()
            self.assertEqual(snapshot['status'], 'ready')
            by_version = {row['version']: row for row in snapshot['entries']}
            self.assertEqual(by_version['0.22.3']['notes'], json.loads(retained)['notes'])
            self.assertEqual(by_version['0.22.3']['noteSource'], 'deployment')
            self.assertEqual(by_version['0.22.3']['publishedAt'], '2026-10-01T03:00:00.1234567Z')
            self.assertEqual(by_version['0.22.2']['notes'], 'GitLab에 직접 남긴 사내 설명입니다.')
            self.assertEqual(by_version['0.22.2']['noteSource'], 'gitlab-release')
            self.assertEqual(by_version['0.22.1']['notes'], '')
            self.assertEqual(by_version['0.22.1']['noteSource'], 'package')
            self.assertEqual(by_version['0.22.1']['publishedAt'], '2026-09-11T00:00:00Z')
            self.assertNotIn('개발자가 만든 기본 설명', json.dumps(snapshot["entries"], ensure_ascii=False))
            self.assertNotIn('glpat-integration-secret', json.dumps(snapshot))
            self.assertEqual(len(calls), 1)
            before = len(registry.calls)
            publish(CONFIG, build, 'glpat-integration-secret', token_kind='access',
                    transport=registry, bundled_history=bundled['releases'])
            self.assertFalse(any(method != 'GET' for method, _, _ in registry.calls[before:]))
            self.assertEqual(registry.files[source.history_url], raw)
            registry.native_notes = '담당자가 GitLab에서 이후에 고친 안내입니다.'
            latest_before = registry.files[source.latest_url]
            publish(CONFIG, build, 'glpat-integration-secret', token_kind='access',
                    transport=registry, bundled_history=bundled['releases'])
            refreshed = {row['version']: row for row in parse_gitlab(json.loads(registry.files[source.history_url]))}
            self.assertEqual(refreshed['0.22.2']['notes'], registry.native_notes)
            self.assertEqual(refreshed['0.22.3']['notes'], json.loads(retained)['notes'])
            self.assertEqual(registry.files[source.latest_url], latest_before)

    def test_verified_package_history_is_consumed_without_credentials_or_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = Registry()
            build = release_files(root / 'release')
            result = publish(CONFIG, build, 'fixture-token', token_kind='deploy', transport=registry)
            self.assertTrue(result['verified'])
            self.assertTrue(result['releaseHistory']['verified'])
            self.assertFalse(result['gitlabRelease']['verified'])
            source = from_config(runtime_config(CONFIG))
            latest = json.loads(registry.files[source.latest_url])
            self.assertEqual(set(latest), {'schema', 'channel', 'version', 'publishedAt', 'title', 'notes', 'files'})
            parse_manifest(latest, source)
            raw = registry.files[source.history_url]
            self.assertEqual(parse_history(raw)['releases'], json.loads(raw)['releases'])
            self.assertEqual(parse_gitlab(json.loads(raw))[0]['version'], VERSION)
            calls = []

            def download(url, **kwargs):
                calls.append(url)
                self.assertEqual(url, source.history_url)
                self.assertNotIn('headers', kwargs)
                return registry.files[url]

            manager = HistoryManager(root / 'state', VERSION, bundle_root=root, source=source, transport=download)
            self.addCleanup(manager.close)
            self.assertEqual(calls, [])
            manager.check()
            manager._thread.join(3)
            snapshot = manager.snapshot()
            self.assertEqual(snapshot['status'], 'ready')
            self.assertEqual(snapshot['entries'][0]['notes'], CONFIG['notes'])
            self.assertEqual(snapshot['entries'][0]['origin'], 'published')
            self.assertNotIn('fixture-token', json.dumps(snapshot))
            before = len(registry.calls)
            repeated = publish(CONFIG, build, 'fixture-token', token_kind='deploy', transport=registry)
            self.assertTrue(repeated['releaseHistory']['verified'])
            self.assertFalse(any(method != 'GET' for method, _, _ in registry.calls[before:]))
            self.assertEqual(raw, registry.files[source.history_url])

    def test_actual_bundled_notes_have_no_invented_deployment_dates(self):
        document = json.loads((ROOT / 'deploy/workspace-release-notes.json').read_text(encoding='utf-8-sig'))
        entries = parse_bundled(document, document['currentVersion'])
        self.assertGreater(len(entries), 1)
        self.assertEqual(entries[0]['version'], document['currentVersion'])
        self.assertTrue(all(entry['origin'] == 'bundled' and entry['publishedAt'] is None for entry in entries))

    def test_history_endpoint_preserves_the_configured_gitlab_server(self):
        source = from_config(runtime_config(CONFIG))
        generated = endpoint(CONFIG, VERSION, 'release-history.json', channel=True)
        self.assertEqual(generated, source.history_url)
        self.assertEqual(source.validate_initial(generated), generated)
        for value in (generated.replace('/42/', '/43/'), generated + '?redirect=https://example.com',
                      generated.replace('gitlab.corp:8443', 'github.com')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                source.validate_initial(value)


if __name__ == '__main__':
    unittest.main()
