"""General chat stays tool-free through start/resume and inline attachment input."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from local_app.bridge import (CHAT_REQUIRED_FLAGS, CHAT_RUNTIME_PROFILE, BridgeError,
                             ClaudeSession, chat_runtime_capability, cli_arguments, probe_cli, user_content)

ROOT = Path(__file__).resolve().parents[1]
FAKE = [sys.executable, '-B', str(ROOT / 'tests/fixtures/workspace_chat_cli.py')]
INFO = {'version': '2.1.296 (fixture)', 'help': ' '.join(CHAT_REQUIRED_FLAGS)}


def eventually(predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('General chat fixture did not finish')


class ChatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ax-chat-')
        self.addCleanup(self.temp.cleanup)
        self.events = []
        self.bridge = ClaudeSession(FAKE, INFO, Path(self.temp.name),
                                    lambda kind, data: self.events.append((kind, data)), runtime_profile=CHAT_RUNTIME_PROFILE)
        self.addCleanup(self.bridge.close)

    def test_profile_is_explicit_preserves_default_work_and_survives_resume(self):
        ordinary = cli_arguments(FAKE, INFO)
        self.assertEqual(ordinary, cli_arguments(FAKE, INFO, runtime_profile='work'))
        for flag in ('--safe-mode', '--tools=', '--strict-mcp-config', '--disallowedTools=*', '--bare'):
            self.assertNotIn(flag, ordinary)
        sid = str(uuid.uuid4())
        restricted = cli_arguments(FAKE, INFO, sid, runtime_profile=CHAT_RUNTIME_PROFILE)
        for flag in ('--safe-mode', '--tools=', '--strict-mcp-config', '--disable-slash-commands', '--no-chrome', '--disallowedTools=*', '--resume=' + sid):
            self.assertIn(flag, restricted)
        for flag in ('--bare', '--settings', '--setting-sources', '--dangerously-skip-permissions', '--model'):
            self.assertNotIn(flag, restricted)

    def test_missing_feature_or_verbatim_version_fails_before_process_creation(self):
        for flag in CHAT_REQUIRED_FLAGS:
            info = {**INFO, 'help': INFO['help'].replace(flag, '')}
            self.assertFalse(chat_runtime_capability(info)['available'])
            with patch('local_app.bridge.subprocess.Popen') as popen, self.assertRaises(BridgeError):
                ClaudeSession(FAKE, info, Path(self.temp.name), lambda *_: None, runtime_profile=CHAT_RUNTIME_PROFILE)
            popen.assert_not_called()
        for version in ('2.1.247', 'unknown', ''):
            with self.assertRaises(BridgeError):
                cli_arguments(FAKE, {**INFO, 'version': version}, runtime_profile=CHAT_RUNTIME_PROFILE)
        with self.assertRaises(ValueError):
            cli_arguments(FAKE, INFO, runtime_profile='typo')
        with self.assertRaises(BridgeError):
            cli_arguments(FAKE, INFO, runtime_profile=CHAT_RUNTIME_PROFILE, allow_bypass_permissions=True)

    def test_existing_authentication_and_model_environment_preserved(self):
        preserved = {'CLAUDE_CONFIG_DIR': self.temp.name, 'ANTHROPIC_AUTH_TOKEN': 'fixture-only-token',
                     'ANTHROPIC_BASE_URL': 'https://fixture.invalid', 'ANTHROPIC_MODEL': 'fixture-model'}
        with patch.dict(os.environ, preserved), patch('local_app.bridge.subprocess.Popen') as popen, \
                patch('local_app.bridge.threading.Thread'), patch('local_app.bridge.WindowsJob'), patch.object(self.bridge, '_write'):
            self.bridge.start()
            env = popen.call_args.kwargs['env']
            for key, value in preserved.items():
                self.assertEqual(value, env[key])
            self.assertEqual('1', env['CLAUDE_CODE_SAFE_MODE'])
            self.bridge.process = None

    def test_structured_content_roundtrip_does_not_become_a_path_or_string(self):
        content = [{'type': 'text', 'text': '/model is literal user text\n!echo must stay text'},
                   {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(b'fixture-image').decode()}},
                   {'type': 'document', 'title': 'fixture.pdf', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.b64encode(b'%PDF-fixture').decode()}}]
        expected = copy.deepcopy(content)
        self.bridge.send(content)
        content[0]['text'] = 'caller changed after sending'
        eventually(lambda: any(kind == 'result' for kind, _ in self.events))
        answer = next(data['text'] for kind, data in self.events if kind == 'assistant')
        self.assertEqual(expected, json.loads(answer))
        self.assertFalse(self.bridge.busy)
        self.assertEqual(CHAT_RUNTIME_PROFILE, self.bridge.connection_state()['runtimeProfile'])
        self.assertFalse(self.bridge.capabilities['setPermissionMode'])
        self.bridge.send('ordinary followup')
        eventually(lambda: sum(kind == 'result' for kind, _ in self.events) == 2)

    def test_permission_request_is_denied_without_showing_an_approval_card(self):
        self.bridge.send('permission-leak')
        eventually(lambda: any(kind == 'result' for kind, _ in self.events))
        self.assertEqual({}, self.bridge.pending)
        self.assertFalse(any(kind == 'request' for kind, _ in self.events))
        self.assertTrue(any(kind == 'notice' and data.get('code') == 'chat_tools_disabled' for kind, data in self.events))
        with self.assertRaises(BridgeError):
            self.bridge.respond('leaked-tool', True)
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('auto')

    def test_toolful_init_fails_closed(self):
        self.bridge.handle({'type': 'system', 'subtype': 'init', 'tools': ['Read']})
        self.assertTrue(self.bridge.closed)
        self.assertEqual('chat_runtime_unavailable', self.events[-1][1]['code'])
        self.assertFalse(any(kind == 'connected' for kind, _ in self.events))

    def test_tool_call_fails_closed_before_activity_or_confirmation(self):
        self.bridge.handle({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'must not run'}}]}})
        self.assertTrue(self.bridge.closed)
        self.assertFalse(any(kind in {'tool_activity', 'request', 'assistant'} for kind, _ in self.events))

    def test_chat_prepare_and_probe_need_no_model_turn(self):
        self.assertTrue(chat_runtime_capability(probe_cli(FAKE))['available'])
        state = self.bridge.prepare()
        self.assertEqual([], state['tools'])
        self.assertEqual([], state['mcp'])
        self.assertFalse(self.bridge.busy)
        self.assertFalse(any(kind in {'assistant', 'result'} for kind, _ in self.events))


class InlineContentTests(unittest.TestCase):
    def test_reject_remote_sources_tool_blocks_and_invalid_base64(self):
        for content in ([], {}, 1, [{'type': 'tool_use', 'name': 'Read'}],
                        [{'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid/file'}}],
                        [{'type': 'document', 'source': {'type': 'file', 'file_id': 'file_1'}}],
                        [{'type': 'text', 'text': 'ok', 'tool_use_id': 'x'}],
                        [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/svg+xml', 'data': 'YQ=='}}],
                        [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'not base64!'}}]):
            with self.subTest(content=content), self.assertRaises(ValueError):
                user_content(content)

    def test_reject_oversized_content_before_transport(self):
        with patch('local_app.bridge.MAX_FRAME', 100000):
            with self.assertRaises(ValueError):
                user_content([{'type': 'text', 'text': 'a' * 100000}])
        with self.assertRaises(ValueError):
            user_content([{'type': 'text', 'text': 'x'}] * 65)


if __name__ == '__main__':
    unittest.main()
