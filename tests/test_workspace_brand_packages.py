"""AX naming remains compatible with previously installed update clients."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from local_app import app_updates, managed_launcher, update_source
from workspace_publisher.config import PublisherError, runtime_config
from workspace_publisher.publish import endpoint, publish, verify_files
from tests.test_workspace_app_updates import release
from tests.test_workspace_publisher_core import CONFIG, VERSION, Registry, release_files


class BrandPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = release_files(self.directory, branded=True)
        self.source = update_source.source_from_config(runtime_config(CONFIG))

    def test_both_exe_names_unpack_to_identical_binary(self):
        extracted = [managed_launcher.unpack_archive(
            (self.directory / f'{brand}-{VERSION}-exe.zip').read_bytes(), VERSION)
            for brand in ('AX-Workspace', 'Company-Workspace')]
        self.assertEqual([b'MZfixture', b'MZfixture'], extracted)

    def test_mixed_zip_members_are_rejected(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr(f'AX-Workspace-{VERSION}.exe', b'MZfixture')
            archive.writestr(f'Company-Workspace-{VERSION}.exe.sha256', 'invalid')
            archive.writestr('README.txt', 'fixture')
        with self.assertRaises(ValueError):
            managed_launcher.unpack_archive(raw.getvalue(), VERSION)

    def test_github_prefers_ax_but_keeps_legacy_releases_readable(self):
        payload, _ = release(VERSION)
        self.assertTrue(app_updates.parse_release(payload)['assets'][0]['name'].startswith('Company-Workspace-'))
        branded = copy.deepcopy(payload['assets'][0])
        branded['name'] = branded['name'].replace('Company-Workspace-', 'AX-Workspace-')
        branded['browser_download_url'] = branded['browser_download_url'].replace('Company-Workspace-', 'AX-Workspace-')
        payload['assets'].append(branded)
        result = app_updates.parse_release(payload)
        self.assertEqual(branded['name'], result['assets'][0]['name'])
        self.assertEqual('SHA256SUMS.txt', result['assets'][1]['name'])
        payload['assets'].append(copy.deepcopy(branded))
        with self.assertRaises(ValueError):
            app_updates.parse_release(payload)

    def test_publisher_uploads_ax_and_legacy_but_channel_stays_old_client_compatible(self):
        registry = Registry()
        publish(CONFIG, self.result, 'fixture-token', transport=registry)
        for row in self.result['files']:
            self.assertEqual(row['sha256'], hashlib.sha256(
                registry.files[endpoint(CONFIG, VERSION, row['name'])]).hexdigest())
        channel = json.loads(registry.files[endpoint(CONFIG, VERSION, 'latest.json', channel=True)])
        self.assertEqual([f'Company-Workspace-{VERSION}-vbs.zip', 'SHA256SUMS.txt',
                          f'Company-Workspace-{VERSION}-exe.zip'], [row['name'] for row in channel['files']])
        self.assertEqual(VERSION, app_updates.parse_manifest(channel, self.source)['tag_name'][1:])
        self.assertEqual('company-workspace', self.source.config['packageName'])
        self.assertEqual('company-workspace-channel', self.source.config['channelPackage'])

    def test_branded_channel_and_exact_urls_are_accepted_without_changing_source_identity(self):
        rows = [row for row in self.result['files'] if row['name'].startswith('AX-') or row['name'] == 'SHA256SUMS.txt']
        payload = {'schema': 1, 'channel': 'stable', 'version': VERSION, 'publishedAt': '2026-10-09T00:00:00Z',
                   'title': 'AX Workspace', 'notes': '',
                   'files': [{key: row[key] for key in ('name', 'size', 'sha256')} for row in rows]}
        self.assertEqual(f'AX-Workspace-{VERSION}-vbs.zip', app_updates.parse_manifest(payload, self.source)['assets'][0]['name'])
        before = self.source.identity
        for brand in ('AX-Workspace', 'Company-Workspace'):
            url = self.source.asset_url(VERSION, f'{brand}-{VERSION}-exe.zip')
            self.assertEqual(url, self.source.validate_initial(url))
        self.assertEqual(before, self.source.identity)
        with self.assertRaises(ValueError):
            self.source.asset_url(VERSION, f'Other-{VERSION}-exe.zip')

    def test_missing_alias_or_different_payload_is_not_publishable(self):
        ax = self.directory / f'AX-Workspace-{VERSION}-vbs.zip'
        ax.write_bytes(b'changed')
        with self.assertRaises(PublisherError):
            verify_files(self.directory, VERSION, runtime_config(CONFIG))

    def test_managed_state_path_and_legacy_vbs_are_preserved(self):
        value = managed_launcher.stage(self.directory / 'state', VERSION, b'MZfixture')
        self.assertTrue(value['path'].endswith('/Company-Workspace.exe'))
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / 'Company-Workspace.vbs').read_bytes(), (root / 'AX-Workspace.vbs').read_bytes())


if __name__ == '__main__':
    unittest.main()
