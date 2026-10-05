import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.server import LocalApp, Server
from local_app.bridge import ControlRestoreRequired


class Bridge:
    closed = False
    cleanup_complete = False
    def __init__(self, app, sid, *, finish=True):
        self.app, self.sid, self.finish = app, sid, finish
        self.sent = []
    def send(self, prompt):
        self.sent.append(prompt)
        if self.finish:
            self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        else:
            self.app.emit(self.sid, 'status', {'state': 'running'})
    def interrupt(self):
        self.closed = self.cleanup_complete = True
        self.app.emit(self.sid, 'status', {'state': 'stopped'})
    def close(self):
        self.closed = self.cleanup_complete = True
        return True


class DispatchRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'plain-claude-work'; self.work.mkdir()
        self.app = LocalApp(self.root / 'state', command=['not-started'], managed_workspace_root=self.root / 'managed')
        self.addCleanup(self.app.close)
        self.sid = self.app.create(str(self.work), True)['id']
        self.bridge = Bridge(self.app, self.sid)
        self.app.get(self.sid)['bridge'] = self.bridge
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, data=None, *, authorized=True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.app.token
        request = Request(self.server.origin + '/api/dispatch?id=' + self.sid,
                          data=json.dumps({'id': self.sid, **data}).encode() if data is not None else None,
                          headers=headers)
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def enqueue(self, text='next', action='enqueue'):
        code, value = self.request({'action': action, 'text': text, 'attachments': [], 'clientRequestId': text})
        self.assertEqual(200, code, value)
        return value

    def test_schedule_overview_requires_auth_and_cannot_start_work(self):
        for authorized in (False, True):
            headers = {'Authorization': 'Bearer ' + self.app.token} if authorized else {}
            request = Request(self.server.origin + '/api/schedules', headers=headers)
            try:
                response = urlopen(request, timeout=4)
            except HTTPError as exc:
                response = exc
            with response:
                value = json.load(response)
                self.assertEqual(200 if authorized else 403, response.status)
                if authorized:
                    self.assertEqual([], value['schedules'])
                    self.assertEqual(0, value['counts']['total'])
        self.assertEqual([], self.bridge.sent)

    def test_plain_cli_queue_uses_existing_send_history_and_one_delivery(self):
        with patch('local_app.harness_client.HarnessClient._installation', side_effect=AssertionError('No harness needed')):
            value = self.enqueue('follow-up')
            identifier = value['queue'][0]['id']
            self.enqueue('follow-up')
            self.assertEqual(1, len(self.app.dispatch.snapshot(self.sid)['queue']))
            self.app.dispatch.pump()
            self.app.dispatch.pump()
        self.assertEqual(['follow-up'], self.bridge.sent)
        item = self.app.get(self.sid)
        self.assertEqual(identifier, item['messages'][-1]['requestId'])
        self.assertEqual(1, len([event for event in item['events'] if event['type'] == 'queued_user']))
        self.assertEqual([], self.app.dispatch.snapshot(self.sid)['queue'])

    def test_approval_then_success_continues_without_extra_approval(self):
        self.app.emit(self.sid, 'request', {'id': 'permission', 'tool': 'Bash', 'input': {}})
        self.enqueue()
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.app.emit(self.sid, 'request_closed', {'id': 'permission'})
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        self.app.dispatch.pump()
        self.assertEqual(['next'], self.bridge.sent)

    def test_stop_between_claim_and_send_prevents_delivery(self):
        self.enqueue()
        original = self.app.send
        def stop_first(*args, **kwargs):
            self.app.stop(self.sid)
            return original(*args, **kwargs)
        with patch.object(self.app, 'send', side_effect=stop_first):
            self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual([], self.app.get(self.sid)['messages'])
        value = self.app.dispatch.snapshot(self.sid)
        self.assertTrue(value['paused'])
        self.assertEqual('needs_review', value['queue'][0]['state'])

    def test_late_result_during_stop_cannot_ack_an_unsent_normal_prompt(self):
        item = self.app.get(self.sid)
        item['state'] = 'done'
        self.bridge.stopping = True
        self.app.dispatch.steering[self.sid] = {'created': time.monotonic(), 'interrupted': set()}
        with self.assertRaisesRegex(ValueError, '중지'):
            self.app.send(self.sid, 'must not disappear', [])
        self.assertEqual([], self.bridge.sent)
        self.assertEqual([], item['messages'])
        self.app.dispatch.steering.clear()
        with self.assertRaisesRegex(ValueError, '중지'):
            self.app.send(self.sid, 'still closing', [])

    def test_now_waits_for_stop_then_reuses_same_conversation_and_does_not_replay_old_work(self):
        item = self.app.get(self.sid)
        item.update(state='running', sessionId='fixture-session')
        self.enqueue('later')
        self.enqueue('new direction', 'steer')
        self.assertTrue(self.bridge.closed)
        resumed = Bridge(self.app, self.sid)
        seen = []
        def factory(*args, **kwargs):
            seen.append(args[4]); return resumed
        with patch('local_app.server.ClaudeSession', side_effect=factory):
            self.app.dispatch.pump()
        self.assertEqual(['fixture-session'], seen)
        self.assertEqual(['new direction'], resumed.sent)
        self.app.dispatch.pump()
        self.assertEqual(['new direction', 'later'], resumed.sent)

    def test_scheduled_once_dispatches_only_once_without_harness(self):
        now = time.time()
        code, value = self.request({'action': 'schedule', 'text': 'scheduled', 'attachments': [],
                                   'clientRequestId': 'schedule-one', 'schedule': {'kind': 'once', 'runAt': now + 60}})
        self.assertEqual(200, code, value)
        self.app.dispatch.queue.clock = lambda: now + 61
        self.app.dispatch.pump(); self.app.dispatch.pump()
        self.assertEqual(['scheduled'], self.bridge.sent)
        schedule = self.app.dispatch.snapshot(self.sid)['schedules'][0]
        self.assertFalse(schedule['enabled'])
        self.assertEqual('done', schedule['lastRun']['status'])

    def test_cross_session_cancel_and_unauthenticated_dispatch_are_rejected(self):
        item = self.enqueue()['queue'][0]
        self.assertEqual(403, self.request(authorized=False)[0])
        self.assertEqual(403, self.request({'action': 'enqueue', 'text': 'bad'}, authorized=False)[0])
        other = self.app.create(str(self.work), True)['id']
        self.assertEqual(400, self.request({'id': other, 'action': 'cancel', 'requestId': item['id']})[0])
        self.assertEqual(1, len(self.app.dispatch.snapshot(self.sid)['queue']))

    def test_untrusted_restart_and_changed_settings_do_not_execute(self):
        self.enqueue()
        item = self.app.get(self.sid)
        item['trusted'] = False
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual(400, self.request({'action': 'resume'})[0])
        item['trusted'] = True
        item['_sessionControls'] = {'model': 'new-model'}
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual('settings_changed', self.app.dispatch.snapshot(self.sid)['reason'])

    def test_control_restore_preflight_preserves_queue_and_requires_explicit_recovery(self):
        self.enqueue('preserved follow-up')
        item = self.app.get(self.sid)
        item['_controlRestore'] = {'status': 'needs_input', 'canSend': False,
                                   'issues': [{'control': 'effort', 'code': 'effort_invalid'}]}
        with patch.object(self.app, 'send', side_effect=ControlRestoreRequired('설정을 확인해 주세요.')) as send:
            self.app.dispatch.pump()
            self.app.dispatch.pump()
            self.assertEqual(1, send.call_count)
        snapshot = self.app.dispatch.snapshot(self.sid)
        self.assertEqual('control_restore_required', snapshot['reason'])
        self.assertEqual('queued', snapshot['queue'][0]['state'])
        self.assertEqual('preserved follow-up', snapshot['queue'][0]['text'])
        self.assertEqual([], item['messages'])
        self.assertEqual([], self.bridge.sent)
        self.assertNotIn('_dispatchClaim', item)
        self.assertEqual(400, self.request({'action': 'resume'})[0])
        item.pop('_controlRestore')
        item['_sessionControls'] = {'effort': 'medium'}
        item['state'] = 'stopped'  # The replacement CLI is ready, without a prompt.
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        self.assertEqual(200, self.request({'action': 'resume'})[0])
        self.app.dispatch.pump()
        self.assertEqual(['preserved follow-up'], self.bridge.sent)
        self.assertEqual(1, len(item['messages']))

    def test_untyped_delivery_error_still_requires_manual_review(self):
        self.enqueue('uncertain follow-up')
        with patch.object(self.app, 'send', side_effect=ValueError('control_restore_required')):
            self.app.dispatch.pump()
        snapshot = self.app.dispatch.snapshot(self.sid)
        self.assertEqual('delivery_unknown', snapshot['reason'])
        self.assertEqual('needs_review', snapshot['queue'][0]['state'])


if __name__ == '__main__':
    unittest.main()
