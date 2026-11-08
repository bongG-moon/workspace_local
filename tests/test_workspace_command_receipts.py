"""Foreground native command receipts permit review, never file attribution."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from local_app.bridge import ClaudeSession
from local_app.tool_activity import MAX_RECORDS, ToolActivityCapture


class CommandReceiptTests(unittest.TestCase):
    def setUp(self):
        self.events, self.commands, self.writes = [], [], []
        self.capture = ToolActivityCapture(self.events.append, run_id='run-one', clock=lambda: 2,
                                          on_command=self.commands.append, on_write=self.writes.append)

    def request(self, identifier='command', tool='Bash', *, parent=None, enabled=True, **inputs):
        self.capture.request({'id': identifier, 'name': tool,
                              'input': {'command': 'python PRIVATE.py', **inputs}},
                             parent=parent, capture_command=enabled, capture_write=True)

    def result(self, identifier='command', native=None, *, parent=None, metadata=None, enabled=True, error=False):
        self.capture.result({'tool_use_id': identifier, 'is_error': error,
                             'content': 'PRIVATE output says wrote C:/PRIVATE/report.csv'},
                            parent=parent, native=native, metadata=metadata, capture_command=enabled)

    def test_native_foreground_success_emits_small_receipt_without_claiming_paths(self):
        for identifier, tool, native in (
                ('native', 'Bash', {'interrupted': False, 'stdout': 'PRIVATE', 'stderr': ''}),
                ('legacy', 'PowerShell', {'exit_code': 0}),
                ('adapter', 'Bash', {'exitCode': 0})):
            self.request(identifier, tool)
            self.result(identifier, native)
        self.assertEqual(['native', 'legacy', 'adapter'], [row['toolUseId'] for row in self.commands])
        self.assertTrue(all(row == {'runId': 'run-one', 'toolUseId': row['toolUseId'], 'tool': row['tool'],
                                    'startedAt': 2, 'finishedAt': 2} for row in self.commands))
        self.assertNotIn('PRIVATE', json.dumps(self.commands))
        self.assertEqual([], self.writes)

    def test_no_native_success_cannot_be_inferred_from_command_or_output(self):
        for index, native in enumerate((None, [], 'success', {}, {'stdout': 'Completed successfully'},
                                         {'interrupted': 'false'}, {'interrupted': 0},
                                         {'exitCode': True}, {'exit_code': '0'}, {'exitCode': 1},
                                         {'interrupted': True, 'exitCode': 0},
                                         {'interrupted': False, 'exit_code': 1})):
            self.request(str(index))
            self.result(str(index), native)
        self.assertEqual([], self.commands)

    def test_error_staged_cancelled_and_background_metadata_never_emit(self):
        rejected = (
            {'non_execution_kind': 'cancelled'}, {'non_execution_kind': None},
            {'remedy': {'kind': 'staged_for_review'}}, {'remedy': 'invalid'},
            {'failed': True}, {'staged': True}, {'cancelled': True}, {'status': 'failed'},
        )
        for index, metadata in enumerate((*rejected, [], 'bad')):
            self.request(str(index))
            self.result(str(index), {'interrupted': False}, metadata=metadata)
        for index, fields in enumerate((
                {'interrupted': True}, {'error': 'failed'}, {'is_error': True}, {'failed': True},
                {'staged': True}, {'cancelled': True}, {'canceled': True}, {'status': 'background'},
                {'backgroundTaskId': 'task'}, {'background_task_id': 'task'},
                {'backgroundedByUser': True}, {'backgroundEndsWithFinalResponse': True},
                {'background': True}, {'timedOutAfterMs': 100}, {'timedOutAfterMs': 0})):
            identifier = 'native-' + str(index)
            self.request(identifier)
            self.result(identifier, {'interrupted': False, **fields})
        self.request('failed')
        self.result('failed', {'interrupted': False}, error=True)
        self.request('background-input', run_in_background=True)
        self.result('background-input', {'interrupted': False})
        self.assertEqual([], self.commands)

    def test_parent_or_orphan_and_other_tools_are_not_foreground_commands(self):
        self.request('orphan', parent='previous-agent')
        self.request('agent', 'Agent')
        self.request('child', parent='agent')
        self.result('child', {'interrupted': False}, parent='wrong')
        self.result('child', {'interrupted': False}, parent='agent')
        self.request('read', 'Read')
        self.result('read', {'interrupted': False})
        self.result('missing', {'interrupted': False})
        self.assertEqual([], self.commands)

    def test_result_requires_enabled_submitted_request_and_matching_parent(self):
        self.request('not-submitted', enabled=False)
        self.result('not-submitted', {'interrupted': False})
        self.request('replay')
        self.result('replay', {'interrupted': False}, enabled=False)
        self.result('replay', {'interrupted': False})
        self.request('root')
        self.result('root', {'interrupted': False}, parent='wrong')
        self.assertEqual([], self.commands)
        self.result('root', {'interrupted': False})
        self.assertEqual(['root'], [row['toolUseId'] for row in self.commands])
        self.result('root', {'interrupted': False})
        self.assertEqual(1, len(self.commands))

    def test_interrupt_and_reused_previous_turn_ids_do_not_create_receipts(self):
        self.request('old')
        tombstones = (self.capture.seen, self.capture.order)
        self.capture.interrupt()
        self.result('old', {'interrupted': False})
        fresh = ToolActivityCapture(self.events.append, run_id='run-two', on_command=self.commands.append,
                                    tombstones=tombstones)
        fresh.request({'id': 'old', 'name': 'Bash', 'input': {}}, capture_command=True)
        fresh.result({'tool_use_id': 'old'}, native={'interrupted': False})
        self.assertEqual([], self.commands)
        self.assertEqual(set(), self.capture.command_ids)

    def test_optional_callback_and_invalid_run_retain_existing_activity_behavior(self):
        for fields in ({}, {'on_command': self.commands.append, 'run_id': '../invalid'}):
            capture = ToolActivityCapture(self.events.append, **fields)
            capture.request({'id': 'one', 'name': 'Bash', 'input': {}}, capture_command=True)
            capture.result({'tool_use_id': 'one'}, native={'interrupted': False})
            self.assertEqual('completed', capture.records[-1]['state'])
        self.assertEqual([], self.commands)

    def test_pending_command_ids_are_bounded_and_cleared(self):
        for index in range(MAX_RECORDS * 2):
            self.request(str(index))
        self.assertLessEqual(len(self.capture.command_ids), MAX_RECORDS)
        self.capture.interrupt()
        self.assertEqual(set(), self.capture.command_ids)

    def test_callback_runs_without_capture_lock_and_cannot_break_activity(self):
        entered, acquired = threading.Event(), threading.Event()
        def callback(value):
            def worker():
                with self.capture.lock:
                    acquired.set()
            thread = threading.Thread(target=worker)
            thread.start()
            entered.set()
            self.assertTrue(acquired.wait(1), 'observation callback holds the capture lock')
            thread.join(1)
            raise RuntimeError('optional recorder unavailable')
        self.capture.on_command = callback
        self.request()
        self.result(native={'interrupted': False})
        self.assertTrue(entered.is_set())
        self.assertEqual('completed', self.events[-1]['state'])


class CommandReceiptBridgeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bridge = ClaudeSession(['never-start'], {}, Path.cwd(),
                                    lambda kind, value: self.events.append((kind, value)))
        self.bridge._tool_activity_run_id = 'run-one'
        self.bridge.busy = True
        self.bridge._turn_submitted = True
        self.addCleanup(self.bridge.close)

    def tool(self, identifier='one', *, tool='Bash', parent=None, **frame):
        self.bridge.handle({'type': 'assistant', 'parent_tool_use_id': parent, **frame,
            'message': {'content': [{'type': 'tool_use', 'id': identifier, 'name': tool,
                                     'input': {'command': 'python PRIVATE.py'}}]}})

    def result(self, identifier='one', native=None, *, parent=None, blocks=None, **frame):
        self.bridge.handle({'type': 'user', 'parent_tool_use_id': parent, **frame,
            'tool_use_result': {'interrupted': False} if native is None else native,
            'message': {'content': blocks or [{'type': 'tool_result', 'tool_use_id': identifier,
                                              'content': 'PRIVATE'}]}})

    def commands(self):
        return [value for kind, value in self.events if kind == 'file_command']

    def test_bridge_emits_current_root_native_success_only(self):
        self.tool()
        self.result()
        self.assertEqual([{'runId': 'run-one', 'toolUseId': 'one', 'tool': 'Bash',
                           'startedAt': self.commands()[0]['startedAt'],
                           'finishedAt': self.commands()[0]['finishedAt']}], self.commands())
        self.assertFalse(any(kind == 'file_write' for kind, _ in self.events))

    def test_replay_request_result_ambiguous_or_invalid_metadata_do_not_emit(self):
        self.tool('replayed', isReplay=True)
        self.result('replayed')
        self.tool('result-replay')
        self.result('result-replay', isReplay=True)
        self.tool('many')
        self.result('many', blocks=[{'type': 'tool_result', 'tool_use_id': 'many'},
                                    {'type': 'tool_result', 'tool_use_id': 'other'}])
        for index, metadata in enumerate(({}, [None], [{'id': 'bad'}, {'id': 'bad'}])):
            identifier = 'invalid-' + str(index)
            self.tool(identifier)
            self.result(identifier, tool_result_meta=metadata)
        self.assertEqual([], self.commands())

    def test_nonexecuted_metadata_is_matched_to_its_own_tool(self):
        self.tool()
        self.result(tool_result_meta=[{'id': 'one', 'non_execution_kind': 'cancelled'}])
        self.tool('two')
        self.result('two', tool_result_meta=[{'id': 'two', 'remedy': {'kind': 'staged_for_review'}}])
        self.assertEqual([], self.commands())

    def test_connection_preparation_stop_or_child_cannot_emit(self):
        self.bridge.busy = False
        self.tool('preparing')
        self.bridge.busy = True
        self.result('preparing')
        self.tool('agent', tool='Agent')
        self.tool('child', parent='agent')
        self.result('child', parent='agent')
        self.tool('stopping')
        self.bridge.stopping = True
        self.result('stopping')
        self.assertEqual([], self.commands())

    def test_new_turn_tombstones_prevent_old_command_result_replay(self):
        self.tool('old')
        self.bridge.handle({'type': 'result', 'result': 'done'})
        self.bridge._tool_activity_run_id = 'run-two'
        with patch('local_app.bridge.threading.Thread'):
            self.bridge.send('next')
        self.bridge._turn_submitted = True
        self.tool('old')
        self.result('old')
        self.tool('fresh')
        self.result('fresh')
        self.assertEqual([('run-two', 'fresh')], [(row['runId'], row['toolUseId']) for row in self.commands()])


if __name__ == '__main__':
    unittest.main()
