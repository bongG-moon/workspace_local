"""NSIS publisher checks stay offline and never install the application."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workspace_publisher.config import PublisherError, normalize, runtime_config
from workspace_publisher.gui import build_identity, settings_only, sharing_file
from workspace_publisher.installer import ONLINE_NAME, compiler_path
from workspace_publisher.publish import endpoint, publish, verify_files
from tests import test_workspace_publisher_core as fixture


class InstallerConfigurationTests(unittest.TestCase):
    def test_new_settings_enable_installer_without_affecting_runtime_settings(self):
        value = normalize({key: item for key, item in fixture.CONFIG.items() if key != 'includeInstaller'})
        self.assertTrue(value['includeInstaller'])
        self.assertEqual('', value['nsisCompiler'])
        self.assertNotIn('includeInstaller', runtime_config(value))
        self.assertNotIn('nsisCompiler', runtime_config(value))
        self.assertEqual(value['includeInstaller'], settings_only(value)['includeInstaller'])
        for invalid in ('yes', 1, None):
            with self.subTest(value=invalid), self.assertRaises(PublisherError):
                normalize({**fixture.CONFIG, 'includeInstaller': invalid})

    def test_compiler_location_does_not_invalidate_completed_bytes_but_output_format_does(self):
        self.assertEqual(build_identity(fixture.CONFIG),
                         build_identity({**fixture.CONFIG, 'nsisCompiler': 'C:/NSIS/makensis.exe'}))
        self.assertNotEqual(build_identity(fixture.CONFIG),
                            build_identity({**fixture.CONFIG, 'includeInstaller': True}))

    def test_compiler_is_found_locally_and_explicit_missing_path_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), \
                patch('workspace_publisher.installer.shutil.which', return_value=None):
            root = Path(temporary)
            path = root / 'build/nsis/portable-3.12/nsis-3.12/makensis.exe'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'fixture compiler')
            self.assertEqual(path, compiler_path(root))
            with patch.dict(os.environ, {'AX_WORKSPACE_NSIS': str(path)}):
                self.assertEqual(path, compiler_path(root))
            with self.assertRaisesRegex(PublisherError, '자동 다운로드는 하지'):
                compiler_path(root, str(root / 'missing/makensis.exe'))

    def test_local_discovery_does_not_walk_arbitrary_nested_folders(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), \
                patch('workspace_publisher.installer.shutil.which', return_value=None):
            root = Path(temporary)
            path = root / 'build/nsis/archive/nested/unrelated/makensis.exe'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'fixture compiler')
            with self.assertRaisesRegex(PublisherError, 'NSIS'):
                compiler_path(root)


class InstallerBuildTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.GitAndBuildTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.publisher = self.fixture.publisher
        self.compiler = self.fixture.repo / 'build/nsis/makensis.exe'
        self.compiler.parent.mkdir(parents=True)
        self.compiler.write_bytes(b'fixture compiler')
        self.config = {**fixture.CONFIG, 'includeInstaller': True, 'nsisCompiler': str(self.compiler)}

    def build(self):
        with patch('local_app.windows_process.powershell_path', return_value='fixture-powershell.exe'):
            return self.publisher.build(self.config)

    def test_installer_build_publish_and_retry_preserve_legacy_update_contract(self):
        result = self.build()
        self.assertEqual(7, len(result['files']))
        self.assertIn('-IncludeInstaller', self.fixture.calls[1])
        self.assertIn(str(self.compiler), self.fixture.calls[1])
        self.assertEqual('', self.fixture.run_git('status', '--porcelain'))
        self.assertFalse((Path(result['sourceRoot']) / 'build/nsis').exists())
        self.assertEqual(result, self.publisher.load_last_build(self.config))
        registry = fixture.Registry()
        self.publisher.transport = registry
        self.assertTrue(self.publisher.publish(self.config, result, 'fixture-token')['verified'])
        setup_url = endpoint(self.config, fixture.VERSION, f'AX-Workspace-{fixture.VERSION}-Setup.exe')
        self.assertEqual(b'MZfixture NSIS installer', registry.files[setup_url])
        online_url = endpoint(self.config, fixture.VERSION, ONLINE_NAME)
        self.assertEqual(b'MZfixture latest GitLab installer', registry.files[online_url])
        channel = json.loads(registry.files[endpoint(self.config, fixture.VERSION, 'latest.json', channel=True)])
        self.assertEqual(3, len(channel['files']))
        self.assertEqual({'schema', 'channel', 'version', 'publishedAt', 'title', 'notes', 'files'}, set(channel))
        self.assertFalse(any(row['name'].startswith('AX-Workspace-') for row in channel['files']))
        checksums = registry.files[endpoint(self.config, fixture.VERSION, 'SHA256SUMS.txt')].decode()
        self.assertIn(ONLINE_NAME, checksums)
        self.assertIn(f'AX-Workspace-{fixture.VERSION}-Setup.exe', checksums)
        self.assertIn(ONLINE_NAME, sharing_file(result))
        self.assertIn('최신 버전', sharing_file(result))
        calls = len(self.fixture.calls)
        self.assertEqual(result, self.publisher.prepare_deploy(self.config))
        self.assertEqual(calls, len(self.fixture.calls))

    def test_missing_compiler_stops_before_any_build_process_or_staging(self):
        self.config['nsisCompiler'] = str(self.fixture.repo / 'missing/makensis.exe')
        with self.assertRaisesRegex(PublisherError, 'NSIS'):
            self.build()
        self.assertEqual([], self.fixture.calls)
        self.assertFalse(self.publisher.work_root.exists())

    def test_wrong_version_source_hash_size_and_test_build_report_are_rejected(self):
        result = self.build()
        report = Path(result['sourceRoot']) / f'build/workspace-installer-{fixture.VERSION}.json'
        original = json.loads(report.read_text())
        for key, value in (('version', '0.22.0'), ('standaloneSha256', '0' * 64),
                           ('sha256', '0' * 64), ('bytes', 1), ('testMode', True), ('productId', 'AX.Workspace.Desktop.Test.demo'),
                           ('installer', str(report.parent / 'other.exe'))):
            report.write_text(json.dumps({**original, key: value}))
            with self.subTest(field=key), self.assertRaises(PublisherError):
                self.publisher.load_last_build(self.config)
        report.write_text(json.dumps(original))
        self.assertEqual(result, self.publisher.load_last_build(self.config))

    def test_changed_installer_cannot_be_published_even_with_changed_checksum_file(self):
        result = self.build()
        directory = Path(result['directory'])
        setup = directory / f'AX-Workspace-{fixture.VERSION}-Setup.exe'
        old_digest = hashlib.sha256(setup.read_bytes()).hexdigest()
        setup.write_bytes(b'MZdifferent installer')
        checksum = directory / 'SHA256SUMS.txt'
        checksum.write_text(checksum.read_text().replace(old_digest, hashlib.sha256(setup.read_bytes()).hexdigest()))
        registry = fixture.Registry()
        self.publisher.transport = registry
        with self.assertRaises(PublisherError):
            self.publisher.publish(self.config, result, 'fixture-token')
        self.assertEqual([], registry.calls)

    def test_existing_zip_build_is_restorable_but_not_misrepresented_as_installer(self):
        previous = self.fixture.build()
        self.assertEqual(previous, self.publisher.load_last_build(self.config))
        with self.assertRaisesRegex(PublisherError, '이전 빌드에는 최신 버전 설치기'):
            self.publisher.prepare_deploy(self.config)
        self.assertEqual(previous, self.publisher.prepare_deploy(fixture.CONFIG))

    def test_previous_offline_installer_build_is_recoverable_but_requires_new_build_for_latest(self):
        previous = self.build()
        directory = Path(previous['directory'])
        (directory / ONLINE_NAME).unlink()
        checksum = directory / 'SHA256SUMS.txt'
        checksum.write_text(''.join(line + '\n' for line in checksum.read_text().splitlines()
                                    if not line.endswith('  ' + ONLINE_NAME)))
        previous['files'] = verify_files(directory, fixture.VERSION, runtime_config(self.config))
        for record in (directory.parent / 'build-result.json', self.publisher.work_root / 'last-build.json'):
            record.write_text(json.dumps(previous))
        self.assertEqual(6, len(previous['files']))
        self.assertEqual(previous, self.publisher.load_last_build(self.config))
        self.assertIn('이 버전 설치', sharing_file(previous))
        with self.assertRaisesRegex(PublisherError, '최신 버전 설치기가 없습니다'):
            self.publisher.prepare_deploy(self.config)
        with patch.object(self.publisher, 'current_version', side_effect=PublisherError('missing source')):
            with self.assertRaisesRegex(PublisherError, '최신 버전 설치기가 없습니다'):
                self.publisher.prepare_deploy(self.config)
        self.assertEqual(previous, self.publisher.prepare_deploy(fixture.CONFIG))

    def test_online_report_rejects_wrong_config_origin_version_digest_and_protocol(self):
        result = self.build()
        report = Path(result['sourceRoot']) / f'build/workspace-online-installer-{fixture.VERSION}.json'
        original = json.loads(report.read_text())
        wrong_source = runtime_config({**self.config, 'baseUrl': 'https://other.gitlab.corp'})
        for key, value in (('version', '0.22.0'), ('sha256', '0' * 64), ('bytes', 1),
                           ('schema', True), ('protocol', 2), ('protocol', True),
                           ('embeddedConfigVerified', False), ('appPayloadBundled', True),
                           ('pythonRequiredToInstall', True),
                           ('productId', 'AX.Workspace.OnlineInstaller.Test'), ('sourceIdentity', '0' * 64),
                           ('installer', str(report.parent / ONLINE_NAME)), ('updateSource', wrong_source),
                           ('updateSource', {**original['updateSource'], 'token': 'private-fixture-token'})):
            report.write_text(json.dumps({**original, key: value}))
            with self.subTest(field=key, value=value), self.assertRaises(PublisherError):
                self.publisher.load_last_build(self.config)
        report.write_text(json.dumps(original))
        self.assertEqual(result, self.publisher.load_last_build(self.config))
        self.assertNotIn('token', report.read_text().lower())
        self.assertNotIn('nsisCompiler', report.read_text())

    def test_online_report_cannot_be_missing_on_a_new_build(self):
        original_runner = self.publisher.runner
        def runner(args, *, cwd, **kwargs):
            returned = original_runner(args, cwd=cwd, **kwargs)
            report = cwd / f'build/workspace-online-installer-{fixture.VERSION}.json'
            if report.exists():
                report.unlink()
            return returned
        self.publisher.runner = runner
        with self.assertRaisesRegex(PublisherError, '최신 버전 설치기'):
            self.build()
        self.assertFalse((self.publisher.work_root / 'last-build.json').exists())

    def test_online_file_is_verified_again_before_upload(self):
        result = self.build()
        directory = Path(result['directory'])
        online = directory / ONLINE_NAME
        before = hashlib.sha256(online.read_bytes()).hexdigest()
        online.write_bytes(b'MZaltered online installer')
        checksum = directory / 'SHA256SUMS.txt'
        checksum.write_text(checksum.read_text().replace(before, hashlib.sha256(online.read_bytes()).hexdigest()))
        registry = fixture.Registry()
        self.publisher.transport = registry
        with self.assertRaises(PublisherError):
            self.publisher.publish(self.config, result, 'fixture-token')
        self.assertEqual([], registry.calls)

    def test_online_requires_offline_setup_and_executable_header(self):
        result = self.build()
        directory = Path(result['directory'])
        setup = directory / f'AX-Workspace-{fixture.VERSION}-Setup.exe'
        setup_bytes = setup.read_bytes()
        setup.unlink()
        with self.assertRaisesRegex(PublisherError, 'NSIS 설치 파일과 호환 ZIP'):
            verify_files(directory, fixture.VERSION, runtime_config(self.config))
        setup.write_bytes(setup_bytes)
        online = directory / ONLINE_NAME
        before = hashlib.sha256(online.read_bytes()).hexdigest()
        online.write_bytes(b'not an executable')
        checksum = directory / 'SHA256SUMS.txt'
        checksum.write_text(checksum.read_text().replace(before, hashlib.sha256(online.read_bytes()).hexdigest()))
        with self.assertRaises(PublisherError):
            verify_files(directory, fixture.VERSION, runtime_config(self.config))

    def test_plain_text_disguised_as_setup_is_rejected(self):
        result = self.build()
        directory = Path(result['directory'])
        setup = directory / f'AX-Workspace-{fixture.VERSION}-Setup.exe'
        before = hashlib.sha256(setup.read_bytes()).hexdigest()
        setup.write_bytes(b'not executable')
        checksum = directory / 'SHA256SUMS.txt'
        checksum.write_text(checksum.read_text().replace(before, hashlib.sha256(setup.read_bytes()).hexdigest()))
        with self.assertRaises(PublisherError):
            verify_files(directory, fixture.VERSION, runtime_config(self.config))


if __name__ == '__main__':
    unittest.main()
