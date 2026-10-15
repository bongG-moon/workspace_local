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
from workspace_publisher.gui import build_identity, settings_only
from workspace_publisher.installer import compiler_path
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
        self.assertEqual(6, len(result['files']))
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
        channel = json.loads(registry.files[endpoint(self.config, fixture.VERSION, 'latest.json', channel=True)])
        self.assertEqual(3, len(channel['files']))
        self.assertFalse(any(row['name'].endswith('-Setup.exe') for row in channel['files']))
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
        with self.assertRaisesRegex(PublisherError, '이전 빌드에는 NSIS'):
            self.publisher.prepare_deploy(self.config)
        self.assertEqual(previous, self.publisher.prepare_deploy(fixture.CONFIG))

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
