"""General-chat model selection uses only acknowledged, session-local CLI controls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import BridgeError, probe_cli
from local_app.idle_connections import _prepare_release, _finish_release
from local_app.server import LocalApp, Server


FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_chat_model_cli.py')]


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('General-chat model fixture did not settle')


class ChatModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ax-chat-model-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / 'existing-cli'
        config.mkdir()
        self.settings = config / 'settings.json'
        self.settings.write_text('{"model":"original-user-config","fixture":true}', encoding='utf-8')
        self.settings_before = self.settings.read_bytes()
        environment = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config),
                                              'ANTHROPIC_MODEL': 'fixture-default', 'AX_CHAT_MODEL_FIXTURE': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = LocalApp(self.root / 'state', command=FAKE, info=probe_cli(FAKE),
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(kind='chat')['id']

    def item(self):
        return self.app.get(self.sid)

    def frames(self):
        wire = Path(self.item()['workspace']) / 'chat-model-wire.jsonl'
        return [json.loads(line) for line in wire.read_text(encoding='utf-8').splitlines()] if wire.exists() else []

    def connection(self):
        return self.app.public(self.item())['connection']

    def assert_no_user_turn(self):
        self.assertFalse(any(frame['type'] == 'user' for frame in self.frames()))
        self.assertEqual([], self.item()['messages'])
        self.assertIsNone(self.item().get('lastRunId'))

    def test_prepare_reads_inherited_model_without_effort_metadata_or_user_turn(self):
        connected = self.app.connect(self.sid)['connection']
        self.assertEqual('fixture-default', connected['model'])
        self.assertIsNone(connected['modelOverride'])
        self.assertTrue(connected['capabilities']['setModel'])
        self.assertFalse(connected['capabilities']['setPermissionMode'])
        self.assertFalse(connected['capabilities']['setEffort'])
        self.assertEqual(['fixture-default', 'fixture-alternative'], [row['value'] for row in connected['availableModels']])
        self.assertEqual([], connected['tools'])
        self.assertEqual([], connected['mcp'])
        self.assertEqual('idle', self.item()['state'])
        self.assert_no_user_turn()
        self.app.connect(self.sid)
        self.assertEqual(1, sum(frame.get('request', {}).get('subtype') == 'get_settings' for frame in self.frames()))

    def test_select_reported_or_custom_model_then_reset_without_config_or_other_chat_changes(self):
        self.app.connect(self.sid)
        other = self.app.create(kind='chat')['id']
        self.app.connect(other)
        for model in ('fixture-alternative', 'company-custom-model'):
            selected = self.app.set_model(self.sid, model)['session']['connection']
            self.assertEqual(model, selected['model'])
            self.assertEqual(model, selected['modelOverride'])
            self.assertEqual('chat-v1', self.item()['bridge'].runtime_profile)
            self.assertFalse(selected['capabilities']['setPermissionMode'])
        reset = self.app.set_model(self.sid, None)['session']['connection']
        self.assertEqual('fixture-default', reset['model'])
        self.assertIsNone(reset['modelOverride'])
        self.assertEqual('fixture-default', self.app.public(self.app.get(other))['connection']['model'])
        self.assertEqual(self.settings_before, self.settings.read_bytes())
        self.assert_no_user_turn()

    def test_no_catalogue_still_allows_manual_model_from_reported_baseline(self):
        with patch.dict(os.environ, {'AX_CHAT_MODEL_FIXTURE': 'no-model-list'}):
            self.app.connect(self.sid)
        self.assertEqual([], self.connection()['availableModels'])
        self.assertTrue(self.connection()['capabilities']['setModel'])
        self.assertEqual('company-custom-model', self.app.set_model(self.sid, 'company-custom-model')['session']['connection']['model'])
        self.assert_no_user_turn()

    def test_unknown_current_model_never_guesses_a_default_or_injects_a_prompt(self):
        with patch.dict(os.environ, {'AX_CHAT_MODEL_FIXTURE': 'settings-unavailable'}):
            self.app.connect(self.sid)
        self.assertEqual('', self.connection()['model'])
        self.assertFalse(self.connection()['capabilities']['setModel'])
        with self.assertRaises(BridgeError):
            self.app.set_model(self.sid, 'fixture-alternative')
        self.assert_no_user_turn()
        self.app.send(self.sid, 'first explicit question', [])
        eventually(lambda: self.item()['state'] == 'done')
        self.assertEqual('fixture-default', self.connection()['model'])
        self.assertTrue(self.connection()['capabilities']['setModel'])
        self.assertEqual('fixture-alternative', self.app.set_model(self.sid, 'fixture-alternative')['session']['connection']['model'])

    def test_active_reply_rejects_model_changes_at_app_and_transport_boundaries(self):
        self.app.connect(self.sid)
        self.app.send(self.sid, 'hold', [])
        eventually(lambda: self.item()['state'] == 'running')
        with self.assertRaisesRegex(ValueError, '현재 답변'):
            self.app.set_model(self.sid, 'fixture-alternative')
        with self.assertRaises(BridgeError):
            self.item()['bridge'].set_model('fixture-alternative')
        self.assertFalse(any(frame.get('request', {}).get('subtype') == 'set_model' for frame in self.frames()))

    def test_rejected_model_preserves_acknowledged_selection_and_invalid_values_never_reach_cli(self):
        self.app.connect(self.sid)
        self.app.set_model(self.sid, 'fixture-alternative')
        with self.assertRaises(BridgeError) as caught:
            self.app.set_model(self.sid, 'fixture-rejected')
        self.assertEqual('model_rejected', caught.exception.code)
        self.assertEqual('fixture-alternative', self.connection()['model'])
        self.assertEqual('fixture-alternative', self.item()['_sessionControls']['model'])
        before = len(self.frames())
        for model in ('', ' ', 'bad\nmodel', 'x' * 161, {}, 1):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.app.set_model(self.sid, model)
        self.assertEqual(before, len(self.frames()))
        self.assert_no_user_turn()

    def test_timeout_retires_unconfirmed_connection_before_retry(self):
        self.app.connect(self.sid)
        original = self.item()['bridge']
        with patch('local_app.bridge.CONTROL_TIMEOUT', .1), self.assertRaises(BridgeError) as caught:
            self.app.set_model(self.sid, 'fixture-timeout')
        self.assertEqual('model_timeout', caught.exception.code)
        self.assertTrue(original.closed)
        self.assertFalse(self.connection()['capabilities']['setModel'])
        self.assert_no_user_turn()
        self.app.connect(self.sid)
        self.assertIsNot(original, self.item()['bridge'])
        self.assertEqual('fixture-default', self.connection()['model'])

    def test_runtime_model_readback_timeout_or_auth_failure_closes_before_any_prompt(self):
        for behavior, code in (('settings-timeout', 'runtime_settings_timeout'),
                               ('settings-auth', 'authentication_failed')):
            with self.subTest(behavior=behavior):
                with patch.dict(os.environ, {'AX_CHAT_MODEL_FIXTURE': behavior}), \
                        patch('local_app.bridge.CONTROL_TIMEOUT', .1), self.assertRaises(BridgeError) as caught:
                    self.app.connect(self.sid)
                self.assertEqual(code, caught.exception.code)
                self.assertTrue(self.item()['bridge'].closed)
                self.assert_no_user_turn()

    def test_authenticated_model_route_supports_chat_before_first_message(self):
        server = Server(self.app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def post(route, data, auth=True):
            headers = {'Content-Type': 'application/json', 'Origin': server.origin}
            if auth:
                headers['Authorization'] = 'Bearer ' + self.app.token
            request = Request(server.origin + route, data=json.dumps({'id': self.sid, **data}).encode(), headers=headers)
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        try:
            with self.assertRaises(HTTPError) as unauthorized:
                post('/api/model', {'model': 'fixture-alternative'}, auth=False)
            self.assertEqual(403, unauthorized.exception.code)
            post('/api/connect', {})
            with self.assertRaises(HTTPError) as missing:
                post('/api/model', {})
            self.assertEqual(400, missing.exception.code)
            selected = post('/api/model', {'model': 'fixture-alternative'})
            self.assertEqual('fixture-alternative', selected['session']['connection']['model'])
            reset = post('/api/model', {'model': None})
            self.assertEqual('fixture-default', reset['session']['connection']['model'])
            self.assertIsNone(reset['modelOverride'])
            self.assert_no_user_turn()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_disconnect_reapplies_model_before_next_user_frame_with_tool_limits_intact(self):
        self.app.connect(self.sid)
        self.app.set_model(self.sid, 'fixture-alternative')
        old = self.item()['bridge']
        old.close()
        self.app.send(self.sid, 'continue after reconnect', [])
        eventually(lambda: self.item()['state'] == 'done')
        self.assertIsNot(old, self.item()['bridge'])
        self.assertEqual('fixture-alternative', self.connection()['model'])
        self.assertEqual('chat-v1', self.item()['bridge'].runtime_profile)
        frames = self.frames()
        user_index = next(index for index, frame in enumerate(frames) if frame['type'] == 'user')
        changes = [index for index, frame in enumerate(frames) if frame.get('request', {}).get('subtype') == 'set_model']
        self.assertEqual(2, len(changes))
        self.assertLess(changes[-1], user_index)
        self.assertTrue(frames[user_index]['client_composed'])
        old.emit('model_changed', {'model': 'stale', 'modelOverride': 'stale'})
        self.assertEqual('fixture-alternative', self.connection()['model'])

    def test_failed_restore_blocks_prompt_until_user_resolves_selection(self):
        self.app.connect(self.sid)
        self.app.set_model(self.sid, 'fixture-alternative')
        self.item()['bridge'].close()
        with patch.dict(os.environ, {'AX_CHAT_MODEL_FIXTURE': 'reject-alternative'}), self.assertRaises(BridgeError):
            self.app.send(self.sid, 'must not be sent', [])
        self.assert_no_user_turn()
        self.assertFalse(self.connection()['controlRestore']['canSend'])
        self.app.set_model(self.sid, None)
        self.app.send(self.sid, 'explicit retry', [])
        eventually(lambda: self.item()['state'] == 'done')
        self.assertEqual('fixture-default', self.connection()['model'])
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.frames()))

    def test_idle_release_preserves_selected_model_and_reset_restores_original_baseline(self):
        self.app.connect(self.sid)
        self.app.set_model(self.sid, 'fixture-alternative')
        self.app.send(self.sid, 'first question', [])
        eventually(lambda: self.item()['state'] == 'done')
        session = self.item()['sessionId']
        with self.app.lock:
            old, resumable = _prepare_release(self.app, self.item(), 'chat-model-test')
        self.assertTrue(_finish_release(self.app, self.item(), old, resumable))
        with patch.dict(os.environ, {'ANTHROPIC_MODEL': 'fixture-new-process-default'}):
            self.app.connect(self.sid)
        self.assertEqual('fixture-alternative', self.connection()['model'])
        self.assertEqual(session, self.item()['sessionId'])
        self.assertNotIn('permissionMode', self.item().get('_idleRestoreControls', {}))
        self.app.set_model(self.sid, None)
        self.assertEqual('fixture-default', self.connection()['model'])
        self.assertEqual(self.settings_before, self.settings.read_bytes())


if __name__ == '__main__':
    unittest.main()
