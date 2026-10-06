"""Idle scheduling must scale with pending work, not remembered conversations."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_app.server import LocalApp


class DispatchResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['not-started'],
                            managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.item = self.app.get(self.sid)
        self.queue = self.app.dispatch.queue
        self.context = self.app.dispatch.context(self.item)

    def remember_many(self):
        # Navigation metadata needs no conversation hydration or disk fixtures.
        for number in range(3000):
            sid = f'remembered-{number}'
            self.app.sessions[sid] = {'id': sid, 'state': 'idle', 'trusted': False,
                                      'workspace': str(self.work), 'bridge': None}

    def enqueue(self, text='next'):
        return self.queue.enqueue(self.sid, text, [], self.context)

    def test_idle_history_does_not_run_per_task_admission_or_hydrate_payloads(self):
        self.remember_many()
        with patch.object(self.app, 'connection_capacity_available',
                          side_effect=AssertionError('idle history is not dispatch work')), \
             patch.object(self.queue, 'claim', side_effect=AssertionError('nothing to claim')), \
             patch.object(self.app, 'get', side_effect=AssertionError('do not hydrate history')), \
             patch.object(self.queue, '_save', side_effect=AssertionError('idle tick must not write')):
            for _ in range(20):
                self.app.dispatch.pump()

    def test_only_candidate_reaches_admission_with_large_history(self):
        self.remember_many()
        self.enqueue()
        with patch.object(self.app, 'connection_capacity_available', return_value=True) as capacity, \
             patch.object(self.app, 'send') as send:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        capacity.assert_called_once_with(self.item)
        self.assertEqual(1, send.call_count)
        self.assertEqual(self.sid, send.call_args.args[0])
        self.assertEqual('submitted', self.queue.snapshot(self.sid)['queue'][0]['state'])

    def test_due_schedule_is_claimed_on_the_same_tick(self):
        now = time.time()
        self.queue.add_schedule(self.sid, 'scheduled', [], kind='once', run_at=now + 60,
                                context=self.context)
        self.queue.clock = lambda: now + 61
        with patch.object(self.app, 'send') as send:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        send.assert_called_once()
        self.assertEqual('scheduled', send.call_args.args[1])

    def test_held_and_uncertain_tasks_remain_unclaimed(self):
        first = self.enqueue('first')
        self.enqueue('second')
        self.queue.pause(self.sid, 'user')
        with patch.object(self.app, 'connection_capacity_available',
                          side_effect=AssertionError('held work must not be admitted')):
            self.app.dispatch.pump()
        self.queue.resume(self.sid)
        self.queue.claim(self.sid, self.item, self.context)
        self.queue.failed(first['id'])
        self.queue.resume(self.sid)
        with patch.object(self.app, 'connection_capacity_available',
                          side_effect=AssertionError('uncertain delivery requires review')):
            self.app.dispatch.pump()
        self.assertEqual(['needs_review', 'queued'],
                         [row['state'] for row in self.queue.snapshot(self.sid)['queue']])

    def test_stopped_steering_is_still_processed_even_while_queue_is_held(self):
        self.enqueue('new direction')
        self.queue.pause(self.sid, 'stopped')
        self.item['state'] = 'stopped'
        self.app.dispatch.steering[self.sid] = {'created': time.monotonic(), 'interrupted': set()}
        with patch.object(self.app, 'send') as send:
            self.app.dispatch.pump()
        send.assert_called_once()
        self.assertNotIn(self.sid, self.app.dispatch.steering)
        self.assertFalse(self.queue.snapshot(self.sid)['paused'])

    def test_claim_rechecks_a_hold_added_after_candidate_snapshot(self):
        self.enqueue()
        def stale_candidates():
            candidates = (self.sid,)
            self.queue.pause(self.sid, 'user')
            return candidates
        with patch.object(self.queue, 'dispatch_candidates', side_effect=stale_candidates), \
             patch.object(self.app, 'send') as send:
            self.app.dispatch.pump()
        send.assert_not_called()
        self.assertEqual('queued', self.queue.snapshot(self.sid)['queue'][0]['state'])


if __name__ == '__main__':
    unittest.main()
