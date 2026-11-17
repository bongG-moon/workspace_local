"""Publisher credentials stay outside saved settings and cross worker boundaries safely."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote, quote_plus

from local_app.registration import DEFAULT_BASE_URL
from workspace_publisher import __main__ as cli
from workspace_publisher.config import DEFAULTS, PublisherError
from workspace_publisher.gui import (PublisherJobs, PublisherWindow, build_identity,
                                     public_result, public_text, settings_only)
from tests.test_workspace_publisher_gui import FakeCore, CONFIG, BUILD, SDK, ROOT

KEY = 'synthetic/registration key+value-' * 2
TOKEN = 'synthetic/GitLab token+value'
ENABLED = {**CONFIG, 'registrationEnabled': True, 'registrationBaseUrl': DEFAULT_BASE_URL}


class RegistrationCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.registration_calls = []
        self.registration_failure = False

    def factory(self, root, *, emit):
        publisher = super().factory(root, emit=emit)
        original_build, original_restore = publisher.build, publisher.load_last_build
        original_publish = publisher.publish

        def build(config, *, cancel=None, registration_key=''):
            self.registration_calls.append(('build', registration_key))
            emit({'kind': 'info', 'message': registration_key + ' ' + quote(registration_key, safe='')})
            if self.registration_failure:
                raise PublisherError('failed ' + registration_key + ' ' + quote_plus(registration_key))
            return original_build(config, cancel=cancel)

        def restore(config, *, cancel=None, registration_key=''):
            self.registration_calls.append(('restore', registration_key))
            return original_restore(config, cancel=cancel)

        def prepare(config, *, build_result=None, cancel=None, registration_key=''):
            self.registration_calls.append(('prepare', registration_key))
            return deepcopy(build_result) if build_result else build(config, cancel=cancel, registration_key=registration_key)

        def publish(config, built, token, token_kind='auto', *, cancel=None, registration_key=''):
            self.registration_calls.append(('publish', registration_key))
            result = original_publish(config, built, token, token_kind, cancel=cancel)
            return {**result, 'registrationKey': registration_key,
                    'note': registration_key + ' ' + quote_plus(registration_key),
                    'registrationConfig': {'key': registration_key, 'keyFingerprint': hashlib.sha256(registration_key.encode()).hexdigest()}}

        publisher.build, publisher.load_last_build = build, restore
        publisher.prepare_deploy, publisher.publish = prepare, publish
        return publisher


class RegistrationJobsTests(unittest.TestCase):
    def setUp(self):
        self.core = RegistrationCore()
        self.jobs = PublisherJobs(ROOT, self.core.factory)

    def finish(self):
        self.jobs.thread.join(3)
        self.assertFalse(self.jobs.thread.is_alive())
        return self.jobs.drain()

    def assert_no_key(self, result):
        value = json.dumps(result, ensure_ascii=False)
        for secret in (KEY, quote(KEY, safe=''), quote_plus(KEY)):
            self.assertNotIn(secret, value)

    def test_enabled_build_passes_key_separately_and_redacts_events(self):
        self.jobs.start('build', ENABLED, registration_key=KEY)
        events = self.finish()
        self.assertEqual('done', events[-1]['kind'])
        self.assertEqual([('build', KEY)], self.core.registration_calls[-1:])
        self.assert_no_key(events)
        saved = next(row[1] for row in self.core.calls if row[0] == 'save')
        self.assertTrue(saved['registrationEnabled'])
        self.assertEqual(DEFAULT_BASE_URL, saved['registrationBaseUrl'])
        self.assert_no_key(saved)

    def test_deploy_passes_key_to_build_and_publish_but_not_public_config(self):
        self.jobs.start('deploy', ENABLED, registration_key=KEY, token=TOKEN)
        events = self.finish()
        self.assertEqual('done', events[-1]['kind'])
        self.assertEqual([('prepare', KEY), ('build', KEY), ('publish', KEY)], self.core.registration_calls)
        self.assert_no_key(events)
        self.assertNotIn(TOKEN, json.dumps(events))
        self.assertNotIn('registrationKey', events[-1]['result']['publication'])

    def test_failed_key_is_not_reflected_in_error_or_progress(self):
        self.core.registration_failure = True
        self.jobs.start('build', ENABLED, registration_key=KEY)
        events = self.finish()
        self.assertEqual('failed', events[-1]['kind'])
        self.assert_no_key(events)

    def test_restored_ready_build_can_publish_without_reentering_key(self):
        self.jobs.start('publish', ENABLED, build_result=BUILD, token=TOKEN)
        events = self.finish()
        self.assertEqual('done', events[-1]['kind'])
        self.assertEqual([('publish', '')], self.core.registration_calls)

    def test_ephemeral_key_identity_invalidates_old_key_without_saving_the_key(self):
        current = build_identity(ENABLED, KEY)
        self.assertNotEqual(current, build_identity(ENABLED, KEY + '-rotated'))
        self.assertNotIn(KEY, current)
        public = settings_only({**ENABLED, 'registrationKey': KEY, 'registration_key': KEY})
        self.assert_no_key(public)

    def test_redaction_covers_raw_encoded_and_nested_key_fields(self):
        text = public_text(TOKEN + KEY + quote(KEY, safe='') + quote_plus(KEY), TOKEN, KEY)
        self.assert_no_key(text)
        result = public_result({'registration_key': KEY, 'key': KEY,
                                'meta': {'keyFingerprint': 'f' * 64}, 'message': KEY}, TOKEN, KEY)
        self.assertEqual({'meta': {'keyFingerprint': 'f' * 64}, 'message': '[집계 인증키 숨김]'}, result)


class RegistrationWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as error:
            self.skipTest('Tk session unavailable: ' + str(error))
        self.sdk_patch = patch('workspace_publisher.gui.sdk_status', return_value=deepcopy(SDK))
        self.sdk_patch.start()
        self.core = RegistrationCore()
        self.window = PublisherWindow(self.root, ROOT, self.core.factory)
        self.finish()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.window.jobs.cancel()
        if self.window.jobs.thread:
            self.window.jobs.thread.join(3)
        if not self.window.closed:
            self.window.destroy()
        self.sdk_patch.stop()

    def finish(self):
        self.window.jobs.thread.join(3)
        self.assertFalse(self.window.jobs.thread.is_alive())
        self.window.poll()

    def test_actual_tk_key_field_is_masked_and_public_settings_exclude_value(self):
        self.assertEqual('●', self.window.registration_key_entry.cget('show'))
        self.assertIn('disabled', self.window.registration_key_entry.state())
        self.window.registration_enabled.set(True)
        self.window.registration_key.set(KEY)
        self.assertNotIn('disabled', self.window.registration_key_entry.state())
        self.assertEqual(DEFAULT_BASE_URL, self.window.fields['registrationBaseUrl'].get())
        self.assertNotIn(KEY, json.dumps(self.window.config()))
        self.window.start('build')
        self.finish()
        self.assertEqual([('build', KEY)], self.core.registration_calls[-1:])
        self.assertNotIn(KEY, self.window.log.get('1.0', 'end'))

    def test_enabled_fresh_build_without_key_focuses_field_before_job(self):
        self.window.registration_enabled.set(True)
        before = list(self.core.calls)
        self.window.start('build')
        self.assertIsNone(self.window.active_action)
        self.assertEqual(before, self.core.calls)
        self.assertIn('인증키', self.window.status.get())

    def test_changing_key_invalidates_existing_ready_build(self):
        self.window.registration_enabled.set(True)
        self.window.registration_key.set(KEY)
        self.window.build_result = deepcopy(BUILD)
        self.window.build_config_identity = self.window.current_build_identity()
        self.window.refresh_controls()
        self.assertNotIn('disabled', self.window.publish_button.state())
        self.window.registration_key.set(KEY + '-rotated')
        self.assertIn('disabled', self.window.publish_button.state())

    def test_ready_build_can_be_republished_with_blank_key_after_restore(self):
        self.window.registration_enabled.set(True)
        built = {**deepcopy(BUILD), 'registrationConfig': {
            'schema': 1, 'baseUrl': DEFAULT_BASE_URL,
            'keyFingerprint': hashlib.sha256(KEY.encode()).hexdigest()}}
        self.window.done('restore', {'build': built}, self.window.current_build_identity())
        self.window.token.set(TOKEN)
        self.core.registration_calls.clear()
        self.window.start('deploy')
        self.finish()
        self.assertEqual([('prepare', ''), ('publish', '')], self.core.registration_calls)
        self.assertEqual('', self.window.registration_key.get())


class RegistrationCliTests(unittest.TestCase):
    def setUp(self):
        self.publisher = Mock()
        self.publisher.load_config.return_value = {**DEFAULTS, 'registrationEnabled': True}
        self.publisher.build.return_value = {'key': KEY, 'note': KEY}
        self.publisher.prepare_deploy.return_value = {'version': '7.0.1', 'files': []}
        self.publisher.publish.return_value = {'verified': True, 'note': KEY}
        self.publisher.publish_source.return_value = {'verified': True}

    def invoke(self, args):
        output = io.StringIO()
        with patch.object(cli, 'Publisher', return_value=self.publisher), redirect_stdout(output):
            result = cli.main(['--repo', str(ROOT), *args])
        return result, output.getvalue()

    def test_build_uses_hidden_registration_prompt_and_never_prints_key(self):
        with patch('getpass.getpass', return_value=KEY) as ask:
            code, output = self.invoke(['build'])
        self.assertEqual(0, code)
        self.assertIn('집계 인증키', ask.call_args.args[0])
        self.assertEqual(KEY, self.publisher.build.call_args.kwargs['registration_key'])
        self.assertNotIn(KEY, output)

    def test_configure_enables_collection_and_saves_only_public_settings(self):
        self.publisher.load_config.return_value = dict(DEFAULTS)
        self.publisher.inspect_source.return_value = {'sourceKind': 'archive', 'releaseTag': ''}
        self.publisher.save_config.side_effect = lambda config: config
        with patch('builtins.input', side_effect=['https://gitlab.example', '42', '', 'y', '']), \
                patch('getpass.getpass') as secret:
            code, output = self.invoke(['configure'])
        self.assertEqual(0, code)
        self.assertTrue(self.publisher.save_config.call_args.args[0]['registrationEnabled'])
        self.assertEqual(DEFAULT_BASE_URL, self.publisher.save_config.call_args.args[0]['registrationBaseUrl'])
        secret.assert_not_called()
        self.assertNotIn(KEY, output)

    def test_deploy_prompts_distinct_gitlab_token_and_registration_key(self):
        with patch('getpass.getpass', side_effect=[TOKEN, KEY]) as ask:
            code, output = self.invoke(['deploy'])
        self.assertEqual(0, code)
        self.assertEqual(2, ask.call_count)
        self.assertEqual(KEY, self.publisher.prepare_deploy.call_args.kwargs['registration_key'])
        self.assertEqual(KEY, self.publisher.publish.call_args.kwargs['registration_key'])
        self.assertNotIn(KEY, output)
        self.assertNotIn(TOKEN, output)

    def test_key_in_error_is_redacted_after_token_cleanup(self):
        self.publisher.prepare_deploy.side_effect = PublisherError(TOKEN + ' ' + KEY)
        with patch('getpass.getpass', side_effect=[TOKEN, KEY]):
            code, output = self.invoke(['deploy'])
        self.assertEqual(1, code)
        self.assertNotIn(KEY, output)
        self.assertNotIn(TOKEN, output)

    def test_raw_key_command_line_argument_is_rejected_without_echo(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(SystemExit):
            self.invoke(['build', '--registration-key', KEY])
        self.assertNotIn(KEY, output.getvalue())


if __name__ == '__main__':
    unittest.main()
