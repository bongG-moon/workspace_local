"""Explicit bypass preparation and control ACK tests; never runs Claude or a task."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from local_app.bridge import BridgeError, ClaudeSession, cli_arguments, probe_cli
from local_app.permission_contract import help_bypass_opt_in, help_permission_modes, mode_cycle, mode_options

FIXTURE = Path(__file__).parent / 'fixtures' / 'workspace_bypass_cli.py'
COMMAND = [sys.executable, '-B', str(FIXTURE.resolve())]
HELP = '''--input-format --output-format --permission-prompt-tool
  --allow-dangerously-skip-permissions Only make the mode selectable.
  --permission-mode <mode> (choices: "manual", "acceptEdits", "plan", "auto", "bypassPermissions", "dontAsk")'''


class BypassContractTests(unittest.TestCase):
    def test_opt_in_requires_installed_mode_and_exact_flag(self):
        self.assertTrue(help_bypass_opt_in(HELP))
        for text in (None, '', HELP.replace('bypassPermissions', 'other'),
                     HELP.replace('--allow-dangerously-skip-permissions', '--dangerously-skip-permissions'),
                     HELP.replace('--allow-dangerously-skip-permissions', 'See --allow-dangerously-skip-permissions')):
            self.assertFalse(help_bypass_opt_in(text))
        self.assertNotIn('bypassPermissions', help_permission_modes(HELP))
        self.assertIn('bypassPermissions', help_permission_modes(HELP, include_bypass=True))

    def test_ordinary_start_never_adds_bypass_flags_or_changes_other_defaults(self):
        ordinary = cli_arguments(COMMAND, {'help': HELP})
        confirmed = cli_arguments(COMMAND, {'help': HELP}, allow_bypass_permissions=True)
        self.assertEqual(ordinary + ['--allow-dangerously-skip-permissions'], confirmed)
        for flag in ('--dangerously-skip-permissions', '--permission-mode', '--settings', '--model', '--effort'):
            self.assertNotIn(flag, ordinary)
            self.assertNotIn(flag, confirmed)
        with self.assertRaises(BridgeError):
            cli_arguments(COMMAND, {'help': ''}, allow_bypass_permissions=True)
        for invalid in (1, 'true', None):
            with self.assertRaises(ValueError):
                cli_arguments(COMMAND, {'help': HELP}, allow_bypass_permissions=invalid)

    def test_bypass_is_explicit_risky_option_never_a_keyboard_cycle_member(self):
        modes = help_permission_modes(HELP, include_bypass=True)
        self.assertNotIn('bypassPermissions', [row['value'] for row in mode_options(modes)])
        option = next(row for row in mode_options(modes, include_bypass=True) if row['value'] == 'bypassPermissions')
        self.assertEqual('high', option['risk'])
        self.assertIs(option['requiresConfirmation'], True)
        self.assertEqual(['manual', 'acceptEdits', 'plan', 'auto'], mode_cycle(modes))
        self.assertNotIn('dontAsk', [row['value'] for row in mode_options(modes)])


class BypassControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = []

    def bridge(self, *, allow=False, behavior=''):
        command = [*COMMAND, *([f'--fixture={behavior}'] if behavior else [])]
        bridge = ClaudeSession(command, {'help': HELP}, self.root,
                               lambda kind, value: self.events.append((kind, value)),
                               allow_bypass_permissions=allow)
        self.addCleanup(bridge.close)
        bridge.prepare()
        return bridge

    def controls(self):
        path = self.root / 'bypass-controls.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def test_unconfirmed_connection_offers_reconnect_but_never_sends_bypass(self):
        bridge = self.bridge()
        state = bridge.model_state()
        self.assertEqual('default', state['permissionMode'])
        self.assertTrue(state['bypassPermissions']['available'])
        self.assertTrue(state['bypassPermissions']['requiresReconnect'])
        with self.assertRaises(BridgeError) as error:
            bridge.set_permission_mode('bypassPermissions')
        self.assertEqual('bypass_opt_in_required', error.exception.code)
        self.assertFalse(any(row['request'].get('mode') == 'bypassPermissions' for row in self.controls()))
        self.assertFalse(bridge.closed)
        self.assertEqual(('fixture-model', 'high'), (bridge.model, bridge.effort))

    def test_explicit_opt_in_keeps_initial_defaults_then_ack_switches_and_reset_restores(self):
        before = dict(os.environ)
        bridge = self.bridge(allow=True)
        initial = bridge.model_state()
        self.assertEqual('default', initial['permissionMode'])
        self.assertFalse(initial['bypassPermissions']['active'])
        self.assertFalse(initial['bypassPermissions']['requiresReconnect'])
        state = bridge.set_permission_mode('bypassPermissions')
        self.assertEqual(('bypassPermissions', 'bypassPermissions'), (state['permissionMode'], state['permissionModeOverride']))
        self.assertTrue(state['bypassPermissions']['active'])
        self.assertNotIn('bypassPermissions', state['permissionModeCycle'])
        restored = bridge.set_permission_mode(None)
        self.assertEqual('default', restored['permissionMode'])
        self.assertIsNone(restored['permissionModeOverride'])
        self.assertEqual(('fixture-model', 'high'), (restored['model'], restored['effort']))
        self.assertEqual(before, dict(os.environ))
        self.assertTrue(all(row['type'] == 'control_request' for row in self.controls()))

    def test_managed_rejection_removes_bypass_only_and_preserves_ordinary_modes(self):
        bridge = self.bridge(allow=True, behavior='policy')
        with self.assertRaises(BridgeError) as error:
            bridge.set_permission_mode('bypassPermissions')
        self.assertEqual('permission_mode_rejected', error.exception.code)
        state = bridge.model_state()
        self.assertEqual('default', state['permissionMode'])
        self.assertIsNone(state['permissionModeOverride'])
        self.assertFalse(state['bypassPermissions']['available'])
        self.assertIn('거절', state['bypassPermissions']['reason'])
        self.assertNotIn('bypassPermissions', [row['value'] for row in state['availablePermissionModes']])
        self.assertEqual('plan', bridge.set_permission_mode('plan')['permissionMode'])

    def test_policy_clamped_success_does_not_record_bypass_selection(self):
        bridge = self.bridge(allow=True, behavior='clamp')
        with self.assertRaises(BridgeError) as error:
            bridge.set_permission_mode('bypassPermissions')
        self.assertEqual('permission_mode_rejected', error.exception.code)
        self.assertEqual('default', bridge.permission_mode)
        self.assertIsNone(bridge.permission_mode_override)
        self.assertFalse(bridge.bypass_state()['available'])
        self.assertFalse(bridge.closed)

    def test_documented_empty_success_ack_is_accepted_without_a_user_message(self):
        bridge = self.bridge(allow=True, behavior='empty-ack')
        self.assertEqual('bypassPermissions', bridge.set_permission_mode('bypassPermissions')['permissionMode'])
        self.assertTrue(all(row['type'] == 'control_request' for row in self.controls()))

    def test_inherited_original_bypass_can_be_displayed_and_restored_without_new_flag(self):
        bridge = self.bridge(behavior='inherited')
        self.assertFalse(bridge.allow_bypass_permissions)
        self.assertEqual('bypassPermissions', bridge.original_permission_mode)
        self.assertTrue(bridge.bypass_state()['enabledForConnection'])
        bridge.set_permission_mode('plan')
        restored = bridge.set_permission_mode(None)
        self.assertEqual('bypassPermissions', restored['permissionMode'])
        self.assertIsNone(restored['permissionModeOverride'])

    def test_busy_or_pending_request_cannot_be_changed_even_with_opt_in(self):
        bridge = self.bridge(allow=True)
        for attribute, value in (('busy', True), ('pending', {'permission': {}})):
            with patch.object(bridge, attribute, value):
                with self.assertRaises(BridgeError) as error:
                    bridge.set_permission_mode('bypassPermissions')
                self.assertEqual('session_busy', error.exception.code)
        self.assertFalse(any(row['request'].get('mode') == 'bypassPermissions' for row in self.controls()))


if __name__ == '__main__':
    unittest.main()
