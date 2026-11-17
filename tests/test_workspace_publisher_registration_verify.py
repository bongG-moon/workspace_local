"""Registration credentials enter only explicitly selected private app packages."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tests.test_workspace_publisher_core import CONFIG, VERSION, Registry, release_files
from workspace_publisher.config import PublisherError, registration_metadata, runtime_config
from workspace_publisher.publish import publish, verify_files
from workspace_publisher.source_publish import _private_path


class PublisherRegistrationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'release'
        self.result = release_files(self.directory, branded=True)
        self.private = {'schema': 1, 'baseUrl': 'http://registration.example',
                        'key': 'synthetic-registration-test-key-' * 2}
        self.metadata = registration_metadata(self.private)

    def inject(self, private):
        legacy = self.directory / f'Company-Workspace-{VERSION}-vbs.zip'
        with zipfile.ZipFile(legacy) as archive:
            rows = {name: archive.read(name) for name in archive.namelist()}
        rows['Company-Workspace/workspace-registration.json'] = json.dumps(private).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, raw in rows.items():
                archive.writestr(name, raw)
        legacy.write_bytes(output.getvalue())
        (self.directory / f'AX-Workspace-{VERSION}-vbs.zip').write_bytes(output.getvalue())
        packages = sorted(self.directory.glob('*.zip'))
        (self.directory / 'SHA256SUMS.txt').write_text(''.join(
            hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
            for path in packages), encoding='utf-8')

    def verify(self, expected):
        return verify_files(self.directory, VERSION, runtime_config(CONFIG),
                            expected_registration=expected)

    def test_private_config_requires_explicit_matching_metadata(self):
        self.inject(self.private)
        with self.assertRaises(PublisherError):
            self.verify(None)
        files = self.verify(self.metadata)
        self.assertEqual(5, len(files))
        self.assertNotIn(self.private['key'], json.dumps(files))

    def test_missing_configuration_and_changed_key_are_rejected(self):
        with self.assertRaises(PublisherError):
            self.verify(self.metadata)
        self.inject({**self.private, 'key': 'other-synthetic-key-' * 3})
        with self.assertRaises(PublisherError) as error:
            self.verify(self.metadata)
        self.assertNotIn(self.private['key'], str(error.exception))

    def test_changed_address_and_raw_key_metadata_are_rejected(self):
        self.inject({**self.private, 'baseUrl': 'http://other.example'})
        with self.assertRaises(PublisherError):
            self.verify(self.metadata)
        with self.assertRaises(PublisherError):
            self.verify(self.private)

    def test_unexpected_private_schema_does_not_reach_publish(self):
        self.inject({**self.private, 'mongodbUri': 'must-not-enter-application'})
        with self.assertRaises(PublisherError):
            self.verify(self.metadata)

    def test_publication_requires_enabled_matching_destination_before_requests(self):
        self.inject(self.private)
        files = self.verify(self.metadata)
        result = {**self.result, 'files': files, 'registrationConfig': self.metadata}
        for extra in ({}, {'registrationEnabled': True, 'registrationBaseUrl': 'http://different.example'}):
            registry = Registry()
            with self.subTest(extra=extra), self.assertRaises(PublisherError):
                publish({**CONFIG, **extra}, result, 'synthetic-publish-token',
                        token_kind='deploy', transport=registry)
            self.assertEqual([], registry.calls)

    def test_matching_private_publication_keeps_key_out_of_manifest(self):
        self.inject(self.private)
        result = {**self.result, 'files': self.verify(self.metadata), 'registrationConfig': self.metadata}
        config = {**CONFIG, 'registrationEnabled': True, 'registrationBaseUrl': self.private['baseUrl']}
        registry = Registry()
        published = publish(config, result, 'synthetic-publish-token', token_kind='deploy', transport=registry)
        self.assertTrue(published['verified'])
        manifests = [body for url, body in registry.files.items()
                     if url.endswith(('latest.json', 'manifest.json', 'release-notes.json'))]
        self.assertTrue(manifests)
        self.assertTrue(all(self.private['key'].encode() not in body for body in manifests))

    def test_registration_file_cannot_enter_source_publication(self):
        for name in ('workspace-registration.json', 'config/WORKSPACE-REGISTRATION.JSON'):
            with self.subTest(name=name), self.assertRaises(PublisherError):
                _private_path(name)


if __name__ == '__main__':
    unittest.main()
