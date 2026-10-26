"""A request's display clock survives navigation, permissions and recovery."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_app.event_recovery import snapshot_recovery
from local_app.history import row as history_row
from local_app.server import LocalApp


class RunElapsedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = LocalApp(Path(self.temp.name) / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(self.temp.name, True)['id']
        self.item = self.app.get(self.sid)
        # Exercise real admission without starting a real AI request or demo timers.
        self.demo = patch('local_app.demo.run', lambda *_: None)
        self.demo.start()
        self.addCleanup(self.demo.stop)

    def start(self, text='first request', **kwargs):
        self.app.send(self.sid, text, [], **kwargs)
        return self.app.public(self.item)

    def test_request_start_survives_progress_other_tasks_and_recovery(self):
        first = self.start()
        self.assertGreater(first['runStartedAt'], 0)
        self.app.emit(self.sid, 'status', {'state': 'running', 'label': 'working'})
        self.app.emit(self.sid, 'assistant', {'text': 'partial answer'})
        self.app.emit(self.sid, 'status', {'state': 'running', 'label': 'another tool'})
        other = self.app.create(self.temp.name, True, title='another task')
        self.app.public(self.app.get(other['id']))
        restored = self.app.public(self.item)
        self.assertEqual(first['runStartedAt'], restored['runStartedAt'])
        self.assertEqual(first['lastRunId'], restored['lastRunId'])
        for event in self.item['events']:
            if event['type'] == 'status':
                self.assertEqual(first['runStartedAt'], event['data']['runStartedAt'])
                self.assertEqual(first['lastRunId'], event['data']['runId'])
        recovery = snapshot_recovery(self.item, restored, 0)['session']
        self.assertEqual(first['runStartedAt'], recovery['runStartedAt'])
        self.assertEqual(1, sum(row['role'] == 'user' for row in self.item['messages']))

    def test_permission_and_question_waits_resume_the_same_request_clock(self):
        first = self.start()
        for tool, state in [('Read', 'approval'), ('AskUserQuestion', 'question')]:
            self.app.emit(self.sid, 'request', {'id': tool, 'tool': tool, 'input': {}})
            self.assertEqual(state, self.app.public(self.item)['state'])
            self.assertEqual(first['runStartedAt'], self.item['events'][-1]['data']['runStartedAt'])
            self.app.emit(self.sid, 'request_closed', {'id': tool})
            self.assertEqual('running', self.item['state'])
            self.assertEqual(first['runStartedAt'], self.item['events'][-1]['data']['runStartedAt'])
            self.assertEqual(first['lastRunId'], self.item['events'][-1]['data']['runId'])
        self.assertEqual(first['runStartedAt'], self.app.public(self.item)['runStartedAt'])

    def test_stop_then_queued_request_gets_a_new_clock_only_when_sent(self):
        first = self.start()
        self.app.emit(self.sid, 'status', {'state': 'stopping', 'runId': first['lastRunId']})
        self.assertEqual(first['runStartedAt'], self.app.public(self.item)['runStartedAt'])
        self.app.emit(self.sid, 'status', {'state': 'stopped', 'runId': first['lastRunId']})
        self.assertIsNone(self.app.public(self.item)['runStartedAt'])
        self.assertIsNone(self.item['events'][-1]['data']['runStartedAt'])
        claim = 'a' * 32
        self.item['_dispatchClaim'] = claim
        second = self.start('queued follow-up', _dispatch_claim=claim)
        self.assertNotEqual(first['lastRunId'], second['lastRunId'])
        self.assertGreaterEqual(second['runStartedAt'], first['runStartedAt'])
        queued = [event for event in self.item['events'] if event['type'] == 'queued_user'][-1]['data']
        self.assertEqual(second['lastRunId'], queued['runId'])
        self.assertEqual(second['runStartedAt'], queued['runStartedAt'])
        self.item.pop('_dispatchClaim')

    def test_completed_run_is_not_reused_during_connection_or_next_file_scan(self):
        first = self.start()
        self.app.emit(self.sid, 'result', {})
        self.assertIsNone(self.app.public(self.item)['runStartedAt'])
        for key, value in [('_connecting', True), ('_modelUpdating', True),
                           ('_observationPending', {'phase': 'start'})]:
            with self.subTest(operation=key):
                self.item.update(state='starting', **{key: value})
                self.assertEqual(first['lastRunId'], self.item['lastRunId'])
                self.assertIsNone(self.app.public(self.item)['runStartedAt'])
                self.app.emit(self.sid, 'status', {'state': 'starting'})
                self.assertIsNone(self.item['events'][-1]['data']['runStartedAt'])
                self.item.pop(key)
        self.item['state'] = 'done'
        self.assertNotIn('runStartedAt', history_row(self.item))

    def test_general_chat_admission_uses_the_same_clock_contract(self):
        chat = self.app.create(kind='chat')
        item = self.app.get(chat['id'])
        real_emit = self.app.emit

        def without_demo_terminal(sid, kind, data, **kwargs):
            if kind not in {'assistant', 'result'}:
                return real_emit(sid, kind, data, **kwargs)

        with patch.object(self.app, 'emit', side_effect=without_demo_terminal):
            self.app.send(chat['id'], 'hello', [])
        first = self.app.public(item)
        self.assertGreater(first['runStartedAt'], 0)
        self.assertEqual('starting', first['state'])
        real_emit(chat['id'], 'status', {'state': 'running'})
        self.assertEqual(first['runStartedAt'], self.app.public(item)['runStartedAt'])


if __name__ == '__main__':
    unittest.main()
