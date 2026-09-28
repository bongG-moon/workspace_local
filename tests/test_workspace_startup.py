"""Launcher policy and failure UX without credentials, UI, or elevation."""
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
class WorkspaceStartupTests(unittest.TestCase):
    def powershell(self, source):
        command = "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); " + source
        return subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
                              capture_output=True, timeout=20, encoding="utf-8")

    def test_policy_is_once_only_and_requires_verified_identity(self):
        source = ". " + ps_quote(HELPER) + """
        $normal = [pscustomobject]@{verified=$true; sid='S-1-5-21-123'; sessionId=1; isAdministrator=$false}
        $admin = [pscustomobject]@{verified=$true; sid='S-1-5-21-123'; sessionId=1; isAdministrator=$true}
        $unverified = [pscustomobject]@{verified=$false; sid='S-1-5-21-123'; sessionId=1; isAdministrator=$false}
        $service = [pscustomobject]@{verified=$true; sid='S-1-5-21-123'; sessionId=0; isAdministrator=$true}
        $results = @()
        foreach ($item in @(@($normal,$false),@($normal,$true),@($admin,$false),@($admin,$true),@($unverified,$false),@($service,$false))) {
          try { $results += Get-WorkspaceLaunchAction $item[0] $item[1] }
          catch { $results += $_.Exception.Message }
        }
        ConvertTo-Json -Compress -InputObject $results
        """
        result = self.powershell(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["run", "run", "relaunch", "WORKSPACE_STARTUP:34",
                                                    "WORKSPACE_STARTUP:30", "WORKSPACE_STARTUP:30"])

    def test_errors_are_classified_without_inventing_python_failure(self):
        source = ". " + ps_quote(HELPER) + """
        @('WORKSPACE_STARTUP:37', 'USER_CONTEXT_DIFFERENT_ACCOUNT', 'USER_CONTEXT_NONINTERACTIVE',
          'USER_CONTEXT_PATH_MISMATCH', 'USER_CONTEXT_INVALID_PATH', 'USER_CONTEXT_UNAVAILABLE', 'unknown') |
            ForEach-Object { Get-WorkspaceStartupCode $_ }
        """
        result = self.powershell(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["37", "31", "31", "32", "32", "30", "45"])
        result = self.powershell(". " + ps_quote(HELPER) + "; Get-WorkspaceStartupMessage 33")
        self.assertIn("더블클릭", result.stdout)
        self.assertNotIn("Python", result.stdout)

    def test_native_reason_property_not_stack_or_class_name_controls_error_category(self):
        source = ". " + ps_quote(HELPER) + """
        Add-Type -TypeDefinition @'
namespace CompanyAgent {
  public sealed class StartupFixtureException : System.Exception {
    public string ReasonCode {get; private set;}
    public StartupFixtureException(string reason) : base("Workspace normal-token relaunch") { ReasonCode=reason; }
  }
  public static class WorkspaceNormalToken {
    public static int Relaunch(string script,string reason,bool demo,bool noBrowser,string state,string sid,int session) {
      throw new StartupFixtureException(reason);
    }
  }
}
'@
        $context = [pscustomobject]@{sid='S-1-5-21-123'; sessionId=1}
        foreach ($reason in @('create_process','linked_token_not_normal','command_too_long','missing_launcher','environment_block')) {
          try { Invoke-WorkspaceNormalTokenRelaunch $context 'fixture.ps1' $reason $true $true '' }
          catch { Get-WorkspaceStartupCode $_.Exception.Message }
        }
        """
        result = self.powershell(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["35", "33", "42", "41", "35"])

    def fixture_launch(self, admin=True, child_code=0, relaunched=False, no_browser=True, identity_error=None, unsafe_token=False):
        # Only the COPIED helper is mocked. Production has no env/CLI bypass.
        with tempfile.TemporaryDirectory(prefix="workspace-start-한글 & ") as raw:
            directory = Path(raw)
            deploy = directory / "deploy"
            deploy.mkdir()
            shutil.copyfile(LAUNCHER, deploy / LAUNCHER.name)
            calls = directory / "calls.txt"
            dialog = directory / "dialog.txt"
            state = directory / "state"
            context = "throw " + ps_quote(identity_error) if identity_error else (
                "[pscustomobject]@{verified=$true; sid='S-1-5-21-987654321'; sessionId=1; "
                "isAdministrator=$" + str(admin).lower() + "; localAppData=" + ps_quote(directory) + "}")
            overrides = "\nfunction Get-WorkspaceVerifiedContext { " + context + " }\n"
            overrides += """
function Invoke-WorkspaceNormalTokenRelaunch {
  param($Context,$ScriptPath,$PythonCommand,$Demo,$NoBrowser,$StateRoot)
  [IO.File]::AppendAllText(CALLS, "$PythonCommand|$Demo|$NoBrowser|$StateRoot`n", (New-Object Text.UTF8Encoding($false)))
  return CHILD_CODE
}
function Show-WorkspaceStartupDialog {
  param($Message)
  [IO.File]::AppendAllText(DIALOG, $Message + "`n", (New-Object Text.UTF8Encoding($false)))
}
function Assert-WorkspaceNormalProcess { param($Context) TOKEN_CHECK }
""".replace("CALLS", ps_quote(calls)).replace("DIALOG", ps_quote(dialog)).replace("CHILD_CODE", str(child_code)).replace(
                "TOKEN_CHECK", "throw 'WORKSPACE_STARTUP:33'" if unsafe_token else "")
            (deploy / HELPER.name).write_text(HELPER.read_text(encoding="utf-8-sig") + overrides, encoding="utf-8-sig")
            command = "& " + ps_quote(deploy / LAUNCHER.name)
            command += " -Demo -PythonCommand missing-workspace-fixture-python -StateRoot " + ps_quote(state)
            if relaunched:
                command += " -NormalTokenRelaunch"
            if no_browser:
                command += " -NoBrowser"
            command += "; exit $LASTEXITCODE"
            result = self.powershell(command)
            return result, calls.read_text(encoding="utf-8") if calls.exists() else "", \
                dialog.read_text(encoding="utf-8") if dialog.exists() else "", state.exists()

    def test_admin_relaunch_runs_once_before_python_or_state_creation(self):
        result, calls, dialog, state_created = self.fixture_launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls.splitlines()), 1)
        self.assertIn("missing-workspace-fixture-python|True|True|", calls)
        self.assertIn("한글 &", calls)
        self.assertEqual(dialog, "")
        self.assertFalse(state_created)

    def test_admin_child_is_rejected_without_loop_or_dialog(self):
        result, calls, dialog, state_created = self.fixture_launch(relaunched=True, no_browser=False)
        self.assertEqual(result.returncode, 34, result.stderr)
        self.assertIn("WS-34", result.stderr)
        self.assertEqual(calls, "")
        self.assertEqual(dialog, "")
        self.assertFalse(state_created)

    def test_only_parent_shows_one_classified_korean_dialog(self):
        result, calls, dialog, state_created = self.fixture_launch(child_code=37, no_browser=False)
        self.assertEqual(result.returncode, 20, result.stderr)
        self.assertEqual(dialog.count("WS-37"), 1)
        self.assertIn("Python", dialog)
        self.assertEqual(len(calls.splitlines()), 1)
        self.assertFalse(state_created)

    def test_timeout_does_not_retry_or_claim_success(self):
        result, calls, dialog, _ = self.fixture_launch(child_code=22)
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertIn("반복 실행하지 말고", result.stderr)
        self.assertEqual(len(calls.splitlines()), 1)
        self.assertEqual(dialog, "")

    def test_wrong_user_is_rejected_before_relaunch_or_state(self):
        result, calls, dialog, state_created = self.fixture_launch(identity_error="USER_CONTEXT_DIFFERENT_ACCOUNT")
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertEqual(calls, "")
        self.assertEqual(dialog, "")
        self.assertFalse(state_created)

    def test_real_missing_python_is_reported_only_after_identity_and_no_dialog_in_child(self):
        result, calls, dialog, _ = self.fixture_launch(admin=False, relaunched=True, no_browser=False)
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn("WS-37", result.stderr)
        self.assertEqual(calls, "")
        self.assertEqual(dialog, "")

    def test_unsafe_non_admin_token_stops_before_python_and_state(self):
        result, calls, dialog, state_created = self.fixture_launch(admin=False, unsafe_token=True)
        self.assertEqual(result.returncode, 33, result.stderr)
        self.assertIn("WS-33", result.stderr)
        self.assertEqual(calls, "")
        self.assertEqual(dialog, "")
        self.assertFalse(state_created)

    def test_vbs_does_not_duplicate_a_reported_failure(self):
        original = (ROOT / "Company-Workspace.vbs").read_text(encoding="ascii")
        # Replace modal rendering in a fixture; preserve real VBS branching and decoding.
        instrumented = re.sub(r'MsgBox (.*), vbExclamation, "Company Workspace"', r'WScript.StdOut.WriteLine \1', original)
        with tempfile.TemporaryDirectory(prefix="workspace-vbs-") as raw:
            folder = Path(raw)
            probe = folder / "host-probe.vbs"
            probe.write_text('WScript.StdOut.WriteLine "workspace-wsh-probe"\nWScript.Quit 71\n', encoding="ascii")
            host = subprocess.run(["cscript.exe", "//NoLogo", "//U", str(probe)],
                                  capture_output=True, timeout=20)
            if host.returncode != 71 or "workspace-wsh-probe".encode("utf-16-le") not in host.stdout:
                self.skipTest("WSH cannot run even the plain ASCII probe; VBS runtime unverified on this host")
            (folder / "deploy").mkdir()
            vbs = folder / "Company-Workspace.vbs"
            vbs.write_text(instrumented, encoding="ascii")
            for status in (37, 20, 0):
                with self.subTest(status=status):
                    (folder / "deploy/Start-CompanyWorkspace.ps1").write_text("exit " + str(status), encoding="ascii")
                    result = subprocess.run(["cscript.exe", "//NoLogo", "//U", str(vbs)],
                                            capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, status, (result.stdout, result.stderr))
                    self.assertNotIn(b"CScript Error", result.stdout, result.stdout)
                    output = result.stdout.decode("utf-16-le")
                    if status in (0, 20):
                        self.assertEqual(output.strip(), "")
                    else:
                        self.assertEqual(output.count("오류 코드"), 1)
                        self.assertNotIn("Python", output)


class WorkspaceStartupContractTests(unittest.TestCase):
    def test_windows_text_encodings_and_single_ui_owner(self):
        for path in (HELPER, LAUNCHER):
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"), str(path))
        vbs = (ROOT / "Company-Workspace.vbs").read_text(encoding="ascii")
        self.assertIn("result <> 0 And result <> 20", vbs)
        self.assertNotIn("-NoProfile", vbs)
        source = LAUNCHER.read_text(encoding="utf-8-sig")
        self.assertLess(source.index("Get-WorkspaceVerifiedContext"), source.index("Threading.Mutex"))
        self.assertLess(source.index("Invoke-WorkspaceNormalTokenRelaunch"), source.index("Threading.Mutex"))
        self.assertLess(source.index("Assert-WorkspaceNormalProcess"), source.index("Threading.Mutex"))
        self.assertIn("if ($NoBrowser -or $NormalTokenRelaunch)", source)
        self.assertNotIn("-Verb RunAs", source)
        self.assertNotIn("-ExecutionPolicy Bypass", source)
        self.assertNotIn("Resolve-SetupUserContext -SkipAdminCheck", HELPER.read_text(encoding="utf-8-sig"))

    def test_vbs_messages_decode_to_korean_without_a_python_guess(self):
        source = (ROOT / "Company-Workspace.vbs").read_text(encoding="ascii")
        messages = ["".join(chr(int(value, 16)) for value in encoded.split())
                    for encoded in re.findall(r'Korean\("([A-F0-9 ]+)"\)', source)]
        self.assertEqual(len(messages), 3)
        self.assertIn("압축 해제", messages[0])
        self.assertTrue(all("실행" in message and "Python" not in message for message in messages))
        self.assertTrue(all("오류 코드" in message for message in messages[1:]))


if __name__ == "__main__":
    unittest.main()
