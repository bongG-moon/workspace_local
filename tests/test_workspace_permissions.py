"""Permission controls through a deterministic child, never Claude."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, ClaudeSession, cli_arguments, probe_cli
from local_app.permission_contract import help_permission_modes, session_choices, request_context

ROOT = Path(__file__).resolve().parents[1]
FAKE = [sys.executable, '-B', str(ROOT / 'tests/fixtures/workspace_permission_cli.py')]


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Permission fixture did not finish')


def suggestion(**changes):
    return {'type': 'addRules', 'destination': 'session', 'behavior': 'allow',
            'rules': [{'toolName': 'Bash', 'ruleContent': 'registered-command --help'},
                      {'toolName': 'Bash', 'ruleContent': 'registered-command metadata'}], **changes}


class PermissionProjectionTests(unittest.TestCase):
    def test_help_choices_are_exact_version_names_and_never_bypass_modes(self):
        for manual in ('manual', 'default'):
            text = f'--permission-mode <mode> Usage (choices: "{manual}", "plan",\n "auto", "acceptEdits", "bypassPermissions", "dontAsk")'
            self.assertEqual({manual, 'plan', 'auto', 'acceptEdits'}, set(help_permission_modes(text)))
        for text in ('', '--permission-mode <mode> No choices', '--other (choices: "auto")',
                     '--permission-mode <mode>\n  --other (choices: "auto")'):
            self.assertEqual([], help_permission_modes(text))

    def test_only_exact_session_allow_rules_are_offered_and_copied(self):
        raw = suggestion()
        request = {'tool_name': 'Bash', 'permission_suggestions': [raw, raw]}
        choices, private = session_choices(request)
        self.assertEqual(1, len(choices))
        self.assertEqual('session', choices[0]['destination'])
        self.assertEqual(2, len(choices[0]['rules']))
        original = copy.deepcopy(private)
        raw['rules'][0]['ruleContent'] = 'mutated source'
        choices[0]['rules'][0]['ruleContent'] = 'mutated display'
        self.assertEqual(original, private)

    def test_documented_destinations_keep_distinct_exact_updates_and_scope_text(self):
        destinations = ('session', 'localSettings', 'projectSettings', 'userSettings')
        raw = [suggestion(destination=scope) for scope in destinations]
        choices, private = session_choices({'tool_name': 'Bash', 'permission_suggestions': raw})
        self.assertEqual(list(destinations), [choice['destination'] for choice in choices])
        self.assertEqual(4, len(set(choice['id'] for choice in choices)))
        self.assertEqual(raw, [private[choice['id']] for choice in choices])
        for choice in choices:
            self.assertTrue(choice['description'])
            self.assertEqual(2, len(choice['rules']))
            if choice['destination'] != 'session':
                self.assertIn('항상 허용', choice['label'])
                self.assertIn('저장', choice['description'])
        self.assertIn('다른 사용자', choices[2]['description'])
        self.assertIn('다른 프로젝트', choices[3]['description'])
        saved = copy.deepcopy(private)
        for row, choice in zip(raw, choices):
            row['rules'][0]['ruleContent'] = 'source edit'
            choice['rules'][0]['ruleContent'] = 'display edit'
        self.assertEqual(saved, private)

    def test_whole_tool_rule_preserves_sdk_null_or_omitted_content(self):
        raw = [suggestion(destination='localSettings', rules=[{'toolName': 'Bash'}]),
               suggestion(destination='userSettings', rules=[{'toolName': 'Bash', 'ruleContent': None}])]
        choices, private = session_choices({'tool_name': 'Bash', 'permission_suggestions': raw})
        self.assertEqual(raw, [private[choice['id']] for choice in choices])

    def test_deny_unsupported_malformed_and_other_tool_rules_are_not_choices(self):
        invalid = [suggestion(destination=scope) for scope in ('managedSettings', 'unknown', '', None, [], {})]
        invalid += [suggestion(type='setMode', mode='bypassPermissions'), suggestion(type='removeRules'),
                    suggestion(behavior='deny'), suggestion(behavior='ask'), suggestion(rules=[]),
                    suggestion(rules=[{'toolName': 'Write'}]), suggestion(rules=[{'toolName': '*'}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': 'x\nunsafe'}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': '\ud800'}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': ''}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': 'x' * 2001}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': []}]),
                    suggestion(rules=[{'toolName': 'Bash', 'ruleContent': 'x', 'unknown': True}])]
        for value in invalid:
            with self.subTest(value=str(value)[:100]):
                self.assertEqual(([], {}), session_choices({'tool_name': 'Bash', 'permission_suggestions': [value]}))
        self.assertEqual(([], {}), session_choices({'tool_name': 'AskUserQuestion', 'permission_suggestions': [suggestion()]}))
        self.assertEqual(([], {}), session_choices(None))

    def test_persistent_choices_never_relax_update_type_behavior_or_tool(self):
        for destination in ('localSettings', 'projectSettings', 'userSettings'):
            for change in ({'type': 'removeRules'}, {'type': 'replaceRules'},
                           {'behavior': 'deny'}, {'behavior': 'ask'},
                           {'rules': [{'toolName': 'Write'}]},
                           {'rules': [{'toolName': 'Bash'}, {'toolName': 'Read'}]},
                           {'mode': 'bypassPermissions'}):
                with self.subTest(destination=destination, change=change):
                    self.assertEqual(([], {}), session_choices({'tool_name': 'Bash',
                        'permission_suggestions': [suggestion(destination=destination, **change)]}))


    def test_cli_suppression_removes_all_rule_choices_including_session(self):
        for destination in ('session', 'localSettings', 'projectSettings', 'userSettings'):
            with self.subTest(destination=destination):
                request = {'tool_name': 'Bash', 'suppress_always_allow_rule': True,
                           'permission_suggestions': [suggestion(destination=destination)]}
                self.assertEqual(([], {}), session_choices(request))
                request['suppress_always_allow_rule'] = False
                self.assertEqual(1, len(session_choices(request)[0]))

    def test_reason_projection_strips_terminal_and_invisible_controls_and_unknown_data(self):
        raw = {'decision_reason': '\x1b[31m경로 확인\x1b[0m\r\n기존 규칙\x00\u202e\ud800'
                                  '\x1b]0;hidden title\x07\x9b33m 필요\x9b0m',
               'decision_reason_type': 'subcommandResults', 'suppress_always_allow_rule': True,
               'matched_ask_rule': {'source': 'userSettings', 'tool_name': 'Bash',
                                    'rule_content': '\x1b[1mpython *\x1b[0m', 'other': 'must not leak'},
               'settings': {'unrelated': 'must not leak'}}
        expected = {'decisionReason': '경로 확인\n기존 규칙 필요',
                    'decisionReasonType': 'subcommandResults', 'suppressAlwaysAllowRule': True,
                    'matchedAskRule': {'source': 'userSettings', 'toolName': 'Bash', 'ruleContent': 'python *'}}
        self.assertEqual(expected, request_context(raw))
        self.assertIn('\x1b', raw['decision_reason'])
        self.assertIn('other', raw['matched_ask_rule'])

    def test_reason_and_rule_text_are_bounded_without_projecting_malformed_fields(self):
        context = request_context({'decision_reason': '가' * 20000,
            'matched_ask_rule': {'source': 'projectSettings', 'tool_name': 'Bash', 'rule_content': 'x' * 4000}})
        self.assertEqual(2000, len(context['decisionReason']))
        self.assertTrue(context['decisionReason'].endswith('…'))
        self.assertEqual(2000, len(context['matchedAskRule']['ruleContent']))
        for request in (None, {}, {'decision_reason': {'text': 'not a string'}},
                        {'decision_reason': '\x1b]incomplete terminal title'},
                        {'decision_reason_type': 'x' * 65},
                        {'matched_ask_rule': {'source': 'x' * 121, 'tool_name': 'Bash'}},
                        {'matched_ask_rule': {'source': 'userSettings', 'tool_name': 'Bash', 'rule_content': None}},
                        {'matched_ask_rule': {'source': 'userSettings', 'tool_name': 'Bash\nWrite'}}):
            with self.subTest(request=request):
                self.assertEqual({}, request_context(request))


class PermissionRequestContextTests(unittest.TestCase):
    def test_cli_reason_reaches_ui_once_and_suppressed_choices_cannot_be_submitted(self):
        events, writes = [], []
        bridge = ClaudeSession([], {}, Path.cwd(), lambda kind, data: events.append((kind, data)))
        raw = {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'fixture only'},
               'decision_reason': '\x1b[31m직접 확인이 필요한 요청\x1b[0m', 'decision_reason_type': 'safetyCheck',
               'matched_ask_rule': {'source': 'userSettings', 'tool_name': 'Bash', 'rule_content': 'fixture *'},
               'suppress_always_allow_rule': True, 'permission_suggestions': [suggestion()]}
        frame = {'type': 'control_request', 'request_id': 'permission-context', 'request': raw}
        bridge.handle(frame)
        bridge.handle(frame)
        requests = [data for kind, data in events if kind == 'request']
        self.assertEqual(1, len(requests))
        self.assertEqual('직접 확인이 필요한 요청', requests[0]['decisionReason'])
        self.assertEqual('safetyCheck', requests[0]['decisionReasonType'])
        self.assertEqual('fixture *', requests[0]['matchedAskRule']['ruleContent'])
        self.assertTrue(requests[0]['suppressAlwaysAllowRule'])
        self.assertEqual([], requests[0]['permissionChoices'])
        self.assertEqual({}, bridge._permission_choices['permission-context'])
        with patch.object(bridge, '_write', side_effect=writes.append):
            with self.assertRaises(ValueError):
                bridge.respond('permission-context', True, permission_choice_id='unoffered')
            # Defense in depth: even an old cached choice cannot override the
            # current request's explicit prohibition on broader rules.
            bridge._permission_choices['permission-context']['stale-choice'] = suggestion()
            with self.assertRaises(ValueError):
                bridge.respond('permission-context', True, permission_choice_id='stale-choice')
            self.assertEqual([], writes)
            bridge.respond('permission-context', True)
        response = writes[0]['response']['response']
        self.assertEqual({'behavior': 'allow', 'updatedInput': raw['input']}, response)
        self.assertFalse(bridge.pending)
        self.assertFalse(bridge._permission_choices)

    def test_suppressed_request_still_allows_single_denial_without_rule_updates(self):
        bridge = ClaudeSession([], {}, Path.cwd(), lambda *_: None)
        bridge.handle({'type': 'control_request', 'request_id': 'denial', 'request': {
            'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {},
            'suppress_always_allow_rule': True, 'permission_suggestions': [suggestion()]}})
        with patch.object(bridge, '_write') as write:
            bridge.respond('denial', False)
        response = write.call_args.args[0]['response']['response']
        self.assertEqual('deny', response['behavior'])
        self.assertNotIn('updatedPermissions', response)


class PermissionTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-permission-')
        self.addCleanup(self.temp.cleanup)
        self.events = []
        self.bridge = self.make_bridge(self.events)

    def make_bridge(self, events):
        bridge = ClaudeSession(FAKE, probe_cli(FAKE), Path(self.temp.name),
                               lambda kind, data: events.append((kind, data)))
        self.addCleanup(bridge.close)
        return bridge

    def finish(self, prompt='ready', bridge=None, events=None):
        bridge, events = bridge or self.bridge, events if events is not None else self.events
        count = sum(kind == 'result' for kind, _ in events)
        bridge.send(prompt)
        eventually(lambda: sum(kind == 'result' for kind, _ in events) > count)

    def request(self):
        count = sum(kind == 'request' for kind, _ in self.events)
        self.bridge.send('permission')
        eventually(lambda: sum(kind == 'request' for kind, _ in self.events) > count)
        return [data for kind, data in self.events if kind == 'request'][-1]

    def test_mode_is_unverified_until_ack_and_reset_restores_observed_mode(self):
        self.assertFalse(self.bridge.capabilities['setPermissionMode'])
        self.finish()
        state = self.bridge.model_state()
        self.assertEqual('manual', state['permissionMode'])
        self.assertEqual('unverified', state['permissionModeSupport'])
        self.assertEqual({'manual', 'plan', 'acceptEdits', 'auto'}, {row['value'] for row in state['availablePermissionModes']})
        before = dict(os.environ)
        state = self.bridge.set_permission_mode('plan')
        self.assertEqual(('plan', 'plan', 'confirmed'), (state['permissionMode'], state['permissionModeOverride'], state['permissionModeSupport']))
        self.finish()
        self.assertEqual('manual', self.bridge.original_permission_mode)
        state = self.bridge.set_permission_mode(None)
        self.assertEqual('manual', state['permissionMode'])
        self.assertIsNone(state['permissionModeOverride'])
        self.assertEqual(before, dict(os.environ))
        self.assertNotIn('--permission-mode', cli_arguments(FAKE, self.bridge.info))

    def test_missing_original_mode_reset_is_unavailable_without_changing_connection(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_OMIT_INIT': '1'}):
            self.finish()
        self.assertEqual('', self.bridge.permission_mode)
        self.assertIsNone(self.bridge.original_permission_mode)
        self.bridge.set_permission_mode('plan')
        self.finish()
        self.assertIsNone(self.bridge.original_permission_mode)
        with self.assertRaises(BridgeError) as caught:
            self.bridge.set_permission_mode(None)
        self.assertEqual('permission_mode_reset_unavailable', caught.exception.code)
        self.assertFalse(self.bridge.closed)
        self.assertEqual('plan', self.bridge.permission_mode_override)
        fresh_events = []
        fresh = self.make_bridge(fresh_events)
        self.finish(bridge=fresh, events=fresh_events)
        self.assertEqual('manual', fresh.permission_mode)

    def test_override_before_first_init_cannot_become_the_original_mode(self):
        self.bridge.start()
        self.assertTrue(self.bridge.ready.wait(3))
        self.bridge.set_permission_mode('plan')
        self.finish()
        self.assertEqual('plan', self.bridge.permission_mode)
        self.assertIsNone(self.bridge.original_permission_mode)
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode(None)
        self.assertFalse(self.bridge.closed)

    def test_mode_rejection_keeps_current_state_and_removes_rejected_choice(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'reject'}):
            self.finish()
        with self.assertRaises(BridgeError) as caught:
            self.bridge.set_permission_mode('auto')
        self.assertEqual('permission_mode_rejected', caught.exception.code)
        self.assertEqual('manual', self.bridge.permission_mode)
        self.assertIsNone(self.bridge.permission_mode_override)
        self.assertNotIn('auto', {row['value'] for row in self.bridge.model_state()['availablePermissionModes']})
        self.assertFalse(self.bridge.closed)

    def test_unsupported_control_disables_mode_changes(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'unsupported'}):
            self.finish()
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('plan')
        self.assertFalse(self.bridge.capabilities['setPermissionMode'])
        self.assertEqual('unavailable', self.bridge.model_state()['permissionModeSupport'])
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode(None)
        self.assertFalse(self.bridge.closed)

    def test_mode_timeout_keeps_child_and_reports_unknown_without_claiming_change(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}):
            self.finish()
        with patch('local_app.bridge.CONTROL_TIMEOUT', .05), self.assertRaises(BridgeError) as caught:
            self.bridge.set_permission_mode('auto')
        self.assertEqual('permission_mode_timeout', caught.exception.code)
        self.assertFalse(self.bridge.closed)
        self.assertEqual('', self.bridge.permission_mode)
        self.assertIsNone(self.bridge.permission_mode_override)
        state = self.bridge.model_state()
        self.assertEqual('unreported', state['permissionModeSource'])
        self.assertEqual('unverified', state['permissionModeSupport'])
        self.assertTrue(state['permissionModeChangePending'])
        self.assertTrue(self.bridge._control_active)
        self.assertEqual(1, len(self.bridge._control_waiters))
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('plan')
        with self.assertRaises(ValueError):
            self.bridge.send('cannot start under an unknown mode')
        self.assertFalse(any(kind == 'permission_mode_changed' for kind, _ in self.events))

    def test_mode_change_excludes_prompt_other_control_and_invalid_names(self):
        self.finish()
        for invalid in ('unknownMode', 'dontAsk', 'bypassPermissions', 'plan\n', {}, 1):
            with self.subTest(invalid=invalid), self.assertRaises(BridgeError):
                self.bridge.set_permission_mode(invalid)
        self.bridge.pending['waiting'] = {'tool_name': 'Bash', 'input': {}}
        self.assertEqual('plan', self.bridge.set_permission_mode('plan')['permissionMode'])
        self.assertIn('waiting', self.bridge.pending)
        self.bridge.pending.clear()
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}):
            events = []
            bridge = self.make_bridge(events)
            self.finish(bridge=bridge, events=events)
        errors = []
        def change():
            try:
                bridge.set_permission_mode('auto')
            except BridgeError as exc:
                errors.append(exc.code)
        worker = threading.Thread(target=change)
        worker.start()
        eventually(lambda: bridge._control_active)
        with self.assertRaises(ValueError):
            bridge.send('must not send')
        with self.assertRaises(BridgeError):
            bridge.set_model('must-not-apply')
        bridge.close()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(['connection_closed'], errors)

    def wire(self):
        return [json.loads(line) for line in (Path(self.temp.name) / 'permission-wire.jsonl').read_text().splitlines()]

    def test_mode_changes_in_active_run_keep_pid_turn_and_session_and_submit_no_prompt(self):
        self.bridge.send('hold')
        eventually(lambda: self.bridge.session_id is not None)
        identity = (self.bridge.process.pid, self.bridge.session_id, self.bridge._turn_epoch)
        self.assertTrue(self.bridge.capabilities['setPermissionModeWhileRunning'])
        for mode in ('plan', 'acceptEdits', 'auto', None):
            state = self.bridge.set_permission_mode(mode)
            self.assertEqual('manual' if mode is None else mode, state['permissionMode'])
            self.assertTrue(self.bridge.busy)
            self.assertEqual(identity, (self.bridge.process.pid, self.bridge.session_id, self.bridge._turn_epoch))
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.wire()))
        self.assertFalse(any(kind in {'result', 'request_closed'} for kind, _ in self.events))

    def test_approval_and_question_waits_are_preserved_and_not_automatically_answered(self):
        for prompt, tool in (('permission', 'Bash'), ('question', 'AskUserQuestion')):
            with self.subTest(tool=tool):
                events = []
                bridge = self.make_bridge(events)
                bridge.send(prompt)
                eventually(lambda: bool(bridge.pending))
                pending, choices = copy.deepcopy(bridge.pending), copy.deepcopy(bridge._permission_choices)
                marker = len(self.wire())
                state = bridge.set_permission_mode('auto')
                self.assertEqual('auto', state['permissionMode'])
                self.assertEqual(pending, bridge.pending)
                self.assertEqual(choices, bridge._permission_choices)
                self.assertTrue(bridge.busy)
                frames = self.wire()[marker:]
                self.assertTrue(all(frame['type'] == 'control_request' for frame in frames))
                self.assertFalse(any(kind in {'result', 'request_closed'} for kind, _ in events))
                bridge.respond(next(iter(pending)), False)
                eventually(lambda: not bridge.busy)

    def test_initialized_starting_turn_can_change_mode_before_prompt_submission(self):
        self.bridge.prepare()
        entered, release = threading.Event(), threading.Event()
        original = self.bridge._send_when_ready
        def delayed_send(prompt, epoch):
            entered.set()
            release.wait(3)
            original(prompt, epoch)
        with patch.object(self.bridge, '_send_when_ready', side_effect=delayed_send):
            self.bridge.send('hold')
            self.assertTrue(entered.wait(3))
            try:
                self.assertEqual('plan', self.bridge.set_permission_mode('plan')['permissionMode'])
                self.assertTrue(self.bridge.busy)
                self.assertFalse(any(frame['type'] == 'user' for frame in self.wire()))
            finally:
                release.set()
            eventually(lambda: self.bridge._turn_submitted)
        self.assertEqual('plan', self.bridge.permission_mode)
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.wire()))

    def test_uninitialized_or_stopping_cli_never_receives_mode_control(self):
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('plan')
        self.bridge.prepare()
        before = self.wire()
        with patch.object(self.bridge, 'stopping', True):
            self.assertFalse(self.bridge.capabilities['setPermissionModeWhileRunning'])
            with self.assertRaises(BridgeError):
                self.bridge.set_permission_mode('plan')
        self.assertEqual(before, self.wire())

    def test_active_timeout_preserves_prompt_and_late_ack_resolves_without_duplicate_turn(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}):
            self.bridge.send('permission')
            eventually(lambda: bool(self.bridge.pending))
        pending, epoch, pid = copy.deepcopy(self.bridge.pending), self.bridge._turn_epoch, self.bridge.process.pid
        with patch('local_app.bridge.CONTROL_TIMEOUT', .03), self.assertRaises(BridgeError) as error:
            self.bridge.set_permission_mode('auto')
        self.assertEqual('permission_mode_timeout', error.exception.code)
        self.assertTrue(self.bridge.busy)
        self.assertEqual(pending, self.bridge.pending)
        eventually(lambda: not self.bridge.model_state()['permissionModeChangePending'] and not self.bridge._control_active)
        self.assertEqual(('auto', 'auto', 'confirmed'), (self.bridge.permission_mode,
            self.bridge.permission_mode_override, self.bridge.model_state()['permissionModeSupport']))
        self.assertFalse(self.bridge._control_active)
        self.assertEqual({}, self.bridge._control_waiters)
        self.assertTrue(self.bridge.busy)
        self.assertEqual((pid, epoch, pending), (self.bridge.process.pid, self.bridge._turn_epoch, self.bridge.pending))
        self.assertEqual(1, sum(frame['type'] == 'user' for frame in self.wire()))
        self.assertEqual(1, sum(kind == 'permission_mode_changed' for kind, _ in self.events))
        event = [data for kind, data in self.events if kind == 'permission_mode_changed'][-1]
        self.assertTrue(event['permissionModeControlAcknowledged'])
        self.bridge.handle({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': 'plan'})
        self.assertFalse(self.events[-1][1]['permissionModeControlAcknowledged'])

    def test_timeout_does_not_block_existing_explicit_approval_answer(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}):
            request = self.request()
        with patch('local_app.bridge.CONTROL_TIMEOUT', .03), self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('auto')
        self.bridge.respond(request['id'], False)
        eventually(lambda: not self.bridge.busy)
        self.assertFalse(self.bridge.closed)
        self.assertFalse(self.bridge.pending)
        self.bridge.close()
        self.assertFalse(self.bridge._permission_pending)
        self.assertEqual({}, self.bridge._control_waiters)

    def test_active_rejection_keeps_existing_approval_and_disables_unsupported_control(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'unsupported'}):
            self.request()
        pending = copy.deepcopy(self.bridge.pending)
        with self.assertRaises(BridgeError) as error:
            self.bridge.set_permission_mode('plan')
        self.assertEqual('permission_mode_rejected', error.exception.code)
        self.assertEqual(pending, self.bridge.pending)
        self.assertTrue(self.bridge.busy)
        self.assertFalse(self.bridge.closed)
        self.assertFalse(self.bridge.capabilities['setPermissionModeWhileRunning'])
        self.assertFalse(self.bridge.model_state()['permissionModeChangePending'])
        self.assertEqual('manual', self.bridge.permission_mode)
        self.assertIsNone(self.bridge.permission_mode_override)
        self.assertFalse(any(kind in {'request_closed', 'error', 'result'} for kind, _ in self.events))

    def test_timeout_status_readback_and_late_rejection_report_only_confirmed_actual_mode(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}):
            self.request()
        self.bridge.set_permission_mode('plan')
        pending_prompt = copy.deepcopy(self.bridge.pending)
        with patch('local_app.bridge.CONTROL_TIMEOUT', .03), self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('auto')
        rid = self.bridge._permission_pending['id']
        self.bridge.handle({'type': 'system', 'subtype': 'status', 'status': None, 'permissionMode': 'manual'})
        state = self.bridge.model_state()
        self.assertEqual(('manual', 'cli-status', 'plan'), (state['permissionMode'], state['permissionModeSource'], state['permissionModeOverride']))
        self.assertTrue(state['permissionModeChangePending'])
        self.bridge.handle({'type': 'control_response', 'response': {'subtype': 'error', 'request_id': rid, 'error': 'Policy rejected change'}})
        state = self.bridge.model_state()
        self.assertEqual(('plan', 'plan'), (state['permissionMode'], state['permissionModeOverride']))
        self.assertFalse(state['permissionModeChangePending'])
        self.assertFalse(self.bridge._control_active)
        self.assertEqual({}, self.bridge._control_waiters)
        self.assertTrue(self.bridge.busy)
        self.assertEqual(pending_prompt, self.bridge.pending)
        self.assertFalse(self.bridge.closed)
        self.assertFalse(any(kind in {'error', 'result', 'request_closed'} for kind, _ in self.events))

    def test_late_ack_keeps_admission_reserved_until_mode_callback_persists_context(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'timeout'}):
            self.finish()
        with patch('local_app.bridge.CONTROL_TIMEOUT', .03), self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('auto')
        rid = self.bridge._permission_pending['id']
        callback_observations = []
        original_emit = self.bridge.emit
        def observe(kind, data):
            if kind == 'permission_mode_changed':
                callback_observations.append((self.bridge._control_active, data['permissionModeChangePending']))
                with self.assertRaises(BridgeError):
                    self.bridge.set_permission_mode('plan')
                with self.assertRaises(ValueError):
                    self.bridge.send('queue cannot consume stale settings')
                # A callback can take the app lock while another thread reads
                # bridge state: the reader must not retain bridge.lock here.
                observed = []
                def read_state():
                    with self.bridge.lock:
                        observed.append(self.bridge.model_state())
                reader = threading.Thread(target=read_state)
                reader.start(); reader.join(1)
                self.assertFalse(reader.is_alive())
                self.assertEqual('auto', observed[0]['permissionMode'])
            original_emit(kind, data)
        with patch.object(self.bridge, 'emit', side_effect=observe):
            self.bridge.handle({'type': 'control_response', 'response': {
                'subtype': 'success', 'request_id': rid, 'response': {'mode': 'auto'}}})
        self.assertEqual([(True, False)], callback_observations)
        self.assertFalse(self.bridge._control_active)
        self.assertEqual('plan', self.bridge.set_permission_mode('plan')['permissionMode'])

    def test_concurrent_mode_controls_are_serialized_and_completion_race_keeps_done_turn(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}):
            self.bridge.send('hold')
            eventually(lambda: self.bridge.session_id is not None)
        errors = []
        def change():
            try:
                self.bridge.set_permission_mode('auto')
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=change)
        worker.start()
        eventually(lambda: self.bridge._control_active)
        with self.assertRaises(BridgeError):
            self.bridge.set_permission_mode('plan')
        self.bridge.handle({'type': 'result', 'session_id': self.bridge.session_id, 'result': 'fixture completion'})
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertFalse(self.bridge.busy)
        self.assertEqual('auto', self.bridge.permission_mode)
        self.assertEqual(1, sum(frame.get('request', {}).get('subtype') == 'set_permission_mode' for frame in self.wire()))

    def test_close_during_mode_control_never_applies_late_response(self):
        with patch.dict(os.environ, {'WORKSPACE_PERMISSION_FIXTURE': 'late-ack'}):
            self.finish()
        errors = []
        def change():
            try:
                self.bridge.set_permission_mode('auto')
            except BridgeError as error:
                errors.append(error.code)
        worker = threading.Thread(target=change)
        worker.start()
        eventually(lambda: self.bridge._control_active)
        self.bridge.close()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(['connection_closed'], errors)
        self.assertEqual('manual', self.bridge.permission_mode)
        self.assertFalse(any(kind == 'permission_mode_changed' for kind, _ in self.events))

    def test_explicit_session_choice_round_trip_avoids_repeat_only_in_this_child(self):
        request = self.request()
        self.assertEqual(1, len(request['permissionChoices']))
        choice = request['permissionChoices'][0]
        self.assertEqual(2, len(choice['rules']))
        self.bridge.respond(request['id'], True, permission_choice_id=choice['id'])
        eventually(lambda: not self.bridge.busy)
        self.assertFalse(self.bridge._permission_choices)
        self.finish('permission')
        self.assertEqual(1, sum(kind == 'request' for kind, _ in self.events))
        with self.assertRaises(ValueError):
            self.bridge.respond(request['id'], True, permission_choice_id=choice['id'])
        events = []
        another = self.make_bridge(events)
        another.send('permission')
        eventually(lambda: bool(another.pending))
        self.assertEqual(1, sum(kind == 'request' for kind, _ in events))
        with self.assertRaises(ValueError):
            another.respond(next(iter(another.pending)), True, permission_choice_id=choice['id'])

    def test_single_allow_deny_and_cancel_never_create_session_permission(self):
        for allow in (True, False):
            request = self.request()
            self.bridge.respond(request['id'], allow)
            eventually(lambda: not self.bridge.busy)
        request = self.request()
        self.bridge.handle({'type': 'control_cancel_request', 'request_id': request['id']})
        with self.assertRaises(ValueError):
            self.bridge.respond(request['id'], True, permission_choice_id=request['permissionChoices'][0]['id'])
        self.assertFalse(self.bridge._permission_choices)

    def test_each_persistent_scope_only_sends_the_exact_explicitly_selected_cli_update(self):
        # The fixture verifies the complete wire update and keeps its state in
        # memory only. This does not claim that real Claude persisted settings.
        for destination in ('localSettings', 'projectSettings', 'userSettings'):
            with self.subTest(destination=destination):
                self.bridge.close()
                self.events = []
                self.bridge = self.make_bridge(self.events)
                with patch.dict(os.environ, {'WORKSPACE_PERMISSION_DESTINATION': destination}):
                    request = self.request()
                choice = request['permissionChoices'][0]
                self.assertEqual(destination, choice['destination'])
                # Once-only and denial remain once-only even when the CLI
                # offered a persistent rule.
                for allow in (True, False):
                    self.bridge.respond(request['id'], allow)
                    eventually(lambda: not self.bridge.busy)
                    request = self.request()
                choice = request['permissionChoices'][0]
                self.bridge.respond(request['id'], True, permission_choice_id=choice['id'])
                eventually(lambda: not self.bridge.busy)
                self.assertFalse(self.bridge.last_result.get('is_error'))
                self.assertEqual('설정 범위 전달됨: ' + destination, self.bridge.last_result['result'])
                self.finish('permission')
                self.assertEqual(3, sum(kind == 'request' for kind, _ in self.events))
                with self.assertRaises(ValueError):
                    self.bridge.respond(request['id'], True, permission_choice_id=choice['id'])

    def test_invalid_choice_cannot_be_attached_to_denial_question_or_other_request(self):
        request = self.request()
        choice_id = request['permissionChoices'][0]['id']
        for allow, value in ((False, choice_id), (True, 'invented'), (True, {'rules': []})):
            with self.subTest(allow=allow, value=value), self.assertRaises(ValueError):
                self.bridge.respond(request['id'], allow, permission_choice_id=value)
        self.assertIn(request['id'], self.bridge.pending)
        self.bridge.handle({'type': 'control_request', 'request_id': 'question', 'request': {
            'subtype': 'can_use_tool', 'tool_name': 'AskUserQuestion', 'input': {'questions': []},
            'permission_suggestions': [suggestion()]}})
        event = [data for kind, data in self.events if kind == 'request'][-1]
        self.assertEqual([], event['permissionChoices'])
        with self.assertRaises(ValueError):
            self.bridge.respond('question', True, {}, permission_choice_id=choice_id)
        self.bridge.close()
        self.assertFalse(self.bridge._permission_choices)


if __name__ == '__main__':
    unittest.main()
