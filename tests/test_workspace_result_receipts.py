"""Completed-result visibility is independent of capped desktop notifications."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.history import HistoryStore, normalize_result_receipt, row as history_row
from local_app.server import LocalApp, Server


class ResultReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = LocalApp(self.root / 'state', demo=True)
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.root), True)['id']
        self.item = self.app.get(self.sid)
        demo = patch('local_app.demo.run', lambda *_: None)
        demo.start()
        self.addCleanup(demo.stop)

    def start(self, sid=None):
        sid = sid or self.sid
        self.app.send(sid, 'request', [])
        return self.app.get(sid)['lastRunId']

    def finish(self, sid=None, **data):
        sid = sid or self.sid
        self.app.emit(sid, 'assistant', {'text': 'Completed reply'})
        self.app.emit(sid, 'result', data)
        return deepcopy(self.app.get(sid).get('resultReceipt'))

    def test_success_has_one_receipt_in_session_event_bootstrap_and_attention(self):
        run_id = self.start()
        expected = {'revision': 1, 'seenRevision': 0, 'runId': run_id}
        self.assertEqual(expected, self.finish())
        self.assertEqual(expected, self.app.public(self.item)['resultReceipt'])
        self.assertEqual(expected, self.item['events'][-1]['data']['resultReceipt'])
        for snapshot in (self.app.bootstrap(), self.app.attention()):
            self.assertEqual(expected, next(row for row in snapshot['sessions'] if row['id'] == self.sid)['resultReceipt'])
        self.app.emit(self.sid, 'result', {})
        self.assertEqual(expected, self.item['resultReceipt'])

    def test_old_history_and_connection_only_events_do_not_invent_unread_results(self):
        self.item['lastRunId'] = 'legacy'
        self.app.emit(self.sid, 'result', {})
        self.assertIsNone(self.app.public(self.item)['resultReceipt'])
        self.assertNotIn('resultReceipt', history_row(self.item))
        for flag in ('_connecting', '_modelUpdating'):
            run_id = self.start()
            self.item[flag] = True
            self.app.emit(self.sid, 'result', {})
            self.assertIsNone(self.item.get('resultReceipt'))
            self.item.pop(flag)
            self.app.emit(self.sid, 'status', {'state': 'stopped', 'runId': run_id})

    def test_stopped_and_error_requests_and_late_result_do_not_mark_completion(self):
        for kind, data in [('error', {'message': 'controlled error'}), ('status', {'state': 'stopped'})]:
            with self.subTest(kind=kind):
                run_id = self.start()
                self.app.emit(self.sid, kind, dict(data, runId=run_id))
                self.assertIsNone(self.item.get('resultReceipt'))
                self.app.emit(self.sid, 'result', {})
                self.assertIsNone(self.item.get('resultReceipt'))

    def test_racing_stop_wins_over_result_then_late_old_origin_cannot_mark_new_request(self):
        old_run = self.start()
        self.app.emit(self.sid, 'status', {'state': 'stopping', 'runId': old_run})
        self.app.emit(self.sid, 'result', {})
        self.assertIsNone(self.item.get('resultReceipt'))
        self.app.emit(self.sid, 'status', {'state': 'stopped', 'runId': old_run})
        new_run = self.start()
        self.app.emit(self.sid, 'result', {}, _origin=(self.item, old_run))
        self.assertIsNone(self.item.get('resultReceipt'))
        self.assertEqual(new_run, self.finish()['runId'])

    def test_pending_question_or_needs_review_does_not_claim_success(self):
        self.start()
        self.item['choice'] = {'id': 'choose', 'question': 'Choose', 'options': [{'id': 'a', 'label': 'A'}]}
        self.finish()
        self.assertIsNone(self.item.get('resultReceipt'))
        self.item.pop('choice')
        self.start()
        self.finish(verification={'state': 'needs-review', 'message': 'Check output'})
        self.assertIsNone(self.item.get('resultReceipt'))

    def test_next_request_retains_prior_unread_and_late_ack_does_not_clear_new_result(self):
        first_run = self.start()
        first = self.finish()
        second_run = self.start()
        self.assertEqual(first, self.item['resultReceipt'])
        second = self.finish()
        self.assertEqual({'revision': 2, 'seenRevision': 0, 'runId': second_run}, second)
        response = self.app.read_result(self.sid, first['revision'])
        self.assertEqual({'revision': 2, 'seenRevision': 1, 'runId': second_run}, response['resultReceipt'])
        self.assertNotEqual(first_run, second_run)
        updated = self.item['updated']
        self.app.read_result(self.sid, 2)
        self.assertEqual(updated, self.item['updated'], 'read acknowledgements must not reorder tasks')
        with patch.object(self.app, 'save') as save:
            self.app.read_result(self.sid, 1)
            self.app.read_result(self.sid, 2)
        save.assert_not_called()
        self.assertEqual(2, self.item['resultReceipt']['seenRevision'])

    def test_saved_receipt_survives_restart_lazy_hydration_and_eviction(self):
        self.start()
        self.finish()
        self.app.read_result(self.sid, 1)
        self.start()
        expected = self.finish()
        restarted = LocalApp(self.app.state, demo=True)
        self.addCleanup(restarted.close)
        unloaded = restarted.sessions[self.sid]
        self.assertTrue(unloaded['_historyUnloaded'])
        with patch('local_app.history.read', side_effect=AssertionError('summary loaded a body')):
            self.assertEqual(expected, next(row for row in restarted.attention()['sessions'] if row['id'] == self.sid)['resultReceipt'])
            self.assertEqual(expected, next(row for row in restarted.bootstrap()['sessions'] if row['id'] == self.sid)['resultReceipt'])
        loaded = restarted.get(self.sid)
        self.assertEqual(expected, loaded['resultReceipt'])
        restarted.history.compact(loaded)
        restarted.history.hydrate(loaded)
        self.assertEqual(expected, loaded['resultReceipt'])
        self.assertEqual(expected, next(row for row in HistoryStore(self.app.state).load() if row['id'] == self.sid)['resultReceipt'])

    def test_parallel_tasks_and_disabled_or_evicted_notifications_keep_receipts(self):
        self.app.desktop.configure({'enabled': False})
        other = self.app.create(str(self.root), True, title='other')['id']
        self.start()
        self.start(other)
        first = self.finish()
        second = self.finish(other)
        for i in range(55):
            self.app.desktop.publish(other, 'other', 'completed', 'different-' + str(i))
        self.app.read_result(self.sid, 1)
        self.assertEqual(first['revision'], self.item['resultReceipt']['seenRevision'])
        self.assertEqual(second, self.app.get(other)['resultReceipt'])
        self.assertLessEqual(len(self.app.desktop.snapshot()['inbox']), 50)

    def test_failed_save_keeps_unread_and_reports_failure(self):
        self.start()
        expected = self.finish()
        with patch.object(self.app.history, 'save', side_effect=OSError('simulated storage failure')):
            with self.assertRaises(OSError):
                self.app.read_result(self.sid, 1)
        self.assertEqual(expected, self.item['resultReceipt'])
        self.app.history.warning = 'History unavailable'
        with self.assertRaises(ValueError):
            self.app.read_result(self.sid, 1)
        self.assertEqual(expected, self.item['resultReceipt'])
        self.app.history.warning = None

    def test_ack_and_history_reject_invalid_cursors_without_mutation(self):
        self.start()
        expected = self.finish()
        for revision in (None, True, 0, -1, 1.0, '1', [], {}, 2, 9007199254740992):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.app.read_result(self.sid, revision)
            self.assertEqual(expected, self.item['resultReceipt'])
        for changed in ({'revision': True}, {'revision': 0}, {'revision': 9007199254740992},
                        {'seenRevision': True}, {'seenRevision': -1}, {'seenRevision': 2},
                        {'runId': ''}, {'runId': ['bad']}, {'runId': '../bad'}):
            self.assertIsNone(normalize_result_receipt({**expected, **changed}))
        self.assertEqual(expected, normalize_result_receipt({**expected, 'private': 'discard'}))

    def test_http_ack_requires_auth_and_exact_shape_and_does_not_invalidate_upgrade(self):
        self.start()
        self.finish()
        server = Server(self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(data, auth=True):
            headers = {'Content-Type': 'application/json', 'Origin': server.origin}
            if auth:
                headers['Authorization'] = 'Bearer ' + self.app.token
            try:
                response = urlopen(Request(server.origin + '/api/session/result-read', headers=headers,
                    data=json.dumps(data).encode('utf-8')), timeout=5)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)
        try:
            payload = {'id': self.sid, 'revision': 1}
            self.assertEqual(403, request(payload, auth=False)[0])
            self.assertEqual(0, self.item['resultReceipt']['seenRevision'])
            for invalid in ({'id': self.sid}, {**payload, 'untrusted': True}, {**payload, 'revision': 2}):
                self.assertEqual(400, request(invalid)[0])
            with patch.object(self.app.upgrade, 'invalidate') as invalidate:
                code, data = request(payload)
            self.assertEqual(200, code, data)
            self.assertEqual(1, data['resultReceipt']['seenRevision'])
            invalidate.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == '__main__':
    unittest.main()
