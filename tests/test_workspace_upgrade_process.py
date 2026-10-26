"""Upgrade real owned child connections without Claude or personal app data."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from local_app.server import LocalApp
from local_app.upgrade_handoff import UpgradeHandoff
from local_app.work_queue import WorkQueue


class UpgradeProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='workspace-upgrade-process-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        fixture = Path(__file__).parent / 'fixtures' / 'workspace_stop_cli.py'
        self.app = LocalApp(self.root / 'state',
            command=[sys.executable, '-B', str(fixture.resolve())],
            info={'version': 'fixture', 'help': ''}, managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.item = self.app.get(self.app.create(str(self.root), True)['id'])
        self.sid = self.item['id']
        self.rid = uuid.uuid4().hex
        self.draft = {'sessionId': self.sid, 'drafts': [
            {'id': self.sid, 'text': '아직 보내지 않은 내용\n두 번째 줄',
             'attachments': [str(self.root / 'report.txt')]}]}

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Local upgrade protocol fixture did not settle')

    def action(self, action, **values):
        return self.app.upgrade.action({'action': action, 'requestId': self.rid, **values})

    def start_and_queue(self):
        self.app.send(self.sid, 'wait original request', [])
        wire = self.root / 'stop-wire.jsonl'
        self.wait(lambda: wire.exists() and 'wait original request' in wire.read_text(encoding='utf-8'))
        self.queued = self.app.dispatch.queue.enqueue(self.sid, '나중에 실행할 요청', [],
            self.app.dispatch.context(self.item))
        self.scheduled = self.app.dispatch.queue.add_schedule(self.sid, '예약된 요청', [],
            kind='once', run_at=time.time() + 3600, context=self.app.dispatch.context(self.item))

    def finish_upgrade(self):
        prepared = self.action('prepare', targetVersion='99.0.0')
        self.assertEqual('capture', prepared['upgrade']['stage'])
        self.assertTrue(self.action('capture', revision=prepared['upgrade']['revision'],
            snapshot=deepcopy(self.draft))['ok'])
        self.assertTrue(self.action('commit')['closed'])
        bridge = self.item['bridge']
        self.assertTrue(bridge.cleanup_complete)
        self.assertIsNotNone(bridge.process.poll())
        self.assertEqual(self.draft, UpgradeHandoff(self.app, '99.0.0').restore['snapshot'])
        restored = WorkQueue(self.app.state).snapshot(self.sid)
        self.assertTrue(restored['paused'])
        self.assertEqual([self.queued['id']], [row['id'] for row in restored['queue']])
        self.assertEqual([self.scheduled['id']], [row['id'] for row in restored['schedules']])
        wire = [json.loads(line) for line in (self.root / 'stop-wire.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(['wait original request'],
            [row['message']['content'] for row in wire if row.get('type') == 'user'])

    def test_stopped_turn_with_live_idle_connection_and_pending_work_can_upgrade(self):
        self.start_and_queue()
        self.app.stop(self.sid)
        self.wait(lambda: self.item['state'] == 'stopped')
        self.assertFalse(self.item['bridge'].closed)
        self.assertIsNone(self.item['bridge'].process.poll())
        self.finish_upgrade()

    def test_finished_retryable_cleanup_error_is_retried_during_upgrade(self):
        self.start_and_queue()
        bridge = self.item['bridge']
        job = bridge._process_job
        if job is None:
            self.skipTest('Windows owned job is required for this cleanup regression')
        release = job.release_empty
        # The child has already exited, but an owned-handle cleanup operation
        # fails once. This is a completed failed attempt, not work still running.
        with patch.object(job, 'release_empty', side_effect=OSError('fixture cleanup failure')):
            self.app.stop(self.sid, disconnect=True)
            self.wait(lambda: bridge.cleanup_retryable and self.item['state'] == 'error')
        self.assertEqual('failed', bridge.stop_state)
        self.assertFalse(bridge.descendant_cleanup_uncertain)
        self.assertIsNotNone(bridge.process.poll())
        with patch.object(job, 'release_empty', wraps=release) as retry:
            self.finish_upgrade()
            retry.assert_called_once_with()
