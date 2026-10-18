"""Release-history metadata and lifecycle tests; all network is a local fixture."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app import app_updates as updates
from local_app import release_history as history
from local_app import update_source as sources


def gitlab_source(project='123', **changes):
    value = {'schema': 1, 'provider': 'gitlab', 'baseUrl': 'https://gitlab.example:8443/company',
             'projectId': project, 'packageName': 'company-workspace',
             'channelPackage': 'company-workspace-channel', 'channelVersion': '0.0.0'}
    value.update(changes)
    return sources.from_config(value)


def release(version='0.23.28', **changes):
    value = {'version': version, 'title': '공식 변경 내용', 'notes': '변경\n**본문** <script>text</script>',
             'publishedAt': '2026-10-18T10:00:00Z'}
    value.update(changes)
    return value


def github_release(version='0.23.28', **changes):
    value = {'draft': False, 'prerelease': False, 'tag_name': 'v' + version,
             'name': '공식 기록', 'body': '본문', 'published_at': '2026-10-18T10:00:00Z',
             'html_url': f'{updates.RELEASE_ROOT}/tag/v{version}'}
    value.update(changes)
    return value


def historical_release(version='0.23.27', note_source='gitlab-release', **changes):
    value = release(version, noteSource=note_source)
    if note_source == 'package':
        value.update(title=f'AX Workspace {version}', notes='')
    value.update(changes)
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


class ParsingTests(unittest.TestCase):
    def test_gitlab_orders_semver_and_keeps_future_versions_without_assets(self):
        entries = history.parse_gitlab({'schema': 1, 'releases': [release('0.9.0'), release('0.23.100'), release('0.23.28')]})
        self.assertEqual(['0.23.100', '0.23.28', '0.9.0'], [e['version'] for e in entries])
        self.assertTrue(all(e['url'] == '' and e['origin'] == 'published' for e in entries))
        self.assertIn('<script>', entries[0]['notes'])  # Remains plain text for the renderer.

    def test_github_filters_unpublished_preview_and_non_semver_tags(self):
        entries = history.parse_github([github_release(), github_release('99.0.0', draft=True),
            github_release('98.0.0', prerelease=True), github_release('97.0.0', published_at=None),
            github_release(tag_name='nightly'), github_release(tag_name='v1.0.0-rc1'), github_release('0.23.9')])
        self.assertEqual(['0.23.28', '0.23.9'], [e['version'] for e in entries])
        self.assertEqual(f'{updates.RELEASE_ROOT}/tag/v0.23.28', entries[0]['url'])

    def test_github_allows_null_text_but_rejects_malformed_text_and_foreign_urls(self):
        item = history.parse_github([github_release(name=None, body=None)])[0]
        self.assertEqual(('v0.23.28', ''), (item['title'], item['notes']))
        for change in ({'name': False}, {'body': False}, {'name': 'x' * 301}, {'body': 'x' * 32769},
                       {'html_url': 'https://evil.example/'}, {'html_url': 'javascript:alert(1)'},
                       {'html_url': f'{updates.RELEASE_ROOT}/tag/v0.23.28?token=secret'}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                history.parse_github([github_release(**change)])

    def test_strict_utc_timestamps_include_fractional_and_explicit_utc(self):
        for stamp in ('2024-02-29T23:59:59Z', '2026-10-18T10:00:00.123456+00:00',
                      '2026-10-18T10:00:00.1234567Z', '2026-10-18T10:00:00.123456789+00:00',
                      '2026-10-18T10:00:00+00:00'):
            with self.subTest(stamp=stamp):
                self.assertEqual(stamp, history.parse_gitlab({'schema': 1, 'releases': [release(publishedAt=stamp)]})[0]['publishedAt'])
        for stamp in (None, '2026-02-29T00:00:00Z', '2026-10-18T24:00:00Z', '2026-10-18',
                      '2026-10-18T00:00:00', '2026-10-18T00:00:00+09:00', '2026-10-18T00:00:00-00:00',
                      '2026-10-18T00:00:00.1234567890Z'):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                history.parse_gitlab({'schema': 1, 'releases': [release(publishedAt=stamp)]})

    def test_gitlab_rejects_extra_fields_bad_schema_versions_and_duplicates(self):
        invalid = [[], {'schema': True, 'releases': []}, {'schema': 3, 'releases': []},
                   {'schema': 1, 'releases': [], 'url': 'https://evil.example'},
                   {'schema': 1, 'releases': [release(url='https://evil.example')]},
                   {'schema': 1, 'releases': [release(), release()]},
                   {'schema': 1, 'releases': [release(str(i) + '.0.0') for i in range(31)]}]
        invalid.extend({'schema': 1, 'releases': [release(version=v)]}
                       for v in ('1.2', 'v1.2.3', '01.2.3', '1.2.3-beta', '1000000.0.0', None))
        for value in invalid:
            with self.subTest(value=str(value)[:100]), self.assertRaises(ValueError):
                history.parse_gitlab(value)
        with self.assertRaises(ValueError):
            history.parse_github([github_release(), github_release()])

    def test_text_character_limits_and_invalid_controls(self):
        item = release(title='한' * 300, notes='한' * 32768)
        self.assertEqual(item['notes'], history.parse_gitlab({'schema': 1, 'releases': [item]})[0]['notes'])
        for changes in ({'title': 'x' * 301}, {'notes': 'x' * 32769}, {'title': None},
                        {'notes': 'bad\x00text'}, {'notes': '\ud800'}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                history.parse_gitlab({'schema': 1, 'releases': [release(**changes)]})

    def test_raw_metadata_bound_utf8_and_duplicate_keys(self):
        for raw in (b'', b'\xff', b' ' * (history.MAX_BYTES + 1),
                    b'{"schema":1,"schema":1,"releases":[]}'):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                history._decode(raw)

    def test_bundled_versions_stay_offline_and_mismatched_versions_are_bounded(self):
        value = {'schema': 1, 'currentVersion': '9.0.0', 'releases': [
            {k: v for k, v in release(ver).items() if k != 'publishedAt'} for ver in ('9.0.0', '0.23.27', '0.23.26')]}
        entries = history.parse_bundled(value, '0.23.27')
        self.assertEqual(['0.23.27', '0.23.26'], [e['version'] for e in entries])
        self.assertTrue(all(e['publishedAt'] is None and e['origin'] == 'bundled' and e['url'] == '' for e in entries))
        self.assertTrue(all('noteSource' not in e for e in entries))
        value['currentVersion'] = '0.23.26'
        self.assertEqual(['0.23.26'], [e['version'] for e in history.parse_bundled(value, '0.23.27')])

    def test_schema2_keeps_native_and_package_source_dates_and_original_text(self):
        rows = [historical_release('0.23.26', 'package', publishedAt='2025-02-03T04:05:06.123456+00:00'),
                historical_release('0.23.27', 'gitlab-release', title='사내 이름', notes='당시 사내 변경 본문',
                                   publishedAt='2025-09-03T06:00:00Z'),
                historical_release('0.23.28', 'deployment')]
        entries = history.parse_gitlab({'schema': 2, 'releases': rows})
        self.assertEqual(['deployment', 'gitlab-release', 'package'], [e['noteSource'] for e in entries])
        for entry, original in zip(entries, reversed(rows)):
            self.assertEqual(original, {key: entry[key] for key in original})
        self.assertEqual('', entries[-1]['notes'])
        self.assertEqual('AX Workspace 0.23.26', entries[-1]['title'])

    def test_schema1_and_github_rows_are_explicit_deployment_records(self):
        self.assertEqual('deployment', history.parse_gitlab({'schema': 1, 'releases': [release()]})[0]['noteSource'])
        self.assertEqual('deployment', history.parse_github([github_release()])[0]['noteSource'])
        self.assertEqual([], history.parse_gitlab({'schema': 2, 'releases': []}))

    def test_schema2_requires_exact_source_enum_and_package_absence_contract(self):
        bad_rows = [release(), historical_release(note_source='other'), historical_release(note_source=None),
                    historical_release(note_source=[]), historical_release(note_source=True),
                    historical_release('0.23.27', 'package', notes='개발자 설명으로 채움'),
                    historical_release('0.23.27', 'package', notes=' '),
                    historical_release('0.23.27', 'package', title='공식 변경 사항'),
                    historical_release('0.23.27', 'package', title='AX Workspace 0.23.26'),
                    historical_release('0.23.27', 'package', publishedAt=None),
                    historical_release('0.23.27', 'package', publishedAt='2025-02-30T00:00:00Z'),
                    historical_release('0.23.27', 'package', publishedAt='2025-02-28T09:00:00+09:00'),
                    historical_release(extra='untrusted')]
        for row in bad_rows:
            with self.subTest(row=str(row)[:150]), self.assertRaises(ValueError):
                history.parse_gitlab({'schema': 2, 'releases': [row]})
        with self.assertRaises(ValueError):
            history.parse_gitlab({'schema': 1, 'releases': [historical_release(note_source='deployment')]})


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-release-history-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        (self.bundle / 'deploy').mkdir(parents=True)
        self.catalog = self.bundle / 'deploy' / 'workspace-release-notes.json'
        self.catalog.write_bytes(encoded({'schema': 1, 'currentVersion': '0.23.27', 'releases': [
            {'version': '0.23.27', 'title': '번들 기록', 'notes': '오프라인'},
            {'version': '0.23.26', 'title': '이전 기록', 'notes': '이전'}]}))
        self.now = [1800000000.0]
        self.source = gitlab_source()
        self.transport = Mock(return_value=encoded({'schema': 1, 'releases': [release()]}))

    def manager(self, **kwargs):
        settings = {'bundle_root': self.bundle, 'source': self.source, 'clock': lambda: self.now[0],
                    'transport': self.transport}
        settings.update(kwargs)
        manager = history.HistoryManager(self.root / 'state', '0.23.27', **settings)
        self.addCleanup(manager.close)
        return manager

    def settle(self, manager):
        thread = manager._thread
        if thread:
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'fixture worker did not complete')
        return manager.snapshot()

    def test_constructor_snapshots_and_demo_never_start_network(self):
        manager = self.manager()
        self.assertEqual('idle', manager.snapshot()['status'])
        self.assertEqual('bundled', manager.snapshot()['entries'][0]['origin'])
        for _ in range(3):
            manager.snapshot()
        demo = self.manager(demo=True)
        demo.check(force=True)
        self.transport.assert_not_called()
        self.assertFalse(manager.path.exists())

    def test_fetch_merges_remote_precedence_orders_and_persists_matching_source(self):
        self.transport.return_value = encoded({'schema': 1, 'releases': [release(), release('0.23.27', title='공식 내용')]})
        manager = self.manager()
        self.assertEqual('loading', manager.check()['status'])
        snapshot = self.settle(manager)
        self.assertEqual('ready', snapshot['status'])
        self.assertEqual(['0.23.28', '0.23.27', '0.23.26'], [e['version'] for e in snapshot['entries']])
        self.assertEqual('공식 내용', snapshot['entries'][1]['title'])
        self.assertEqual('published', snapshot['entries'][1]['origin'])
        self.assertEqual(self.source.history_url, self.transport.call_args.args[0])
        self.assertEqual(history.MAX_BYTES, self.transport.call_args.kwargs['limit'])
        self.assertLessEqual(self.transport.call_args.kwargs['deadline'], time.monotonic() + updates.CHECK_DEADLINE)
        snapshot['entries'][0]['notes'] = 'mutated'
        self.assertNotEqual('mutated', manager.snapshot()['entries'][0]['notes'])
        restored = self.manager()
        self.assertEqual(manager.snapshot(), restored.snapshot())
        restored.check()
        self.transport.assert_called_once()

    def test_cache_ttl_and_force_throttle_do_not_poll(self):
        manager = self.manager()
        manager.check()
        self.settle(manager)
        self.now[0] += 59
        manager.check(force=True)
        self.assertEqual(1, self.transport.call_count)
        self.now[0] += 1
        manager.check(force=True)
        self.settle(manager)
        self.assertEqual(2, self.transport.call_count)
        self.now[0] += 299
        manager.check()
        self.assertEqual(2, self.transport.call_count)
        self.now[0] += 1
        manager.check()
        self.settle(manager)
        self.assertEqual(3, self.transport.call_count)

    def test_inflight_checks_coalesce_even_after_ttl_or_force(self):
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        def download(*args, **kwargs):
            entered.set()
            finish.wait(3)
            return encoded({'schema': 1, 'releases': [release()]})
        self.transport.side_effect = download
        manager = self.manager()
        manager.check()
        self.assertTrue(entered.wait(2))
        self.now[0] += 1000
        for index in range(20):
            self.assertEqual('loading', manager.check(force=bool(index % 2))['status'])
        self.assertEqual(1, self.transport.call_count)
        finish.set()
        self.assertEqual('ready', self.settle(manager)['status'])

    def test_failed_check_preserves_cached_and_bundled_records_and_redacts_errors(self):
        manager = self.manager()
        manager.check()
        before = self.settle(manager)
        cache = manager.path.read_bytes()
        self.now[0] += 301
        self.transport.side_effect = RuntimeError('https://user:secret@private.example/ path token=secret')
        manager.check()
        after = self.settle(manager)
        self.assertEqual('error', after['status'])
        self.assertEqual(before['entries'], after['entries'])
        self.assertEqual(history.HISTORY_ERROR, after['error'])
        self.assertNotIn('secret', encoded(after).decode())
        self.assertEqual(cache, manager.path.read_bytes())
        manager.check(force=True)
        self.assertEqual(2, self.transport.call_count)

    def test_invalid_and_oversized_remote_data_cannot_replace_good_cache(self):
        manager = self.manager()
        manager.check()
        before = self.settle(manager)['entries']
        for raw in (b'[]', encoded({'schema': 1, 'releases': [release(), release()]}),
                    b'x' * (history.MAX_BYTES + 1)):
            with self.subTest(raw=raw[:40]):
                self.now[0] += 301
                self.transport.return_value = raw
                manager.check()
                snapshot = self.settle(manager)
                self.assertEqual('error', snapshot['status'])
                self.assertEqual(before, snapshot['entries'])

    def test_cache_never_crosses_source_projects_servers_or_provider(self):
        manager = self.manager()
        manager.check()
        self.settle(manager)
        for source in (gitlab_source('456'), gitlab_source(baseUrl='https://other.example'), sources.GITHUB_SOURCE):
            with self.subTest(source=source.public()):
                other = self.manager(source=source)
                self.assertIsNone(other.snapshot()['checkedAt'])
                self.assertTrue(all(e['origin'] == 'bundled' for e in other.snapshot()['entries']))
        self.transport.assert_called_once()

    def test_corrupt_cache_is_visible_but_offline_notes_survive_and_recover(self):
        manager = self.manager()
        manager.path.parent.mkdir(parents=True)
        manager.path.write_bytes(b'{"schema":1,"schema":1}')
        restored = self.manager()
        self.assertEqual('error', restored.snapshot()['status'])
        self.assertEqual(2, len(restored.snapshot()['entries']))
        restored.check()
        self.assertEqual('ready', self.settle(restored)['status'])

    def test_cache_rejects_untrusted_urls_invalid_time_and_oversized_file(self):
        manager = self.manager()
        manager.check()
        self.settle(manager)
        saved = json.loads(manager.path.read_bytes())
        mutations = [lambda value: value['entries'][0].update(url='https://evil.example'),
                     lambda value: value.update(checkedAt=float('nan')),
                     lambda value: value.update(checkedAt=self.now[0] + 301),
                     lambda value: value['entries'][0].update(origin='bundled')]
        for mutate in mutations:
            altered = copy.deepcopy(saved)
            mutate(altered)
            manager.path.write_bytes(encoded(altered))
            with self.subTest(altered=altered.get('checkedAt')):
                self.assertEqual('error', self.manager().snapshot()['status'])
        manager.path.write_bytes(b' ' * (history.MAX_BYTES + 1))
        self.assertEqual('error', self.manager().snapshot()['status'])

    def test_disk_cache_write_failure_keeps_memory_result(self):
        manager = self.manager()
        with patch.object(history.HistoryStore, '_write', side_effect=PermissionError('fixture')):
            manager.check()
            snapshot = self.settle(manager)
        self.assertEqual('ready', snapshot['status'])
        self.assertEqual('0.23.28', snapshot['entries'][0]['version'])

    def test_missing_or_invalid_bundle_does_not_block_remote_history(self):
        for raw in (None, b'not-json', encoded({'schema': 1, 'currentVersion': 'bad', 'releases': []})):
            if raw is None:
                self.catalog.unlink(missing_ok=True)
            else:
                self.catalog.write_bytes(raw)
            with self.subTest(raw=raw):
                manager = self.manager()
                manager.check(force=True)
                self.assertEqual('ready', self.settle(manager)['status'])

    def test_invalid_source_fails_closed_without_fallback(self):
        manager = self.manager(source=sources.UpdateSource(provider='invalid', error=sources.SOURCE_ERROR))
        self.assertEqual('error', manager.check(force=True)['status'])
        self.assertEqual(2, len(manager.snapshot()['entries']))
        self.transport.assert_not_called()

    def test_close_cancels_worker_without_waiting_and_discards_late_response(self):
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        observed = []
        def download(*args, cancel, **kwargs):
            observed.append(cancel)
            entered.set()
            finish.wait(3)
            return encoded({'schema': 1, 'releases': [release()]})
        self.transport.side_effect = download
        manager = self.manager()
        manager.check()
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        self.assertTrue(manager.close())
        self.assertLess(time.monotonic() - start, .5)
        self.assertTrue(observed[0].is_set())
        finish.set()
        snapshot = self.settle(manager)
        self.assertEqual('idle', snapshot['status'])
        self.assertTrue(all(e['origin'] == 'bundled' for e in snapshot['entries']))
        self.assertFalse(manager.path.exists())
        manager.check(force=True)
        self.transport.assert_called_once()

    def test_github_manager_uses_fixed_list_without_installability_requirement(self):
        self.transport.return_value = encoded([github_release()])
        manager = self.manager(source=sources.GITHUB_SOURCE)
        manager.check()
        self.assertEqual('0.23.28', self.settle(manager)['entries'][0]['version'])
        self.assertEqual(sources.GITHUB_HISTORY_API, self.transport.call_args.args[0])

    def test_legacy_six_field_cache_normalizes_without_startup_fetch_or_write(self):
        for source in (self.source, sources.GITHUB_SOURCE):
            with self.subTest(provider=source.provider):
                manager = self.manager(source=source)
                manager.path.parent.mkdir(parents=True, exist_ok=True)
                row = history._entry('0.23.27', '예전 캐시', '당시 본문', '2025-08-01T00:00:00Z', provider=source.provider)
                row.pop('noteSource')
                legacy = encoded({'schema': 1, 'sourceIdentity': source.identity, 'checkedAt': self.now[0], 'entries': [row]})
                manager.path.write_bytes(legacy)
                restored = self.manager(source=source)
                snapshot = restored.snapshot()
                self.assertEqual('ready', snapshot['status'])
                self.assertEqual({**row, 'noteSource': 'deployment'}, snapshot['entries'][0])
                self.assertEqual(legacy, manager.path.read_bytes())
                self.transport.assert_not_called()
                self.transport.return_value = encoded([github_release()] if source.provider == 'github'
                    else {'schema': 2, 'releases': [historical_release()]})
                self.now[0] += 301
                restored.check()
                self.settle(restored)
                saved = json.loads(manager.path.read_bytes())
                self.assertEqual(2, saved['schema'])
                self.assertIn('noteSource', saved['entries'][0])
                self.transport.reset_mock()

    def test_changed_native_internal_notes_override_same_version_bundled_and_cached_text(self):
        manager = self.manager()
        self.transport.return_value = encoded({'schema': 1, 'releases': [release('0.23.27', notes='이전 배포 설명')]})
        manager.check()
        self.settle(manager)
        row = historical_release('0.23.27', 'gitlab-release', title='사내 수정 이름', notes='당시 사내 본문\n새 줄',
                                 publishedAt='2024-01-02T03:04:05.123456789Z')
        self.transport.return_value = encoded({'schema': 2, 'releases': [row]})
        self.now[0] += 301
        manager.check()
        entry = self.settle(manager)['entries'][0]
        self.assertEqual(row, {key: entry[key] for key in row})
        self.assertEqual('published', entry['origin'])
        self.assertEqual(manager.snapshot(), self.manager().snapshot())

    def test_package_empty_notes_override_bundled_and_remain_empty_after_restore_and_failure(self):
        row = historical_release('0.23.27', 'package', publishedAt='2024-07-05T01:02:03.1234567Z')
        self.transport.return_value = encoded({'schema': 2, 'releases': [row]})
        manager = self.manager()
        self.assertEqual('오프라인', manager.snapshot()['entries'][0]['notes'])
        manager.check()
        entry = self.settle(manager)['entries'][0]
        self.assertEqual('', entry['notes'])
        self.assertEqual(row, {key: entry[key] for key in row})
        restored = self.manager()
        self.assertEqual(entry, restored.snapshot()['entries'][0])
        self.transport.side_effect = OSError('fixture failure')
        self.now[0] += 301
        restored.check()
        self.assertEqual(entry, self.settle(restored)['entries'][0])
        other = self.manager(source=gitlab_source('456'))
        self.assertEqual('bundled', other.snapshot()['entries'][0]['origin'])
        self.assertEqual('오프라인', other.snapshot()['entries'][0]['notes'])

    def test_cache_source_whitelist_rejects_unknown_mixed_legacy_and_fabricated_package_notes(self):
        manager = self.manager()
        self.transport.return_value = encoded({'schema': 2, 'releases': [historical_release(note_source='package')]})
        manager.check()
        self.settle(manager)
        saved = json.loads(manager.path.read_bytes())
        mutations = [lambda value: value['entries'][0].update(noteSource='other'),
                     lambda value: value['entries'][0].update(noteSource=[]),
                     lambda value: value['entries'][0].update(notes='개발자 문서'),
                     lambda value: value['entries'][0].update(title='가짜 설명'),
                     lambda value: value['entries'][0].update(extra='not allowed'),
                     lambda value: value['entries'][0].pop('noteSource'),
                     lambda value: value.update(schema=1)]
        for mutate in mutations:
            altered = copy.deepcopy(saved)
            mutate(altered)
            manager.path.write_bytes(encoded(altered))
            with self.subTest(altered=altered):
                self.assertEqual('error', self.manager().snapshot()['status'])
        github = copy.deepcopy(saved)
        github['sourceIdentity'] = sources.GITHUB_SOURCE.identity
        github['entries'][0]['url'] = f'{updates.RELEASE_ROOT}/tag/v0.23.27'
        manager.path.write_bytes(encoded(github))
        self.assertEqual('error', self.manager(source=sources.GITHUB_SOURCE).snapshot()['status'])


class TransportSecurityTests(unittest.TestCase):
    def test_only_exact_history_urls_are_allowed_before_network(self):
        github = sources.GITHUB_HISTORY_API
        gitlab = gitlab_source()
        invalid = [(sources.GITHUB_SOURCE, github + '&page=2'), (sources.GITHUB_SOURCE, github.replace('page=1', 'page=2')),
                   (sources.GITHUB_SOURCE, github.replace('api.github.com', 'evil.example')),
                   (gitlab, gitlab.history_url + '?token=secret'),
                   (gitlab, gitlab.history_url.replace('/123/', '/456/')),
                   (gitlab, gitlab.history_url.replace('release-history.json', 'private.json'))]
        with patch.object(updates, 'build_opener') as opener:
            for source, url in invalid:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    updates.source_download(source, url, limit=100, deadline=time.monotonic() + 10, cancel=threading.Event())
            opener.assert_not_called()

    def test_history_transports_are_anonymous_bounded_and_reject_unapproved_redirects(self):
        gitlab = gitlab_source(allowedDownloadOrigins=['https://store.example'])
        cases = [(sources.GITHUB_SOURCE, sources.GITHUB_HISTORY_API, True),
                 (sources.GITHUB_SOURCE, 'https://release-assets.githubusercontent.com/object', False),
                 (gitlab, gitlab.history_url, True), (gitlab, 'https://store.example/object?signature=public', True),
                 (gitlab, 'https://evil.example/object', False)]
        for source, final, valid in cases:
            with self.subTest(final=final):
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.status, response.headers = 200, {'Content-Length': '2'}
                response.geturl.return_value = final
                response.read1 = Mock(side_effect=io.BytesIO(b'{}').read)
                opener = Mock()
                opener.open.return_value = response
                with patch.object(updates, 'build_opener', return_value=opener):
                    call = lambda: updates.source_download(source, source.history_url, limit=2,
                        deadline=time.monotonic() + 10, cancel=threading.Event())
                    if valid:
                        self.assertEqual(b'{}', call())
                    else:
                        with self.assertRaises(ValueError):
                            call()
                        response.read1.assert_not_called()
                    request = opener.open.call_args.args[0]
                    self.assertFalse(any(k.lower() in {'authorization', 'private-token', 'cookie', 'job-token', 'deploy-token'}
                                         for k, _ in request.header_items()))
                    self.assertIn('json', request.get_header('Accept'))
                    self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 8)


if __name__ == '__main__':
    unittest.main()
