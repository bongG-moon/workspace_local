"""Real local app/child-process model recovery, without a live API or user settings."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from local_app.bridge import probe_cli
from local_app.server import LocalApp


FAKE = [sys.executable, '-B', str(Path(__file__).parent / 'fixtures/workspace_model_recovery_cli.py')]


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Isolated model recovery did not settle')


class ModelErrorRecoveryTests(unittest.TestCase):
    def run_recovery(self, kind, denial, code):
        with tempfile.TemporaryDirectory(prefix='ax-model-recovery-') as directory:
            root = Path(directory)
            config = root / 'isolated-cli'
            config.mkdir()
            settings = config / 'settings.json'
            settings.write_text(json.dumps({'model': 'Fixture-Big-Latest', 'env': {
                'ANTHROPIC_DEFAULT_OPUS_MODEL': 'Fixture-Big-Latest',
                'ANTHROPIC_DEFAULT_SONNET_MODEL': 'Fixture-Small-Latest'}}), encoding='utf-8')
            original_settings = settings.read_bytes()
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(config), 'AX_MODEL_RECOVERY_CASE': denial}):
                app = LocalApp(root / 'state', command=FAKE, info=probe_cli(FAKE),
                               managed_workspace_root=root / 'managed')
                try:
                    def create(name):
                        if kind == 'chat':
                            return app.create(kind='chat')['id']
                        folder = root / name
                        folder.mkdir()
                        return app.create(str(folder), True)['id']

                    sid, other = create('work'), create('other-work')
                    app.connect(sid)
                    app.connect(other)
                    item = app.get(sid)
                    bridge = item['bridge']
                    original_pid = bridge.process.pid

                    def frames(task=sid):
                        path = Path(app.get(task)['workspace']) / 'model-recovery-wire.jsonl'
                        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

                    def users(task=sid):
                        return [row for row in frames(task) if row['type'] == 'user']

                    self.assertEqual([], users())
                    self.assertEqual('Fixture-Big-Latest', app.public(item)['connection']['model'])
                    app.send(sid, 'first-explicit-fixture-question', [])
                    eventually(lambda: item['state'] == 'error')
                    errors = [row['data'] for row in item['events'] if row['type'] == 'error']
                    self.assertEqual([code], [row.get('code') for row in errors])
                    self.assertFalse(bridge.closed)
                    self.assertTrue(app.public(item)['connection']['capabilities']['setModel'])
                    resume = item['sessionId']
                    self.assertTrue(resume)
                    self.assertEqual(1, len(users()))
                    self.assertEqual([], [row for row in frames() if row.get('request', {}).get('subtype') == 'set_model'])

                    selected = app.set_model(sid, 'Fixture-Small-Latest')['session']['connection']
                    self.assertEqual('Fixture-Small-Latest', selected['model'])
                    self.assertEqual('Fixture-Small-Latest', selected['modelOverride'])
                    self.assertEqual(1, len(users()), 'Selecting a model must not retransmit the failed question')
                    self.assertEqual(original_pid, item['bridge'].process.pid)
                    self.assertEqual('Fixture-Big-Latest', app.public(app.get(other))['connection']['model'])
                    self.assertEqual([], users(other))

                    app.send(sid, 'second-explicit-fixture-question', [])
                    eventually(lambda: item['state'] == 'done')
                    self.assertEqual(original_pid, item['bridge'].process.pid)
                    self.assertEqual(resume, item['sessionId'])
                    self.assertEqual(2, len(users()))
                    self.assertIn('first-explicit-fixture-question', str(users()[0]['message']['content']))
                    self.assertIn('second-explicit-fixture-question', str(users()[1]['message']['content']))
                    self.assertTrue(any(row['role'] == 'assistant' and 'Fixture-Small-Latest' in row['text'] for row in item['messages']))
                    self.assertEqual(['first-explicit-fixture-question', 'second-explicit-fixture-question'],
                                     [row['text'] for row in item['messages'] if row['role'] == 'user'])
                    self.assertEqual('Fixture-Big-Latest', bridge.original_model)
                    self.assertEqual(original_settings, settings.read_bytes())
                    self.assertEqual([], users(other))
                    if kind == 'chat':
                        self.assertEqual('chat-v1', bridge.runtime_profile)
                        self.assertEqual([], app.public(item)['connection']['tools'])
                        self.assertFalse(app.public(item)['connection']['capabilities']['setPermissionMode'])
                finally:
                    app.close()

    def test_work_model_permission_can_recover_without_login_or_automatic_replay(self):
        self.run_recovery('work', 'permission', 'model_permission')

    def test_chat_model_permission_can_recover_without_login_or_automatic_replay(self):
        self.run_recovery('chat', 'permission', 'model_permission')

    def test_work_missing_model_can_recover_without_login_or_automatic_replay(self):
        self.run_recovery('work', 'missing', 'model_not_found')

    def test_chat_missing_model_can_recover_without_login_or_automatic_replay(self):
        self.run_recovery('chat', 'missing', 'model_not_found')


if __name__ == '__main__':
    unittest.main()
