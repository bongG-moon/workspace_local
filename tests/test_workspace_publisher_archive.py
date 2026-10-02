"""Verified release ZIPs, including real Windows file-sharing collisions."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from local_app.app_updates import verify_archive
from local_app.update_source import source_from_config
from tests import test_workspace_publisher_core as publisher_fixtures
from workspace_publisher import archive
from workspace_publisher.config import PublisherError


class ArchiveFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='publisher zip 한글 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'Company-Workspace'
        self.source.mkdir()
        (self.source / 'empty-folder').mkdir()
        self.destination = self.root / 'Company-Workspace.zip'
        self.files = {
            'local_app/web/js/modules/input-keys.js': b'export const keys = ["Enter", "Tab"];\r\n',
            'local_app/server.py': b'WORKSPACE_VERSION = "0.23.3"\n',
            'docs/사용 안내.txt': '한글 경로와 내용을 그대로 보관합니다.\n'.encode('utf-8'),
            'desktop/Workspace.Desktop.exe': b'MZ' + bytes(range(256)) * 9,
            '.hidden-file': b'hidden source content',
            'empty.txt': b'',
        }
        for name, data in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.locked_file = self.source / 'local_app/web/js/modules/input-keys.js'

    def assert_archive(self, path=None, *, include_root=True, expected=None):
        expected = self.files if expected is None else expected
        prefix = self.source.name + '/' if include_root else ''
        with zipfile.ZipFile(path or self.destination) as result:
            names = [item.filename for item in result.infolist() if not item.is_dir()]
            self.assertEqual({prefix + name for name in expected}, set(names))
            self.assertEqual(len(expected), len(names))
            self.assertIn(prefix + 'empty-folder/', result.namelist())
            self.assertIsNone(result.testzip())
            for name, data in expected.items():
                actual = result.read(prefix + name)
                self.assertEqual(len(data), len(actual), name)
                self.assertEqual(hashlib.sha256(data).digest(), hashlib.sha256(actual).digest(), name)


class ArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_include_root_and_contents_only_layout_preserve_every_file_hash(self):
        for include_root in (True, False):
            with self.subTest(include_root=include_root):
                destination = self.root / ('root.zip' if include_root else 'contents.zip')
                self.assertEqual(destination, archive.create_archive(self.source, destination, include_root=include_root))
                self.assert_archive(destination, include_root=include_root)

    def test_generated_vbs_zip_passes_actual_app_update_archive_validation(self):
        result = publisher_fixtures.release_files(self.root / 'valid-release')
        original = Path(result['directory']) / f'Company-Workspace-{publisher_fixtures.VERSION}-vbs.zip'
        extracted = self.root / 'extracted-valid-release'
        with zipfile.ZipFile(original) as incoming:
            original_files = {item.filename: incoming.read(item) for item in incoming.infolist() if not item.is_dir()}
            incoming.extractall(extracted)
        output = self.root / 'verified-repacked-vbs.zip'
        archive.create_archive(extracted / 'Company-Workspace', output, include_root=True)
        with zipfile.ZipFile(output) as rebuilt:
            self.assertNotIn('Company-Workspace/', rebuilt.namelist())
            rebuilt_files = {item.filename: rebuilt.read(item) for item in rebuilt.infolist() if not item.is_dir()}
        self.assertEqual(original_files, rebuilt_files)
        # This is the same production validator called when the running app
        # verifies a downloaded update; a root-only directory entry is invalid.
        verify_archive(output.read_bytes(), publisher_fixtures.VERSION,
                       source=source_from_config(result['runtimeConfig']))

    def test_existing_destination_is_not_replaced(self):
        self.destination.write_bytes(b'existing reviewed release')
        with self.assertRaises(PublisherError):
            archive.create_archive(self.source, self.destination)
        self.assertEqual(b'existing reviewed release', self.destination.read_bytes())

    def test_destination_created_during_verification_is_not_replaced(self):
        verify = archive._verify_archive
        def competing_output(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.destination.write_bytes(b'another release created concurrently')
            return result
        with patch.object(archive, '_verify_archive', side_effect=competing_output), self.assertRaises(PublisherError):
            archive.create_archive(self.source, self.destination)
        self.assertEqual(b'another release created concurrently', self.destination.read_bytes())

    def test_destination_inside_source_does_not_modify_source(self):
        before = set(self.source.rglob('*'))
        destination = self.source / 'output.zip'
        with self.assertRaises(PublisherError):
            archive.create_archive(self.source, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(before, set(self.source.rglob('*')))

    def test_readonly_source_is_readable_and_attribute_is_preserved(self):
        self.locked_file.chmod(stat.S_IREAD)
        self.addCleanup(self.locked_file.chmod, stat.S_IWRITE | stat.S_IREAD)
        archive.create_archive(self.source, self.destination)
        self.assert_archive()
        self.assertFalse(self.locked_file.stat().st_mode & stat.S_IWRITE)

    def test_previously_modified_files_and_exe_metadata_are_not_false_changes(self):
        # On Windows Python 3.13, path stat and fd stat expose different ctime
        # meanings; .exe paths also acquire executable mode bits only by name.
        # Preserve a real creation/write gap instead of relying on clock ticks.
        time.sleep(0.02)
        self.files['.hidden-file'] = b'a file edited after creation'
        (self.source / '.hidden-file').write_bytes(self.files['.hidden-file'])
        archive.create_archive(self.source, self.destination)
        self.assert_archive()

    def test_source_changes_after_archive_verification_are_not_success(self):
        for change in ('modify_same_size_and_mtime', 'remove', 'add'):
            with self.subTest(change=change):
                original = self.locked_file.read_bytes()
                before = self.locked_file.stat()
                extra = self.source / 'extra-unreviewed.txt'
                verify = archive._verify_archive
                def changed(*args, **kwargs):
                    result = verify(*args, **kwargs)
                    if change == 'modify_same_size_and_mtime':
                        self.locked_file.write_bytes(b'x' * len(original))
                        os.utime(self.locked_file, ns=(before.st_atime_ns, before.st_mtime_ns))
                    elif change == 'remove':
                        self.locked_file.unlink()
                    else:
                        extra.write_bytes(b'unreviewed')
                    return result
                try:
                    with patch.object(archive, '_verify_archive', side_effect=changed) as verification:
                        with self.assertRaises(PublisherError):
                            archive.create_archive(self.source, self.destination)
                    verification.assert_called_once()
                    self.assertFalse(self.destination.exists())
                finally:
                    self.locked_file.write_bytes(original)
                    os.utime(self.locked_file, ns=(before.st_atime_ns, before.st_mtime_ns))
                    extra.unlink(missing_ok=True)

    def test_modified_missing_or_added_zip_member_never_becomes_final_output(self):
        for change in ('modify', 'remove', 'add'):
            with self.subTest(change=change):
                verify = archive._verify_archive
                def damaged(zip_path, expected):
                    path = Path(zip_path)
                    with zipfile.ZipFile(path) as incoming:
                        entries = [(item, incoming.read(item)) for item in incoming.infolist()]
                    replacement = path.with_name(path.name + '.rewritten')
                    with zipfile.ZipFile(replacement, 'w', compression=zipfile.ZIP_DEFLATED) as outgoing:
                        selected = next(item.filename for item, _ in entries if not item.is_dir())
                        for item, content in entries:
                            if item.filename == selected:
                                if change == 'remove':
                                    continue
                                if change == 'modify':
                                    content = b'corrupt content'
                            outgoing.writestr(item, content)
                        if change == 'add':
                            outgoing.writestr('unreviewed.txt', b'unreviewed')
                    os.replace(replacement, path)
                    return verify(path, expected)
                with patch.object(archive, '_verify_archive', side_effect=damaged) as verification:
                    with self.assertRaises(PublisherError):
                        archive.create_archive(self.source, self.destination)
                verification.assert_called_once()
                self.assertFalse(self.destination.exists())

    def test_source_and_zip_hashing_use_bounded_stream_reads(self):
        large = self.source / 'large.bin'
        chunk = bytes(range(256)) * 4096
        with large.open('wb') as outgoing:
            for _ in range(6):
                outgoing.write(chunk)
        source_reads, zip_reads = [], []
        open_source, zip_read = archive._open_source, zipfile.ZipExtFile.read
        class ObservedSource:
            def __init__(self, original):
                self.original = original
            def __enter__(self):
                self.original.__enter__()
                return self
            def __exit__(self, *args):
                return self.original.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.original, name)
            def read(self, size=-1):
                source_reads.append(size)
                if not 0 < size <= 1024 * 1024:
                    raise AssertionError('Source reads must remain bounded')
                return self.original.read(size)
        def observed_zip(stream, size=-1):
            zip_reads.append(size)
            if not 0 < size <= 1024 * 1024:
                raise AssertionError('ZIP verification reads must remain bounded')
            return zip_read(stream, size)
        with patch.object(archive, '_open_source', side_effect=lambda path: ObservedSource(open_source(path))), \
                patch.object(zipfile.ZipExtFile, 'read', observed_zip):
            archive.create_archive(self.source, self.destination)
        self.assertGreater(len(source_reads), 6)
        self.assertGreater(len(zip_reads), 6)
        with zipfile.ZipFile(self.destination) as result:
            actual = result.read(self.source.name + '/large.bin')
        digest = hashlib.sha256()
        for _ in range(6):
            digest.update(chunk)
        self.assertEqual(digest.digest(), hashlib.sha256(actual).digest())

    def test_long_korean_paths_are_preserved(self):
        relative = '/'.join(['깊은폴더_' + 'a' * 25] * 9) + '/자료.txt'
        target = self.source / relative
        native = lambda path: '\\\\?\\' + str(path) if os.name == 'nt' else str(path)
        self.addCleanup(shutil.rmtree, native(self.source))
        os.makedirs(native(target.parent))
        with open(native(target), 'wb') as outgoing:
            outgoing.write('긴 경로의 원문'.encode('utf-8'))
        archive.create_archive(self.source, self.destination)
        self.assert_archive(expected={**self.files, relative: '긴 경로의 원문'.encode('utf-8')})

    def test_nonsharing_access_denied_is_not_retried(self):
        failure = PermissionError('access denied fixture')
        failure.winerror = 5
        with patch.object(archive, '_open_source', side_effect=failure) as opener, \
                patch.object(archive.time, 'sleep') as sleep, self.assertRaises(PublisherError):
            archive.create_archive(self.source, self.destination, attempts=8, retry_delay=0.05)
        opener.assert_called_once()
        sleep.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_cleanup_failure_preserves_original_error_and_is_bounded(self):
        for winerror, expected_calls in ((32, 2), (5, 1)):
            with self.subTest(winerror=winerror):
                failure = PermissionError('cleanup is blocked')
                failure.winerror = winerror
                before = set(self.root.iterdir())
                with patch.object(archive, '_verify_archive', side_effect=PublisherError('original ZIP verification failure')), \
                        patch.object(archive.os, 'unlink', side_effect=failure) as remove:
                    with self.assertRaises(PublisherError) as caught:
                        archive.create_archive(self.source, self.destination, attempts=2, retry_delay=0)
                self.assertEqual(expected_calls, remove.call_count)
                self.assertIn('original ZIP verification failure', str(caught.exception))
                self.assertIn(f'WinError {winerror}', str(caught.exception))
                self.assertFalse(self.destination.exists())
                leftover = set(self.root.iterdir()) - before
                self.assertEqual(1, len(leftover))
                for path in leftover:
                    self.assertTrue(path.name.startswith('.workspace-archive-'))
                    path.unlink()

    def test_symlink_is_rejected(self):
        link = self.source / 'linked.js'
        try:
            link.symlink_to(self.locked_file)
        except OSError:
            self.skipTest('This Windows token cannot create symbolic links.')
        with self.assertRaises(PublisherError):
            archive.create_archive(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows reparse-point behavior')
    def test_windows_directory_junction_is_rejected(self):
        target = self.root / 'outside-source'
        target.mkdir()
        (target / 'private.txt').write_bytes(b'not in the source tree')
        link = self.source / 'junction'
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            self.skipTest('This Windows token cannot create directory junctions.')
        self.addCleanup(link.rmdir)
        with self.assertRaises(PublisherError):
            archive.create_archive(self.source, self.destination)
        self.assertFalse(self.destination.exists())


@unittest.skipUnless(os.name == 'nt', 'Requires Windows file-sharing semantics')
class WindowsLockTests(ArchiveFixture, unittest.TestCase):
    """No application is closed and no machine security setting is changed."""

    def held_handle(self, share):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(self.locked_file), 0x80000000, share, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        mutex = threading.Lock()
        state = {'open': True}
        def release():
            with mutex:
                if state['open']:
                    if not close(handle):
                        raise ctypes.WinError(ctypes.get_last_error())
                    state['open'] = False
        self.addCleanup(release)
        return release

    def test_real_shared_read_handle_does_not_block_archive(self):
        self.held_handle(0x00000001)  # FILE_SHARE_READ
        archive.create_archive(self.source, self.destination)
        self.assert_archive()

    def test_shared_handle_reproduces_compress_archive_failure_but_new_helper_succeeds(self):
        self.held_handle(0x00000001 | 0x00000002 | 0x00000004)  # SHARE_READ | WRITE | DELETE
        windows = Path(os.environ.get('WINDIR', 'C:/Windows'))
        powershell = windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        if not powershell.is_file():
            self.skipTest('Windows PowerShell is unavailable.')
        environment = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
        environment['WORKSPACE_ARCHIVE_TEST_SOURCE'] = str(self.source)
        environment['WORKSPACE_ARCHIVE_TEST_DESTINATION'] = str(self.root / 'old-compress.zip')
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                                 'Compress-Archive -LiteralPath $env:WORKSPACE_ARCHIVE_TEST_SOURCE '
                                 '-DestinationPath $env:WORKSPACE_ARCHIVE_TEST_DESTINATION -ErrorAction Stop'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertNotEqual(0, result.returncode, 'The old exclusive-open behavior was not reproduced.')
        self.assertIn(b'input-keys.js', result.stdout + result.stderr)
        archive.create_archive(self.source, self.destination)
        self.assert_archive()

    def test_real_exclusive_lock_retries_until_timed_release(self):
        release = self.held_handle(0)
        timer = threading.Timer(0.15, release)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        archive.create_archive(self.source, self.destination, attempts=8, retry_delay=0.05)
        self.assertGreaterEqual(time.monotonic() - started, 0.10)
        self.assert_archive()

    def test_real_persistent_exclusive_lock_fails_within_retry_bound(self):
        self.held_handle(0)
        started = time.monotonic()
        with self.assertRaises(PublisherError) as failure:
            archive.create_archive(self.source, self.destination, attempts=3, retry_delay=0.01)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn('input-keys.js', str(failure.exception))
        self.assertFalse(self.destination.exists())
        self.assertEqual([self.source], list(self.root.iterdir()))


if __name__ == '__main__':
    unittest.main()
