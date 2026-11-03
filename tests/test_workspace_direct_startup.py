import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

from local_app.startup import StartupError, verify_process
from local_app.owned_process import OutputLimitExceeded, run_owned


BEGIN = '__WORKSPACE_VERIFY_V1_BEGIN__'
END = '__WORKSPACE_VERIFY_V1_END__'
NOISE = b'[0x610329E0] ANOMALY: meaningless REX prefix used'
OUTPUT_LIMIT = 64 * 1024


def verified_output(command, mode='normal'):
    nonce = re.search(BEGIN + r'([a-f0-9]{32}):', command[-1]).group(1)
    return (BEGIN + nonce + ':' + mode + ':' + nonce + END).encode('ascii')


def verified_runner(mode='normal', transform=lambda value: value, returncode=0):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode,
                                           transform(verified_output(command, mode)), b'')
    return Mock(side_effect=run)


class DirectStartupTests(unittest.TestCase):
    def test_startup_error_preserves_only_validated_launch_codes(self):
        for code in range(30, 46):
            failure = StartupError('safe message', code=code)
            self.assertIsInstance(failure, RuntimeError)
            self.assertEqual(code, failure.code)
            self.assertEqual('safe message', str(failure))
        for code in (None, True, 29, 46, '33', 33.0):
            self.assertEqual(45, StartupError('safe message', code=code).code)
        self.assertEqual(45, StartupError('safe message').code)

    @patch('local_app.startup.os.name', 'nt')
    @patch('local_app.startup.Path.is_file', return_value=False)
    def test_missing_helper_preserves_failure_code_without_starting_child(self, is_file):
        runner = Mock()
        with self.assertRaisesRegex(StartupError, 'WS-41') as failure:
            verify_process(runner=runner)
        self.assertEqual(41, failure.exception.code)
        runner.assert_not_called()

    @patch('local_app.startup.os.name', 'nt')
    def test_direct_start_uses_the_same_guard_without_environment_overrides(self):
        runner = verified_runner()
        verify_process(runner=runner)
        args, kwargs = runner.call_args
        script = args[0][-1]
        self.assertTrue(Path(args[0][0]).is_absolute())
        self.assertEqual(Path(args[0][0]).name.lower(), 'powershell.exe')
        self.assertIn('Get-WorkspaceVerifiedContext', script)
        self.assertIn('Get-WorkspaceExecutionMode -Context $context', script)
        self.assertNotIn('Get-WorkspaceLaunchAction', script)
        self.assertNotIn('env', kwargs)
        self.assertEqual('Bypass', args[0][args[0].index('-ExecutionPolicy') + 1])
        self.assertNotIn('Set-ExecutionPolicy', script)
        self.assertNotIn('RunAs', script)
        self.assertEqual(OUTPUT_LIMIT, kwargs['output_limit'])

    @patch('local_app.startup.os.name', 'nt')
    def test_failed_guard_never_exposes_child_output_or_starts_without_verification(self):
        runner = Mock(return_value=Mock(returncode=33, stdout=b'private', stderr=b'secret'))
        with self.assertRaisesRegex(RuntimeError, 'WS-33') as error:
            verify_process(runner=runner)
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(33, error.exception.code)

    @patch('local_app.startup.os.name', 'nt')
    def test_even_a_valid_frame_cannot_override_a_nonzero_guard_exit(self):
        for mode in ('normal', 'administrator'):
            for returncode, code in ((33, 33), (45, 45), (1, 45), (99, 45)):
                with self.subTest(mode=mode, returncode=returncode):
                    runner = verified_runner(mode, returncode=returncode)
                    with self.assertRaisesRegex(StartupError, f'WS-{code}') as failure:
                        verify_process(runner=runner)
                    self.assertEqual(code, failure.exception.code)
                    self.assertEqual(1, runner.call_count)

    @patch('local_app.startup.os.name', 'nt')
    def test_timeout_has_no_unguarded_retry(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired('powershell', 30))
        with self.assertRaisesRegex(StartupError, '실행 환경') as failure:
            verify_process(runner=runner)
        self.assertEqual(45, failure.exception.code)
        self.assertEqual(1, runner.call_count)

    @patch('local_app.startup.os.name', 'nt')
    def test_process_and_output_limit_errors_have_no_unguarded_retry(self):
        for error in (OSError('private'), OutputLimitExceeded()):
            with self.subTest(error=type(error).__name__):
                runner = Mock(side_effect=error)
                with self.assertRaisesRegex(RuntimeError, '실행 환경') as failure:
                    verify_process(runner=runner)
                self.assertNotIn('private', str(failure.exception))
                self.assertEqual(45, failure.exception.code)
                self.assertEqual(1, runner.call_count)

    def test_main_calls_guard_before_local_app(self):
        source = (Path(__file__).resolve().parents[1] / 'local_app/server.py').read_text(encoding='utf-8')
        main = source.split('def main():', 1)[1]
        self.assertLess(main.index('verify_process(execution_mode=args.execution_mode)'), main.index('LocalApp('))

    @patch('local_app.startup.os.name', 'nt')
    def test_administrator_mode_requires_real_admin_token_and_same_identity(self):
        runner = verified_runner('administrator')
        verify_process(execution_mode='administrator', runner=runner)
        script = runner.call_args.args[0][-1]
        self.assertIn("if ($mode -ne 'administrator')", script)
        self.assertIn('Get-WorkspaceExecutionMode -Context $context', script)
        self.assertIn('Get-WorkspaceVerifiedContext', script)
        self.assertNotIn('RunAs', script)
        runner = verified_runner('administrator', returncode=33)
        with self.assertRaisesRegex(RuntimeError, 'WS-33'):
            verify_process(execution_mode='administrator', runner=runner)

    @patch('local_app.startup.os.name', 'nt')
    def test_auto_returns_verified_actual_role_without_a_preference_or_relaunch(self):
        for mode in ('normal', 'administrator'):
            runner = verified_runner(mode)
            self.assertEqual(mode, verify_process(runner=runner))
            script = runner.call_args.args[0][-1]
            self.assertNotIn('RunAs', script)
            self.assertNotIn('execution-mode.json', script)
            self.assertNotIn('Relaunch', script)

    @patch('local_app.startup.os.name', 'nt')
    def test_ambiguous_verified_response_cannot_enable_a_role(self):
        for output in (b'', b'normal', b'administrator', b'administrator' + NOISE,
                       b'normal administrator', b'auto', b'private error', None):
            runner = Mock(return_value=Mock(returncode=0, stdout=output))
            with self.assertRaisesRegex(StartupError, 'WS-45') as failure:
                verify_process(runner=runner)
            self.assertEqual(45, failure.exception.code)
        for actual, requested in (('normal', 'administrator'), ('administrator', 'normal')):
            with self.subTest(actual=actual, requested=requested):
                runner = verified_runner(actual, lambda value: NOISE + value + NOISE)
                with self.assertRaisesRegex(RuntimeError, 'WS-45'):
                    verify_process(execution_mode=requested, runner=runner)

    @patch('local_app.startup.os.name', 'nt')
    def test_framed_modes_survive_inline_and_separate_line_native_diagnostics(self):
        surrounds = ((NOISE, b''), (b'', NOISE), (NOISE, NOISE),
                     (NOISE + b'\r\n', b'\r\n' + NOISE),
                     (b'normal administrator\xff', b'\xfenormal administrator'))
        for mode in ('normal', 'administrator'):
            for prefix, suffix in surrounds:
                for text_output in (False, True):
                    with self.subTest(mode=mode, prefix=prefix, suffix=suffix, text=text_output):
                        def surround(value):
                            output = prefix + value + suffix
                            return output.decode('ascii', errors='replace') if text_output else output
                        runner = verified_runner(mode, surround)
                        self.assertEqual(mode, verify_process(execution_mode=mode, runner=runner))

    @patch('local_app.startup.os.name', 'nt')
    def test_malformed_duplicate_and_unbound_frames_are_rejected(self):
        for mode in ('normal', 'administrator'):
            changes = {
                'missing begin': lambda value: value.replace(BEGIN.encode(), b'', 1),
                'missing end': lambda value: value.replace(END.encode(), b'', 1),
                'wrong protocol': lambda value: value.replace(b'_V1_', b'_V2_'),
                'wrong nonce': lambda value: value.replace(value[len(BEGIN):len(BEGIN) + 32], b'x' * 32),
                'wrong end nonce': lambda value: value[:-len(END) - 32] + b'x' * 32 + END.encode(),
                'duplicate frame': lambda value: value + NOISE + value,
                'conflicting frame': lambda value: value + value.replace(mode.encode(), b'administrator' if mode == 'normal' else b'normal'),
                'incomplete extra frame': lambda value: BEGIN.encode() + value,
                'extra end': lambda value: value + END.encode(),
                'reversed frame': lambda value: value[-(len(END) + 33):] + value[:-(len(END) + 33)],
                'empty mode': lambda value: value.replace(mode.encode(), b''),
                'auto is not a role': lambda value: value.replace(mode.encode(), b'auto'),
                'wrong mode case': lambda value: value.replace(mode.encode(), mode.upper().encode()),
                'mode whitespace': lambda value: value.replace(mode.encode(), b' ' + mode.encode()),
                'mode suffix': lambda value: value.replace(mode.encode(), mode.encode() + b'private'),
                'both modes': lambda value: value.replace(mode.encode(), b'normal administrator'),
                'corrupt mode': lambda value: value.replace(mode.encode(), mode.encode() + b'\xff'),
                'inline diagnostic inside frame': lambda value: value.replace(mode.encode(), mode.encode() + NOISE),
            }
            for label, change in changes.items():
                with self.subTest(mode=mode, change=label):
                    runner = verified_runner(mode, change)
                    with self.assertRaisesRegex(RuntimeError, 'WS-45'):
                        verify_process(runner=runner)

    @patch('local_app.startup.os.name', 'nt')
    def test_each_invocation_requires_its_own_nonce_and_rejects_replayed_output(self):
        outputs = []
        def replay(command, **kwargs):
            outputs.append(verified_output(command))
            return subprocess.CompletedProcess(command, 0, outputs[0], b'')
        runner = Mock(side_effect=replay)
        with patch('local_app.startup.secrets.token_hex', side_effect=['a' * 32, 'b' * 32]) as tokens:
            self.assertEqual('normal', verify_process(runner=runner))
            with self.assertRaisesRegex(RuntimeError, 'WS-45'):
                verify_process(runner=runner)
        self.assertEqual([((16,), {}), ((16,), {})], tokens.call_args_list)
        self.assertNotEqual(outputs[0], outputs[1])

    @patch('local_app.startup.os.name', 'nt')
    def test_parser_also_bounds_injected_runner_output(self):
        for mode in ('normal', 'administrator'):
            for extra, expected in ((0, mode), (1, None)):
                with self.subTest(mode=mode, extra=extra):
                    runner = verified_runner(mode, lambda value: b'x' * (OUTPUT_LIMIT - len(value) + extra) + value)
                    if expected:
                        self.assertEqual(expected, verify_process(runner=runner))
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'WS-45'):
                            verify_process(runner=runner)


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell startup framing')
class NativeDirectStartupFrameTests(unittest.TestCase):
    def test_real_inherited_token_check_accepts_native_stdout_noise_around_frame(self):
        from local_app.windows_peer import WindowsPeer
        flags = WindowsPeer().flags(os.getpid())
        expected = 'administrator' if flags['administrator'] and flags['elevated'] and flags['integrity'] == 0x3000 else 'normal'
        for newline in ('', '`r`n'):
            with self.subTest(newline=bool(newline)):
                def noisy_runner(command, **kwargs):
                    command = list(command)
                    noise = '[Console]::Write("' + NOISE.decode() + newline + '"); '
                    command[-1] = noise + command[-1].replace('; exit 0 ', '; ' + noise + 'exit 0 ')
                    return run_owned(command, **kwargs)
                self.assertEqual(expected, verify_process(runner=noisy_runner))
