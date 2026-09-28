"""Standalone WS-33 diagnostics: reviewed sources, read-only calls, safe reports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "Check-Workspace.ps1"
COMMAND = ROOT / "Check-Workspace.cmd"
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
SOURCES = (
    "deploy/Start-CompanyWorkspace.ps1",
    "deploy/CompanyWorkspace.Startup.ps1",
    "deploy/CompanyWorkspace.NormalToken.cs",
    "deploy/CompanyAgent.UserContext.ps1",
)


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(source):
    # Suppress personal profiles only in this isolated test harness. The shipped
    # CMD preserves the user's normal PowerShell startup and execution policy.
    return subprocess.run(
        [str(PS), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", source],
        capture_output=True, encoding="utf-8", timeout=30,
    )


class WorkspaceDiagnosticContractTests(unittest.TestCase):
    def test_reviewed_source_hashes_match_with_bom_and_line_endings_normalized(self):
        script = SCRIPT.read_text(encoding="utf-8-sig")
        expected = dict(re.findall(r"'(deploy/[^']+)'\s*=\s*'([a-f0-9]{64})'", script))
        self.assertEqual(set(SOURCES), set(expected))
        self.assertRegex(script, r"targetSource\s*=\s*'4396726'")
        for relative, digest in expected.items():
            with self.subTest(source=relative):
                source = (ROOT / relative).read_text(encoding="utf-8-sig")
                normalized = source.replace("\r\n", "\n").encode("utf-8")
                self.assertEqual(digest, hashlib.sha256(normalized).hexdigest())

    def test_cmd_preserves_profile_and_policy_without_requesting_elevation(self):
        command = COMMAND.read_text(encoding="utf-8-sig")
        self.assertIn(
            '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
            '-NoLogo -File "%~dp0Check-Workspace.ps1"', command,
        )
        self.assertNotRegex(command.lower(), r"noprofile|bypass|executionpolicy|runas|encodedcommand")

    def test_diagnostic_native_calls_are_allowlisted_and_never_relaunch(self):
        script = SCRIPT.read_text(encoding="utf-8-sig")
        self.assertEqual(
            {"InspectCurrentToken", "ValidateNormalProcess", "ValidateSourceToken", "ValidateNormalTokenCandidate"},
            set(re.findall(r"\[CompanyAgent\.WorkspaceNormalToken\]::(\w+)\s*\(", script)),
        )
        self.assertEqual(
            {"GetCurrentProcess", "OpenProcessToken", "ReadLinkedToken", "ReadToken"},
            set(re.findall(r"\.GetMethod\('([^']+)'", script)),
        )
        self.assertNotRegex(
            script,
            r"(?i)(?:\]::|\.)Relaunch\s*\(|\bInvoke-WorkspaceNormalTokenRelaunch\b|"
            r"\bStart-Process\b|\b(?:CreateProcessWithTokenW|CreateProcessAsUserW|"
            r"DuplicateTokenEx|AdjustTokenPrivileges|ImpersonateLoggedOnUser)\b",
        )


@unittest.skipUnless(os.name == "nt" and PS.is_file(), "Windows PowerShell diagnostic")
class WorkspaceDiagnosticExecutionTests(unittest.TestCase):
    """Only disposable copies run; no app, settings, credentials or token launch."""

    @classmethod
    def setUpClass(cls):
        identity = run_powershell("""
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
try { @{ sid=$identity.User.Value; account=$identity.Name } | ConvertTo-Json -Compress }
finally { $identity.Dispose() }
""")
        if identity.returncode:
            raise AssertionError("Could not read the test process identity")
        cls.private_identity = json.loads(identity.stdout)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workspace diagnostic 한글 & ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        shutil.copyfile(SCRIPT, self.directory / SCRIPT.name)
        (self.directory / "deploy").mkdir()
        for relative in SOURCES:
            shutil.copyfile(ROOT / relative, self.directory / relative)

    def run_diagnostic(self, preamble=""):
        completed = run_powershell(preamble + "\n& " + ps_literal(self.directory / SCRIPT.name))
        self.assertEqual(0, completed.returncode, "Diagnostic failed before producing a report")
        self.assertEqual("", completed.stderr)
        reports = list(self.directory.glob("Workspace-Diagnostic-*.json"))
        self.assertEqual(1, len(reports))
        report_text = reports[0].read_text(encoding="utf-8")
        report = json.loads(report_text)
        self.assertIn(report_text.strip(), completed.stdout)
        self.assert_private_values_absent(report_text + completed.stdout)
        self.assert_report_schema(report)
        return report

    def assert_private_values_absent(self, output):
        private = [str(ROOT), str(self.directory), *self.private_identity.values()]
        private.extend(os.environ.get(name, "") for name in ("USERPROFILE", "LOCALAPPDATA", "APPDATA"))
        for value in private:
            if value:
                self.assertFalse(value.casefold() in output.casefold(), "Diagnostic output included a private value")
        self.assertIsNone(re.search(r"S-1-\d+(?:-\d+)+|[A-Za-z]:[\\/]|\\\\[^\\\s]+\\", output),
                          "Diagnostic output included an identity or absolute path")

    def assert_report_schema(self, report):
        self.assertEqual({
            "diagnosticVersion", "targetSource", "status", "sourceMatches", "files",
            "identityVerified", "current", "linked", "predictedGuard", "uacEnabled",
            "process64Bit", "stage", "reason", "nativeCode", "notTested",
        }, set(report))
        self.assertEqual("ws33-1", report["diagnosticVersion"])
        self.assertEqual("4396726", report["targetSource"])
        self.assertEqual(set(SOURCES), set(report["files"]))
        self.assertTrue(set(report["files"].values()) <= {"matched", "missing", "different_version"})
        self.assertEqual([
            "original_vbs_process", "primary_token_duplication", "child_process_launch", "claude_or_python",
        ], report["notTested"])
        for name in ("current", "linked"):
            snapshot = report[name]
            if snapshot is not None:
                self.assertEqual({
                    "sameUser", "sameSession", "elevationType", "elevated", "integrity", "administrator", "primary",
                }, set(snapshot))
                for field, value in snapshot.items():
                    self.assertIs(type(value), int if field in {"elevationType", "integrity"} else bool)
        if report["reason"] is not None:
            self.assertRegex(report["reason"], r"^(?:[a-z_]{1,64}|USER_CONTEXT_[A-Z_]+|WORKSPACE_STARTUP:[0-9]{2}|DIAGNOSTIC_[A-Z_]+)$")

    def assert_rejected_before_identity(self, report, reason):
        self.assertEqual("incomplete", report["status"])
        self.assertEqual("source_check", report["stage"])
        self.assertEqual(reason, report["reason"])
        self.assertFalse(report["identityVerified"])
        self.assertIsNone(report["current"])
        self.assertIsNone(report["linked"])
        self.assertEqual("not_checked", report["predictedGuard"])

    def test_matching_sources_produce_a_redacted_report_in_current_environment(self):
        report = self.run_diagnostic()
        self.assertTrue(report["sourceMatches"])
        self.assertEqual({"matched"}, set(report["files"].values()))
        self.assertIn(report["status"], {"observed", "incomplete"})
        self.assertNotEqual("source_check", report["stage"])

    def test_missing_sources_stop_before_loading_any_helper(self):
        for relative in SOURCES:
            with self.subTest(source=relative):
                path = self.directory / relative
                original = path.read_bytes()
                path.unlink()
                try:
                    report = self.run_diagnostic()
                    self.assert_rejected_before_identity(report, "DIAGNOSTIC_SOURCE_MISMATCH")
                    self.assertFalse(report["sourceMatches"])
                    self.assertEqual("missing", report["files"][relative])
                finally:
                    path.write_bytes(original)
                    for output in self.directory.glob("Workspace-Diagnostic-*.json"):
                        output.unlink()

    def test_changed_helper_is_not_executed(self):
        helper = self.directory / "deploy/CompanyWorkspace.Startup.ps1"
        marker = self.directory / "must-not-execute.txt"
        helper.write_text(
            "[IO.File]::WriteAllText(" + ps_literal(marker) + ", 'executed')\n"
            + helper.read_text(encoding="utf-8-sig"), encoding="utf-8-sig",
        )
        report = self.run_diagnostic()
        self.assert_rejected_before_identity(report, "DIAGNOSTIC_SOURCE_MISMATCH")
        self.assertFalse(report["sourceMatches"])
        self.assertEqual("different_version", report["files"]["deploy/CompanyWorkspace.Startup.ps1"])
        self.assertFalse(marker.exists(), "The unreviewed helper executed")

    def test_preloaded_company_agent_types_are_rejected(self):
        for type_name in ("WorkspaceNormalToken", "SetupUserContextNative"):
            with self.subTest(type_name=type_name):
                report = self.run_diagnostic(
                    "Add-Type -TypeDefinition 'namespace CompanyAgent { public static class "
                    + type_name + " {} }'",
                )
                self.assert_rejected_before_identity(report, "DIAGNOSTIC_ALREADY_LOADED_TYPE")
                self.assertTrue(report["sourceMatches"])
                for output in self.directory.glob("Workspace-Diagnostic-*.json"):
                    output.unlink()

    @unittest.skipUnless(os.environ.get("COMPANY_AGENT_TEST_REAL_USER_CONTEXT") == "1",
                         "Opt in from the ordinary signed-in Windows user, outside the sandbox")
    def test_actual_signed_in_normal_token_is_accepted(self):
        report = self.run_diagnostic()
        self.assertEqual("observed", report["status"])
        self.assertTrue(report["sourceMatches"])
        self.assertTrue(report["identityVerified"])
        self.assertEqual("normal_process_accepted", report["predictedGuard"])
        self.assertIsNone(report["reason"])
        self.assertIsNone(report["linked"])
        current = report["current"]
        self.assertTrue(current["sameUser"])
        self.assertTrue(current["sameSession"])
        self.assertTrue(current["primary"])
        self.assertFalse(current["elevated"])
        self.assertFalse(current["administrator"])
        self.assertEqual(8192, current["integrity"])
        self.assertIn(current["elevationType"], (1, 3))


if __name__ == "__main__":
    unittest.main()
