"""Execution evidence comes only from correlated CLI tool results."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from local_app.bridge import ClaudeSession
from local_app.executions import (ExecutionCapture, MAX_COMMAND, MAX_DESCRIPTION, MAX_OUTPUT,
                                  MAX_RECORDS, MAX_TOTAL, merge_execution, normalize_execution,
                                  normalize_executions, result_text)
from local_app.history import HistoryStore, row


def record(identifier='tool-one', **changes):
    return {'id': identifier, 'tool': 'Bash', 'command': 'printf hello', 'description': 'Check output',
            'state': 'requested', 'output': '', 'truncated': False, 'startedAt': 1.0, **changes}


class ExecutionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bridge = ClaudeSession(['never-start'], {}, Path.cwd(),
            lambda kind, data: self.events.append((kind, data)))

    def executions(self):
        return [value for kind, value in self.events if kind == 'execution']

    def tool(self, identifier='tool-one', *, parent=None, tool='Bash', command='printf hello'):
        self.bridge.handle({'type': 'assistant', 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_use', 'id': identifier, 'name': tool,
                'input': {'command': command, 'description': 'Check output'}}]}})

    def result(self, identifier='tool-one', *, parent=None, content='hello', **fields):
        self.bridge.handle({'type': 'user', 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': identifier,
                                    'content': content, **fields}]}})

    def test_correlated_success_and_error_capture_exact_command_and_output(self):
        self.tool()
        self.result(content=[{'type': 'text', 'text': 'hello'}, {'type': 'text', 'text': 'world'}])
        self.tool('ps', tool='PowerShell', command='Get-Item missing')
        self.result('ps', content='Cannot find path', is_error=True)
        values = self.executions()
        self.assertEqual(['requested', 'completed', 'requested', 'error'], [value['state'] for value in values])
        self.assertEqual('hello\nworld', values[1]['output'])
        self.assertEqual('Get-Item missing', values[-1]['command'])
        self.assertEqual('Cannot find path', values[-1]['output'])
        self.assertGreaterEqual(values[-1]['finishedAt'], values[-1]['startedAt'])
        self.assertNotIn('stdout', values[-1])
        self.assertNotIn('stderr', values[-1])

    def test_unknown_child_and_non_shell_tools_never_create_execution_evidence(self):
        self.result('unknown')
        self.tool('child', parent='delegate')
        self.result('child')
        self.tool('read', tool='Read')
        self.result('read')
        self.tool('root')
        self.result('root', parent='delegate')
        self.assertEqual(['requested'], [value['state'] for value in self.executions()])

    def test_request_and_result_replay_emit_only_once_and_do_not_regress(self):
        self.tool(); self.tool()
        self.result(); self.result(is_error=True)
        self.tool()
        self.assertEqual(['requested', 'completed'], [value['state'] for value in self.executions()])

    def test_turn_success_without_tool_result_remains_interrupted_not_completed(self):
        self.tool()
        self.bridge.handle({'type': 'result', 'is_error': False, 'result': 'All done'})
        values = self.executions()
        self.assertEqual(['requested', 'interrupted'], [value['state'] for value in values])
        self.assertEqual('', values[-1]['output'])
        self.assertLess(self.events.index(('execution', values[-1])), next(i for i, event in enumerate(self.events) if event[0] == 'result'))

    def test_turn_error_and_stop_and_close_do_not_invent_tool_success(self):
        for terminal in ('error', 'stop', 'close'):
            with self.subTest(terminal=terminal):
                self.setUp()
                self.tool()
                if terminal == 'error':
                    self.bridge.handle({'type': 'result', 'is_error': True, 'errors': ['failed']})
                elif terminal == 'stop':
                    self.bridge.interrupt()
                else:
                    self.bridge.close()
                self.assertEqual(['requested', 'interrupted'], [value['state'] for value in self.executions()])
                self.bridge.close()
                self.assertEqual(2, len(self.executions()))

    def test_malformed_ids_and_blocks_cannot_crash_or_become_success(self):
        for identifier in (None, [], {}, '', '<script>', 'x' * 161):
            self.tool(identifier)
            self.result(identifier)
        self.bridge.handle({'type': 'assistant', 'message': {'content': [None, 'bad',
            {'type': 'tool_use', 'name': [], 'id': 'bad', 'input': {}},
            {'type': 'tool_use', 'name': 'Bash', 'id': 'bad', 'input': []}]}})
        self.tool()
        self.result(is_error='false')
        self.assertEqual(['requested'], [value['state'] for value in self.executions()])

    def test_execution_capture_is_lazy_for_object_new_fixtures(self):
        fixture = object.__new__(ClaudeSession)
        fixture.lock = threading.RLock()
        fixture.emit = lambda kind, data: self.events.append((kind, data))
        capture = fixture._execution_capture()
        capture.request({'id': 'fixture', 'name': 'Bash', 'input': {'command': 'echo fixture'}})
        fixture._interrupt_executions()
        self.assertIs(capture, fixture._execution_capture())
        self.assertEqual(['requested', 'interrupted'], [value['state'] for value in self.executions()])

    def test_closed_capture_does_not_admit_a_late_tool_frame(self):
        self.tool()
        self.bridge._interrupt_executions(closed=True)
        self.tool('late-frame')
        self.result('late-frame')
        self.assertEqual(['requested', 'interrupted'], [value['state'] for value in self.executions()])
        fresh = ClaudeSession(['never-start'], {}, Path.cwd(), self.bridge.emit)
        fresh._interrupt_executions(closed=True)
        fresh._execution_capture().request({'id': 'late', 'name': 'Bash', 'input': {'command': 'echo late'}})
        self.assertEqual(2, len(self.executions()))


class ExecutionBoundsTests(unittest.TestCase):
    def test_fields_and_total_records_are_bounded_and_latest_records_are_kept(self):
        values = []
        for index in range(80):
            values = merge_execution(values, record(str(index), command='command ' * 2000,
                description='description ' * 100, output='output ' * 4000, state='completed', finishedAt=2))
        self.assertLessEqual(len(values), MAX_RECORDS)
        self.assertLessEqual(sum(len(v) for row in values for v in row.values() if isinstance(v, str)), MAX_TOTAL)
        self.assertEqual('79', values[-1]['id'])
        self.assertTrue(all(value['truncated'] for value in values))
        self.assertTrue(all(len(value['command']) <= MAX_COMMAND and len(value['output']) <= MAX_OUTPUT
                            and len(value['description']) <= MAX_DESCRIPTION for value in values))

    def test_binary_image_content_is_not_stringified_or_retained(self):
        image = 'iVBORw0KGgo' + 'A' * 1000
        output, cut = result_text([{'type': 'image', 'source': {'type': 'base64', 'data': image}},
                                  {'type': 'text', 'text': 'actual text'}, {'stdout': 'not a text block'}])
        self.assertEqual('actual text', output)
        self.assertTrue(cut)
        value = normalize_execution(record(output='data:image/png;base64,' + image))
        self.assertNotIn(image, value['output'])
        self.assertTrue(value['truncated'])
        self.assertEqual(('', True), result_text({'stdout': 'unverified stream'}))

    def test_invalid_records_are_dropped_and_duplicate_events_are_terminal_monotonic(self):
        for changes in ({'id': []}, {'state': []}, {'tool': {}}, {'startedAt': float('nan')},
                        {'startedAt': 10 ** 1000}, {'command': []}):
            self.assertIsNone(normalize_execution(record(**changes)))
        values = merge_execution([], record(), run_id='run-one')
        values = merge_execution(values, record(state='completed', output='actual', finishedAt=2), run_id='run-one')
        values = merge_execution(values, record(), run_id='run-one')
        values = merge_execution(values, record(state='error', output='replay'), run_id='run-one')
        self.assertEqual(1, len(values))
        self.assertEqual(('completed', 'actual'), (values[0]['state'], values[0]['output']))
        self.assertEqual(2, len(merge_execution(values, record(), run_id='run-two')))

    def test_capture_retention_and_replay_tombstones_remain_bounded(self):
        capture = ExecutionCapture(lambda value: None, clock=lambda: 1)
        for index in range(4100):
            capture.request({'id': str(index), 'name': 'Bash', 'input': {'command': 'echo ok'}})
        self.assertLessEqual(len(capture.records), MAX_RECORDS)
        self.assertLessEqual(len(capture.seen), 4096)
        self.assertLessEqual(len(capture.order), 4096)


class ExecutionHistoryTests(unittest.TestCase):
    def item(self):
        return {'id': str(uuid.uuid4()), 'title': 'Execution fixture', 'workspace': str(Path.cwd()),
                'created': 1.0, 'sessionId': None,
                'messages': [{'role': 'user', 'text': 'run', 'runId': 'run-one'},
                             {'role': 'assistant', 'text': 'result', 'runId': 'run-one'}],
                'executions': [record(runId='run-one'), record('finished', runId='run-one',
                              state='completed', output='hello', finishedAt=2)]}

    def test_save_reload_and_lazy_hydration_preserve_evidence_without_invented_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = self.item()
            store = HistoryStore(root)
            store.save([item])
            saved = json.loads((root / 'history-sessions' / (item['id'] + '.json')).read_text('utf-8'))
            self.assertEqual('requested', saved['executions'][0]['state'])
            loaded = HistoryStore(root).load()[0]
            self.assertEqual(['interrupted', 'completed'], [value['state'] for value in loaded['executions']])
            self.assertNotIn('finishedAt', loaded['executions'][0])
            self.assertEqual('hello', loaded['executions'][1]['output'])
            self.assertTrue(all(value['runId'] == 'run-one' for value in loaded['messages']))
            lazy = HistoryStore(root).load(lazy=True)[0]
            self.assertNotIn('executions', lazy)
            store.hydrate(lazy)
            self.assertEqual(loaded['executions'], lazy['executions'])
            store.compact(lazy)
            self.assertNotIn('executions', lazy)

    def test_history_drops_invalid_message_run_ids_and_untrusted_extra_execution_fields(self):
        item = self.item()
        item['messages'][0]['runId'] = 'x' * 65
        item['messages'][1]['runId'] = '<script>'
        item['executions'][0]['image'] = {'data': 'never stored'}
        value = row(item)
        self.assertTrue(all('runId' not in message for message in value['messages']))
        self.assertNotIn('image', value['executions'][0])
        self.assertEqual([], normalize_executions({'bad': 'records'}))


if __name__ == '__main__':
    unittest.main()
