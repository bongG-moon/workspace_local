"""Slow readers recover the authoritative display without replaying a prompt."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.event_recovery import (EVENT_LIMIT, display_streams, needs_recovery, reset_display,
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

    def fetch(self, path):
        with urlopen(Request(self.server.origin + path,
                             headers={'Authorization': 'Bearer ' + self.app.token}), timeout=5) as response:
            return json.loads(response.read())

    def events(self, after, sid=None):
        return self.fetch(f'/api/events?id={sid or self.sid}&after={after}')

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

    def test_evicted_history_is_reopened_for_a_gap_only(self):
        self.app.emit(self.sid, 'assistant', {'text': 'first answer'})
        for index in range(EVENT_LIMIT):
            self.app.emit(self.sid, 'notice', {'message': str(index)})
        cursor = self.item['seq']
        self.app.emit(self.sid, 'assistant_delta', {'text': 'next answer'})
        for _ in range(8):
            other = self.app.create(str(self.root), True)
            self.app.get(other['id'])
        self.assertTrue(self.item.get('_historyUnloaded'))
        access = self.app._history_access.copy()

        heartbeat = self.app.poll_events(self.sid, self.item['seq'], timeout=0)
        self.assertEqual({'events': [], 'seq': self.item['seq'], 'state': self.item['state']}, heartbeat)
        response = self.events(cursor)
        self.assertEqual([('assistant_delta', 'next answer')],
                         [(e['type'], e['data']['text']) for e in response['events']])
        self.assertEqual(self.item['seq'], response['seq'])
        self.assertTrue(self.item.get('_historyUnloaded'))
        self.assertEqual(access, self.app._history_access)

        snapshot = self.events(0)['recovery']
        self.assertEqual('event_gap', snapshot['reason'])
        self.assertEqual(['first answer'], [row['text'] for row in snapshot['session']['messages']])

    def test_old_cursor_recovers_durable_body_from_cold_index_after_restart(self):
        self.app.emit(self.sid, 'assistant', {'text': 'before restart'})
        cursor = self.item['seq']
        self.app.close()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.server.app = self.app
        self.assertTrue(self.app.sessions[self.sid]['_historyIndexOnly'])

        response = self.events(cursor)
        self.assertEqual(0, response['seq'])
        self.assertEqual('cursor_reset', response['recovery']['reason'])
        self.assertEqual(['before restart'],
                         [m['text'] for m in response['recovery']['session']['messages']])

    def test_poll_rechecks_visibility_while_waiting_without_reopening_history(self):
        self.app.history.compact(self.item)
        entered = threading.Event()
        original = self.app.session_visibility.contains

        def visible(sid):
            hidden = original(sid)
            if sid == self.sid:
                entered.set()
            return hidden

        with patch.object(self.app.session_visibility, 'contains', side_effect=visible), ThreadPoolExecutor(1) as pool:
            waiting = pool.submit(self.events, self.item['seq'])
            self.assertTrue(entered.wait(2))
            with self.app.lock:
                self.app.session_visibility.set_hidden(self.sid, True)
            with self.assertRaises(HTTPError) as caught:
                waiting.result(3)
            with caught.exception as error:
                self.assertEqual(400, error.code)
                self.assertIn('보관', json.loads(error.read())['error'])
        self.assertTrue(self.item.get('_historyUnloaded'))

    def test_pending_removal_or_deleted_session_rejects_even_a_buffered_event(self):
        self.app.emit(self.sid, 'notice', {'message': 'saved'})
        for flag in ('_removingFromList', '_deleting'):
            with self.subTest(flag=flag), patch.dict(self.item, {flag: True}):
                with self.assertRaises(HTTPError) as caught:
                    self.events(0)
                with caught.exception as error:
                    self.assertEqual(400, error.code)
        chat = self.app.create(kind='chat')['id']
        self.app.delete_chat(chat, confirmed=True)
        with self.assertRaises(HTTPError) as caught:
            self.events(0, chat)
        with caught.exception as error:
            self.assertEqual(400, error.code)

    def test_leaving_one_view_keeps_the_other_view_and_background_work_running(self):
        other = self.app.create(str(self.root), True)['id']
        self.app.emit(self.sid, 'status', {'state': 'running', 'label': 'working'})
        cursor = self.item['seq']
        uri = f'/api/events?id={self.sid}&after={cursor}'
        headers = {'Authorization': 'Bearer ' + self.app.token}
        started, finished = threading.Event(), threading.Event()
        handle_get = Handler.do_GET

        def observe_handler(handler):
            if handler.headers.get('User-Agent') != 'departing test view':
                return handle_get(handler)
            started.set()
            try:
                return handle_get(handler)
            finally:
                finished.set()

        def connect():
            return closing(HTTPConnection(*self.server.server_address, timeout=4))

        with patch.object(Handler, 'do_GET', observe_handler), connect() as legacy, connect() as remaining:
            # Older HTTP readers can finish writing their request and still expect an event.
            legacy.request('GET', uri, headers=headers)
            legacy.sock.shutdown(socket.SHUT_WR)
            remaining.request('GET', uri + '&cancelOnDisconnect=1', headers=headers)
            with connect() as departed:
                departed.request('GET', uri + '&cancelOnDisconnect=1', headers={**headers, 'User-Agent': 'departing test view'})
                self.assertTrue(started.wait(2))
            self.assertTrue(finished.wait(3), 'the disconnected view kept its idle event handler')
            self.assertEqual(other, self.fetch(f'/api/session?id={other}')['id'])
            self.app.emit(other, 'assistant', {'text': 'separate answer'})
            self.app.emit(self.sid, 'assistant', {'text': 'completed in background'})
            with legacy.getresponse() as response, remaining.getresponse() as second_response:
                self.assertEqual((200, 200), (response.status, second_response.status))
                first, second = json.load(response), json.load(second_response)
        self.assertEqual(first, second)
        self.assertEqual(['completed in background'], [e['data']['text'] for e in first['events']])
        self.app.emit(self.sid, 'result', {})
        self.assertEqual(['result'], [e['type'] for e in self.events(first['seq'])['events']])
        resumed = self.fetch(f'/api/session?id={self.sid}')
        self.assertEqual('done', resumed['state'])
        self.assertEqual(['completed in background'], [m['text'] for m in resumed['messages']])
        self.assertEqual(['separate answer'], [e['data']['text'] for e in self.events(0, other)['events']])

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
