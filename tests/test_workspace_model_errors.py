"""Model ACL failures are recoverable without replaying a business request."""
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from local_app.bridge import BridgeError, ClaudeSession, model_error_code


DENIAL = 'API Error: 403 authentication_failed: team not allowed to access model: Fixture-Big-Latest token=SECRET'


class ModelErrorTests(unittest.TestCase):
    def session(self):
        events = []
        session = ClaudeSession(['unused'], {}, Path.cwd(), lambda kind, data: events.append((kind, data)))
        session.session_id = str(uuid.uuid4())
        session._initialized = True
        session.model = session.original_model = 'Fixture-Big-Latest'
        session.available_models = [{'value': 'Fixture-Small-Latest'}]
        session.busy = True
        session._turn_submitted = True
        return session, events

    def assistant(self, session, text=DENIAL, **extra):
        session.handle({'type': 'assistant', 'error': 'authentication_failed',
                        'message': {'content': [{'type': 'text', 'text': text}]}, **extra})

    def terminal(self, session, **extra):
        session.handle({'type': 'result', 'is_error': True, 'session_id': session.session_id,
                        'result': 'authentication_failed', **extra})

    def test_explicit_model_diagnostics_only_and_bounded(self):
        for text in (DENIAL, 'You do not have access to model Fixture-Big-Latest',
                     'You do not have permission to use this model',
                     'Model Fixture-Big-Latest: permission denied',
                     'model: Fixture-Big-Latest is not permitted'):
            with self.subTest(text=text):
                self.assertEqual('model_permission', model_error_code(text))
        for text in ('Model Fixture-Unknown not found', 'unknown model Fixture-Unknown',
                     'Unsupported model: foo', 'model_not_found'):
            with self.subTest(text=text):
                self.assertEqual('model_not_found', model_error_code(text))
        for text in ('permission denied for requested file model.py', '403 Forbidden',
                     'authentication_failed', 'unknown request set_model',
                     'no permission to read this file', 'x' * 8192 + DENIAL):
            with self.subTest(text=text[:80]):
                self.assertIsNone(model_error_code(text))

    def test_denied_assistant_waits_for_terminal_and_preserves_live_model_control(self):
        session, events = self.session()
        with patch.object(session, '_write') as write:
            self.assistant(session)
            self.assertTrue(session.busy)
            self.assertFalse(session.closed)
            self.assertEqual([], events)
            self.terminal(session)
            self.assertFalse(session.busy)
            self.assertFalse(session.closed)
            self.assertTrue(session.capabilities['setModel'])
            self.assertEqual(session.session_id, session.resume_id)
            self.assertEqual(['model_permission'], [d['code'] for k, d in events if k == 'error'])
            self.assertNotIn('SECRET', json.dumps(events))
            write.assert_not_called()

    def test_terminal_diagnostic_takes_priority_over_coarse_auth_category(self):
        session, events = self.session()
        self.terminal(session, result='authentication_failed', errors=[DENIAL])
        self.assertFalse(session.closed)
        self.assertEqual('model_permission', events[-1][1]['code'])

    def test_native_success_subtype_with_error_flag_is_still_a_model_failure(self):
        # Claude 2.1.306 calls this result subtype "success" even when the
        # corporate gateway returns a model ACL error. is_error is decisive.
        session, events = self.session()
        self.assistant(session, 'Failed to authenticate. API Error: 403 {"error":{"type":"authentication_error",'
                       '"message":"team not allowed to access model: Fixture-Big-Latest"}}')
        self.terminal(session, subtype='success', result='Failed to authenticate. API Error: 403 '
                       '{"error":{"type":"authentication_error",'
                       '"message":"team not allowed to access model: Fixture-Big-Latest"}}')
        self.assertFalse(session.closed)
        self.assertFalse(session.busy)
        self.assertEqual(['model_permission'], [row['code'] for kind, row in events if kind == 'error'])
        self.assertFalse(any(kind == 'result' for kind, _ in events))

    def test_model_not_found_is_not_a_login_error(self):
        session, events = self.session()
        self.assistant(session, 'API Error: 404 model Fixture-Unknown not found')
        self.terminal(session)
        self.assertEqual('model_not_found', events[-1][1]['code'])
        self.assertFalse(session.closed)

    def test_native_fallback_continues_same_turn_without_overriding_preference(self):
        session, events = self.session()
        session.model_override = 'opus'
        with patch.object(session, '_write') as write:
            self.assistant(session)
            session.handle({'type': 'system', 'subtype': 'model_fallback',
                'session_id': session.session_id, 'original_model': 'Fixture-Big-Latest',
                'fallback_model': 'Fixture-Small-Latest', 'trigger': 'permission_denied',
                'content': 'RAW SECRET MUST NOT LEAK'})
            self.assertTrue(session.busy)
            self.assertEqual('Fixture-Small-Latest', session.turn_model)
            self.assertIsNone(session._turn_model_error)
            self.assertEqual('Fixture-Big-Latest', session.model)
            self.assertEqual('opus', session.model_override)
            self.terminal(session, is_error=False, result='response from fallback')
            self.assertFalse(any(k == 'error' for k, _ in events))
            self.assertTrue(any(k == 'result' for k, _ in events))
            self.assertNotIn('SECRET', json.dumps(events))
            write.assert_not_called()
            with patch('local_app.bridge.threading.Thread'):
                session.send('next user question')
            self.assertIsNone(session.turn_model)
            self.assertIsNone(session._turn_model_error)

    def test_failed_native_fallback_uses_latest_terminal_error(self):
        session, events = self.session()
        self.assistant(session)
        session.handle({'type': 'system', 'subtype': 'model_fallback',
            'session_id': session.session_id, 'original_model': 'Fixture-Big-Latest',
            'fallback_model': 'Fixture-Small-Latest', 'trigger': 'permission_denied'})
        self.terminal(session, result='Network request failed')
        self.assertEqual('task_failed', events[-1][1]['code'])

    def test_fallback_rejects_foreign_inactive_replayed_or_unbounded_metadata(self):
        for extra in ({'session_id': 'other'}, {'parent_tool_use_id': 'worker'},
                      {'isReplay': True}, {'fallback_model': 'x' * 201},
                      {'fallback_model': 'SECRET\nTOKEN'}, {'trigger': 'unknown'}):
            with self.subTest(extra=extra):
                session, events = self.session()
                session.handle({'type': 'system', 'subtype': 'model_fallback',
                    'session_id': session.session_id, 'original_model': 'Fixture-Big-Latest',
                    'fallback_model': 'Fixture-Small-Latest', 'trigger': 'permission_denied', **extra})
                self.assertEqual([], events)
                self.assertIsNone(session.turn_model)
        session, events = self.session()
        session.busy = False
        session.handle({'type': 'system', 'subtype': 'model_fallback',
            'original_model': 'Fixture-Big-Latest', 'fallback_model': 'Fixture-Small-Latest',
            'trigger': 'permission_denied'})
        self.assertEqual([], events)

    def test_normal_text_parent_and_replay_cannot_change_root_error_state(self):
        session, events = self.session()
        for extra in ({'parent_tool_use_id': 'worker'}, {'isReplay': True}):
            self.assistant(session, **extra)
            self.assertFalse(session.closed)
            self.assertIsNone(session._turn_model_error)
        session.handle({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': DENIAL}]}})
        self.assertFalse(session.closed)
        self.assertIsNone(session._turn_model_error)
        self.assertTrue(any(k == 'assistant' for k, _ in events))

    def test_true_authentication_still_retires_connection(self):
        session, events = self.session()
        self.assistant(session, 'Failed to authenticate: OAuth session expired')
        self.assertTrue(session.closed)
        self.assertFalse(session.busy)
        self.assertEqual('cli_authentication', events[-1][1]['code'])

    def test_new_credential_failure_supersedes_earlier_model_denial(self):
        session, events = self.session()
        self.assistant(session)
        self.terminal(session, result='Failed to authenticate: OAuth session expired')
        self.assertTrue(session.closed)
        self.assertEqual('cli_authentication', events[-1][1]['code'])

    def test_inactive_foreign_and_unsent_model_error_cannot_poison_current_turn(self):
        for state in ('inactive', 'unsent', 'foreign'):
            with self.subTest(state=state):
                session, events = self.session()
                if state == 'inactive':
                    session.busy = False
                if state == 'unsent':
                    session._turn_submitted = False
                self.assistant(session, **({'session_id': 'foreign'} if state == 'foreign' else {}))
                self.assertIsNone(session._turn_model_error)
                self.assertFalse(session.closed)
                self.assertEqual([], events)

    def test_failed_model_readback_retires_unconfirmed_selection_with_correct_diagnostic(self):
        session, events = self.session()
        session._runtime_settings_error = 'model_permission'
        with self.assertRaises(BridgeError) as caught:
            session._raise_runtime_settings_error()
        self.assertEqual('model_permission', caught.exception.code)
        self.assertTrue(session.closed)

    def test_control_denial_does_not_disable_model_control_or_write_settings(self):
        session, events = self.session()
        session.busy = False
        session.process = Mock(poll=Mock(return_value=None))
        frames = []
        def respond(frame):
            frames.append(frame)
            request = frame['request']
            rejected = request['model'] == 'Fixture-Big-Latest'
            session.handle({'type': 'control_response', 'response': {
                'request_id': frame['request_id'], 'subtype': 'error' if rejected else 'success',
                **({'error': DENIAL} if rejected else {'response': {}})}})
        with patch.object(session, '_write', side_effect=respond):
            with self.assertRaises(BridgeError) as caught:
                session.set_model('Fixture-Big-Latest')
            self.assertEqual('model_permission', caught.exception.code)
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertFalse(session.closed)
            self.assertTrue(session.capabilities['setModel'])
            self.assertEqual('Fixture-Big-Latest', session.model)
            session.set_model('Fixture-Small-Latest')
        self.assertEqual('Fixture-Small-Latest', session.model)
        self.assertEqual(['set_model', 'set_model'], [f['request']['subtype'] for f in frames])

    def test_error_terminal_clears_pending_tools_and_records_stop_failure(self):
        session, events = self.session()
        session.pending = {'permission': {}}
        session._permission_choices = {'permission': {}}
        session.tasks = {'worker'}
        with patch.object(session, '_record_stop_terminal') as terminal:
            self.terminal(session, result=DENIAL)
            terminal.assert_called_once_with(None, error=True)
        self.assertEqual({}, session.pending)
        self.assertEqual({}, session._permission_choices)
        self.assertEqual(set(), session.tasks)
        self.assertTrue(any(k == 'request_closed' for k, _ in events))

    def test_matching_user_stop_supersedes_only_remembered_model_failure(self):
        for diagnostic in ('[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use',
                           '[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=null',
                           'prompt_check: the turn was stopped before its prompt was checked'):
            with self.subTest(diagnostic=diagnostic):
                session, events = self.session()
                self.assistant(session)
                session.stopping = True
                attempt = {'phase': 'waiting', 'session': session.session_id, 'ack': True,
                           'changed': threading.Event()}
                session._stop_attempt = attempt
                with patch.object(session, '_record_stop_terminal') as stopped:
                    self.terminal(session, result=diagnostic)
                    stopped.assert_called_once_with(attempt)
                self.assertFalse(session.busy)
                self.assertIsNone(session._turn_model_error)
                self.assertFalse(any(kind in {'error', 'result'} for kind, _ in events))

    def test_current_model_failure_during_stop_is_not_hidden_as_a_benign_interrupt(self):
        session, events = self.session()
        self.assistant(session)
        session.stopping = True
        attempt = {'phase': 'waiting', 'session': session.session_id, 'ack': True,
                   'changed': threading.Event()}
        session._stop_attempt = attempt
        with patch.object(session, '_record_stop_terminal') as stopped:
            self.terminal(session, result='[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use',
                          errors=[DENIAL])
            stopped.assert_called_once_with(attempt, error=True)
        self.assertEqual(['model_permission'], [row['code'] for kind, row in events if kind == 'error'])


if __name__ == '__main__':
    unittest.main()
