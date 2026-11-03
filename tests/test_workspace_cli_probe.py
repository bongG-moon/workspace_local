"""CLI metadata parsing never changes the stream-JSON transport or user text."""
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_app.bridge import CHAT_REQUIRED_FLAGS, ClaudeSession, chat_runtime_capability, probe_cli


VERSION = '2.1.316 (Claude Code)'
NOISE = '[0x7FFE1234] ANOMALY: meaningless REX prefix used'
HELP = '--input-format --output-format --permission-prompt-tool ' + ' '.join(CHAT_REQUIRED_FLAGS)


class CLIProbeTests(unittest.TestCase):
    def probe(self, version, help_text=HELP):
        with patch('local_app.bridge.run_owned', side_effect=[
                subprocess.CompletedProcess([], 0, version, 'private stderr'),
                subprocess.CompletedProcess([], 0, help_text, 'private stderr')]) as run:
            info = probe_cli(['fixture-cli'])
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['fixture-cli', '--version'], ['fixture-cli', '--help']])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 12)
            self.assertTrue(call.kwargs['capture_output'])
            self.assertEqual(call.kwargs['encoding'], 'utf-8')
            self.assertEqual(call.kwargs['errors'], 'replace')
        return info

    def test_known_version_survives_leading_trailing_and_same_line_diagnostics(self):
        for output in (VERSION, VERSION + '\r\n', '\n ' + VERSION + '\t\n',
                       VERSION + '\n' + NOISE, VERSION + NOISE,
                       VERSION + ' ' + NOISE, NOISE + '\r\n' + VERSION,
                       NOISE + '\n' + VERSION + NOISE):
            with self.subTest(output=output):
                info = self.probe(output)
                self.assertEqual(info['version'], VERSION)
                self.assertTrue(chat_runtime_capability(info)['available'])

    def test_unknown_malformed_and_conflicting_versions_are_not_displayed_or_version_gated(self):
        for output in ('', NOISE, 'untrusted first line\n' + NOISE,
                       'wrapper 99.1.999', '2.1.316 (Another CLI)',
                       'x2.1.316 (Claude Code)', '2.1.316.4 (Claude Code)',
                       '1234567.1.316 (Claude Code)', '2.1.316 (Claude Code)Extra',
                       '2.1.316-preview (Claude Code)',
                       '2.1.316\n(Claude Code)',
                       VERSION + '\n2.1.315 (Claude Code)'):
            with self.subTest(output=output):
                info = self.probe(output)
                self.assertEqual(info['version'], '')
                self.assertEqual(info['help'], HELP)
                self.assertFalse(chat_runtime_capability(info)['available'])

    def test_duplicate_known_version_and_spacing_are_normalized(self):
        info = self.probe(VERSION + '\n2.1.316\t(Claude Code)')
        self.assertEqual(info['version'], VERSION)

    def test_help_content_is_retained_for_capability_detection(self):
        help_text = NOISE + '\n' + HELP + '\nquoted output: ' + VERSION + NOISE
        self.assertEqual(self.probe(VERSION + NOISE, help_text)['help'], help_text)
        with self.assertRaisesRegex(ValueError, '대화 연결 기능'):
            self.probe(VERSION, '--input-format --output-format')

    def test_failure_exit_is_never_hidden_by_a_valid_version(self):
        for failed_flag in (0, 1):
            results = [subprocess.CompletedProcess([], 0, VERSION, ''),
                       subprocess.CompletedProcess([], 0, HELP, '')]
            results[failed_flag].returncode = 23
            with self.subTest(failed_flag=failed_flag), \
                    patch('local_app.bridge.run_owned', side_effect=results) as run:
                with self.assertRaisesRegex(ValueError, '기존 터미널') as error:
                    probe_cli(['fixture-cli'])
                self.assertEqual(run.call_count, failed_flag + 1)
                self.assertNotIn(VERSION, str(error.exception))

    def test_timeout_from_either_probe_propagates_without_retry(self):
        for timed_out_flag in (0, 1):
            timeout = subprocess.TimeoutExpired(['fixture-cli'], 12)
            results = [subprocess.CompletedProcess([], 0, VERSION, ''), timeout]
            if timed_out_flag == 0:
                results = [timeout]
            with self.subTest(timed_out_flag=timed_out_flag), \
                    patch('local_app.bridge.run_owned', side_effect=results) as run:
                with self.assertRaises(subprocess.TimeoutExpired) as error:
                    probe_cli(['fixture-cli'])
                self.assertIs(error.exception, timeout)
                self.assertEqual(run.call_count, timed_out_flag + 1)


class CLIProbeTransportBoundaryTests(unittest.TestCase):
    def read_frames(self, wire):
        events = []
        bridge = ClaudeSession(['fixture-cli'], {}, Path.cwd(),
                               lambda kind, data: events.append((kind, data)))
        bridge.process = SimpleNamespace(stdout=io.BytesIO(wire))
        def close():
            bridge.closed = True
        with patch.object(bridge, 'close', side_effect=close), \
                patch.object(bridge, '_progress_error'), \
                patch.object(bridge, '_interrupt_executions'):
            bridge._read()
        return events

    def test_diagnostic_looking_user_text_in_valid_json_is_preserved(self):
        text = VERSION + '\n' + NOISE
        frame = {'type': 'assistant', 'message': {'id': 'm1',
                 'content': [{'type': 'text', 'text': text}]}}
        events = self.read_frames((json.dumps(frame) + '\n').encode('utf-8'))
        self.assertEqual([data['text'] for kind, data in events if kind == 'assistant'], [text])
        self.assertFalse(any('UTF-8' in data.get('message', '') for kind, data in events if kind == 'error'))


if __name__ == '__main__':
    unittest.main()
