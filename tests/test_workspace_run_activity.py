"""Live progress names only observed work and keeps transcript memory bounded."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_app.bridge import ClaudeSession
from local_app.progress_log import MAX_RECORD_BYTES, ProgressCapture, clean_text
from local_app.server import LocalApp
from local_app.tool_activity import (MAX_RECORDS, RunActivityCapture, ToolActivityCapture,
                                     normalize_activities, normalize_run_activity)


def assistant(*blocks, message='message-one', parent=None, **fields):
    return dict(type='assistant', message={'id': message, 'content': list(blocks)},
                parent_tool_use_id=parent, **fields)


def tool(identifier='tool-one', name='Read', **inputs):
    return {'type': 'tool_use', 'id': identifier, 'name': name, 'input': inputs}


def stream(kind, **fields):
    return {'type': 'stream_event', 'event': {'type': kind, **fields}}


def live(phase='answering', run='run-one', stamp=1, **fields):
    return dict(runId=run, phase=phase, updatedAt=stamp, **fields)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.capture = RunActivityCapture(self.events.append, run_id='run-one', clock=lambda: 1.0)

    def test_streamed_tool_preparation_is_distinct_from_requested_and_confirmed_running(self):
        self.capture.handle(stream('message_start', message={'id': 'message-one'}))
        self.capture.handle(stream('content_block_start', index=0, content_block=tool()))
        self.capture.handle(stream('message_start', message={'id': 'message-one'}))
        self.capture.handle(stream('content_block_delta', index=0,
                                   delta={'type': 'input_json_delta', 'partial_json': '{"file_path":"PRIVATE PATH"'}))
        self.assertEqual(['receiving', 'tool_preparing'], [row['phase'] for row in self.events])
        self.capture.handle(assistant(tool(file_path='C:/reports/report.csv')))
        self.capture.handle({'type': 'tool_progress', 'tool_use_id': 'tool-one', 'tool_name': 'Wrong', 'elapsed_time_seconds': 1})
        self.assertEqual('tool_requested', self.events[-1]['phase'])
        self.capture.handle({'type': 'tool_progress', 'tool_use_id': 'tool-one', 'tool_name': 'Read', 'elapsed_time_seconds': 1})
        self.capture.handle({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'tool-one', 'content': 'PRIVATE OUTPUT'}]}})
        self.assertEqual(['receiving', 'tool_preparing', 'tool_requested', 'tool_running', 'tool_result'],
                         [row['phase'] for row in self.events])
        self.assertEqual('report.csv', self.events[2]['target'])
        self.assertIn('준비', self.events[1]['label'])
        raw = json.dumps(self.events)
        self.assertNotIn('PRIVATE', raw)
        self.assertNotIn('C:/reports', raw)

    def test_text_deltas_are_phase_throttled_and_never_retain_body_or_thinking(self):
        self.capture.handle(stream('message_start', message={'id': 'message-one'}))
        self.capture.handle(stream('content_block_start', index=0,
                                   content_block={'type': 'thinking', 'thinking': 'PRIVATE THINKING'}))
        self.capture.handle(stream('content_block_delta', index=0,
                                   delta={'type': 'thinking_delta', 'thinking': 'PRIVATE THINKING'}))
        for _ in range(1000):
            self.capture.handle(stream('content_block_delta', index=1,
                                       delta={'type': 'text_delta', 'text': 'PUBLIC CONTENT'}))
        self.assertEqual(['receiving', 'answering'], [row['phase'] for row in self.events])
        self.assertNotIn('CONTENT', json.dumps(self.events))
        self.assertNotIn('THINKING', repr(vars(self.capture)))
        self.assertNotIn('CONTENT', repr(vars(self.capture)))

    def test_orphan_nested_tool_mismatch_and_terminal_replay_do_not_change_phase(self):
        self.capture.handle(assistant(tool('agent', 'Agent', description='Prepare report')))
        self.capture.handle(assistant(tool('child', 'Bash', command='PRIVATE COMMAND'), parent='agent'))
        count = len(self.events)
        self.capture.handle(assistant({'type': 'text', 'text': 'stale'}, parent='orphan'))
        self.capture.handle({'type': 'tool_progress', 'tool_use_id': 'child', 'tool_name': 'Bash', 'elapsed_time_seconds': 1})
        self.assertEqual(count, len(self.events))
        frame = {'type': 'user', 'parent_tool_use_id': 'agent', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'child', 'is_error': True, 'content': 'PRIVATE ERROR'}]}}
        self.capture.handle(frame)
        self.assertTrue(self.events[-1]['failed'])
        self.assertEqual('agent', self.events[-1]['parentToolUseId'])
        count = len(self.events)
        self.capture.handle(frame)
        self.capture.handle(assistant(tool('child', 'Bash'), parent='agent'))
        self.assertEqual(count, len(self.events))
        self.assertNotIn('PRIVATE', json.dumps(self.events))

    def test_old_frame_message_tool_task_ids_cannot_become_new_run_activity(self):
        shared = OrderedDict()
        old = RunActivityCapture(self.events.append, run_id='old', tombstones=shared)
        old.handle(assistant(tool('old-tool'), uuid='frame-old'))
        old.handle({'type': 'system', 'subtype': 'task_started', 'task_id': 'old-task'})
        old.finish()
        new = RunActivityCapture(self.events.append, run_id='new', tombstones=shared)
        count = len(self.events)
        new.handle(assistant({'type': 'text', 'text': 'late'}, message='message-one'))
        new.handle(assistant(tool('old-tool'), message='message-new'))
        new.handle(assistant({'type': 'text', 'text': 'late'}, message='other', uuid='frame-old'))
        new.handle({'type': 'system', 'subtype': 'task_started', 'task_id': 'old-task'})
        old.handle(assistant({'type': 'text', 'text': 'after close'}))
        self.assertEqual(count, len(self.events))
        new.handle(assistant({'type': 'text', 'text': 'new'}, message='fresh'))
        self.assertEqual('new', self.events[-1]['runId'])
        for field in ('frames', 'tools', 'texts', 'messages'):
            self.assertFalse(getattr(old, field))

    def test_settings_unknown_frames_and_empty_stream_do_not_invent_answer_writing(self):
        for data in ({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': 'plan'},
                     {'type': 'system', 'subtype': 'init'},
                     {'type': 'control_response', 'response': {'status': 'running'}},
                     stream('content_block_start', index=0, content_block=tool()),
                     stream('content_block_delta', index=0, delta={'type': 'text_delta', 'text': 'orphan'})):
            self.capture.handle(data)
        self.assertEqual([], self.events)
        self.capture.handle({'type': 'system', 'subtype': 'status', 'status': 'compacting'})
        self.assertEqual('compacting', self.events[-1]['phase'])

    def test_bad_fields_and_secret_targets_are_bounded_and_label_is_not_trusted(self):
        row = normalize_run_activity(live('tool_requested', tool='Bash', toolUseId='tool', target='token=PRIVATE', label='FAKE RUNNING'))
        self.assertEqual('', row['target'])
        self.assertNotIn('FAKE', row['label'])
        for fields in ({'phase': 'invented'}, {'updatedAt': float('inf')}, {'runId': '../bad'},
                       {'phase': 'tool_running', 'tool': 'Bad Tool', 'toolUseId': 'tool'}):
            self.assertIsNone(normalize_run_activity({**live(), **fields}))
        self.capture.handle(assistant(tool('credential', 'Bash', description='glpat-12345678901234567890')))
        self.assertNotIn('1234567890', json.dumps(self.events))
        self.capture.handle(assistant(tool('long', 'Bash', description='확인 ' * 1000)))
        self.assertLessEqual(len(self.events[-1]['target']), 160)
        self.assertLess(len(self.events[-1]['label']), 250)


class BridgeMemoryTests(unittest.TestCase):
    def test_long_unbroken_unicode_output_stays_bounded_and_secrets_still_redact(self):
        value, truncated = clean_text('가' * 30000 + '\nANTHROPIC_AUTH_TOKEN="PRIVATE VALUE"')
        self.assertTrue(truncated)
        self.assertLessEqual(len(value.encode()), MAX_RECORD_BYTES)
        self.assertTrue(value.startswith('가' * 100))
        self.assertNotIn('PRIVATE', value)
        self.assertNotIn('PRIVATE', clean_text('key_prefix_' * 20 + 'token="PRIVATE VALUE"')[0])

    def test_large_stream_keeps_hashes_and_final_assistant_output_unchanged(self):
        events = []
        bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, value: events.append((kind, value)))
        value = 'Actual public line\n' * 50000
        bridge.handle(stream('message_start', message={'id': 'large-message'}))
        bridge.handle(stream('content_block_start', index=3, content_block={'type': 'text', 'text': ''}))
        for index in range(0, len(value), 8192):
            bridge.handle(stream('content_block_delta', index=3, delta={'type': 'text_delta', 'text': value[index:index + 8192]}))
        bridge.handle(assistant({'type': 'text', 'text': value}, message='large-message'))
        bridge.handle(assistant({'type': 'text', 'text': value}, message='large-message'))
        bridge.handle({'type': 'result', 'result': value})
        answers = [row for kind, row in events if kind == 'assistant']
        self.assertEqual(1, len(answers))
        self.assertEqual(value, answers[0]['text'])
        self.assertEqual(3, answers[0]['index'])
        self.assertEqual(value, ''.join(row['text'] for kind, row in events if kind == 'assistant_delta'))
        digest = hashlib.sha256(value.encode()).digest()
        self.assertEqual([digest], list(bridge.seen_text))
        self.assertTrue(all(row[2] == digest for row in bridge._assistant_frames))
        self.assertEqual(digest, next(iter(bridge._stream_blocks.values())).digest())

    def test_core_fingerprints_are_bounded_and_distinct_blocks_remain_distinct(self):
        events = []
        bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, value: events.append((kind, value)))
        with patch('local_app.bridge.MAX_TURN_EVIDENCE', 8):
            for i in range(24):
                bridge.handle(assistant({'type': 'text', 'text': 'block ' + str(i)}, message='same-message'))
        self.assertEqual(24, sum(kind == 'assistant' for kind, _ in events))
        self.assertEqual(8, len(bridge.seen_text))
        self.assertEqual(8, len(bridge._assistant_frames))
        self.assertEqual(8, len(bridge._finalized_blocks))

    def test_progress_completed_raw_blocks_are_freed_with_stop_before_full_dedupe(self):
        rows = []
        capture = ProgressCapture(rows.append, run_id='run')
        value = 'Actual detail\n' * 10000
        capture.handle(stream('message_start', message={'id': 'large-message'}))
        capture.handle(stream('content_block_start', index=0, content_block={'type': 'text', 'text': ''}))
        for index in range(0, len(value), 8192):
            capture.handle(stream('content_block_delta', index=0, delta={'type': 'text_delta', 'text': value[index:index + 8192]}))
        self.assertLessEqual(len(capture.blocks[0]['text'].encode()), MAX_RECORD_BYTES)
        capture.handle(stream('content_block_stop', index=0))
        self.assertEqual('', capture.blocks[0]['text'])
        capture.handle(assistant({'type': 'text', 'text': value}, message='large-message'))
        self.assertEqual('', capture.blocks[0]['text'])
        self.assertEqual(1, len(rows))
        capture.finish()
        for field in ('frames', 'tools', 'seen', 'root_texts', 'blocks'):
            self.assertFalse(getattr(capture, field))

    def test_live_phase_requires_submitted_run_and_closes_on_terminal_result(self):
        events = []
        bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, value: events.append((kind, value)))
        bridge.handle(stream('message_start', message={'id': 'prepare'}))
        self.assertFalse(any(kind == 'run_activity' for kind, _ in events))
        bridge._tool_activity_run_id = 'run-one'
        bridge._begin_tool_activity_turn()
        bridge.busy = True
        bridge.handle(stream('message_start', message={'id': 'active'}))
        bridge.handle(stream('content_block_start', index=0, content_block=tool()))
        self.assertEqual('tool_preparing', [r for k, r in events if k == 'run_activity'][-1]['phase'])
        bridge.handle({'type': 'result', 'result': 'done'})
        count = sum(kind == 'run_activity' for kind, _ in events)
        bridge.handle(stream('message_start', message={'id': 'late'}))
        self.assertEqual(count, sum(kind == 'run_activity' for kind, _ in events))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(self.temp.name, True)['id']
        self.item = self.app.get(self.sid)
        self.item.update(lastRunId='run-one', state='running')

    def test_latest_live_metadata_is_coalesced_without_hiding_approval_or_changing_state(self):
        self.app.emit(self.sid, 'request', {'id': 'approve', 'tool': 'Bash', 'input': {}})
        self.item['choice'] = {'id': 'choice'}
        for i in range(350):
            self.app.emit(self.sid, 'run_activity', live('answering' if i % 2 else 'receiving', stamp=i + 1))
        self.assertEqual('approval', self.item['state'])
        self.assertIn('approve', self.item['requests'])
        self.assertEqual('choice', self.item['choice']['id'])
        self.assertEqual(['request', 'run_activity'], [row['type'] for row in self.item['events']])
        self.assertEqual('answering', self.app.public(self.item)['runActivity']['phase'])
        self.assertEqual([], self.item['messages'])
        self.assertFalse(self.app.progress_logs.metadata(self.sid)['available'])
        self.app.emit(self.sid, 'status', {'state': 'stopped', 'label': '중지'})
        self.assertIsNone(self.app.public(self.item)['runActivity'])

    def test_stale_run_timestamp_idle_model_control_and_retired_bridge_are_ignored(self):
        self.app.emit(self.sid, 'run_activity', live(stamp=5))
        self.app.emit(self.sid, 'run_activity', live('receiving', stamp=4))
        self.app.emit(self.sid, 'run_activity', live('receiving', stamp=6, run='old'))
        for flag in ('_connecting', '_modelUpdating'):
            self.item[flag] = True
            self.app.emit(self.sid, 'run_activity', live('receiving', stamp=7))
            self.item.pop(flag)
        self.item['state'] = 'done'
        self.app.emit(self.sid, 'run_activity', live('receiving', stamp=8))
        self.assertIsNone(self.app.public(self.item)['runActivity'])
        self.item['state'] = 'running'
        current = SimpleNamespace(_tool_activity_run_id=None)
        self.item['bridge'] = current
        self.app._emit_bridge(self.sid, current, 'run_activity', live('receiving', stamp=9))
        self.app._emit_bridge(self.sid, SimpleNamespace(_tool_activity_run_id='run-one'), 'run_activity', live('receiving', stamp=9))
        self.item['bridge'] = None
        self.assertEqual(5, self.item['runActivity']['updatedAt'])
        self.assertEqual(1, len(self.item['events']))

    def test_pending_tool_survives_completed_churn_capture_snapshot_and_normalize(self):
        capture = ToolActivityCapture(lambda value: self.app.emit(self.sid, 'tool_activity', value), run_id='run-one')
        capture.request(tool('pending', 'Bash', description='Long check'))
        for i in range(100):
            identifier = 'short-' + str(i)
            capture.request(tool(identifier))
            capture.result({'tool_use_id': identifier})
        self.assertEqual(MAX_RECORDS, len(capture.records))
        self.assertTrue(any(row['id'] == 'pending' for row in self.app.public(self.item)['toolActivity']))
        capture.result({'tool_use_id': 'pending'})
        self.assertEqual('completed', next(row['state'] for row in capture.records if row['id'] == 'pending'))
        raw = [{'id': 'pending', 'tool': 'Bash', 'startedAt': 1, 'state': 'requested'}]
        raw.extend({'id': 'done-' + str(i), 'tool': 'Read', 'startedAt': 1, 'state': 'completed'} for i in range(100))
        normalized = normalize_activities(raw)
        self.assertEqual(MAX_RECORDS, len(normalized))
        self.assertEqual('pending', normalized[0]['id'])


if __name__ == '__main__':
    unittest.main()
