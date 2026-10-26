"""Per-request completed clocks survive history without inventing old timings."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_app.event_recovery import snapshot_recovery
from local_app.history import HistoryStore, normalize_run_timing, row as history_row
from local_app.server import LocalApp


class Clock:
    def __init__(self):
        self.wall = 1_800_000_000.0
        self.elapsed = 100.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.wall += seconds
        self.elapsed += seconds

    def __getattr__(self, name):
        return getattr(time, name)


class RequestTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.clock = Clock()
        for mocking in (patch('local_app.server.time', self.clock), patch('local_app.demo.run', lambda *_: None)):
            mocking.start()
            self.addCleanup(mocking.stop)

    def start(self, text='request', sid=None, **kwargs):
        sid = sid or self.sid
        self.app.send(sid, text, [], **kwargs)
        return self.app.get(sid)['lastRunId']

    def saved(self, item=None):
        return [message['runTiming'] for message in (item or self.item)['messages'] if 'runTiming' in message]

    def test_completed_request_includes_approval_wait_and_survives_snapshot_history_and_lazy_load(self):
        run = self.start()
        started = self.item['runStartedAt']
        self.clock.advance(2)
        self.app.emit(self.sid, 'request', {'id': 'approval', 'tool': 'Read', 'input': {}})
        self.clock.advance(10)
        self.app.emit(self.sid, 'request_closed', {'id': 'approval'})
        self.clock.advance(.345)
        self.app.emit(self.sid, 'assistant', {'text': 'Done'})
        self.app.emit(self.sid, 'result', {'durationMs': 1})  # Provider time cannot replace our clock.
        timing = self.item['events'][-1]['data']['runTiming']
        self.assertEqual({'runId': run, 'startedAt': started, 'finishedAt': self.clock.wall,
                          'durationMs': 12345, 'status': 'done'}, timing)
        self.assertEqual([timing], self.saved())
        self.assertNotIn('_runTimer', self.item)
        recovered = snapshot_recovery(self.item, self.app.public(self.item), 0)['session']
        self.assertEqual(timing, recovered['messages'][0]['runTiming'])
        store = HistoryStore(self.app.state)
        loaded = next(item for item in store.load(lazy=True) if item['id'] == self.sid)
        store.hydrate(loaded)
        self.assertEqual([timing], self.saved(loaded))
        store.compact(loaded)
        store.hydrate(loaded)
        self.assertEqual([timing], self.saved(loaded))

    def test_final_file_scan_is_excluded_from_request_duration(self):
        self.start()
        self.clock.advance(3.5)
        finished = self.clock.wall
        scan = self.app._finish_observation
        def slow_scan(*args, **kwargs):
            self.clock.advance(90)
            return scan(*args, **kwargs)
        with patch.object(self.app, '_finish_observation', side_effect=slow_scan):
            self.app.emit(self.sid, 'result', {})
        self.assertEqual(3500, self.saved()[0]['durationMs'])
        self.assertEqual(finished, self.saved()[0]['finishedAt'])

    def test_error_duration_uses_monotonic_clock_when_system_clock_moves_backwards(self):
        self.start()
        self.clock.advance(4)
        self.clock.wall -= 1000
        self.app.emit(self.sid, 'error', {'message': 'Controlled failure'})
        timing = self.saved()[0]
        self.assertEqual('error', timing['status'])
        self.assertEqual(4000, timing['durationMs'])
        self.assertLess(timing['finishedAt'], timing['startedAt'])
        self.assertEqual(timing, history_row(self.item)['messages'][0]['runTiming'])

    def test_stop_wins_over_racing_result_and_later_idle_errors_do_not_rewrite_it(self):
        run = self.start()
        self.clock.advance(2)
        self.app.emit(self.sid, 'status', {'state': 'stopping', 'runId': run})
        self.app.emit(self.sid, 'result', {})
        self.assertEqual([], self.saved())
        self.assertIn('_runTimer', self.item)
        self.clock.advance(1)
        self.app.emit(self.sid, 'status', {'state': 'stopped', 'runId': run})
        stopped = deepcopy(self.saved()[0])
        self.assertEqual('stopped', stopped['status'])
        self.assertEqual(3000, stopped['durationMs'])
        for kind, data in [('status', {'state': 'stopped', 'runId': run}),
                           ('error', {'message': 'Later idle connection failure'})]:
            self.clock.advance(20)
            self.app.emit(self.sid, kind, data)
            self.assertNotIn('runTiming', self.item['events'][-1]['data'])
            self.assertEqual([stopped], self.saved())

    def test_queue_wait_is_excluded_and_followup_gets_its_own_clock(self):
        first = self.start('first')
        row = self.app.dispatch.action(self.sid, {'action': 'enqueue', 'text': 'followup', 'attachments': []})['queue'][0]
        self.clock.advance(100)
        self.app.emit(self.sid, 'result', {})
        self.clock.advance(40)
        self.app.dispatch.pump()
        second = self.item['lastRunId']
        self.assertNotEqual(first, second)
        self.assertEqual(row['id'], self.item['messages'][-1]['requestId'])
        self.clock.advance(3)
        self.app.emit(self.sid, 'error', {'message': 'Followup failed'})
        self.assertEqual([(first, 100000, 'done'), (second, 3000, 'error')],
                         [(value['runId'], value['durationMs'], value['status']) for value in self.saved()])

    def test_apply_now_style_stop_then_new_turn_and_late_origin_do_not_mix(self):
        first = self.start('first')
        self.clock.advance(5)
        self.app.emit(self.sid, 'status', {'state': 'stopped', 'runId': first})
        second = self.start('new direction')
        self.clock.advance(7)
        self.app.emit(self.sid, 'error', {'message': 'Old callback'}, _origin=(self.item, first))
        self.assertEqual('starting', self.item['state'])
        self.assertEqual(second, self.item['_runTimer']['runId'])
        self.app.emit(self.sid, 'result', {})
        self.assertEqual([(first, 5000, 'stopped'), (second, 7000, 'done')],
                         [(value['runId'], value['durationMs'], value['status']) for value in self.saved()])

    def test_parallel_tasks_have_independent_start_and_finish(self):
        other = self.app.get(self.app.create(str(self.root), True, title='other')['id'])
        first = self.start()
        self.clock.advance(2)
        second = self.start('other request', other['id'])
        self.clock.advance(3)
        self.app.emit(self.sid, 'result', {})
        self.clock.advance(5)
        self.app.emit(other['id'], 'status', {'state': 'stopped', 'runId': second})
        self.assertEqual((first, 5000, 'done'), tuple(self.saved()[0][key] for key in ('runId', 'durationMs', 'status')))
        self.assertEqual((second, 8000, 'stopped'), tuple(self.saved(other)[0][key] for key in ('runId', 'durationMs', 'status')))

    def test_old_history_and_interrupted_unfinished_request_get_no_fabricated_duration(self):
        self.item.update(lastRunId='legacy', runStartedAt=self.clock.wall - 500,
                         messages=[{'role': 'user', 'text': 'old', 'runId': 'legacy'}])
        self.app.emit(self.sid, 'error', {'message': 'No authoritative start'})
        self.assertEqual([], self.saved())
        self.start('unfinished')
        store = HistoryStore(self.app.state)
        restored = next(item for item in store.load() if item['id'] == self.sid)
        self.assertEqual([], self.saved(restored))
        self.assertNotIn('_runTimer', restored)
        self.assertNotIn('runStartedAt', restored)

    def test_cancelled_file_preparation_has_no_run_or_timing(self):
        scan = self.app._capture_observation
        def cancel_before_send(*args, **kwargs):
            self.clock.advance(60)
            self.app.stop(self.sid)
            return scan(*args, **kwargs)
        with patch.object(self.app, '_capture_observation', side_effect=cancel_before_send):
            with self.assertRaises(ValueError):
                self.start()
        self.assertEqual([], self.item['messages'])
        self.assertNotIn('_runTimer', self.item)
        self.assertFalse(any('runTiming' in event['data'] for event in self.item['events']))

    def test_trimmed_user_message_keeps_one_timing_on_remaining_reply(self):
        run = self.start()
        # The normal bounded display can retain replies after trimming its user.
        self.item['messages'] = [{'role': 'assistant', 'text': 'remaining reply', 'runId': run}]
        self.clock.advance(6)
        self.app.emit(self.sid, 'result', {})
        self.assertEqual(6000, history_row(self.item)['messages'][0]['runTiming']['durationMs'])

    def test_general_chat_uses_the_same_terminal_contract(self):
        chat = self.app.create(kind='chat')
        self.app.send(chat['id'], 'hello', [])
        timing = self.saved(self.app.get(chat['id']))[0]
        self.assertEqual('done', timing['status'])
        self.assertEqual(0, timing['durationMs'])

    def test_live_transcript_limit_moves_completed_timing_to_remaining_same_run_reply(self):
        previous = self.start('previous')
        self.clock.advance(1)
        self.app.emit(self.sid, 'result', {})
        timing = deepcopy(self.saved()[0])
        current = self.start('current')
        self.item['messages'] = [self.item['messages'][0],
            {'role': 'assistant', 'text': 'old reply remains', 'runId': previous}] + [
            {'role': 'assistant', 'text': 'current reply', 'runId': current} for _ in range(148)]
        self.app.emit(self.sid, 'assistant', {'text': 'next block'})
        self.assertEqual(150, len(self.item['messages']))
        self.assertEqual('old reply remains', self.item['messages'][0]['text'])
        self.assertEqual([timing], self.saved())
        self.assertEqual(timing, history_row(self.item)['messages'][0]['runTiming'])

    def test_persisted_transcript_count_and_text_limits_retain_only_visible_run_timing(self):
        run = self.start()
        self.app.emit(self.sid, 'result', {})
        original = deepcopy(self.item['messages'][0])
        timing = original['runTiming']
        for text, count in [('reply', 150), ('x' * 100000, 6)]:
            with self.subTest(limit=len(text)):
                candidate = {**self.item, 'messages': [original] + [
                    {'role': 'assistant', 'text': text, 'runId': run} for _ in range(count)]}
                normalized = history_row(candidate)
                self.assertEqual(1, sum('runTiming' in message for message in normalized['messages']))
                self.assertEqual(timing, normalized['messages'][-1]['runTiming'])
                candidate['messages'] = [original] + [
                    {'role': 'assistant', 'text': text, 'runId': 'different-run'} for _ in range(count)]
                self.assertFalse(any('runTiming' in message for message in history_row(candidate)['messages']))

    def test_optional_timing_rejects_invalid_fields_and_strips_unknown_data(self):
        run = self.start()
        self.clock.advance(2)
        self.app.emit(self.sid, 'result', {})
        timing = self.saved()[0]
        self.assertEqual(timing, normalize_run_timing({**timing, 'secret': 'not persisted'}, run))
        for changed in ({'runId': 'another'}, {'status': 'running'}, {'status': []}, {'status': {}}, {'durationMs': True},
                        {'durationMs': -1}, {'durationMs': float('inf')}, {'startedAt': float('nan')},
                        {'finishedAt': None}, {'startedAt': False}):
            with self.subTest(changed=changed):
                self.item['messages'][0]['runTiming'] = {**timing, **changed}
                self.assertNotIn('runTiming', history_row(self.item)['messages'][0])


if __name__ == '__main__':
    unittest.main()
