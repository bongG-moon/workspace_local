"""Startup display and browser routing without opening any native window."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy/CompanyWorkspace.Startup.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows PowerShell display")
class WorkspaceWindowDisplayTests(unittest.TestCase):
    def run_ps(self, source):
        result = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command",
             "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); $ErrorActionPreference = 'Stop'; " + source],
            capture_output=True, encoding="utf-8", timeout=25,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_startup_display_uses_per_monitor_dpi_without_showing_a_window(self):
        if sys.getwindowsversion().build < 15063:
            self.skipTest("Per-monitor v2 requires Windows 10 build 15063")
        value = self.run_ps(". " + ps_quote(HELPER) + r"""
Initialize-WorkspaceStartupDisplay
@{
  mode = [WorkspacePicker.DisplayScaling]::CurrentMode;
  visualStyles = [Windows.Forms.Application]::RenderWithVisualStyles;
  windows = [Windows.Forms.Application]::OpenForms.Count
} | ConvertTo-Json -Compress
""")
        self.assertEqual(value, {"mode": "PerMonitorV2", "visualStyles": True, "windows": 0})

    def test_warning_survives_missing_broken_and_failing_optional_scaling_helper(self):
        # Replace only the final UI call in a disposable copy. Run the shipped
        # warning function and its real initialization; no MessageBox is shown.
        native_show = "[Windows.Forms.MessageBox]::Show($Message, 'Company Workspace', 'OK', 'Warning')"
        original = HELPER.read_text(encoding="utf-8-sig")
        self.assertEqual(original.count(native_show), 1)
        instrumented = original.replace(native_show, "(Write-FixtureWarning $Message)")
        cases = {
            "missing": None,
            "broken": "this is not valid C#",
            "failing": """namespace WorkspacePicker {
public static class DisplayScaling {
  public static int Attempts;
  public static void Initialize() { Attempts++; throw new System.InvalidOperationException("fixture"); }
}}""",
        }
        for name, optional_source in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="workspace-display-한글 ") as raw:
                directory = Path(raw)
                fixture = directory / "deploy" / HELPER.name
                fixture.parent.mkdir()
                fixture.write_text(instrumented, encoding="utf-8-sig")
                if optional_source is not None:
                    picker = directory / "local_app/WorkspacePicker.cs"
                    picker.parent.mkdir()
                    picker.write_text(optional_source, encoding="utf-8")
                value = self.run_ps(". " + ps_quote(fixture) + r"""
$script:warnings = @()
function Write-FixtureWarning($Message) { $script:warnings += $Message }
Show-WorkspaceStartupDialog -Message 'Original startup failure: WS-41'
$attempts = if ('WorkspacePicker.DisplayScaling' -as [type]) { [WorkspacePicker.DisplayScaling]::Attempts } else { 0 }
@{
  warnings = @($script:warnings);
  attempts = $attempts;
  visualStyles = [Windows.Forms.Application]::RenderWithVisualStyles;
  windows = [Windows.Forms.Application]::OpenForms.Count
} | ConvertTo-Json -Compress
""")
                self.assertEqual(value["warnings"], ["Original startup failure: WS-41"])
                self.assertEqual(value["attempts"], 1 if name == "failing" else 0)
                self.assertTrue(value["visualStyles"])
                self.assertEqual(value["windows"], 0)

    def test_reopen_matches_all_initial_edge_locations_and_keeps_original_uri(self):
        value = self.run_ps(". " + ps_quote(HELPER) + r"""
${env:ProgramFiles(x86)} = 'C:\fixture\Program Files (x86)'
$env:ProgramFiles = 'C:\fixture\Program Files'
$env:LOCALAPPDATA = 'C:\fixture\사용자\AppData\Local'
$roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)
$edges = @($roots | ForEach-Object { Join-Path $_ 'Microsoft\Edge\Application\msedge.exe' })
$script:available = @()
$script:events = @()
function Test-Path {
  param($LiteralPath, $PathType)
  if ($PathType -ne 'Leaf') { throw 'Only executable files are eligible' }
  return $script:available -contains $LiteralPath
}
function Start-Process {
  param($FilePath, $ArgumentList, $WindowStyle)
  $script:events += @{file=$FilePath; arguments=@($ArgumentList); window=$WindowStyle}
}
$uri = [Uri]'http://127.0.0.1:54321/#token=abcdefghijklmnopqrstuvwxyz_0123456789-ABCDE'
foreach ($index in @(0,1,2,-1,3)) {
  $script:available = if ($index -eq 3) { $edges } elseif ($index -ge 0) { @($edges[$index]) } else { @() }
  Open-WorkspaceWindow -Uri $uri
}
${env:ProgramFiles(x86)} = ''
$env:ProgramFiles = ''
$script:available = @($edges[2])
Open-WorkspaceWindow -Uri $uri
@{events=@($script:events); edges=$edges; uri=$uri.AbsoluteUri} | ConvertTo-Json -Compress -Depth 4
""")
        self.assertEqual(len(value["events"]), 6)
        for event, index in zip(value["events"], [0, 1, 2, None, 0, 2]):
            with self.subTest(index=index):
                if index is None:
                    self.assertEqual(event["file"], value["uri"])
                    self.assertEqual(event["arguments"], [None])
                else:
                    self.assertEqual(event["file"], value["edges"][index])
                    self.assertEqual(event["arguments"], ["--new-window", "--app=" + value["uri"]])
                    self.assertEqual(event["window"], "Normal")

    def test_reopen_failure_keeps_the_existing_startup_error_contract(self):
        value = self.run_ps(". " + ps_quote(HELPER) + r"""
function Test-Path { return $false }
function Start-Process { throw 'Fixture cannot open a browser' }
$message = try { Open-WorkspaceWindow -Uri 'http://127.0.0.1:54321/#token=fixture' } catch { $_.Exception.Message }
@{message=$message} | ConvertTo-Json -Compress
""")
        self.assertEqual(value["message"], "WORKSPACE_STARTUP:40")


if __name__ == "__main__":
    unittest.main()
