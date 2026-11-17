"""Private registration settings are opt-in, validated, and never echoed."""
import getpass
import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/prepare-workspace-registration.py'
spec = importlib.util.spec_from_file_location('prepare_registration', SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RegistrationPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'payload/workspace-registration.json'
        self.source = self.root / 'private.json'
        self.settings = {'schema': 1, 'baseUrl': helper.DEFAULT_BASE_URL,
                         'key': 'not-a-production-key-' * 3}

    def test_hidden_key_prompt_is_enough_with_the_corporate_default_address(self):
        prompted = []
        def prompt(message):
            prompted.append(message)
            return self.settings['key']
        helper.prepare(None, self.output, prompt=prompt)
        self.assertEqual(self.settings, json.loads(self.output.read_bytes()))
        self.assertEqual(1, len(prompted))

    def test_input_copy_is_validated_and_never_changes_the_source(self):
        self.source.write_text(json.dumps(self.settings, indent=4), encoding='utf-8-sig')
        original = self.source.read_bytes()
        helper.prepare(self.source, self.output)
        self.assertEqual(original, self.source.read_bytes())
        self.assertEqual(self.settings, json.loads(self.output.read_bytes()))

    def test_invalid_input_and_unknown_fields_create_no_output(self):
        for data in (b'not-json', b'x' * 16385,
                     json.dumps({**self.settings, 'mongodbUri': 'must-not-ship'}).encode(),
                     json.dumps({**self.settings, 'key': 'short'}).encode()):
            with self.subTest(length=len(data)):
                self.source.write_bytes(data)
                with self.assertRaises(ValueError):
                    helper.prepare(self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_existing_output_is_not_replaced(self):
        self.source.write_text(json.dumps(self.settings), encoding='utf-8')
        self.output.parent.mkdir()
        self.output.write_bytes(b'keep-existing-private-key')
        with self.assertRaises(FileExistsError):
            helper.prepare(self.source, self.output)
        self.assertEqual(b'keep-existing-private-key', self.output.read_bytes())

    def test_non_hidden_fallback_is_rejected_before_key_input(self):
        def unavailable(_message):
            warnings.warn('Cannot control echo', getpass.GetPassWarning)
            self.fail('A terminal without hidden input must not continue')
        with self.assertRaises(getpass.GetPassWarning):
            helper.prepare(None, self.output, prompt=unavailable)
        self.assertFalse(self.output.exists())

    def test_cli_success_and_failure_output_never_contain_private_input(self):
        for valid in (True, False):
            value = self.settings if valid else {**self.settings, 'extra': self.settings['key']}
            self.source.write_text(json.dumps(value), encoding='utf-8')
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(SCRIPT),
                '--input', str(self.source), '--output', str(self.output)],
                capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(0 if valid else 1, result.returncode)
            self.assertNotIn(self.settings['key'], result.stdout + result.stderr)
            if self.output.exists():
                self.output.unlink()

    def test_key_is_not_accepted_as_a_command_line_argument(self):
        output = io.StringIO()
        with patch.object(helper.getpass, 'getpass', side_effect=AssertionError('must not prompt')):
            with redirect_stderr(output), self.assertRaises(SystemExit) as error:
                helper.main(['--key', self.settings['key']])
        self.assertEqual(2, error.exception.code)
        self.assertNotIn(self.settings['key'], output.getvalue())


if __name__ == '__main__':
    unittest.main()
