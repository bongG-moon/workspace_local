"""Download ZIP builds use exact source bytes without creating Git history."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from workspace_publisher.config import PublisherError
from workspace_publisher.core import Publisher
from workspace_publisher import source_archive
from tests import test_workspace_publisher_core as fixture_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_manifest_generator', ROOT / 'scripts/write-workspace-source-manifest.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class ArchiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive source 한글 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {'.gitignore': b'build/\ndist/\n__pycache__/\n',
                      'local_app/server.py': b'WORKSPACE_VERSION = "0.23.0"\n',
                      'Publish-Workspace.py': b'# source\r\n',
                      'docs/사용법.txt': '원본 자료\n'.encode()}
        for name, raw in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.raw = source_archive.manifest_bytes(self.files)
        (self.root / source_archive.MANIFEST).write_bytes(self.raw)

    def test_exact_source_inventory_retains_newlines_and_uses_manifest_identity(self):
        info = source_archive.inspect(self.root)
        self.assertEqual('archive', info['sourceKind'])
        self.assertFalse(info['canSync'])
        self.assertEqual('', info['commit'])
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), info['sourceId'])
        descriptor = json.loads(self.raw)
        entry = next(row for row in descriptor['files'] if row['path'] == 'Publish-Workspace.py')
        self.assertEqual(hashlib.sha256(b'# source\r\n').hexdigest(), entry['sha256'])
        target = self.root / 'build/source'
        target.parent.mkdir()
        source_archive.copy_verified(self.root, target, info['sourceId'])
        self.assertEqual(info['sourceId'], source_archive.inspect(target)['sourceId'])
        self.assertEqual(b'# source\r\n', (target / 'Publish-Workspace.py').read_bytes())
        self.assertFalse((target / 'build').exists())

    def test_missing_extra_or_modified_source_is_not_accepted(self):
        original = self.root / 'Publish-Workspace.py'
        raw = original.read_bytes()
        original.unlink()
        with self.assertRaisesRegex(PublisherError, '빠진 파일'):
            source_archive.inspect(self.root)
        original.write_bytes(raw + b'# changed')
        with self.assertRaisesRegex(PublisherError, '원본 목록과 다릅니다'):
            source_archive.inspect(self.root)
        original.write_bytes(raw)
        extra = self.root / '.env'
        extra.write_text('PRIVATE_SETTING=do-not-copy')
        with self.assertRaisesRegex(PublisherError, '추가된 파일'):
            source_archive.inspect(self.root)

    def test_only_local_build_and_cache_folders_are_excluded_from_copy(self):
        for name in ('build/publisher/config.json', 'dist/output.exe', 'local_app/__pycache__/server.pyc'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'local generated data')
        source_archive.inspect(self.root)
        nested = self.root / 'docs/build/extra.txt'
        nested.parent.mkdir(parents=True)
        nested.write_text('not a root build folder')
        with self.assertRaisesRegex(PublisherError, '추가된 파일'):
            source_archive.inspect(self.root)

    def test_missing_malformed_duplicate_and_traversal_manifest_are_rejected(self):
        manifest = self.root / source_archive.MANIFEST
        for raw in (b'not json', b'{}', b'x' * (source_archive.MAX_MANIFEST + 1)):
            manifest.write_bytes(raw)
            with self.subTest(size=len(raw)), self.assertRaises(PublisherError):
                source_archive.inspect(self.root)
        original = json.loads(self.raw)
        for name in ('../outside.py', '/outside.py', 'C:/outside.py', 'local_app\\server.py',
                     'build/secret.txt', '__pycache__/bad.py', 'NUL.py', '.git/config'):
            value = json.loads(self.raw)
            value['files'][0]['path'] = name
            manifest.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(name=name), self.assertRaises(PublisherError):
                source_archive.inspect(self.root)
        original['files'].append(original['files'][0])
        manifest.write_text(json.dumps(original))
        with self.assertRaises(PublisherError):
            source_archive.inspect(self.root)

    def test_manifest_generator_rejects_compiled_or_disguised_payloads(self):
        for name, raw in (('tool.exe', b'fixture'), ('library.dll', b'fixture'),
                          ('sdk.nupkg', b'fixture'), ('innocent.txt', b'MZnative binary'),
                          ('archive.dat', b'PK\x03\x04zip data')):
            with self.subTest(name=name), self.assertRaises(PublisherError):
                source_archive.manifest_bytes({**self.files, name: raw})

    def test_symlink_to_source_or_skipped_folder_is_rejected(self):
        path = self.root / 'linked.py'
        try:
            path.symlink_to(self.root / 'Publish-Workspace.py')
        except OSError:
            self.skipTest('This Windows token cannot create symbolic links.')
        with self.assertRaises(PublisherError):
            source_archive.inspect(self.root)
        path.unlink()
        (self.root / 'build').symlink_to(self.root / 'docs', target_is_directory=True)
        with self.assertRaises(PublisherError):
            source_archive.inspect(self.root)

    def test_cancel_stops_source_check(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(PublisherError, '취소'):
            source_archive.inspect(self.root, cancel=cancel)

    def test_source_read_is_bounded_by_actual_size_and_detects_concurrent_growth(self):
        path = self.root / 'Publish-Workspace.py'
        raw = path.read_bytes()
        calls = []
        class ObservedStream(io.BytesIO):
            def read(self, size=-1):
                calls.append(size)
                return super().read(size)
        with patch.object(Path, 'open', return_value=ObservedStream(raw)):
            self.assertEqual(raw, source_archive._read(path, source_archive.MAX_FILE))
        self.assertEqual([len(raw) + 1], calls)
        # The lstat still reports the original small file, while an opened
        # stream models bytes appended immediately afterward.
        with patch.object(Path, 'open', return_value=ObservedStream(raw + b'growth')):
            with self.assertRaisesRegex(PublisherError, '크기가 바뀌었습니다'):
                source_archive._read(path, source_archive.MAX_FILE)
        self.assertEqual([len(raw) + 1, len(raw) + 1], calls)


class DownloadZipBuildTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.GitAndBuildTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        fixture.write('.gitattributes', '* -text\n')
        fixture.run_git('add', '.gitattributes')
        # Real git archive models GitHub's repository ZIP, including its exact
        # blob bytes, but extraction lives inside a separate parent repository.
        manifest = source_archive.manifest_bytes(generator.staged_files(fixture.repo))
        fixture.write(source_archive.MANIFEST, manifest)
        fixture.run_git('add', source_archive.MANIFEST)
        fixture.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'source inventory')
        archive = fixture.repo / 'build/download.zip'
        fixture.run_git('archive', '--format=zip', '--output=' + str(archive), 'HEAD')
        self.root = fixture.repo / 'build/download-source'
        with zipfile.ZipFile(archive) as incoming:
            incoming.extractall(self.root)
        sdk = self.root / 'build/desktop-sdk/1.0.fixture.nupkg'
        sdk.parent.mkdir(parents=True)
        shutil.copyfile(fixture.repo / 'build/desktop-sdk/1.0.fixture.nupkg', sdk)
        self.publisher = Publisher(self.root, runner=fixture.runner)

    def build(self):
        with patch('local_app.windows_process.powershell_path', return_value='fixture-powershell.exe'):
            return self.publisher.build(fixture_module.CONFIG)

    def test_download_zip_inspect_save_build_and_publish_without_git(self):
        before = source_archive.inspect(self.root)
        with patch('workspace_publisher.git_sync.git', side_effect=AssertionError('archive must never invoke Git')):
            info = self.publisher.inspect_source()
            self.assertFalse(info['canSync'])
            self.publisher.save_config(fixture_module.CONFIG)
            result = self.build()
            self.assertEqual('archive', result['sourceKind'])
            self.assertEqual('', result['commit'])
            self.assertEqual(before['sourceId'], result['sourceId'])
            self.assertEqual(result, Publisher(self.root).load_last_build(fixture_module.CONFIG))
            self.publisher.transport = fixture_module.Registry()
            self.assertTrue(self.publisher.publish(fixture_module.CONFIG, result, 'fixture-token')['verified'])
        self.assertFalse((self.root / '.git').exists())
        self.assertEqual(before, source_archive.inspect(self.root))
        self.assertFalse((self.root / 'workspace-update-source.json').exists())

    def test_source_sync_disabled_with_actionable_explanation_and_no_git(self):
        with patch('workspace_publisher.git_sync.git', side_effect=AssertionError('no Git')):
            for call in (lambda: self.publisher.preview_sync(fixture_module.CONFIG),
                         lambda: self.publisher.sync(fixture_module.CONFIG, {})):
                with self.assertRaisesRegex(PublisherError, 'Git 이력이 없어'):
                    call()

    def test_edits_during_build_fail_before_recording_success(self):
        original_runner = self.fixture.runner
        def tamper(args, **kwargs):
            result = original_runner(args, **kwargs)
            if '-File' not in args:
                (self.root / 'local_app/server.py').write_text('WORKSPACE_VERSION = "0.23.0"\n# edit')
            return result
        self.publisher.runner = tamper
        with self.assertRaisesRegex(PublisherError, '원본 목록과 다릅니다'):
            self.build()
        self.assertFalse((self.publisher.work_root / 'last-build.json').exists())

    def test_copied_source_edits_are_detected_after_compiler_returns(self):
        original_runner = self.fixture.runner
        def tamper(args, **kwargs):
            result = original_runner(args, **kwargs)
            if '-File' not in args:
                (kwargs['cwd'] / 'deploy/New-WorkspaceStandalone.ps1').write_text('# changed')
            return result
        self.publisher.runner = tamper
        with self.assertRaisesRegex(PublisherError, '원본 목록과 다릅니다'):
            self.build()
        self.assertFalse((self.publisher.work_root / 'last-build.json').exists())

    def test_retry_uses_saved_bytes_even_if_newer_source_is_present(self):
        result = self.build()
        (self.root / 'local_app/server.py').write_text('new release downloaded later')
        recovered = Publisher(self.root).load_last_build(fixture_module.CONFIG)
        self.assertEqual(result, recovered)
        self.publisher.transport = fixture_module.Registry()
        edited_config = {**fixture_module.CONFIG, 'notes': '재시도할 변경 안내'}
        self.publisher.save_config(edited_config)
        self.assertEqual(edited_config['notes'], self.publisher.load_config()['notes'])
        self.assertTrue(self.publisher.publish(edited_config, recovered, 'fixture-token')['verified'])
        with self.assertRaisesRegex(PublisherError, '원본 목록과 다릅니다'):
            self.build()

    def test_retry_settings_cannot_escape_fixed_generated_path(self):
        self.build()
        self.publisher.config_path = self.root / 'source-settings.json'
        with self.assertRaisesRegex(PublisherError, '빌드 폴더'):
            self.publisher.save_config(fixture_module.CONFIG)
        self.assertFalse(self.publisher.config_path.exists())

    def test_generator_uses_staged_bytes_not_unstaged_worktree(self):
        original = generator.staged_files(self.fixture.repo)
        self.fixture.write('local_app/server.py', 'WORKSPACE_VERSION = "99.0.0"\n')
        self.assertEqual(original, generator.staged_files(self.fixture.repo))
        self.assertEqual(0, generator.main(['--root', str(self.fixture.repo), '--check']))


if __name__ == '__main__':
    unittest.main()
