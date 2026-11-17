"""Registration keys travel only into explicit private app payloads."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote, quote_plus
import zipfile

from tests import test_workspace_publisher_core as fixtures
from workspace_publisher.config import (PublisherError, normalize, registration_config,
    registration_metadata, checked_registration_metadata)
from workspace_publisher.core import Publisher

KEY = 'synthetic-registration-key-not-for-production-+/=' * 2
OTHER_KEY = 'another-synthetic-registration-key-for-tests' * 2
CONFIG = {**fixtures.CONFIG, 'registrationEnabled': True,
          'registrationBaseUrl': 'http://registration.example/internal'}


class RegistrationConfigTests(unittest.TestCase):
    def test_legacy_preferences_are_disabled_and_credentials_are_never_settings(self):
        self.assertFalse(normalize(fixtures.CONFIG)['registrationEnabled'])
        for key in ('registrationKey', 'registration_key', 'key'):
            with self.subTest(key=key), self.assertRaises(PublisherError):
                normalize({**CONFIG, key: KEY})

    def test_http_registration_is_separate_from_https_gitlab(self):
        self.assertEqual('http://registration.example/internal', normalize(CONFIG)['registrationBaseUrl'])
        self.assertEqual(KEY, registration_config(CONFIG, KEY)['key'])
        with self.assertRaises(PublisherError):
            normalize({**CONFIG, 'baseUrl': 'http://gitlab.example'})
        for address in ('http://user:secret@registration.example', 'http://registration.example/?key=x',
                        'http://registration.example/../private', 'file:///private'):
            with self.subTest(address=address), self.assertRaises(PublisherError):
                normalize({**CONFIG, 'registrationBaseUrl': address})

    def test_private_key_validation_and_public_fingerprint(self):
        for key in ('', 'short', 'x' * 257, 123):
            with self.subTest(key_type=type(key).__name__), self.assertRaises(PublisherError):
                registration_config(CONFIG, key)
        metadata = registration_metadata(registration_config(CONFIG, KEY))
        self.assertEqual(hashlib.sha256(KEY.encode()).hexdigest(), metadata['keyFingerprint'])
        self.assertNotIn(KEY, json.dumps(metadata))
        self.assertEqual(metadata, checked_registration_metadata(metadata))
        with self.assertRaises(PublisherError):
            checked_registration_metadata({**metadata, 'key': KEY})


class PublisherRegistrationBuildTests(unittest.TestCase):
    write = fixtures.GitAndBuildTests.write
    run_git = fixtures.GitAndBuildTests.run_git

    def setUp(self):
        fixtures.GitAndBuildTests.setUp(self)
        self.events = []
        self.publisher.emit = self.events.append

    def runner(self, args, *, cwd, **kwargs):
        result = fixtures.GitAndBuildTests.runner(self, args, cwd=cwd, **kwargs)
        if '-RegistrationConfig' not in args:
            return result
        private = json.loads(Path(args[args.index('-RegistrationConfig') + 1]).read_text(encoding='utf-8'))
        if 'New-WorkspaceStandalone.ps1' in str(args):
            report = cwd / f'build/workspace-standalone-{fixtures.VERSION}-build.json'
            value = json.loads(report.read_text())
            value['payloadFiles'] += 1
            report.write_text(json.dumps(value))
        elif 'New-WorkspaceRelease.ps1' in str(args):
            output = Path(args[args.index('-OutputDirectory') + 1])
            for archive_path in output.glob('*-vbs.zip'):
                with zipfile.ZipFile(archive_path, 'a', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('Company-Workspace/workspace-registration.json', json.dumps(private))
            packages = sorted(path for path in output.iterdir() if path.suffix in {'.zip', '.exe'})
            (output / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() +
                '  ' + path.name + '\n' for path in packages), encoding='utf-8')
            report = cwd / f'build/workspace-release-{fixtures.VERSION}.json'
            value = json.loads(report.read_text())
            value['identicalSourceFiles'] += 1
            value['privateRegistrationConfigured'] = True
            report.write_text(json.dumps(value))
        return result

    def build(self, key=KEY):
        with patch('local_app.windows_process.powershell_path', return_value='powershell.exe'):
            return self.publisher.build(CONFIG, registration_key=key)

    def test_missing_key_fails_before_source_copy_runner_or_saved_preferences(self):
        with self.assertRaisesRegex(PublisherError, '인증키'):
            self.publisher.build(CONFIG)
        self.assertFalse(self.calls)
        self.assertFalse(self.publisher.config_path.exists())

    def test_key_is_forwarded_to_both_builds_and_bundle_check_by_file_only(self):
        result = self.build()
        self.assertEqual(3, len(self.calls))
        private_paths = []
        for index, args in enumerate(self.calls):
            flag = '-RegistrationConfig' if index < 2 else '--registration-config'
            self.assertIn(flag, args)
            private_paths.append(Path(args[args.index(flag) + 1]))
            self.assertNotIn(KEY, ' '.join(args))
        self.assertEqual(1, len(set(private_paths)))
        self.assertEqual(KEY, json.loads(private_paths[0].read_text())['key'])
        self.assertEqual(registration_metadata(registration_config(CONFIG, KEY)), result['registrationConfig'])
        for path in (self.publisher.config_path, self.publisher.work_root / 'last-build.json',
                     Path(result['directory']).parent / 'build-result.json'):
            self.assertNotIn(KEY, path.read_text(encoding='utf-8'))
        self.assertNotIn(KEY, json.dumps(result) + json.dumps(self.events))
        for archive_path in Path(result['directory']).glob('*-vbs.zip'):
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(KEY, json.loads(archive.read('Company-Workspace/workspace-registration.json'))['key'])

    def test_ready_private_build_can_be_restored_and_published_without_key_reentry(self):
        result = self.build()
        restarted = Publisher(self.repo, transport=fixtures.Registry())
        self.assertEqual(result, restarted.load_last_build(CONFIG))
        with patch.object(restarted, 'build', side_effect=AssertionError('must reuse verified files')):
            self.assertEqual(result, restarted.prepare_deploy(CONFIG))
        self.assertTrue(restarted.publish(CONFIG, result, 'fixture-token')['verified'])

    def test_new_key_or_disabled_collection_never_reuses_old_private_payload(self):
        result = self.build()
        with patch.object(self.publisher, 'build', return_value={'new': True}) as build:
            self.assertEqual({'new': True}, self.publisher.prepare_deploy(CONFIG, build_result=result, registration_key=OTHER_KEY))
            self.assertEqual(OTHER_KEY, build.call_args.kwargs['registration_key'])
        with self.assertRaises(PublisherError):
            self.publisher.load_last_build(CONFIG, registration_key=OTHER_KEY)
        with patch.object(self.publisher, 'build', return_value={'off': True}) as build:
            disabled = {**CONFIG, 'registrationEnabled': False}
            self.assertEqual({'off': True}, self.publisher.prepare_deploy(disabled, build_result=result))
            self.assertNotIn('registration_key', build.call_args.kwargs)

    def test_registration_metadata_tampering_is_detected_against_private_payload(self):
        result = self.build()
        result['registrationConfig']['keyFingerprint'] = '0' * 64
        for path in (self.publisher.work_root / 'last-build.json', Path(result['directory']).parent / 'build-result.json'):
            path.write_text(json.dumps(result), encoding='utf-8')
        with self.assertRaises(PublisherError):
            self.publisher.load_last_build(CONFIG)

    def test_build_logs_and_raised_tool_errors_redact_raw_and_encoded_key(self):
        def runner(args, *, cwd, **kwargs):
            text = '\n'.join([KEY, quote(KEY, safe=''), quote_plus(KEY)])
            log = self.publisher._build_log(cwd, 'probe', 'synthetic failure', text, text)
            self.assertNotIn(KEY, log.read_text(encoding='utf-8'))
            self.assertNotIn(quote(KEY, safe=''), log.read_text(encoding='utf-8'))
            crossing = self.publisher._build_log(cwd, 'crossing', 'synthetic failure',
                'x' * (65536 - len(KEY) + 1) + KEY)
            self.assertNotIn(KEY[:-1], crossing.read_text(encoding='utf-8'))
            raise RuntimeError('synthetic failure ' + KEY)
        self.publisher.runner = runner
        with self.assertRaises(PublisherError) as error:
            self.build()
        self.assertNotIn(KEY, str(error.exception))
        self.assertNotIn(quote(KEY, safe=''), str(error.exception))
        self.assertEqual((), self.publisher._build_secrets)

    def test_unicode_keys_are_redacted_before_encoding_fallback_and_limit_cut(self):
        key = 'é' * 32
        self.publisher._build_secrets = (key,)
        cwd = self.publisher.work_root / 'build-log-fixture/source'
        cwd.mkdir(parents=True)
        bound = 65536 + 8192
        output = key.encode('utf-8') + b'x' * (bound - len(key.encode('utf-8')) - 1) + '😀'.encode('utf-8')
        cases = {
            'truncated-utf8': output,
            'mixed-encoding': key.encode('utf-8') + b'\xff',
            'legacy-encoding': ('key=' + '가' * 32).encode('cp949'),
            'utf16': key.encode('utf-16-le'),
            'quoted-utf16': quote(key, safe='').encode('utf-16-be'),
        }
        for name, raw in cases.items():
            with self.subTest(encoding=name):
                self.publisher._build_secrets = (key, '가' * 32)
                path = self.publisher._build_log(cwd, name, 'synthetic output', raw)
                logged = path.read_text(encoding='utf-8')
                for secret in (key, '가' * 32):
                    self.assertNotIn(secret, logged)
                    for encoding in ('utf-8', 'cp949', 'utf-16-le', 'utf-16-be'):
                        try:
                            transformed = secret.encode('utf-8').decode(encoding)
                        except UnicodeError:
                            continue
                        self.assertNotIn(transformed, logged)
                self.assertNotIn(quote(key, safe=''), logged)
        self.publisher._build_secrets = ()


if __name__ == '__main__':
    unittest.main()
