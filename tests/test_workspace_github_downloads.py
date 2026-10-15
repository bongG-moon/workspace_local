"""Offline distribution tests: installer aliases must never replace updater ZIPs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from local_app.app_updates import RELEASE_ROOT, _checksum, parse_release
from workspace_publisher.config import PublisherError
from workspace_publisher.source_archive import MANIFEST, manifest_bytes

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('github_downloads', ROOT / 'scripts/prepare-workspace-github-downloads.py')
downloads = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloads)
VERSION = '0.23.25'


class GithubDownloadsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'local_app').mkdir()
        self.server = f'WORKSPACE_VERSION = "{VERSION}"\n'.encode()
        (self.root / 'local_app/server.py').write_bytes(self.server)
        self.release = self.root / 'release'
        self.release.mkdir()
        self.original = {}
        for brand in ('AX-Workspace', 'Company-Workspace'):
            for edition in ('exe', 'vbs'):
                name = f'{brand}-{VERSION}-{edition}.zip'
                with zipfile.ZipFile(self.release / name, 'w') as archive:
                    archive.writestr('fixture.txt', f'original portable bytes for {brand} {edition}')
                self.original[name] = (self.release / name).read_bytes()
        (self.release / 'SHA256SUMS.txt').write_text(''.join(
            f'{hashlib.sha256(raw).hexdigest()}  {name}\n' for name, raw in self.original.items()), encoding='utf-8')
        self.installer = self.root / f'AX-Workspace-{VERSION}-Setup.exe'
        self.installer.write_bytes(b'MZ fixture installer, never a portable launcher')
        self.report = self.root / 'installer-report.json'
        self.metadata = {'version': VERSION, 'installer': str(self.installer),
                         'sha256': downloads.digest(self.installer), 'bytes': self.installer.stat().st_size,
                         'standaloneSha256': 'a' * 64, 'productId': 'AX.Workspace.Desktop', 'testMode': False}
        self.write_report()
        self.source_zip = self.root / 'source.zip'
        self.write_source()
        self.output = self.root / 'prepared'

    def write_report(self, **changes):
        self.report.write_text(json.dumps({**self.metadata, **changes}), encoding='utf-8')

    def write_source(self, *, version=VERSION, prefix='workspace_local/', corrupt=False, extra=None):
        files = {'.gitignore': b'build/\n', 'local_app/server.py': f'WORKSPACE_VERSION = "{version}"\n'.encode()}
        manifest = manifest_bytes(files)
        if corrupt:
            files['local_app/server.py'] += b'# altered after manifest\n'
        with zipfile.ZipFile(self.source_zip, 'w') as archive:
            for name, raw in files.items():
                archive.writestr(prefix + name, raw)
            archive.writestr(prefix + MANIFEST, manifest)
            if extra:
                archive.writestr(*extra)

    def prepare(self):
        return downloads.prepare(self.release, self.report, self.source_zip, self.output, root=self.root)

    def test_old_zip_bytes_preserved_and_all_exe_names_are_identical_installer_aliases(self):
        result = self.prepare()
        self.assertEqual(10, len(result['files']))
        for name, original in self.original.items():
            self.assertEqual(original, (self.output / name).read_bytes())
            self.assertEqual(original, (self.release / name).read_bytes())
        aliases = [path for path in self.output.iterdir() if path.suffix == '.exe']
        self.assertEqual(4, len(aliases))
        self.assertEqual({self.metadata['sha256']}, {downloads.digest(path) for path in aliases})
        checksums = (self.output / 'SHA256SUMS.txt').read_bytes()
        for path in self.output.iterdir():
            if path.name != 'SHA256SUMS.txt':
                self.assertEqual(downloads.digest(path), _checksum(checksums, path.name))
        self.assertEqual(self.source_zip.read_bytes(),
                         (self.output / f'workspace-local-{VERSION}-source.zip').read_bytes())

    def test_existing_updater_ignores_installer_aliases_and_source_archive(self):
        result = self.prepare()
        payload = {'tag_name': 'v' + VERSION, 'draft': False, 'prerelease': False,
                   'html_url': f'{RELEASE_ROOT}/tag/v{VERSION}', 'published_at': '2026-10-15T00:00:00Z',
                   'assets': [{'name': item['name'], 'size': item['bytes'], 'digest': 'sha256:' + item['sha256'],
                               'state': 'uploaded',
                               'browser_download_url': f'{RELEASE_ROOT}/download/v{VERSION}/{item["name"]}'}
                              for item in result['files']]}
        selected = parse_release(payload)['assets']
        self.assertEqual([f'AX-Workspace-{VERSION}-vbs.zip', 'SHA256SUMS.txt',
                          f'AX-Workspace-{VERSION}-exe.zip'], [row['name'] for row in selected])
        checksums = (self.output / 'SHA256SUMS.txt').read_bytes()
        for item in selected:
            if item['name'].endswith('.zip'):
                self.assertEqual(item['digest'], 'sha256:' + _checksum(checksums, item['name']))

    def test_untrusted_report_fields_are_rejected_before_creating_output(self):
        for changes in ({'testMode': True}, {'productId': 'AX.Workspace.Desktop.Test.demo'},
                        {'version': '0.23.24'}, {'bytes': 1}, {'bytes': True}, {'sha256': '0' * 64},
                        {'standaloneSha256': ''}, {'installer': 'AX-Workspace-0.23.25-Setup.exe'}):
            with self.subTest(changes=changes):
                self.write_report(**changes)
                with self.assertRaises(PublisherError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_changed_updater_zip_and_duplicate_checksums_are_rejected(self):
        path = self.release / next(iter(self.original))
        path.write_bytes(b'changed original bytes')
        with self.assertRaises(PublisherError):
            self.prepare()
        self.assertFalse(self.output.exists())
        path.write_bytes(self.original[path.name])
        checksum = self.release / 'SHA256SUMS.txt'
        original = checksum.read_text()
        checksum.write_text(original + original.splitlines()[0] + '\n')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_changed_installer_or_wrong_installer_filename_is_rejected(self):
        original = self.installer.read_bytes()
        self.installer.write_bytes(b'MZ changed bytes')
        with self.assertRaises(PublisherError):
            self.prepare()
        self.installer.write_bytes(original)
        renamed = self.root / 'fixture.exe'
        renamed.write_bytes(original)
        self.write_report(installer=str(renamed))
        with self.assertRaises(PublisherError):
            self.prepare()

    def test_source_inventory_and_version_must_match(self):
        for options in ({'version': '0.23.24'}, {'corrupt': True},
                        {'extra': ('workspace_local/other.txt', b'not in manifest')},
                        {'extra': ('../escape.txt', b'unsafe')},
                        {'extra': ('workspace_local/program.exe', b'MZnot source')}):
            with self.subTest(options=options):
                self.write_source(**options)
                with self.assertRaises(PublisherError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_source_zip_without_prefix_is_supported(self):
        self.write_source(prefix='')
        self.assertEqual(VERSION, self.prepare()['version'])

    def test_existing_output_or_input_path_collision_never_overwrites_files(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_bytes(b'keep')
        with self.assertRaises(PublisherError):
            self.prepare()
        self.assertEqual(b'keep', marker.read_bytes())
        self.output = self.release
        with self.assertRaises(PublisherError):
            self.prepare()
        for name, raw in self.original.items():
            self.assertEqual(raw, (self.release / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
