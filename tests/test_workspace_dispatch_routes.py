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

    def test_fresh_followups_after_old_stop_run_in_order_without_resume_or_opening_queue(self):
        # Reproduce an old stop with no queued work, followed by an explicit
        # new request. The old hold used to strand every later follow-up.
        self.app.dispatch.manual_stop(self.sid)
        self.bridge.finish = False
        self.app.send(self.sid, 'current request', [])
        self.enqueue('first follow-up')
        self.enqueue('second follow-up')
        self.assertFalse(self.app.dispatch.snapshot(self.sid)['paused'])
        self.app.dispatch.pump()
        self.assertEqual(['current request'], self.bridge.sent)
        self.bridge.finish = True
        self.app.dispatch.start()
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session',
                                         'verification': {'state': 'unverified'}})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.app.dispatch.snapshot(self.sid)['queue']:
            time.sleep(.02)
        self.assertEqual(['current request', 'first follow-up', 'second follow-up'], self.bridge.sent)
        self.assertEqual([], self.app.dispatch.snapshot(self.sid)['queue'])
        self.assertIsNone(self.app.dispatch.error)
        self.app.dispatch.pump()
        self.assertEqual(3, len(self.bridge.sent))

    def test_new_enqueue_during_error_keeps_hold_until_explicit_recovery(self):
        self.app.emit(self.sid, 'error', {'code': 'cli_disconnected', 'message': 'connection ended'})
        value = self.enqueue('do not start after disconnect')
        self.assertTrue(value['paused'])
        self.assertEqual('error', value['reason'])
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)

    def test_new_enqueue_preserves_current_hook_error_even_if_request_is_done(self):
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session',
                                         'verification': {'state': 'needs-review'}})
        value = self.enqueue('wait for hook recovery')
        self.assertEqual('done', self.app.get(self.sid)['state'])
        self.assertTrue(value['paused'])
        self.assertEqual('error', value['reason'])
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)

    def test_late_result_after_stop_cannot_clear_hold_on_new_enqueue(self):
        self.app.dispatch.manual_stop(self.sid)
        self.bridge.stop_state = 'stopped'
        self.app.emit(self.sid, 'status', {'state': 'stopped'})
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        value = self.enqueue('wait for confirmed recovery')
        self.assertTrue(value['paused'])
        self.assertEqual('stopped', value['reason'])
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)

    def test_unconfirmed_control_restore_does_not_clear_old_error_hold(self):
        self.app.dispatch.queue.pause(self.sid, 'error')
        self.app.get(self.sid)['_controlRestore'] = {'status': 'needs_input', 'canSend': False}
        value = self.enqueue('wait for controls')
        self.assertTrue(value['paused'])
        self.assertEqual('error', value['reason'])
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)

    def test_error_stop_and_disconnect_hold_followup_even_after_late_success(self):
        for case in ('task_failed', 'cli_disconnected', 'stopped'):
            with self.subTest(case=case):
                self.sid = self.app.create(str(self.work), True)['id']
                self.bridge = Bridge(self.app, self.sid, finish=False)
                self.app.get(self.sid)['bridge'] = self.bridge
                self.app.send(self.sid, 'original', [])
                value = self.enqueue('keep queued')
                identifier = value['queue'][0]['id']
                if case == 'stopped':
                    self.app.stop(self.sid)
                else:
                    self.app.emit(self.sid, 'error', {'code': case, 'message': 'incomplete'})
                self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
                self.app.dispatch.pump()
                self.assertEqual(['original'], self.bridge.sent)
                state = self.app.dispatch.snapshot(self.sid)
                self.assertTrue(state['paused'])
                self.assertEqual(identifier, state['queue'][0]['id'])
                self.assertEqual('queued', state['queue'][0]['state'])

    def test_new_follow_up_does_not_resume_older_interrupted_queue(self):
        self.bridge.finish = False
        self.app.send(self.sid, 'previous request', [])
        self.enqueue('older pending work')
        self.app.dispatch.manual_stop(self.sid)
        self.app.emit(self.sid, 'status', {'state': 'stopped'})
        self.app.send(self.sid, 'explicit new request', [])
        value = self.enqueue('new follow-up')
        self.assertTrue(value['paused'])
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        self.app.dispatch.pump()
        self.assertEqual(['previous request', 'explicit new request'], self.bridge.sent)
        self.assertEqual(2, len(self.app.dispatch.snapshot(self.sid)['queue']))

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

    def apply_now(self, row, client='apply-once'):
        return self.request({'action': 'apply_now', 'requestId': row['id'],
                             'editRevision': row.get('editRevision', 0), 'clientRequestId': client})

    def test_saved_now_reuses_row_files_and_session_then_preserves_remaining_order(self):
        item = self.app.get(self.sid)
        item.update(state='running', sessionId='fixture-session')
        self.enqueue('first'); self.enqueue('third')
        attachment = self.work / 'saved.csv'; attachment.write_text('value\n7\n')
        code, value = self.request({'action': 'enqueue', 'text': 'selected saved request',
                                   'attachments': [str(attachment)], 'clientRequestId': 'with-file'})
        selected = value['queue'][-1]
        self.assertEqual(200, code)
        self.assertEqual(200, self.apply_now(selected)[0])
        self.assertEqual(selected['id'], self.app.dispatch.snapshot(self.sid)['applyingId'])
        with patch.object(self.bridge, 'interrupt', side_effect=AssertionError('must not interrupt twice')):
            self.assertTrue(self.apply_now(selected)[1]['alreadyApplied'])
        resumed = Bridge(self.app, self.sid)
        with patch('local_app.server.ClaudeSession', return_value=resumed):
            self.app.dispatch.pump()
        self.assertIn('selected saved request', resumed.sent[0])
        self.assertIn('saved.csv', resumed.sent[0])
        self.assertEqual(selected['id'], item['messages'][-1]['requestId'])
        persisted = next(row for row in self.app.dispatch.queue.data['queue'] if row['id'] == selected['id'])
        self.assertEqual([str(attachment.resolve())], persisted['attachments'])
        self.app.dispatch.pump(); self.app.dispatch.pump()
        self.assertEqual(['first', 'third'], resumed.sent[1:])
        self.assertEqual(3, len(item['messages']))
        self.assertTrue(self.apply_now(selected)[1]['alreadyApplied'])
        self.app.dispatch.pump()
        self.assertEqual(3, len(resumed.sent))

    def test_saved_now_idle_prioritizes_without_interrupt_and_no_request_clone(self):
        self.enqueue('first')
        selected = self.enqueue('selected')['queue'][-1]
        self.app.dispatch.queue.pause(self.sid, 'user')
        with patch.object(self.bridge, 'interrupt', side_effect=AssertionError('idle must not interrupt')):
            code, _ = self.apply_now(selected)
        self.assertEqual(200, code)
        self.app.dispatch.pump(); self.app.dispatch.pump()
        self.assertEqual(['selected', 'first'], self.bridge.sent)
        self.assertEqual(2, len(self.app.dispatch.queue.data['queue']))

    def test_saved_now_stale_edit_cancel_and_missing_attachment_do_not_interrupt(self):
        self.app.get(self.sid)['state'] = 'running'
        selected = self.enqueue('original')['queue'][0]
        self.app.dispatch.queue.edit(self.sid, selected['id'], 'changed', [], self.app.dispatch.context(self.app.get(self.sid)))
        with patch.object(self.bridge, 'interrupt', side_effect=AssertionError('must not interrupt')):
            self.assertEqual(400, self.apply_now(selected)[0])
            selected = self.app.dispatch.snapshot(self.sid)['queue'][0]
            missing = str(self.work / 'deleted.csv')
            self.app.dispatch.queue.edit(self.sid, selected['id'], 'changed', [missing])
            selected = self.app.dispatch.snapshot(self.sid)['queue'][0]
            self.assertEqual(400, self.apply_now(selected)[0])
            self.app.dispatch.queue.cancel(self.sid, selected['id'])
            self.assertEqual(400, self.apply_now(selected)[0])
        self.assertEqual([], self.bridge.sent)

    def test_saved_now_when_auto_dispatch_wins_is_an_ack_not_an_interrupt(self):
        self.bridge.finish = False
        selected = self.enqueue('already sending')['queue'][0]
        self.app.dispatch.pump()
        with patch.object(self.bridge, 'interrupt', side_effect=AssertionError('already sending')):
            code, result = self.apply_now(selected)
        self.assertEqual(200, code)
        self.assertTrue(result['alreadyApplied'])
        self.assertEqual(['already sending'], self.bridge.sent)

    def test_saved_now_current_completion_before_click_uses_idle_path(self):
        self.app.get(self.sid)['state'] = 'running'
        selected = self.enqueue('after completion')['queue'][0]
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        with patch.object(self.bridge, 'interrupt', side_effect=AssertionError('already complete')):
            self.assertEqual(200, self.apply_now(selected)[0])
        self.app.dispatch.pump()
        self.assertEqual(['after completion'], self.bridge.sent)

    def test_saved_now_stop_exception_keeps_saved_request_and_never_auto_sends(self):
        self.app.get(self.sid)['state'] = 'running'
        selected = self.enqueue('preserved')['queue'][0]
        with patch.object(self.bridge, 'interrupt', side_effect=OSError('cannot stop')):
            self.assertEqual(400, self.apply_now(selected)[0])
        self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        snapshot = self.app.dispatch.snapshot(self.sid)
        self.assertEqual('stopped', snapshot['reason'])
        self.assertEqual('queued', snapshot['queue'][0]['state'])
        self.assertEqual(selected['id'], snapshot['queue'][0]['id'])
        self.assertTrue(self.apply_now(selected)[1]['alreadyApplied'])

    def test_saved_now_waits_for_stop_ack_and_protects_target_from_mutations(self):
        self.app.get(self.sid)['state'] = 'running'
        selected = self.enqueue('selected')['queue'][0]
        with patch.object(self.bridge, 'interrupt') as interrupt:
            self.assertEqual(200, self.apply_now(selected)[0])
            self.assertEqual(200, self.apply_now(selected)[0])
            interrupt.assert_called_once()
        self.app.dispatch.pump()
        self.assertEqual([], self.bridge.sent)
        for action in ('cancel', 'update', 'reorder', 'resume'):
            self.assertEqual(400, self.request({'action': action, 'requestId': selected['id'],
                'text': 'stale change', 'attachments': [], 'order': [selected['id']]})[0])
        self.app.dispatch.steering[self.sid]['created'] -= 31
        self.app.dispatch.pump()
        self.assertEqual('stopped', self.app.dispatch.snapshot(self.sid)['reason'])
        self.assertEqual(selected['id'], self.app.dispatch.snapshot(self.sid)['queue'][0]['id'])

    def test_saved_now_disconnect_and_manual_stop_cancel_pending_transition_without_losing_row(self):
        for kind in ('disconnect', 'manual'):
            with self.subTest(kind=kind):
                self.sid = self.app.create(str(self.work), True)['id']
                self.bridge = Bridge(self.app, self.sid)
                item = self.app.get(self.sid); item.update(bridge=self.bridge, state='running')
                selected = self.enqueue(kind)['queue'][0]
                with patch.object(self.bridge, 'interrupt'):
                    self.assertEqual(200, self.apply_now(selected)[0])
                if kind == 'disconnect':
                    self.app.emit(self.sid, 'error', {'code': 'cli_disconnected', 'message': 'closed'})
                else:
                    self.app.dispatch.manual_stop(self.sid)
                self.app.dispatch.pump()
                self.app.emit(self.sid, 'result', {'sessionId': 'fixture-session'})
                self.app.dispatch.pump()
                self.assertEqual([], self.bridge.sent)
                self.assertEqual(selected['id'], self.app.dispatch.snapshot(self.sid)['queue'][0]['id'])

    def test_saved_now_does_not_bypass_uncertain_delivery_or_control_confirmation(self):
        selected = self.enqueue('selected')['queue'][0]
        item = self.app.get(self.sid)
        for flag in ('_controlRestore', '_connecting', '_permissionUpdating', '_stopAdmission'):
            item[flag] = True
            self.assertEqual(400, self.apply_now(selected)[0], flag)
            item.pop(flag)
        item['_sessionControls'] = {'model': 'changed'}
        self.assertEqual(400, self.apply_now(selected)[0])
        item.pop('_sessionControls')
        self.app.dispatch.queue.data['queue'].append({**selected, 'id': 'a' * 32, 'status': 'needs_review'})
        self.assertEqual(400, self.apply_now(selected)[0])
        self.assertEqual([], self.bridge.sent)

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
