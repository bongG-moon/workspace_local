"""Release discovery and user-requested updates without external network or launch."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
import warnings
import zipfile
import zlib

from local_app import app_updates as updates


VERSION = '0.22.0'
PREFIX = 'Company-Workspace/'


def package(version=VERSION, *, extra=(), omit=(), compression=zipfile.ZIP_DEFLATED):
    files = {
        'Company-Workspace.vbs': b'launcher',
        'deploy/Start-CompanyWorkspace.ps1': b'launcher',
        'local_app/server.py': f'WORKSPACE_VERSION = "{version}"\n'.encode(),
        'local_app/web/index.html': b'<html></html>',
        'desktop/Workspace.Desktop.exe': b'MZfixture',
        'desktop/Microsoft.Web.WebView2.Core.dll': b'fixture',
        'desktop/Microsoft.Web.WebView2.WinForms.dll': b'fixture',
        'desktop/WebView2Loader.dll': b'fixture',
    }
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, 'w', compression) as archive:
        warnings.simplefilter('ignore', UserWarning)
        def write(name, data):
            info = copy.copy(name) if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            archive.writestr(info, data, compress_type=compression)
        for name, data in files.items():
            if name not in omit:
                write(PREFIX + name, data)
        for name, data in extra:
            write(name, data)
    return stream.getvalue()


def release(version=VERSION, data=None):
    """Relevant fields from the documented GitHub latest-release response."""
    data = package(version) if data is None else data
    name = f'Company-Workspace-{version}-vbs.zip'
    sums = f'{hashlib.sha256(data).hexdigest()}  {name}\n'.encode()
    base = f'{updates.RELEASE_ROOT}/download/v{version}/'
    result = {
        'url': f'https://api.github.com/repos/{updates.REPOSITORY}/releases/123',
        'id': 123, 'tag_name': 'v' + version, 'name': 'Company Workspace ' + version,
        'html_url': f'{updates.RELEASE_ROOT}/tag/v{version}',
        'draft': False, 'prerelease': False, 'published_at': '2026-10-02T10:00:00Z',
        'body': '## 변경 내용\n파일 패널 가독성 개선\n<script>alert(1)</script>',
        'assets': [
            {'name': name, 'state': 'uploaded', 'size': len(data), 'browser_download_url': base + name,
             'digest': 'sha256:' + hashlib.sha256(data).hexdigest()},
            {'name': 'SHA256SUMS.txt', 'state': 'uploaded', 'size': len(sums),
             'browser_download_url': base + 'SHA256SUMS.txt',
             'digest': 'sha256:' + hashlib.sha256(sums).hexdigest()},
        ],
    }
    return result, {base + name: data, base + 'SHA256SUMS.txt': sums}


class Transport:
    def __init__(self, version=VERSION, data=None):
        self.metadata, self.assets = release(version, data)
        self.calls = []
        self.failure = None

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if self.failure:
            raise self.failure
        if url == updates.API_URL:
            return json.dumps(self.metadata).encode()
        data = self.assets[url]
        if kwargs.get('progress'):
            kwargs['progress'](len(data), len(data))
        return data


class ReleaseValidationTests(unittest.TestCase):
    def test_documented_metadata_retains_only_bounded_release_and_two_fixed_assets(self):
        value, _ = release()
        value['body'] = '가' * 40000
        parsed = updates.parse_release(value)
        self.assertLessEqual(len(parsed['body'].encode()), updates.MAX_NOTES)
        self.assertEqual(['Company-Workspace-0.22.0-vbs.zip', 'SHA256SUMS.txt'],
                         [a['name'] for a in parsed['assets']])
        self.assertNotIn('id', parsed)

    def test_rejects_draft_prerelease_nonstable_wrong_repository_and_missing_fields(self):
        for field, value in [('draft', True), ('prerelease', True), ('draft', 0),
                             ('tag_name', 'v0.22.0-rc.1'), ('tag_name', '0.22.0'),
                             ('tag_name', 'v00.22.0'), ('tag_name', 'v0.22.0+build'),
                             ('html_url', 'https://github.com/other/workspace_local/releases/tag/v0.22.0'),
                             ('published_at', None), ('body', {'html': 'not text'})]:
            with self.subTest(field=field, value=value):
                payload, _ = release()
                payload[field] = value
                with self.assertRaises(updates.UpdateError):
                    updates.parse_release(payload)

    def test_rejects_incorrect_asset_url_size_digest_state_and_duplicate(self):
        for field, value in [('browser_download_url', 'https://github.com/other/workspace_local/a.zip'),
                             ('size', updates.MAX_ARCHIVE + 1), ('size', True), ('size', 0),
                             ('digest', 'md5:' + 'a' * 32), ('digest', ''), ('state', 'new')]:
            with self.subTest(field=field, value=value):
                payload, _ = release()
                payload['assets'][0][field] = value
                with self.assertRaises(updates.UpdateError):
                    updates.parse_release(payload)
        payload, _ = release()
        payload['assets'].append(payload['assets'][0])
        with self.assertRaises(updates.UpdateError):
            updates.parse_release(payload)
        payload, _ = release()
        payload['assets'].pop()
        with self.assertRaises(updates.UpdateError):
            updates.parse_release(payload)

    def test_legacy_missing_api_digest_is_allowed_but_checksum_asset_still_required(self):
        payload, _ = release()
        for asset in payload['assets']:
            del asset['digest']
        self.assertIsNone(updates.parse_release(payload)['assets'][0]['digest'])

    def test_stable_versions_compare_numerically_and_reject_arbitrary_arguments(self):
        self.assertGreater(updates.version_tuple('0.22.10'), updates.version_tuple('0.22.9'))
        for value in ['v1.0.0', '1.0', '../1.0.0', '1.0.0;command', '1.01.0', True, None, '1000000.1.1']:
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                updates.version_tuple(value)


class ArchiveValidationTests(unittest.TestCase):
    def test_verifies_package_without_extracting_or_executing_it(self):
        data = package()
        self.assertEqual((data, hashlib.sha256(data).hexdigest()), updates.verify_archive(data, VERSION))

    def test_rejects_missing_required_files_and_mismatched_embedded_version(self):
        for data in [package(omit=['desktop/WebView2Loader.dll']), package('0.21.10'), b'not a zip']:
            with self.subTest(size=len(data)), self.assertRaises(updates.UpdateError):
                updates.verify_archive(data, VERSION)

    def test_rejects_windows_path_escape_duplicate_reserved_stream_and_runtime_members(self):
        names = ['../evil', PREFIX + '../evil', PREFIX + 'local_app/../../evil',
                 '/Company-Workspace/evil', 'C:/evil', PREFIX + 'local_app/server.py',
                 PREFIX + 'LOCAL_APP/server.py', PREFIX + 'evil:stream', PREFIX + 'CON.txt',
                 PREFIX + 'bad./file', PREFIX + 'runtime/python.exe', PREFIX + 'Runtime/python.exe',
                 PREFIX + 'a//b', PREFIX + 'trailing /file']
        for name in names:
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                updates.verify_archive(package(extra=[(name, b'bad')]), VERSION)

    def test_accepts_real_powershell_backslash_members_but_rejects_nul_and_backslash_escape(self):
        data = package()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [info.filename.encode() for info in archive.infolist()]
        for name in names:
            data = data.replace(name, name.replace(b'/', b'\\'))
        self.assertEqual(data, updates.verify_archive(data, VERSION)[0])
        for name in ('a\x00b', '..\\evil'):
            placeholder = 'x' * len(name)
            data = package(extra=[(PREFIX + placeholder, b'bad')])
            data = data.replace((PREFIX + placeholder).encode(), (PREFIX + name).encode())
            with self.subTest(name=name), self.assertRaises(updates.UpdateError):
                updates.verify_archive(data, VERSION)

    def test_rejects_file_directory_collisions(self):
        with self.assertRaises(updates.UpdateError):
            updates.verify_archive(package(extra=[(PREFIX + 'extra', b'a'), (PREFIX + 'extra/child', b'b')]), VERSION)

    def test_rejects_symlink_reparse_and_encrypted_members(self):
        for attr in [(stat.S_IFLNK | 0o777) << 16, 0x400]:
            info = zipfile.ZipInfo(PREFIX + 'linked')
            info.create_system, info.external_attr = 3, attr
            with self.subTest(attr=attr), self.assertRaises(updates.UpdateError):
                updates.verify_archive(package(extra=[(info, b'bad')]), VERSION)
        data = bytearray(package(compression=zipfile.ZIP_STORED))
        central = data.index(b'PK\x01\x02')
        struct.pack_into('<H', data, central + 8, struct.unpack_from('<H', data, central + 8)[0] | 1)
        with self.assertRaises(updates.UpdateError):
            updates.verify_archive(bytes(data), VERSION)

    def test_rejects_corrupt_crc_even_for_non_executed_readme(self):
        data = bytearray(package(extra=[(PREFIX + 'README.txt', b'CHECK_CRC_HERE')], compression=zipfile.ZIP_STORED))
        offset = data.index(b'CHECK_CRC_HERE')
        data[offset] ^= 1
        with self.assertRaises(updates.UpdateError):
            updates.verify_archive(bytes(data), VERSION)

    def test_enforces_compressed_member_total_and_count_bounds(self):
        data = package()
        for constant, value in [('MAX_ARCHIVE', 10), ('MAX_MEMBERS', 2), ('MAX_EXPANDED', 10)]:
            with self.subTest(constant=constant), patch.object(updates, constant, value):
                with self.assertRaises(updates.UpdateError):
                    updates.verify_archive(data, VERSION)

    def test_invalid_compressed_stream_is_a_validation_error(self):
        with patch.object(zipfile.ZipExtFile, 'read', side_effect=zlib.error('corrupt compressed stream')):
            with self.assertRaises(updates.UpdateError):
                updates.verify_archive(package(), VERSION)


class DownloadValidationTests(unittest.TestCase):
    def test_rejects_foreign_redirects_credentials_ports_fragments_and_plain_http(self):
        initial = f'{updates.RELEASE_ROOT}/download/v0.22.0/Company-Workspace-0.22.0-vbs.zip'
        values = ['http://release-assets.githubusercontent.com/a', 'https://evil.example/a',
                  'https://github.com/other/repo/releases/a', 'https://user@release-assets.githubusercontent.com/a',
                  'https://release-assets.githubusercontent.com:8443/a',
                  'https://release-assets.githubusercontent.com/a#fragment',
                  'https://release-assets.githubusercontent.com.evil.example/a',
                  'https://release-assets.githubusercontent.com/\npath']
        for value in values:
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                updates._download_url(value, initial)
        for host in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            url = f'https://{host}/asset?signed=value'
            self.assertEqual(url, updates._download_url(url, initial))
        with self.assertRaises(updates.UpdateError):
            updates._download_url('https://api.github.com/repos/other/repo/releases/latest', updates.API_URL)

    def test_redirect_handler_checks_before_following(self):
        handler = updates._Redirects(updates.API_URL, threading.Event(), time.monotonic() + 10)
        with self.assertRaises(updates.UpdateError):
            handler.redirect_request(Request(updates.API_URL), None, 302, 'Found', {}, 'https://evil.example/')

    def test_anonymous_download_checks_length_and_byte_limit(self):
        for raw, length, limit, valid in [(b'abc', '3', 3, True), (b'abcd', '4', 3, False),
                                           (b'abcd', None, 3, False), (b'ab', '3', 3, False)]:
            with self.subTest(raw=raw, length=length, limit=limit):
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.status = 200
                response.headers = {'Content-Length': length} if length else {}
                response.geturl.return_value = updates.API_URL
                response.read1 = io.BytesIO(raw).read
                opener = Mock()
                opener.open.return_value = response
                with patch.object(updates, 'build_opener', return_value=opener):
                    call = lambda: updates.github_download(updates.API_URL, limit=limit,
                        deadline=time.monotonic() + 10, cancel=threading.Event())
                    if valid:
                        self.assertEqual(raw, call())
                        req = opener.open.call_args.args[0]
                        self.assertFalse(req.has_header('Authorization'))
                        self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 8)
                    else:
                        with self.assertRaises(updates.UpdateError):
                            call()

    def test_cancelled_and_expired_calls_never_open_network(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(updates, 'build_opener') as opener:
            for cancel, deadline in [(cancelled, time.monotonic() + 10), (threading.Event(), time.monotonic() - 1)]:
                with self.assertRaises(updates.UpdateError):
                    updates.github_download(updates.API_URL, limit=10, deadline=deadline, cancel=cancel)
            opener.assert_not_called()


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-updates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = [1000.0]
        self.transport = Transport()
        self.installer = Mock(return_value={'status': 'launching'})
        self.manager = self.make()

    def make(self, **kwargs):
        manager = updates.UpdateManager(self.root, kwargs.pop('current_version', '0.21.10'),
            transport=kwargs.pop('transport', self.transport), installer=kwargs.pop('installer', self.installer),
            clock=lambda: self.clock[0], **kwargs)
        self.addCleanup(manager.close)
        return manager

    def finish(self, manager=None):
        manager = manager or self.manager
        if manager._thread:
            manager._thread.join(3)
            self.assertFalse(manager._thread.is_alive(), 'Worker did not finish')
        return manager.snapshot()

    def checked(self):
        self.assertEqual('checking', self.manager.check()['status'])
        return self.finish()

    def test_no_network_in_constructor_or_snapshot_and_check_never_installs(self):
        self.manager.snapshot()
        self.assertEqual([], self.transport.calls)
        result = self.checked()
        self.assertEqual('available', result['status'])
        self.assertTrue(result['canInstall'])
        self.assertEqual([updates.API_URL], self.transport.calls)
        self.installer.assert_not_called()
        self.assertEqual({'currentVersion', 'status', 'autoCheck', 'lastChecked', 'release',
                          'progress', 'error', 'canInstall', 'source'}, set(result))
        self.assertEqual({'version', 'title', 'notes', 'publishedAt', 'url'}, set(result['release']))
        result['release']['version'] = '1.0.0'
        self.assertEqual(VERSION, self.manager.snapshot()['release']['version'])

    def test_only_exact_cached_newer_version_can_be_user_installed(self):
        with self.assertRaises(ValueError):
            self.manager.install(VERSION)
        self.checked()
        for version in ['0.22.1', '0.21.10', '0.20.0']:
            with self.assertRaises(ValueError):
                self.manager.install(version)
        self.assertEqual('downloading', self.manager.install(VERSION)['status'])
        result = self.finish()
        self.assertEqual('launching', result['status'])
        self.assertEqual(100, result['progress'])
        version, data, digest = self.installer.call_args.args
        self.assertEqual(VERSION, version)
        self.assertEqual(package(), data)
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertIs(self.manager._cancel, self.installer.call_args.kwargs['cancel'])
        self.manager.install(VERSION)
        self.assertEqual(1, self.installer.call_count)

    def test_current_or_older_release_is_not_installable(self):
        for version in ['0.21.10', '0.20.0']:
            with self.subTest(version=version):
                manager = self.make(transport=Transport(version))
                manager.check(manual=True)
                result = self.finish(manager)
                self.assertEqual('current', result['status'])
                self.assertFalse(result['canInstall'])

    def test_automatic_cache_and_manual_cooldown_are_persisted(self):
        self.checked()
        self.manager.start()
        self.manager.check(manual=True)
        self.assertEqual(1, len(self.transport.calls))
        self.clock[0] += 61
        self.manager.check()
        self.assertEqual(1, len(self.transport.calls))
        self.manager.check(manual=True)
        self.finish()
        self.assertEqual(2, len(self.transport.calls))
        another = self.make()
        self.assertEqual('available', another.snapshot()['status'])
        another.start()
        self.assertEqual(2, len(self.transport.calls))
        self.clock[0] += updates.CHECK_INTERVAL
        another.check()
        self.finish(another)
        self.assertEqual(3, len(self.transport.calls))

    def test_disable_auto_check_persists_and_manual_check_still_works(self):
        self.assertEqual('disabled', self.manager.configure(False)['status'])
        another = self.make()
        another.start()
        self.assertFalse(another.snapshot()['autoCheck'])
        self.assertEqual([], self.transport.calls)
        another.check(manual=True)
        self.assertEqual('available', self.finish(another)['status'])
        for value in ['false', 0, None]:
            with self.assertRaises(ValueError):
                another.configure(value)

    def test_demo_never_checks_writes_or_installs(self):
        manager = self.make(demo=True)
        manager.start()
        manager.check(manual=True)
        manager.configure(True)
        self.assertEqual('disabled', manager.snapshot()['status'])
        with self.assertRaises(ValueError):
            manager.install(VERSION)
        self.assertEqual([], self.transport.calls)
        self.assertFalse((self.root / 'app-updates.json').exists())

    def test_start_is_once_and_only_one_network_worker_runs_without_holding_state_lock(self):
        entered, unblock = threading.Event(), threading.Event()
        original = self.transport
        def transport(url, **kwargs):
            entered.set()
            self.assertTrue(unblock.wait(2))
            return original(url, **kwargs)
        manager = self.make(transport=transport)
        manager.start()
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        self.assertEqual('checking', manager.snapshot()['status'])
        for _ in range(5):
            manager.start()
            manager.check(manual=True)
        self.assertLess(time.monotonic() - before, .5)
        unblock.set()
        self.finish(manager)
        self.assertEqual([updates.API_URL], original.calls)

    def test_scheduler_uses_cancellable_fifteen_minute_wait_and_only_requests_check(self):
        manager = self.make()
        with patch.object(manager._cancel, 'wait', side_effect=[False, True]) as wait:
            with patch.object(manager, 'check') as check:
                manager._schedule()
        self.assertEqual([((900,), {}), ((900,), {})], wait.call_args_list)
        check.assert_called_once_with()
        self.installer.assert_not_called()

    def test_check_errors_are_safe_and_cached_notes_survive_but_cannot_install(self):
        self.checked()
        self.clock[0] += 61
        self.transport.failure = URLError('secret-token-and-private-url')
        self.manager.check(manual=True)
        result = self.finish()
        self.assertEqual('error', result['status'])
        self.assertEqual(VERSION, result['release']['version'])
        self.assertFalse(result['canInstall'])
        self.assertNotIn('secret', result['error'])
        self.assertFalse(self.make().snapshot()['canInstall'])

    def test_rate_limit_error_has_generic_korean_message(self):
        self.transport.failure = HTTPError(updates.API_URL, 429, 'private details', {}, None)
        self.manager.check()
        self.assertIn('요청 한도', self.finish()['error'])

    def test_oversized_or_invalid_json_is_rejected(self):
        for data in [b'x' * (updates.MAX_METADATA + 1), b'not json', b'[]']:
            with self.subTest(size=len(data)):
                self.clock[0] += 61
                manager = self.make(transport=lambda *args, **kwargs: data)
                manager.check(manual=True)
                self.assertEqual('error', self.finish(manager)['status'])
        self.installer.assert_not_called()

    def test_api_digest_and_required_checksum_corruption_prevent_installer(self):
        for mutation in ('api', 'sum', 'data', 'inner_crc'):
            with self.subTest(mutation=mutation):
                transport = Transport()
                if mutation == 'api':
                    transport.metadata['assets'][0]['digest'] = 'sha256:' + '0' * 64
                elif mutation == 'sum':
                    item = transport.metadata['assets'][1]
                    old = transport.assets[item['browser_download_url']]
                    bad = b'0' * 64 + old[64:]
                    transport.assets[item['browser_download_url']] = bad
                    item['digest'] = 'sha256:' + hashlib.sha256(bad).hexdigest()
                elif mutation == 'data':
                    item = transport.metadata['assets'][0]
                    transport.assets[item['browser_download_url']] += b'changed'
                else:
                    data = bytearray(package(extra=[(PREFIX + 'README.txt', b'CHECK_CRC_HERE')], compression=zipfile.ZIP_STORED))
                    data[data.index(b'CHECK_CRC_HERE')] ^= 1
                    transport = Transport(data=bytes(data))
                manager = self.make(transport=transport)
                manager.check(manual=True)
                self.finish(manager)
                manager.install(VERSION)
                result = self.finish(manager)
                self.assertEqual('error', result['status'])
                self.assertTrue(result['canInstall'])
        self.installer.assert_not_called()

    def test_missing_legacy_asset_digest_still_requires_outer_checksum_match(self):
        for asset in self.transport.metadata['assets']:
            asset.pop('digest')
        self.checked()
        self.manager.install(VERSION)
        self.assertEqual('launching', self.finish()['status'])

    def test_installer_failure_preserves_old_app_and_all_external_configuration(self):
        old = self.root / 'old-installation.exe'
        old.write_bytes(b'old app stays here')
        settings = self.root / 'claude-settings.json'
        settings.write_text('{"preserve":true}')
        self.installer.side_effect = OSError('private path')
        self.checked()
        self.manager.install(VERSION)
        result = self.finish()
        self.assertEqual('error', result['status'])
        self.assertEqual(b'old app stays here', old.read_bytes())
        self.assertEqual('{"preserve":true}', settings.read_text())
        self.assertEqual('새 버전을 실행하지 못했습니다. 기존 앱에서 다시 시도해 주세요.', result['error'])

    def test_close_cancels_download_without_wait_and_never_calls_installer_afterwards(self):
        self.checked()
        entered, unblock = threading.Event(), threading.Event()
        original = self.transport
        def transport(url, **kwargs):
            entered.set()
            self.assertTrue(unblock.wait(2))
            return original(url, **kwargs)
        self.manager.transport = transport
        self.manager.install(VERSION)
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        self.manager.close()
        self.assertLess(time.monotonic() - before, .2)
        unblock.set()
        self.finish()
        self.installer.assert_not_called()
        self.assertFalse(self.manager.snapshot()['canInstall'])

    def test_failed_launcher_process_becomes_retryable_error_without_exposing_handle(self):
        process = Mock()
        process.poll.return_value = 1
        self.installer.return_value = {'status': 'launching', '_process': process}
        manager = self.make(handoff_status=lambda: {'upgrade': None})
        manager.check(manual=True)
        self.finish(manager)
        manager.install(VERSION)
        result = self.finish(manager)
        self.assertEqual('error', result['status'])
        self.assertTrue(result['canInstall'])
        self.assertNotIn('_process', json.dumps(result))
        self.assertNotIn('_process', manager.path.read_text())

    def test_zero_launcher_exit_does_not_claim_success_without_matching_prepare(self):
        process = Mock()
        process.poll.return_value = 0
        self.installer.return_value = {'status': 'launching', '_process': process}
        manager = self.make(handoff_status=lambda: {'upgrade': None})
        manager.check(manual=True)
        self.finish(manager)
        with patch.object(updates, 'LAUNCH_TIMEOUT', .025), patch.object(updates, 'LAUNCH_POLL_INTERVAL', .001):
            manager.install(VERSION)
            result = self.finish(manager)
        self.assertEqual('error', result['status'])
        self.assertIn('시작을 확인하지 못했습니다', result['error'])
        self.assertTrue(result['canInstall'])

    def test_cancelled_expired_and_failed_handoff_leave_retryable_status(self):
        for stage in ('cancelled', 'expired', 'failed'):
            with self.subTest(stage=stage):
                self.clock[0] += 61
                pending = [None]
                def installer(*args, **kwargs):
                    pending[0] = {'requestId': 'new-request', 'targetVersion': VERSION, 'stage': stage}
                    return {'status': 'launching'}
                manager = self.make(installer=installer, handoff_status=lambda: {'upgrade': pending[0]})
                manager.check(manual=True)
                self.finish(manager)
                manager.install(VERSION)
                result = self.finish(manager)
                self.assertEqual('error', result['status'])
                self.assertTrue(result['canInstall'])

    def test_stale_cancelled_request_is_ignored_during_retry_and_wrong_target_cannot_ack(self):
        previous = {'requestId': 'previous', 'targetVersion': VERSION, 'stage': 'cancelled'}
        for monitored in (previous, {'requestId': 'fresh', 'targetVersion': '0.23.0', 'stage': 'waiting'}):
            with self.subTest(monitored=monitored):
                self.clock[0] += 61
                status = Mock(side_effect=lambda: {'upgrade': previous if status.call_count == 1 else monitored})
                manager = self.make(handoff_status=status)
                manager.check(manual=True)
                self.finish(manager)
                with patch.object(updates, 'LAUNCH_TIMEOUT', .025), patch.object(updates, 'LAUNCH_POLL_INTERVAL', .001):
                    manager.install(VERSION)
                    result = self.finish(manager)
                self.assertEqual('error', result['status'])
                self.assertIn('시작을 확인하지 못했습니다', result['error'])

    def test_fresh_waiting_handoff_outlives_launch_timeout_and_close_wakes_monitor(self):
        seen = threading.Event()
        calls = [0]
        def status():
            calls[0] += 1
            if calls[0] == 1:
                return {'upgrade': None}
            seen.set()
            return {'upgrade': {'requestId': 'fresh', 'targetVersion': VERSION, 'stage': 'waiting'}}
        manager = self.make(handoff_status=status)
        manager.check(manual=True)
        self.finish(manager)
        with patch.object(updates, 'LAUNCH_TIMEOUT', 0), patch.object(updates, 'LAUNCH_POLL_INTERVAL', .001):
            manager.install(VERSION)
            self.assertTrue(seen.wait(1))
            self.assertEqual('launching', manager.snapshot()['status'])
            manager.close()
            self.finish(manager)
        self.assertIsNone(manager.snapshot()['error'])
        self.assertFalse(manager.snapshot()['canInstall'])

    def test_handoff_callback_does_not_hold_manager_lock(self):
        snapshots = []
        calls = [0]
        manager = None
        def status():
            probe = threading.Thread(target=lambda: snapshots.append(manager.snapshot()))
            probe.start()
            probe.join(.5)
            self.assertFalse(probe.is_alive(), 'Handoff callback retained the manager lock')
            calls[0] += 1
            return {'upgrade': None if calls[0] == 1 else
                    {'requestId': 'fresh', 'targetVersion': VERSION, 'stage': 'closed'}}
        manager = self.make(handoff_status=status)
        manager.check(manual=True)
        self.finish(manager)
        manager.install(VERSION)
        result = self.finish(manager)
        self.assertEqual('launching', result['status'])
        self.assertEqual(2, len(snapshots))


if __name__ == '__main__':
    unittest.main()
