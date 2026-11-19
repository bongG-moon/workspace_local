"""Failures from isolated profiles and fake CLI frames, with no user profiles loaded."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from local_app.bridge import ClaudeSession, LaunchDiagnosticError, launch_marker_error, probe_cli, resolve_cli


WRAPPER = Path(__file__).resolve().parents[1] / 'local_app' / 'Invoke-TerminalClaude.ps1'
MARKER = 'AXWS_LAUNCH/1|profile_parse|profile_restore|5.1|Function|1|4|utf8_without_bom'


class LaunchDiagnosticTests(unittest.TestCase):
    def test_only_bundled_wrapper_and_reserved_exit_interpret_marker(self):
        wrapper = ['fixture', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WRAPPER), '-Entry', 'claude', '-LoadProfiles']
        for command, code, expected in [(wrapper, 78, 'profile_parse'), (['fixture'], 78, 'cli_version'), (wrapper, 1, 'cli_version')]:
            with self.subTest(command=command, code=code), patch('local_app.bridge.run_owned', return_value=subprocess.CompletedProcess([], code, '', 'example-secret\n'+MARKER)):
                with self.assertRaises(LaunchDiagnosticError) as raised:
                    probe_cli(command)
                self.assertEqual(expected, raised.exception.diagnostic['category'])
                self.assertNotIn('example-secret', json.dumps(raised.exception.diagnostic))

    def test_malformed_evidence_and_unrelated_launcher_warning(self):
        self.assertIsNone(launch_marker_error(MARKER.replace('5.1', 'private user path')))
        startup = MARKER.replace('profile_restore', 'launcher_profile')
        with patch.dict(os.environ, {'COMPANY_WORKSPACE_CLAUDE_STARTUP_DIAGNOSTIC': startup,
                    'COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE': '1', 'COMPANY_AGENT_CLAUDE': ''}):
            with self.assertRaises(LaunchDiagnosticError) as raised:
                resolve_cli()
            self.assertEqual('command_not_found', raised.exception.diagnostic['category'])
            self.assertEqual('profile_parse', raised.exception.diagnostic['startupProfile']['category'])

    def test_initialize_refusal_preserves_observed_provider_category(self):
        for reason, code in [('private refusal', 'launch_cli_initialize'), ('not logged in', 'cli_authentication'), ('not allowed to use this model', 'model_permission')]:
            with self.subTest(code=code):
                session = ClaudeSession(['fixture'], {}, Path.cwd(), lambda *args: None)
                session.handle({'type': 'control_response', 'response': {'request_id': session.initialize_id, 'subtype': 'error', 'error': reason}})
                self.assertEqual(code, session._launch_failure.code)
                self.assertNotIn(reason, str(session._launch_failure))

    def test_first_turn_auth_and_model_failures_keep_separate_startup_observation(self):
        marker = MARKER.replace('profile_restore', 'launcher_profile')
        selected = ['ps', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(WRAPPER.with_name('Invoke-SelectedClaude.ps1')), '-Entry', 'claude', 'Function']
        with patch.dict(os.environ, {'COMPANY_WORKSPACE_CLAUDE_STARTUP_DIAGNOSTIC': marker, 'COMPANY_AGENT_CLAUDE': ''}):
            for command in (['fixture'], selected):
                for reason, code in [('not logged in', 'cli_authentication'), ('not allowed to use this model', 'model_permission')]:
                    with self.subTest(command=command[0], code=code):
                        events = []
                        session = ClaudeSession(command, {}, Path.cwd(), lambda kind, data: events.append((kind, data)))
                        session._tool_activity_run_id = 'example-run'
                        session._begin_tool_activity_turn()
                        session._initialized = session.busy = session._turn_submitted = True
                        session.handle({'type': 'result', 'is_error': True, 'result': reason})
                        failure, = [data for kind, data in events if kind == 'error']
                        self.assertEqual(code, failure['code'])
                        if command == selected:
                            self.assertNotIn('diagnostic', failure)
                        else:
                            self.assertEqual({'startupProfile'}, set(failure['diagnostic']))
                            self.assertEqual('launcher_profile', failure['diagnostic']['startupProfile']['stage'])
                        progress, = [data for kind, data in events if kind == 'progress_record' and data['kind'] == 'error']
                        self.assertEqual(command != selected, 'profile_parse / launcher_profile' in progress['text'])

    def test_transport_progress_adds_only_fixed_category_and_stage_to_existing_row(self):
        events = []
        session = ClaudeSession(['fixture'], {}, Path.cwd(), lambda kind, data: events.append((kind, data)))
        session._tool_activity_run_id = 'example-run'
        session._begin_tool_activity_turn()
        session.busy = True
        failure = launch_marker_error('FIXTURE_SECRET\n' + MARKER)
        session._progress_error(str(failure), failure)
        progress, = [data for kind, data in events if kind == 'progress_record']
        self.assertIn('profile_parse / profile_restore', progress['text'])
        self.assertNotIn('FIXTURE_SECRET', str(progress))


@unittest.skipUnless(os.name == 'nt', 'Windows profile parsing')
class ProfileSmokeTests(unittest.TestCase):
    def run_profile(self, shell, source, expected_definition=None):
        with tempfile.TemporaryDirectory() as raw:
            profile = Path(raw) / 'profile.ps1'
            profile.write_bytes(source)
            # Override all four paths inside an isolated -NoProfile child.
            driver = Path(raw) / 'run.ps1'
            driver.write_text("""
param($ProfileFile, $Wrapper)
$PROFILE = [pscustomobject]@{ AllUsersAllHosts=$ProfileFile; AllUsersCurrentHost=$ProfileFile; CurrentUserAllHosts=$ProfileFile; CurrentUserCurrentHost=$ProfileFile }
& $Wrapper -Entry claude -LoadProfiles --version
exit $LASTEXITCODE
""", encoding='ascii')
            env = os.environ.copy()
            digest = hashlib.sha256((expected_definition or 'different body').encode()).digest()
            env['COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH'] = '-'.join(f'{byte:02X}' for byte in digest)
            cp = subprocess.run([shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(driver), str(profile), str(WRAPPER)],
                                env=env, capture_output=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(source, profile.read_bytes())
            stderr = cp.stderr.decode('utf-8', errors='replace')
            self.assertNotIn('FIXTURE_SECRET', stderr)
            self.assertNotIn(raw, stderr)
            return cp.returncode, launch_marker_error(stderr), cp.stdout.decode('utf-8', errors='replace').strip()

    def test_real_51_and_7_syntax_and_function_identity(self):
        for name in ('powershell.exe', 'pwsh.exe'):
            shell = shutil.which(name)
            if not shell:
                continue
            for source, expected in [(b'# FIXTURE_SECRET\nif (', 'profile_parse'),
                                     (b'throw "FIXTURE_SECRET"', 'profile_load'),
                                     (b'function claude { "FIXTURE_SECRET" }', 'function_changed')]:
                with self.subTest(shell=name, expected=expected):
                    status, error, stdout = self.run_profile(shell, source)
                    self.assertEqual(78, status)
                    self.assertEqual('', stdout)
                    self.assertEqual(expected, error.diagnostic['category'])
                    self.assertNotIn('encodingHint', error.diagnostic)

    def test_live_connection_reports_wrapper_failure_once_before_first_frame(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shim = root / 'entry.ps1'
            shim.write_text('throw "FIXTURE_SECRET"', encoding='ascii')
            calls = []
            command = [shutil.which('powershell.exe'), '-NoLogo', '-NoProfile', '-ExecutionPolicy',
                       'Bypass', '-File', str(WRAPPER), '-Entry', str(shim)]
            session = ClaudeSession(command, {}, root, lambda kind, data: calls.append((kind, data)))
            try:
                with self.assertRaises(LaunchDiagnosticError) as raised:
                    session.prepare()
                self.assertEqual('cli_start', raised.exception.diagnostic['category'])
                errors = [data for kind, data in calls if kind == 'error']
                self.assertEqual([raised.exception.diagnostic], [e.get('diagnostic') for e in errors])
                self.assertNotIn('FIXTURE_SECRET', json.dumps(errors))
            finally:
                session.close()

    def test_bomless_korean_profile_uses_same_parser_for_encoding_hint(self):
        definition = " 'fixture ok' "
        source = ("# 한글 시작 설정\n$label = 'FIXTURE_SECRET’\nfunction claude {" + definition + "}").encode()
        for name in ('powershell.exe', 'pwsh.exe'):
            shell = shutil.which(name)
            if not shell:
                continue
            with self.subTest(shell=name):
                status, error, stdout = self.run_profile(shell, source, definition)
                if name == 'powershell.exe' and status != 0:
                    self.assertEqual('profile_parse', error.diagnostic['category'])
                    self.assertEqual('utf8_without_bom', error.diagnostic.get('encodingHint'))
                    self.assertGreater(error.diagnostic['errorLine'], 0)
                else:  # PS7, or a 5.1 system already using UTF-8 as the ANSI code page.
                    self.assertEqual((0, None, 'fixture ok'), (status, error, stdout))
                self.assertEqual((0, None, 'fixture ok'), self.run_profile(shell, b'\xef\xbb\xbf'+source, definition))

    def test_51_does_not_call_a_real_syntax_error_or_invalid_utf8_an_encoding_problem(self):
        shell = shutil.which('powershell.exe')
        for source in (b'# \xff\nif (', '# 한글 주석\nif ('.encode()):
            status, error, _ = self.run_profile(shell, source)
            self.assertEqual(78, status)
            self.assertEqual('profile_parse', error.diagnostic['category'])
            self.assertNotIn('encodingHint', error.diagnostic)
