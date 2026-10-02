"""Site settings must be validated before becoming part of a shipped app."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_update_source', ROOT / 'scripts/prepare-workspace-update-source.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class UpdatePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'settings.json'
        self.output = self.root / 'payload/workspace-update-source.json'
        self.settings = {'schema': 1, 'provider': 'gitlab', 'baseUrl': 'https://gitlab.example.test/team',
                         'projectId': '123', 'packageName': 'company-workspace',
                         'channelPackage': 'company-workspace-channel', 'channelVersion': '0.0.0',
                         'allowedDownloadOrigins': []}

    def test_generated_settings_are_canonical_and_source_is_unchanged(self):
        self.config.write_text(json.dumps(self.settings, indent=4), encoding='utf-8-sig')
        original = self.config.read_bytes()
        first = helper.prepare(self.config, self.output)
        data = self.output.read_bytes()
        self.assertEqual(self.settings, json.loads(data))
        self.assertEqual(original, self.config.read_bytes())
        self.assertEqual(first, helper.prepare(self.config, self.output))
        self.assertEqual(data, self.output.read_bytes())

    def test_credentials_and_unknown_fields_are_not_bundled(self):
        for name in ('token', 'sshKey', 'authorization', 'password'):
            with self.subTest(name=name):
                self.config.write_text(json.dumps(dict(self.settings, **{name: 'do-not-ship'})), encoding='utf-8')
                with self.assertRaises(ValueError):
                    helper.prepare(self.config, self.output)
                self.assertFalse(self.output.exists())

    def test_invalid_oversize_and_insecure_inputs_never_create_payload(self):
        for data in ('not json', 'x' * (32 * 1024 + 1), json.dumps(dict(self.settings, baseUrl='http://example.test'))):
            with self.subTest(data=data[:30]):
                self.config.write_text(data, encoding='utf-8')
                with self.assertRaises(ValueError):
                    helper.prepare(self.config, self.output)
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
