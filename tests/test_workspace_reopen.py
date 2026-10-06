"""Reopen a previous app safely without a real browser, dialog, or CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy/CompanyWorkspace.Startup.ps1"
LAUNCHER = ROOT / "deploy/Start-CompanyWorkspace.ps1"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class WorkspaceReopenTests(unittest.TestCase):
    def powershell(self, source):
        return subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); " + source],
            capture_output=True, encoding="utf-8", timeout=20,
        )

    def fixture_launch(self, *, same_version=False, same_root=True, no_browser=False,
                       relaunched=False, closing=False, wait_finished=False, open_failure=False,
                       probe_dialog_mutex=False, reopen_supported=True, version_override=None, upgrade_ready=False,
                       shutdown_state=None, quit_result='closed', execution_protocol=3, running_mode='normal', inherited_mode='normal'):
        # Execute the shipped launcher with only disposable helper overrides.
        # Unexpected Python/browser/CLI startup fails instead of touching the PC.
        with tempfile.TemporaryDirectory(prefix="workspace-reopen-한글 & ") as raw:
            directory = Path(raw)
            deploy = directory / "deploy"
            deploy.mkdir()
            shutil.copyfile(LAUNCHER, deploy / LAUNCHER.name)
            state = directory / "state"
            state.mkdir()
            runtime = state / "runtime.json"
            url = "http://127.0.0.1:54321/#token=" + "a" * 43
            original = json.dumps({"url": url, "pid": 12345})
            runtime.write_text(original, encoding="utf-8")
            events = directory / "events.jsonl"
            mutex_probe = directory / "probe-lock.ps1"
            mutex_probe.write_text(r"""
param([string] $MutexName)
$mutex = New-Object Threading.Mutex($false, $MutexName)
$acquired = $false
try {
  $acquired = $mutex.WaitOne(0)
  if ($acquired) { 'available' } else { 'blocked' }
} finally {
  if ($acquired) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}
""", encoding="utf-8-sig")
            version = re.search(r"workspaceVersion -eq '([^']+)'", LAUNCHER.read_text(encoding="utf-8-sig"))[1]
            overrides = r"""
function Write-FixtureEvent($Kind, $Value) {
  $record = @{kind=$Kind; value=$Value} | ConvertTo-Json -Compress
  [IO.File]::AppendAllText(EVENTS, $record + "`n", (New-Object Text.UTF8Encoding($false)))
}
function Get-WorkspaceVerifiedContext {
  [pscustomobject]@{verified=$true; sid=SID; sessionId=1; isAdministrator=IS_ADMIN; localAppData=ROOT}
}
function Assert-WorkspaceNormalProcess { param($Context) }
function Get-WorkspaceExecutionMode { param($Context) return INHERITED_MODE }
function Invoke-RestMethod {
  param($Uri,$Headers,$TimeoutSec,$Method,$ContentType,$Body,$MaximumRedirection)
  if ($Headers.Authorization -ne ('Bearer ' + ('a' * 43))) {throw 'incorrect authentication'}
  if ($Uri -eq 'http://127.0.0.1:54321/api/quit') {
    if ($Method -ne 'Post' -or $ContentType -ne 'application/json' -or $Body -ne '{}' -or $MaximumRedirection -ne 0 -or $TimeoutSec -ne 30) {throw 'incorrect cleanup request'}
    Write-FixtureEvent 'quit' $Uri
    QUIT_RESULT
  }
  if ($Uri -ne 'http://127.0.0.1:54321/api/bootstrap') {throw 'incorrect endpoint'}
  Write-FixtureEvent 'health' $Uri
  [pscustomobject]@{application='company-workspace'; demo=$false; workspaceVersion=VERSION; executionMode=LIVE_MODE; executionModeProtocol=EXECUTION_PROTOCOL; appRoot=APPROOT; closing=CLOSING; shutdownState=SHUTDOWN_STATE; window=[pscustomobject]@{reopenSupported=REOPEN}}
}
function Open-WorkspaceWindow {
  param([Uri]$Uri,[bool]$ReuseSupported)
  Write-FixtureEvent 'open' $Uri.AbsoluteUri
  OPEN_RESULT
}
function Show-WorkspaceStartupDialog {
  param($Message)
  Write-FixtureEvent 'dialog' $Message
  DIALOG_PROBE
}
function Wait-WorkspaceShutdown {
  param($Origin,$Auth,$RuntimePath)
  if ($Origin -ne 'http://127.0.0.1:54321' -or $Auth -ne ('a' * 43) -or $RuntimePath -ne RUNTIME) {throw 'incorrect wait endpoint'}
  Write-FixtureEvent 'wait' $Origin
  return WAIT_RESULT
}
function Start-Process { throw 'unexpected process launch' }
"""
            substitutions = {
                "LIVE_MODE":ps_quote(running_mode), "INHERITED_MODE":ps_quote(inherited_mode),
                "IS_ADMIN":'$true' if inherited_mode=='administrator' else '$false',
                "EXECUTION_PROTOCOL": '$null' if execution_protocol is None else str(execution_protocol),
                "REOPEN": "$true" if reopen_supported else "$false",
                "EVENTS": ps_quote(events), "SID": ps_quote("fixture-" + directory.name),
                "ROOT": ps_quote(directory), "VERSION": ps_quote(version_override or (version if same_version else "0.20.0")),
                "APPROOT": ps_quote(directory if same_root else directory / "other-app"),
                "CLOSING": "$true" if closing else "$false", "RUNTIME": ps_quote(runtime),
                "SHUTDOWN_STATE": ps_quote(shutdown_state) if shutdown_state is not None else '$null',
                "QUIT_RESULT": {
                    'closed': 'return [pscustomobject]@{ok=$true; closed=$true}',
                    'rejected': 'return [pscustomobject]@{ok=$false; closed=$false}',
                    'ambiguous': 'return [pscustomobject]@{ok=$true}',
                    'unavailable': "throw 'cleanup request unavailable'",
                }[quit_result],
                "WAIT_RESULT": "$true" if wait_finished else "$false",
                "OPEN_RESULT": "throw 'WORKSPACE_STARTUP:40'" if open_failure else "",
                "DIALOG_PROBE": (
                    "$mutexProbeResult = & " + ps_quote(Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
                    + " -NoLogo -NoProfile -NonInteractive -File " + ps_quote(mutex_probe)
                    + " -MutexName " + ps_quote("Local\\CompanyWorkspace-fixture-" + directory.name)
                    + "\n  Write-FixtureEvent 'dialog-lock-available' ($mutexProbeResult -eq 'available')"
                ) if probe_dialog_mutex else "",
            }
            overrides = re.sub(r"\b(?:" + "|".join(substitutions) + r")\b",
                               lambda match: substitutions[match[0]], overrides)
            if upgrade_ready:
                overrides += r'''
function Get-WorkspacePythonExecutable { param($Command) return 'C:\Fixture\python.exe' }
function Invoke-WorkspacePythonQuery {
  param($Executable,$Arguments,$WorkingDirectory)
  Write-FixtureEvent 'preflight' $Arguments
  return '{"ok":true}'
}
function Start-Process {
  param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru)
  Write-FixtureEvent 'coordinator' $WindowStyle
  $index = [Array]::IndexOf($ArgumentList, '--request-id')
  $requestId = $ArgumentList[$index+1]
  $ack = @{requestId=$requestId; status='accepted'} | ConvertTo-Json -Compress
  [IO.File]::WriteAllText((Join-Path STATE ('upgrade-launch-' + $requestId + '.json')), $ack)
  return [pscustomobject]@{HasExited=$false}
}
'''.replace('STATE', ps_quote(state))
            (deploy / HELPER.name).write_text(HELPER.read_text(encoding="utf-8-sig") + overrides,
                                             encoding="utf-8-sig")
            flags = " -NoBrowser" if no_browser else ""
            if relaunched:
                flags += " -NormalTokenRelaunch"
            command = "& " + ps_quote(deploy / LAUNCHER.name)
            command += " -PythonCommand missing-workspace-fixture-python -StateRoot " + ps_quote(state) + flags
            # Parent launcher must stop on every handled runtime branch. Without
            # a live runtime, the deliberately missing Python reports WS-37.
            env_before = os.environ.get("COMPANY_AGENT_CLAUDE")
            try:
                os.environ["COMPANY_AGENT_CLAUDE"] = "not-invoked-fixture"
                result = self.powershell(command + "; exit $LASTEXITCODE")
            finally:
                if env_before is None:
                    os.environ.pop("COMPANY_AGENT_CLAUDE", None)
                else:
                    os.environ["COMPANY_AGENT_CLAUDE"] = env_before
            records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
            self.assertEqual(runtime.read_text(encoding="utf-8"), original, "Existing runtime must remain untouched")
            return result, records, url

    def test_other_windows_role_fails_fast_without_reopen_quit_or_coordinator(self):
        for running, inherited in (('normal','administrator'),('administrator','normal')):
            with self.subTest(running=running,inherited=inherited):
                result,events,_=self.fixture_launch(same_version=True,no_browser=True,
                    running_mode=running,inherited_mode=inherited)
                self.assertEqual(39,result.returncode,result.stderr)
                self.assertIn('다른 실행 권한',result.stderr)
                self.assertIn('완전 종료',result.stderr)
                self.assertEqual(['health'],[event['kind'] for event in events])

    def test_same_administrator_role_reopens_without_a_new_coordinator(self):
        result,events,_=self.fixture_launch(same_version=True,running_mode='administrator',inherited_mode='administrator')
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual(['health','open'],[event['kind'] for event in events])

    def test_previous_version_keeps_old_window_when_new_python_preflight_fails(self):
        result, events, url = self.fixture_launch()
        self.assertEqual(result.returncode, 20, result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "open", "dialog"])
        self.assertEqual(events[1]["value"], url)
        self.assertIn("WS-37", events[2]["value"])
        self.assertNotIn("오른쪽 위 전원", events[2]["value"])

    def test_warning_does_not_hold_launch_mutex_while_dialog_is_open(self):
        # Probe from another process inside the mocked modal call. Acquiring on
        # the launcher thread would be reentrant and would miss this regression.
        result, events, _ = self.fixture_launch(probe_dialog_mutex=True)
        self.assertEqual(result.returncode, 20, result.stderr)
        self.assertEqual([item["kind"] for item in events],
                         ["health", "open", "dialog", "dialog-lock-available"])
        self.assertIs(events[-1]["value"], True)

    def test_different_folder_reopens_without_starting_new_app(self):
        result, events, _ = self.fixture_launch(same_version=True, same_root=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "open"])

    def test_same_version_without_execution_policy_prepares_cooperative_replacement(self):
        result, events, _ = self.fixture_launch(same_version=True, execution_protocol=None, upgrade_ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'open', 'preflight', 'coordinator'])
        self.assertEqual(events[-1]['value'], 'Hidden')

    def test_original_same_version_is_replaced_by_corrected_protocol(self):
        result, events, _ = self.fixture_launch(same_version=True, execution_protocol=1, upgrade_ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'open', 'preflight', 'coordinator'])

    def test_future_policy_same_version_is_not_replaced_by_older_patch(self):
        result, events, _ = self.fixture_launch(same_version=True, execution_protocol=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'open'])

    def test_no_browser_mismatch_retains_ws39_without_open_or_dialog(self):
        result, events, _ = self.fixture_launch(no_browser=True)
        self.assertEqual(result.returncode, 39, result.stderr)
        self.assertIn("WS-39", result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health"])

    def test_relaunched_child_reopens_but_parent_owns_the_only_dialog(self):
        result, events, _ = self.fixture_launch(relaunched=True)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn("WS-37", result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "open"])

    def test_same_build_reopens_without_upgrade_warning(self):
        result, events, _ = self.fixture_launch(same_version=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "open"])

    def test_newer_running_version_is_reused_without_downgrade(self):
        result, events, _ = self.fixture_launch(version_override='99.0.0')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['health', 'open'], [row['kind'] for row in events])

    def test_previous_version_preflights_then_hands_off_without_waiting_for_work(self):
        result, events, _ = self.fixture_launch(upgrade_ready=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(['health', 'open', 'preflight', 'coordinator'], [row['kind'] for row in events])
        self.assertEqual('Hidden', events[-1]['value'])

    def test_legacy_server_without_reopen_api_never_launches_a_browser(self):
        result, events, _ = self.fixture_launch(reopen_supported=False)
        self.assertEqual(20, result.returncode)
        self.assertEqual(['health', 'dialog'], [item['kind'] for item in events])
        self.assertIn('WS-39', events[-1]['value'])

    def test_window_launch_failure_does_not_fall_through_to_start_another_server(self):
        result, events, _ = self.fixture_launch(same_version=True, open_failure=True, relaunched=True)
        self.assertEqual(result.returncode, 40, result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "open"])

    def test_closing_server_timeout_does_not_open_window_or_start_new_app(self):
        result, events, _ = self.fixture_launch(closing=True, no_browser=True)
        self.assertEqual(result.returncode, 39, result.stderr)
        self.assertIn("종료 처리 중", result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "wait"])

    def test_completed_shutdown_advances_to_new_app_startup_without_old_window(self):
        result, events, _ = self.fixture_launch(closing=True, wait_finished=True, no_browser=True)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn("WS-37", result.stderr)
        self.assertEqual([item["kind"] for item in events], ["health", "wait"])

    def test_failed_shutdown_retries_owned_cleanup_once_before_waiting(self):
        result, events, _ = self.fixture_launch(same_version=True, closing=True, shutdown_state='failed',
                                               wait_finished=True, no_browser=True)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'quit', 'wait'])

    def test_failed_shutdown_retry_requires_explicit_cleanup_confirmation(self):
        for reply in ('rejected', 'ambiguous', 'unavailable'):
            with self.subTest(reply=reply):
                result, events, _ = self.fixture_launch(same_version=True, closing=True,
                    shutdown_state='failed', quit_result=reply, wait_finished=True, no_browser=True)
                self.assertEqual(result.returncode, 39, result.stderr)
                self.assertEqual([item['kind'] for item in events], ['health', 'quit'])
                self.assertIn('종료를 마치지 못했습니다', result.stderr)
                self.assertIn('Check-Workspace.cmd', result.stderr)
                self.assertNotIn('이전 버전 또는 다른 폴더', result.stderr)

    def test_failed_shutdown_retry_still_waits_for_owned_endpoint_and_record(self):
        result, events, _ = self.fixture_launch(same_version=True, closing=True, shutdown_state='failed',
                                               wait_finished=False, no_browser=True)
        self.assertEqual(result.returncode, 39, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'quit', 'wait'])

    def test_in_progress_shutdown_is_only_observed(self):
        result, events, _ = self.fixture_launch(same_version=True, closing=True,
                                               shutdown_state='closing', no_browser=True)
        self.assertEqual(result.returncode, 39, result.stderr)
        self.assertEqual([item['kind'] for item in events], ['health', 'wait'])

    def test_shutdown_wait_requires_endpoint_and_runtime_to_disappear(self):
        for endpoint_live, runtime_exists, expected in ((True, True, False), (True, False, False),
                                                       (False, True, False), (False, False, True)):
            with self.subTest(endpoint_live=endpoint_live, runtime_exists=runtime_exists):
                source = ". " + ps_quote(HELPER) + "\n"
                source += "function Start-Sleep {}\n"
                source += "function Invoke-RestMethod { " + ("return @{}" if endpoint_live else "throw (New-Object Net.Sockets.SocketException 10061)") + " }\n"
                source += "function Test-Path { return $" + str(runtime_exists).lower() + " }\n"
                source += "Wait-WorkspaceShutdown -Origin 'http://127.0.0.1:54321' -Auth 'fixture' -RuntimePath 'fixture' -TimeoutSeconds 0 | ConvertTo-Json -Compress"
                result = self.powershell(source)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIs(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
