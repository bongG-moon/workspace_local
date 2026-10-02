"""Detailed progress is paged, bounded and independent of concise activity."""
from collections import OrderedDict
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from local_app.bridge import ClaudeSession
from local_app.progress_log import MAX_RECORD_BYTES, ProgressCapture, ProgressStore, clean_text
from local_app.server import LocalApp, Server


def assistant(*blocks, message_id='message-one', parent=None, **fields):
    return dict(type='assistant', message={'id': message_id, 'content': list(blocks)},
                parent_tool_use_id=parent, **fields)


def text(value):
    return {'type': 'text', 'text': value}


def tool(identifier='read-one', name='Read', **inputs):
    return {'type': 'tool_use', 'id': identifier, 'name': name, 'input': inputs}


def result(identifier='read-one', content='actual output', parent=None, **fields):
    return {'type': 'user', 'parent_tool_use_id': parent, 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': identifier, 'content': content, **fields}]}}


def record(key='one', run='run-one', value='reported detail', **fields):
    return dict(key=key, runId=run, time=3.0, kind='assistant', title='Claude 메시지', text=value, **fields)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'progress'
        self.store = ProgressStore(self.directory)
        self.sid = str(uuid.uuid4())

    def test_absent_preupgrade_log_is_explicit_and_read_does_not_create_files(self):
        self.assertFalse(self.store.metadata(self.sid)['available'])
        response = self.store.page(self.sid)
        self.assertEqual([], response['records'])
        self.assertIn('복원할 수 없습니다', response['notice'])
        self.assertFalse(self.directory.exists())

    def test_many_records_persist_and_page_latest_ascending_with_exclusive_cursor(self):
        for i in range(140):
            self.store.append(self.sid, record(str(i), run='a' if i % 2 else 'b', value=str(i)))
        fresh = ProgressStore(self.directory)
        page = fresh.page(self.sid, run_id='a', limit=50)
        self.assertEqual(140, page['progress']['count'])
        self.assertEqual(list(range(41, 140, 2)), [int(r['text']) for r in page['records']])
        self.assertTrue(page['hasMore'])
        older = fresh.page(self.sid, run_id='a', before=page['nextBefore'], limit=50)
        self.assertEqual(list(range(1, 40, 2)), [int(r['text']) for r in older['records']])
        self.assertFalse(older['hasMore'])
        self.assertIsNone(older['nextBefore'])

    def test_same_block_update_has_stable_sequence_and_incremented_revision(self):
        first = self.store.append(self.sid, record())
        self.assertIsNone(self.store.append(self.sid, record()))
        changed = self.store.append(self.sid, record(value='more actual detail'))
        self.assertEqual(first['lastSeq'], changed['lastSeq'])
        self.assertEqual(first['revision'] + 1, changed['revision'])
        self.assertEqual(1, changed['count'])
        self.assertEqual('more actual detail', self.store.page(self.sid)['records'][0]['text'])

    def test_unicode_record_limit_and_disk_rotation_are_explicit(self):
        self.store.append(self.sid, record(value='긴 내용 ' * 20000))
        row = self.store.page(self.sid)['records'][0]
        self.assertLessEqual(len(row['text'].encode('utf-8')), MAX_RECORD_BYTES)
        self.assertTrue(row['truncated'])
        self.store = ProgressStore(self.directory, max_bytes=128 * 1024)
        for i in range(80):
            self.store.append(self.sid, record(str(i), value=('detail ' + str(i) + '\n') * 200))
        page = self.store.page(self.sid)
        self.assertTrue(page['progress']['truncated'])
        self.assertIn('오래된', page['notice'])
        self.assertLess(page['progress']['count'], 80)
        self.assertLessEqual((self.directory / (self.sid + '.sqlite3')).stat().st_size, 128 * 1024)
        self.assertFalse(list(self.directory.glob('*-journal')))

    def test_invalid_cursors_ids_and_paths_do_not_open_arbitrary_files(self):
        for fields in ({'before': '1 OR 1=1'}, {'before': '-2'}, {'limit': '101'},
                       {'limit': '-1'}, {'run_id': '../bad'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.page(self.sid, **fields)
        self.assertFalse(self.store.metadata('../escape')['available'])
        self.assertFalse(self.directory.exists())

    def test_storage_failure_never_raises_and_explains_missing_detail(self):
        with patch('local_app.progress_log.sqlite3.connect', side_effect=sqlite3.OperationalError('private disk error')):
            meta = self.store.append(self.sid, record())
            page = self.store.page(self.sid)
        self.assertFalse(meta['available'])
        self.assertTrue(meta['truncated'])
        self.assertIn('대화 처리는 계속', page['notice'])
        self.assertNotIn('private disk error', json.dumps(page))

    def test_linked_log_directory_is_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            self.directory.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Windows symlink permission is unavailable')
        self.assertFalse(self.store.append(self.sid, record())['available'])
        self.assertEqual([], list(outside.iterdir()))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.capture = ProgressCapture(self.rows.append, run_id='run-one', clock=lambda: 4.0)

    def stream(self, event, **fields):
        self.capture.handle(dict(type='stream_event', event=event, **fields))

    def test_same_message_multiple_single_block_frames_preserved_and_replay_deduped(self):
        first = assistant(text('First block'))
        second = assistant(text('Second block'))
        for frame in (first, second, first, second):
            self.capture.handle(frame)
        self.assertEqual(['First block', 'Second block'], [r['text'] for r in self.rows])

    def test_stream_then_final_before_stop_is_one_record_per_public_block(self):
        self.stream({'type': 'message_start', 'message': {'id': 'message-one'}})
        for index, value in enumerate(('First block', 'Second block')):
            self.stream({'type': 'content_block_start', 'index': index, 'content_block': text('')})
            self.stream({'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': value}})
            self.capture.handle(assistant(text(value)))
            self.stream({'type': 'content_block_stop', 'index': index})
            self.capture.handle(assistant(text(value)))
        self.assertEqual(['First block', 'Second block'], [r['text'] for r in self.rows])
        self.capture.handle({'type': 'result', 'result': 'Second block', 'duration_ms': 10})
        self.assertEqual(1, sum(r['text'] == 'Second block' for r in self.rows))
        self.assertEqual('CLI 보고 소요 시간: 10ms', self.rows[-1]['text'])

    def test_split_credentials_private_thinking_and_control_frames_never_stored(self):
        self.stream({'type': 'message_start', 'message': {'id': 'message-one'}})
        self.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': 'PRIVATE THINKING'}})
        self.stream({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'PRIVATE THINKING'}})
        self.stream({'type': 'content_block_start', 'index': 1, 'content_block': text('')})
        for fragment in ('Authorization: Bea', 'rer custom-', 'secret-value'):
            self.stream({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': fragment}})
        self.assertEqual([], self.rows)
        self.stream({'type': 'content_block_stop', 'index': 1})
        for frame in ({'type': 'control_request', 'request': {'token': 'CONTROL SECRET'}},
                      {'type': 'auth_status', 'output': ['AUTH SECRET']},
                      {'type': 'system', 'subtype': 'init', 'apiKey': 'INIT SECRET'},
                      {'type': 'system', 'subtype': 'unknown', 'text': 'RAW SYSTEM SECRET'}):
            self.capture.handle(frame)
        raw = json.dumps(self.rows)
        for secret in ('custom-', 'secret-value', 'PRIVATE THINKING', 'CONTROL SECRET', 'AUTH SECRET', 'INIT SECRET', 'RAW SYSTEM SECRET'):
            self.assertNotIn(secret, raw)
        self.assertEqual(1, len(self.rows))

    def test_stream_stop_before_complete_assistant_also_dedupes(self):
        self.stream({'type': 'message_start', 'message': {'id': 'message-one'}})
        self.stream({'type': 'content_block_start', 'index': 0, 'content_block': text('public hello')})
        self.stream({'type': 'content_block_stop', 'index': 0})
        self.capture.handle(assistant(text('public hello')))
        self.capture.handle({'type': 'result', 'result': 'public hello'})
        self.assertEqual(1, sum(r['text'] == 'public hello' for r in self.rows))

    def test_actual_commands_inputs_results_and_nested_worker_text_are_retained(self):
        self.capture.handle(assistant(tool('agent', 'Agent', prompt='Inspect files carefully')))
        self.capture.handle(assistant(text('Worker examined the file'), tool('child', 'Bash', command='python verify.py'), parent='agent'))
        self.capture.handle(result('child', [text('verified 143 files'), {'type': 'image', 'source': {'type': 'base64', 'data': 'BINARY SECRET'}}], parent='agent'))
        self.capture.handle(result('child', 'unrelated root result'))
        self.capture.handle(assistant(text('orphan worker'), parent='previous-turn-agent'))
        self.assertEqual(['tool_input', 'assistant', 'tool_input', 'tool_result'], [r['kind'] for r in self.rows])
        self.assertIn('python verify.py', self.rows[2]['text'])
        self.assertEqual('agent', self.rows[3]['parentToolUseId'])
        self.assertEqual('verified 143 files', self.rows[3]['text'])
        self.assertNotIn('BINARY SECRET', json.dumps(self.rows))

    def test_more_than_eighty_tools_keep_parent_and_full_detail(self):
        self.capture.handle(assistant(tool('agent', 'Agent')))
        for i in range(150):
            self.capture.handle(assistant(tool('tool-' + str(i), 'Read', path='report-' + str(i)), parent='agent'))
        self.capture.handle(assistant(text('Still a child after 150 tools'), parent='agent'))
        self.assertEqual(152, len(self.rows))

    def test_useful_hooks_task_status_errors_and_results_only(self):
        for event in ({'subtype': 'task_started', 'task_id': 'task', 'description': 'Make report', 'task_type': 'local_agent'},
                      {'subtype': 'task_updated', 'task_id': 'task', 'patch': {'status': 'completed', 'secret': 'PRIVATE'}},
                      {'subtype': 'hook_response', 'hook_name': 'Review', 'stdout': 'passed', 'exit_code': 0, 'raw': 'PRIVATE'},
                      {'subtype': 'status', 'status': 'compacting'},
                      {'subtype': 'status', 'status': None, 'permissionMode': 'plan'}):
            self.capture.handle(dict(type='system', **event))
        self.capture.handle({'type': 'result', 'is_error': True, 'errors': ['check failed token=PRIVATE_VALUE'], 'duration_ms': 15})
        self.assertEqual(['task', 'task', 'hook', 'status', 'error'], [r['kind'] for r in self.rows])
        self.assertIn('check failed', self.rows[-1]['text'])
        self.assertNotIn('PRIVATE', json.dumps(self.rows))

    def test_redaction_covers_structured_keys_headers_commands_urls_and_media(self):
        secret = 'PRIVATEVALUE'
        self.capture.handle(assistant(tool('one', 'Bash', command=f'curl -H "Authorization: Bearer {secret}" https://user:{secret}@example.test/?token={secret}',
                                           nested={'apiKey': secret, 'password': secret}, source={'type': 'base64', 'data': secret})))
        self.capture.handle(result(content=[text(f'glpat-12345678901234567890\nAuthorization: Bearer {secret}\nCookie: a={secret}; b={secret}\ndata:image/png;base64,' + 'A' * 1000)]))
        raw = json.dumps(self.rows)
        self.assertNotIn(secret, raw)
        self.assertNotIn('12345678901234567890', raw)
        self.assertNotIn('A' * 100, raw)
        for value in (f'Authorization: Bearer {secret}', f'Bearer {secret}', f'password="{secret} with spaces"', f'--token {secret}'):
            self.assertNotIn(secret, clean_text(value)[0])

    def test_old_message_tool_frame_ids_cannot_be_relabelled_to_new_run(self):
        tombstones = OrderedDict()
        first = ProgressCapture(self.rows.append, run_id='old', tombstones=tombstones)
        first.handle(assistant(text('old message'), tool('old-tool'), uuid='old-frame'))
        first.finish()
        count = len(self.rows)
        second = ProgressCapture(self.rows.append, run_id='new', tombstones=tombstones)
        second.handle(assistant(text('late'), message_id='message-one'))
        second.handle(assistant(tool('old-tool'), message_id='new-message'))
        second.handle({'type': 'result', 'uuid': 'old-frame', 'result': 'late result'})
        first.handle(assistant(text('after close')))
        self.assertEqual(count, len(self.rows))
        second.handle(assistant(text('new report'), message_id='new-message'))
        self.assertEqual('new', self.rows[-1]['runId'])

    def test_unsubmitted_capture_and_broken_writer_do_not_affect_execution(self):
        ProgressCapture(self.rows.append, run_id=None).handle(assistant(text('connection preparation')))
        self.assertEqual([], self.rows)
        capture = ProgressCapture(lambda _: (_ for _ in ()).throw(OSError('disk failed')), run_id='run')
        capture.handle(assistant(text('actual report')))


class BridgeTests(unittest.TestCase):
    def test_transport_error_is_recorded_safely_only_during_submitted_run(self):
        events = []
        bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, value: events.append((kind, value)))
        bridge._progress_error('connection preparation')
        self.assertEqual([], events)
        bridge._tool_activity_run_id = 'run-one'
        bridge._begin_tool_activity_turn()
        bridge.busy = True
        bridge._progress_error('CLI connection failed Authorization: Bearer PRIVATE_ERROR_VALUE')
        bridge._interrupt_executions()
        bridge._progress_error('late connection error')
        records = [value for kind, value in events if kind == 'progress_record']
        self.assertEqual(1, len(records))
        self.assertEqual('error', records[0]['kind'])
        self.assertIn('CLI connection failed', records[0]['text'])
        self.assertNotIn('PRIVATE_ERROR_VALUE', records[0]['text'])

    def test_only_submitted_turn_records_and_terminal_seals_capture(self):
        events = []
        bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, value: events.append((kind, value)))
        bridge.handle(assistant(text('preparation')))
        self.assertFalse(any(k == 'progress_record' for k, _ in events))
        bridge._tool_activity_run_id = 'run-one'
        bridge._begin_tool_activity_turn()
        bridge.busy = True
        bridge.handle(assistant(tool('agent', 'Agent'), text('Main report'), message_id='submitted'))
        bridge.handle(assistant(text('Child report'), parent='agent', message_id='child'))
        bridge.handle({'type': 'result', 'result': 'Main report'})
        count = sum(k == 'progress_record' for k, _ in events)
        bridge.handle(assistant(text('late report'), message_id='late'))
        self.assertEqual(count, sum(k == 'progress_record' for k, _ in events))
        rows = [v for k, v in events if k == 'progress_record']
        self.assertTrue(any(v['text'] == 'Child report' for v in rows))
        self.assertFalse(any(k == 'assistant' and v.get('text') == 'Child report' for k, v in events))
        self.assertTrue(all(v['runId'] == 'run-one' for v in rows))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        self.item.update(lastRunId='run-one', state='running')

    def test_metadata_only_event_does_not_change_state_or_evict_approval(self):
        self.app.emit(self.sid, 'request', {'id': 'approval-one', 'tool': 'Bash', 'input': {}})
        before = self.item['seq']
        for i in range(350):
            self.app.emit(self.sid, 'progress_record', record(str(i), value='DETAIL NOT EVENT ' + str(i)))
        self.assertEqual('approval', self.item['state'])
        self.assertEqual(2, len(self.item['events']))
        self.assertEqual('request', self.item['events'][0]['type'])
        event = self.item['events'][-1]
        self.assertGreater(event['seq'], before)
        self.assertEqual('progress_changed', event['type'])
        self.assertEqual(350, event['data']['progress']['count'])
        self.assertNotIn('DETAIL NOT EVENT', json.dumps(self.item['events']))
        public = self.app.public(self.item)
        self.assertEqual(350, public['progress']['count'])
        self.assertNotIn('DETAIL NOT EVENT', json.dumps(public))
        self.assertEqual([], self.item['messages'])

    def test_stale_inactive_preparation_and_retired_bridge_callbacks_are_ignored(self):
        self.app.emit(self.sid, 'progress_record', record(run='old-run'))
        self.item['state'] = 'done'
        self.app.emit(self.sid, 'progress_record', record())
        self.item['state'] = 'running'
        current = SimpleNamespace(_tool_activity_run_id=None)
        self.item['bridge'] = current
        self.app._emit_bridge(self.sid, current, 'progress_record', record())
        self.app._emit_bridge(self.sid, SimpleNamespace(_tool_activity_run_id='run-one'), 'progress_record', record())
        self.item['bridge'] = None
        self.assertFalse(self.app.public(self.item)['progress']['available'])

    def test_real_routes_require_auth_origin_visible_session_and_page_before_run_filter(self):
        server = Server(self.app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def shutdown():
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.addCleanup(shutdown)
        for i in range(5):
            self.app.emit(self.sid, 'progress_record', record(str(i)))
        def fetch(params=None, headers=None):
            request = Request(server.origin + '/api/progress?' + urlencode(params or {'id': self.sid}),
                              headers=headers if headers is not None else {'Authorization': 'Bearer ' + self.app.token})
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        page = fetch({'id': self.sid, 'runId': 'run-one', 'limit': 2})
        self.assertEqual([4, 5], [r['seq'] for r in page['records']])
        self.assertEqual(4, page['nextBefore'])
        self.assertEqual([], fetch({'id': self.sid, 'runId': 'other'})['records'])
        for headers in ({}, {'Authorization': 'Bearer incorrect'},
                        {'Authorization': 'Bearer ' + self.app.token, 'Origin': 'https://evil.example'}):
            with self.subTest(headers=list(headers)), self.assertRaises(HTTPError) as caught:
                fetch(headers=headers)
            self.assertEqual(403, caught.exception.code)
        for params in ({'id': '../escape'}, {'id': str(uuid.uuid4())}, {'id': self.sid, 'limit': 101}):
            with self.subTest(params=params), self.assertRaises(HTTPError) as caught:
                fetch(params)
            self.assertEqual(400, caught.exception.code)
        self.item.update(state='done')
        self.app.hide_session(self.sid, True)
        with self.assertRaises(HTTPError) as caught:
            fetch()
        self.assertEqual(400, caught.exception.code)


if __name__ == '__main__':
    unittest.main()
