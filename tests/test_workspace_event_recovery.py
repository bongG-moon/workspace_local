"""Slow readers recover the authoritative display without replaying a prompt."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from local_app.event_recovery import (display_streams, needs_recovery, reset_display,
                                      snapshot_recovery, track_display_event, trim_events)
from local_app.server import Handler, LocalApp, Server


class DisplayRecoveryTests(unittest.TestCase):
    def item(self):
        return {'id': 'one', 'seq': 0, 'state': 'running', 'messages': [], 'events': []}

    def event(self, item, kind, data):
        track_display_event(item, kind, data)
        item['seq'] += 1
        item['events'].append({'seq': item['seq'], 'type': kind, 'data': data})
        trim_events(item)

    def test_only_capacity_eviction_advances_floor_not_coalesced_sequences(self):
        item = self.item()
        for index in range(350):
            item['events'] = [row for row in item['events'] if row['type'] != 'progress_changed']
            self.event(item, 'progress_changed', {'count': index})
        self.assertEqual(1, len(item['events']))
        self.assertFalse(needs_recovery(item, 0))
        for index in range(300):
            self.event(item, 'notice', {'message': str(index)})
        self.assertEqual(350, item['_eventsDiscardedThrough'])
        self.assertTrue(needs_recovery(item, 349))
        self.assertFalse(needs_recovery(item, 350))
        self.assertEqual(300, len(item['events']))

    def test_partial_prefix_order_finalization_and_frozen_snapshot(self):
        item = self.item()
        item['messages'].append({'role': 'user', 'text': 'request'})
        for index in range(320):
            self.event(item, 'assistant_delta', {'messageId': 'answer', 'index': 2, 'text': f'{index} '})
        snapshot = snapshot_recovery(item, {'id': 'one', 'messages': item['messages'], 'seq': item['seq']}, 0)
        self.assertEqual(''.join(f'{i} ' for i in range(320)), snapshot['session']['streams'][0]['text'])
        self.assertEqual(0, snapshot['session']['streams'][0]['afterMessage'])
        self.assertNotIn('_eventAnchor', snapshot['session']['messages'][0])
        self.event(item, 'assistant_delta', {'messageId': 'answer', 'index': 2, 'text': 'later'})
        self.assertNotIn('later', snapshot['session']['streams'][0]['text'])
        self.event(item, 'assistant', {'messageId': 'answer', 'index': 2, 'text': 'complete'})
        self.assertEqual([], display_streams(item))

    def test_terminal_fragment_is_bounded_transient_not_persisted_as_completed(self):
        item = self.item()
        self.event(item, 'assistant_delta', {'messageId': 'answer', 'text': 'partial'})
        item['state'] = 'error'
        self.event(item, 'error', {'message': 'provider failure'})
        snapshot = snapshot_recovery(item, {'messages': []}, 0)['session']
        self.assertEqual([], snapshot['messages'])
        self.assertTrue(snapshot['streams'][0]['interrupted'])
        self.assertEqual('provider failure', snapshot['displayError'])
        self.event(item, 'status', {'state': 'starting', 'label': 'starting next'})
        self.assertEqual([], display_streams(item))
        self.assertNotIn('_displayError', item)

    def test_streams_share_existing_display_bounds_and_release_on_disconnect(self):
        item = self.item()
        for index in range(7):
            self.event(item, 'assistant_delta', {'messageId': str(index), 'text': 'a' * 110000})
        rows = display_streams(item)
        self.assertLessEqual(sum(len(row['text']) for row in rows), 500000)
        self.assertTrue(all(len(row['text']) <= 100000 for row in rows))
        self.assertTrue(rows[-1]['uiTruncated'])
        for index in range(20):
            self.event(item, 'assistant_delta', {'messageId': f'new-{index}', 'text': 'x'})
        self.assertLessEqual(len(display_streams(item)), 16)
        self.event(item, 'connection_idle', {})
        self.assertEqual([], display_streams(item))

    def test_fresh_timeline_clears_old_floor_and_detects_old_client_cursor(self):
        item = self.item()
        item['messages'] = [{'role': 'user', 'text': 'old fork message', '_eventAnchor': 1}]
        for _ in range(305):
            self.event(item, 'notice', {})
        item.update(seq=0, events=[])
        reset_display(item)
        self.assertNotIn('_eventAnchor', item['messages'][0])
        self.assertFalse(needs_recovery(item, 0))
        self.assertTrue(needs_recovery(item, 305))
        self.assertEqual('cursor_reset', snapshot_recovery(item, {}, 305)['reason'])


class EventRecoveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.server = Server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def events(self, after):
        with urlopen(Request(self.server.origin + f'/api/events?id={self.sid}&after={after}',
                             headers={'Authorization': 'Bearer ' + self.app.token}), timeout=5) as response:
            return json.loads(response.read())

    def overflow(self):
        self.app.emit(self.sid, 'assistant', {'messageId': 'first', 'text': 'completed answer'})
        for index in range(320):
            self.app.emit(self.sid, 'assistant_delta', {'messageId': 'second', 'index': 0, 'text': f'{index} '})

    def test_actual_http_gap_returns_completed_answer_and_full_partial_prefix_once(self):
        self.item['state'] = 'running'
        self.overflow()
        self.app.emit(self.sid, 'request', {'id': 'permission', 'tool': 'Read', 'input': {'file_path': 'safe.txt'}})
        payload = self.events(0)
        self.assertEqual([], payload['events'])
        snapshot = payload['recovery']['session']
        self.assertEqual(payload['seq'], snapshot['seq'])
        self.assertEqual('completed answer', snapshot['messages'][0]['text'])
        self.assertEqual(''.join(f'{i} ' for i in range(320)), snapshot['streams'][0]['text'])
        self.assertEqual('permission', snapshot['requests'][0]['id'])
        self.assertEqual('approval', snapshot['state'])
        self.app.emit(self.sid, 'assistant', {'messageId': 'second', 'index': 0, 'text': 'final answer'})
        next_payload = self.events(payload['seq'])
        self.assertNotIn('recovery', next_payload)
        self.assertEqual(['assistant'], [row['type'] for row in next_payload['events']])
        self.assertEqual([], display_streams(self.item))

    def test_normal_poll_does_not_construct_public_snapshot_or_repeat_request(self):
        self.app.emit(self.sid, 'assistant_delta', {'messageId': 'one', 'text': 'prefix'})
        with patch.object(self.app, 'public', side_effect=AssertionError('unnecessary full snapshot')):
            payload = self.events(0)
        self.assertNotIn('recovery', payload)
        self.assertEqual(1, len(payload['events']))
        self.assertEqual([], self.item['messages'])

    def test_actual_coalesced_activity_does_not_trigger_false_gap(self):
        self.item.update(state='running', lastRunId='one')
        for index in range(350):
            self.app.emit(self.sid, 'run_activity', {'runId': 'one', 'phase': 'answering', 'updatedAt': index + 1})
        with patch.object(self.app, 'public', side_effect=AssertionError('false gap')):
            payload = self.events(0)
        self.assertNotIn('recovery', payload)
        self.assertEqual(1, len(payload['events']))
        self.assertEqual(350, payload['events'][0]['seq'])

    def test_recovery_cursor_snapshot_remain_frozen_when_terminal_arrives_during_reply(self):
        self.item['state'] = 'running'
        self.overflow()
        entered, release = threading.Event(), threading.Event()
        original = Handler.reply
        def slow_reply(handler, value, *args, **kwargs):
            if isinstance(value, dict) and 'recovery' in value:
                entered.set()
                if not release.wait(5):
                    raise AssertionError('reply test gate was not released')
            return original(handler, value, *args, **kwargs)
        with patch.object(Handler, 'reply', slow_reply), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.events, 0)
            try:
                self.assertTrue(entered.wait(2))
                self.app.emit(self.sid, 'assistant', {'messageId': 'second', 'index': 0, 'text': 'done now'})
                self.app.emit(self.sid, 'result', {})
            finally:
                release.set()
            first = future.result(3)
        self.assertEqual('running', first['recovery']['session']['state'])
        self.assertEqual(321, first['seq'])
        self.assertEqual(1, len(first['recovery']['session']['messages']))
        self.assertTrue(first['recovery']['session']['streams'][0]['text'].startswith('0 1 '))
        events = self.events(first['seq'])['events']
        self.assertEqual(['assistant', 'result'], [event['type'] for event in events])


if __name__ == '__main__':
    unittest.main()
