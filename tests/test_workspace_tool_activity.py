"""Tool progress comes from correlated CLI evidence, never inferred prose."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from local_app.bridge import ClaudeSession
from local_app.history import HistoryStore, row
from local_app.server import LocalApp
from local_app.tool_activity import (MAX_RECORDS, MAX_TOTAL, MAX_TARGET, ToolActivityCapture,
                                     merge_activity, normalize_activity, normalize_activities)


def record(identifier='tool-one', **fields):
    return {'id': identifier, 'tool': 'Read', 'target': 'report.csv',
            'state': 'requested', 'startedAt': 1.0, **fields}


class ToolActivityCaptureTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.capture = ToolActivityCapture(self.events.append, clock=lambda: 2, run_id='run-one')

    def request(self, identifier='tool-one', name='Read', inputs=None, parent=None):
        self.capture.request({'id': identifier, 'name': name,
                              'input': inputs if inputs is not None else {'file_path': 'C:/private/report.csv'}}, parent=parent)

    def result(self, identifier='tool-one', parent=None, **fields):
        self.capture.result({'tool_use_id': identifier, **fields}, parent=parent)

    def progress(self, identifier='tool-one', name='Read', parent=None, **fields):
        self.capture.progress({'tool_use_id': identifier, 'tool_name': name,
                               'parent_tool_use_id': parent, 'elapsed_time_seconds': 4, **fields})

    def test_read_skill_and_mcp_capture_only_display_targets(self):
        self.request()
        self.request('skill', 'Skill', {'skill': 'company:report', 'args': 'SECRET ARGS'})
        self.request('mcp', 'mcp__server__query', {'query': 'SECRET QUERY', 'token': 'PRIVATE'})
        self.assertEqual(['자료 읽기', '스킬 사용', '연결 도구 호출'], [row['action'] for row in self.events])
        self.assertEqual(['report.csv', 'company:report', ''], [row['target'] for row in self.events])
        self.assertTrue(all(row['runId'] == 'run-one' for row in self.events))
        raw = json.dumps(self.events)
        self.assertNotIn('private', raw)
        self.assertNotIn('SECRET', raw)
        self.assertNotIn('PRIVATE', raw)

    def test_shell_agent_description_only_never_command_prompt_or_result(self):
        self.request(name='Bash', inputs={'description': 'Check project files', 'command': 'echo PRIVATE COMMAND'})
        self.result(content=[{'type': 'text', 'text': 'PRIVATE OUTPUT'}, {'type': 'image', 'data': 'BINARY'}])
        self.request('agent', 'Agent', {'description': 'Check the layout', 'prompt': 'PRIVATE PROMPT'})
        self.assertEqual(['Check project files', 'Check project files', 'Check the layout'], [row['target'] for row in self.events])
        self.assertNotIn('PRIVATE', json.dumps(self.events))
        self.assertNotIn('BINARY', json.dumps(self.events))

    def test_sensitive_description_and_url_query_are_not_retained(self):
        self.request(name='PowerShell', inputs={'description': 'token=PRIVATE'})
        self.request('url', 'Bash', {'description': 'Read https://example.test/x?token=PRIVATE'})
        self.request('address', 'Task', {'description': 'Visit https://example.test/x?q=private'})
        self.assertEqual(['', '', 'Visit [주소]'], [row['target'] for row in self.events])

    def test_generic_tool_retains_actual_name_without_arbitrary_inputs(self):
        self.request(name='NewTool', inputs={'description': 'PRIVATE', 'file_path': 'PRIVATE'})
        self.assertEqual(('NewTool', '도구 호출', ''), tuple(self.events[0][key] for key in ('tool', 'action', 'target')))

    def test_requested_is_not_running_until_valid_matching_progress(self):
        self.request()
        self.progress('unknown')
        self.progress(name='Write')
        self.progress(parent='wrong')
        for value in (-1, float('inf'), float('nan'), True, '4', 10 ** 1000):
            self.progress(elapsed_time_seconds=value)
        self.assertEqual(['requested'], [row['state'] for row in self.events])
        self.progress()
        self.progress()
        self.assertEqual(['requested', 'running'], [row['state'] for row in self.events])

    def test_exact_result_correlation_error_and_no_terminal_regression(self):
        self.request()
        self.result('unknown')
        self.result(is_error='false')
        self.result(parent='wrong')
        self.result(is_error=True)
        self.progress()
        self.result(is_error=False)
        self.request()
        self.assertEqual(['requested', 'error'], [row['state'] for row in self.events])
        self.assertEqual(2, self.events[-1]['finishedAt'])

    def test_child_requires_this_turn_parent_and_matching_result_parent(self):
        self.request('orphan', parent='previous-agent')
        self.request('agent', 'Agent', {})
        self.request('child', parent='agent')
        self.result('child')
        self.result('child', parent='wrong')
        self.result('child', parent='agent')
        self.assertEqual(['agent', 'child', 'child'], [row['id'] for row in self.events])
        self.assertEqual('agent', self.events[-1]['parentToolUseId'])
        self.assertEqual('completed', self.events[-1]['state'])

    def test_long_agent_child_still_correlates_after_parent_leaves_display_window(self):
        self.request('agent', 'Agent', {})
        for index in range(MAX_RECORDS + 2):
            self.request(str(index), parent='agent')
        self.assertEqual(str(MAX_RECORDS + 1), self.events[-1]['id'])
        self.assertEqual(MAX_RECORDS, len(self.capture.records))

    def test_interrupt_seals_capture_and_does_not_invent_result(self):
        self.request()
        self.progress()
        self.capture.interrupt()
        self.request('late')
        self.result()
        self.capture.interrupt()
        self.assertEqual(['requested', 'running', 'interrupted'], [row['state'] for row in self.events])

    def test_malformed_tool_frames_and_parent_cannot_enter_records(self):
        for identifier in (None, [], {}, '', '<script>', 'a' * 161):
            self.request(identifier)
        for tool in (None, [], {}, '', '<script>', 'a' * 161):
            self.request(name=tool)
        for inputs in (None, [], 'abc'):
            self.capture.request({'id': 'bad', 'name': 'Read', 'input': inputs})
        self.request(parent=[])
        self.request(parent='tool-one')
        self.assertEqual([], self.events)

    def test_bounds_and_replay_tombstones_are_finite(self):
        for index in range(4100):
            self.request(str(index), name='Bash', inputs={'description': 'x' * 500})
        self.assertLessEqual(len(self.capture.records), MAX_RECORDS)
        self.assertLessEqual(len(self.capture.seen), 4096)
        self.assertLessEqual(len(self.capture.turn_ids), 4096)
        self.assertTrue(all(len(row['target']) <= MAX_TARGET for row in self.capture.records))
        self.assertLessEqual(sum(len(v) for row in self.capture.records for v in row.values() if isinstance(v, str)), MAX_TOTAL)


class ToolActivityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bridge = ClaudeSession(['never-start'], {}, Path.cwd(), lambda kind, data: self.events.append((kind, data)))

    def tool(self, identifier='tool-one', tool='Read', parent=None):
        self.bridge.handle({'type': 'assistant', 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_use', 'id': identifier, 'name': tool,
                                    'input': {'file_path': 'C:/private/test.txt'}}]}})

    def result(self, identifier='tool-one', parent=None):
        self.bridge.handle({'type': 'user', 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': identifier,
                                    'content': 'PRIVATE RESULT'}]}})

    def activities(self):
        return [value for kind, value in self.events if kind == 'tool_activity']

    def test_nested_tool_result_records_without_leaking_child_text_or_shell_cards(self):
        self.tool('agent', 'Agent')
        self.tool('child', 'Bash', parent='agent')
        self.bridge.handle({'type': 'assistant', 'parent_tool_use_id': 'agent',
                            'message': {'content': [{'type': 'text', 'text': 'PRIVATE CHILD TEXT'}]}})
        self.result('child', parent='agent')
        self.assertEqual(['requested', 'requested', 'completed'], [row['state'] for row in self.activities()])
        self.assertFalse(any(kind in {'assistant', 'execution'} for kind, _ in self.events))
        self.assertNotIn('PRIVATE', json.dumps(self.activities()))

    def test_partial_argument_blocks_do_not_invent_tools_or_complete_them(self):
        for kind in ('content_block_start', 'content_block_delta', 'content_block_stop'):
            self.bridge.handle({'type': 'stream_event', 'event': {'type': kind, 'index': 0,
                'content_block': {'type': 'tool_use', 'id': 'partial', 'name': 'Read', 'input': {}},
                'delta': {'type': 'input_json_delta', 'partial_json': '{'}}})
        self.assertEqual([], self.activities())
        self.tool()
        self.bridge.handle({'type': 'tool_progress', 'tool_use_id': 'tool-one', 'tool_name': 'Read',
                            'parent_tool_use_id': None, 'elapsed_time_seconds': 1})
        self.assertEqual(['requested', 'running'], [row['state'] for row in self.activities()])

    def test_result_stop_close_and_error_leave_unmatched_calls_interrupted(self):
        for ending in ('result', 'error', 'stop', 'close'):
            with self.subTest(ending=ending):
                self.setUp()
                self.tool()
                if ending in {'result', 'error'}:
                    self.bridge.handle({'type': 'result', 'is_error': ending == 'error', 'result': 'done'})
                elif ending == 'stop':
                    self.bridge.interrupt()
                else:
                    self.bridge.close()
                self.assertEqual(['requested', 'interrupted'], [row['state'] for row in self.activities()])
                self.tool('late')
                self.assertEqual(2, len(self.activities()))

    def test_new_send_resets_turn_but_old_request_result_progress_cannot_replay(self):
        self.bridge._tool_activity_run_id = 'run-one'
        self.tool()
        old = self.bridge._tool_activity_capture()
        self.bridge.handle({'type': 'result', 'result': 'done'})
        self.bridge._tool_activity_run_id = 'run-two'
        with patch('local_app.bridge.threading.Thread'):
            self.bridge.send('next request')
        self.tool()
        self.result()
        self.tool('child', parent='tool-one')
        self.bridge.handle({'type': 'tool_progress', 'tool_use_id': 'tool-one', 'tool_name': 'Read',
                            'parent_tool_use_id': None, 'elapsed_time_seconds': 1})
        self.tool('fresh')
        self.result('fresh')
        self.assertEqual(['run-one', 'run-one', 'run-two', 'run-two'], [row['runId'] for row in self.activities()])
        self.assertTrue(old.closed)
        self.assertNotEqual(old, self.bridge._tool_activity_capture())

    def test_intermediate_result_with_delegated_task_keeps_observing_child_work(self):
        self.tool('agent', 'Agent')
        self.bridge.tasks.add('task-one')
        self.bridge.handle({'type': 'result', 'result': 'waiting'})
        self.tool('child', parent='agent')
        self.result('child', parent='agent')
        self.assertEqual('completed', self.activities()[-1]['state'])
        self.assertNotIn('result', [kind for kind, _ in self.events])


class ToolActivityStorageTests(unittest.TestCase):
    def test_normalization_drops_extra_data_and_preserves_identity_and_terminal_state(self):
        values = merge_activity([], record(output='PRIVATE', input={'token': 'PRIVATE'}, action='invented'))
        values = merge_activity(values, record(state='running', target='changed'))
        values = merge_activity(values, record(state='requested'))
        self.assertEqual(('running', 'report.csv', '자료 읽기'), tuple(values[0][key] for key in ('state', 'target', 'action')))
        values = merge_activity(values, record(state='completed'))
        values = merge_activity(values, record(state='error', tool='Write'))
        self.assertEqual('completed', values[0]['state'])
        self.assertNotIn('PRIVATE', json.dumps(values))
        self.assertEqual(2, len(merge_activity(values, record(runId='another'))))

    def test_invalid_history_rows_are_dropped_and_bounds_hold(self):
        for fields in ({'state': []}, {'tool': []}, {'id': '<script>'}, {'startedAt': float('nan')},
                       {'startedAt': True}, {'startedAt': 10 ** 1000}, {'parentToolUseId': 'tool-one'}):
            self.assertIsNone(normalize_activity(record(**fields)))
        self.assertEqual([], normalize_activities(None))
        rows = normalize_activities([record(str(i), tool='X' * 160, target='x' * 160, runId='r' * 64) for i in range(100)])
        self.assertLessEqual(len(rows), MAX_RECORDS)
        self.assertLessEqual(sum(len(v) for row in rows for v in row.values() if isinstance(v, str)), MAX_TOTAL)

    def test_history_save_load_lazy_hydrate_keep_bounded_interrupted_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {'id': str(uuid.uuid4()), 'title': 'Tool evidence', 'workspace': str(Path.cwd()),
                    'created': 1, 'sessionId': None, 'messages': [],
                    'toolActivity': [record(runId='run-one'), record('done', state='completed', runId='run-one')]}
            store = HistoryStore(root)
            store.save([item])
            saved = json.loads((root / 'history-sessions' / (item['id'] + '.json')).read_text('utf-8'))
            self.assertEqual('requested', saved['toolActivity'][0]['state'])
            loaded = HistoryStore(root).load()[0]
            self.assertEqual(['interrupted', 'completed'], [row['state'] for row in loaded['toolActivity']])
            self.assertNotIn('finishedAt', loaded['toolActivity'][0])
            lazy = HistoryStore(root).load(lazy=True)[0]
            self.assertNotIn('toolActivity', lazy)
            store.hydrate(lazy)
            self.assertEqual(loaded['toolActivity'], lazy['toolActivity'])
            store.compact(lazy)
            self.assertNotIn('toolActivity', lazy)
            self.assertEqual([], row({**item, 'toolActivity': None})['toolActivity'])

    def test_server_correlates_current_run_rejects_late_run_and_keeps_approval_state(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory), demo=True)
            try:
                item = app.get(app.create('', True, managed=True)['id'])
                item['lastRunId'] = 'run-current'
                item['state'] = 'approval'
                app.emit(item['id'], 'tool_activity', record(runId='run-old'))
                self.assertNotIn('toolActivity', item)
                app.emit(item['id'], 'tool_activity', record())
                self.assertEqual('run-current', item['toolActivity'][0]['runId'])
                self.assertEqual('approval', item['state'])
                seq = item['seq']
                app.emit(item['id'], 'tool_activity', record())
                self.assertEqual(seq, item['seq'])
                self.assertEqual(item['toolActivity'], app.public(item)['toolActivity'])
                app.emit(item['id'], 'status', {'state': 'running'})
                self.assertEqual('run-current', item['events'][-1]['data']['runId'])
                app.emit(item['id'], 'status', {'state': 'stopped'})
                self.assertEqual('interrupted', item['toolActivity'][0]['state'])
                self.assertEqual(item['toolActivity'], item['events'][-1]['data']['toolActivity'])
            finally:
                app.close()

    def test_prepare_and_retired_bridge_cannot_publish_activity_without_submitted_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory), demo=True)
            try:
                item = app.get(app.create('', True, managed=True)['id'])
                app.emit(item['id'], 'tool_activity', record())
                self.assertNotIn('toolActivity', item)
                item['lastRunId'] = 'previous-history-run'
                bridge = SimpleNamespace(_tool_activity_run_id=None)
                item['bridge'] = bridge
                app._emit_bridge(item['id'], bridge, 'tool_activity', record())
                self.assertNotIn('toolActivity', item)
                bridge._tool_activity_run_id = 'current'
                item['lastRunId'] = 'current'
                app._emit_bridge(item['id'], SimpleNamespace(_tool_activity_run_id='current'), 'tool_activity', record(runId='current'))
                self.assertNotIn('toolActivity', item)
                app._emit_bridge(item['id'], bridge, 'tool_activity', record(runId='previous-history-run'))
                self.assertNotIn('toolActivity', item)
                app._emit_bridge(item['id'], bridge, 'tool_activity', record(runId='current'))
                self.assertEqual('current', item['toolActivity'][0]['runId'])
                # A delayed callback keeps its original capture run even though
                # the long-lived bridge has accepted a newer user request.
                bridge._tool_activity_run_id = 'next'
                item['lastRunId'] = 'next'
                app._emit_bridge(item['id'], bridge, 'tool_activity', record(state='completed', runId='current'))
                self.assertEqual('requested', item['toolActivity'][0]['state'])
            finally:
                item['bridge'] = None
                app.close()

    def test_server_terminal_history_reload_retains_tool_results_and_interrupted_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LocalApp(Path(directory), demo=True)
            item = app.get(app.create('', True, managed=True)['id'])
            item['lastRunId'] = 'run-one'
            app.emit(item['id'], 'tool_activity', record('done'))
            app.emit(item['id'], 'tool_activity', record('done', state='completed'))
            app.emit(item['id'], 'tool_activity', record('waiting'))
            app.emit(item['id'], 'result', {'sessionId': None})
            sid = item['id']
            self.assertEqual(['completed', 'interrupted'], [v['state'] for v in item['toolActivity']])
            app.close()
            resumed = LocalApp(Path(directory), demo=True)
            try:
                self.assertEqual(['completed', 'interrupted'], [v['state'] for v in resumed.public(resumed.get(sid))['toolActivity']])
            finally:
                resumed.close()


if __name__ == '__main__':
    unittest.main()
