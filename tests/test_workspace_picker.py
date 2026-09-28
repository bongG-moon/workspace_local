"""Picker lifecycle tests. These never show or automate a native dialog."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
PICKER = ROOT / "local_app" / "Pick-Path.ps1"
POWERSHELL = shutil.which("powershell")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class PickerLifecycleTests(unittest.TestCase):
    def run_ps(self, body: str, apartment="-STA") -> dict:
        path = str(PICKER).replace("'", "''")
        script = ("$ErrorActionPreference = 'Stop'\n"
                  "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)\n"
                  f". '{path}'\n") + body
        command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", apartment, "-NonInteractive", "-EncodedCommand", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip())

    def scenario(self, kind="folder", result="OK", error_at="", has_external=True):
        return self.run_ps(r"""
$script:events = New-Object 'System.Collections.Generic.List[string]'
$script:result = '__RESULT__'
$script:errorAt = '__ERROR__'
$script:window = [pscustomobject]@{TopMost = $false; ActivateWhenShown = $false}
$script:window | Add-Member ScriptMethod Show {
    param($external)
    $script:events.Add($(if ($null -eq $external) {'show:fallback'} else {'show:owned'}))
}
$script:window | Add-Member ScriptMethod BringToFront {$script:events.Add('front:' + $this.TopMost)}
$script:window | Add-Member ScriptMethod Activate {
    $script:events.Add('activate:' + $this.TopMost)
    if ($script:errorAt -eq 'activate') {throw 'activation failed'}
}
$script:window | Add-Member ScriptMethod Close {
    $script:events.Add('close:' + $this.TopMost)
    if ($script:errorAt -eq 'close') {throw 'close failed'}
}
$script:window | Add-Member ScriptMethod Dispose {$script:events.Add('owner-dispose')}
$script:dialog = [pscustomobject]@{SelectedPath = 'C:\work\selected'; FileNames = @('C:\work\a.pdf','C:\work\b.txt')}
$script:dialog | Add-Member ScriptMethod ShowDialog {
    param($owner)
    if ($owner -ne $script:window) {throw 'incorrect modal owner'}
    $script:events.Add('dialog:' + $owner.TopMost)
    if ($script:errorAt -eq 'dialog') {throw 'dialog failed'}
    return $script:result
}
$script:dialog | Add-Member ScriptMethod Dispose {
    $script:events.Add('dialog-dispose')
    if ($script:errorAt -eq 'dispose') {throw 'dispose failed'}
}
$script:external = [pscustomobject]@{Foreground = $true}
$script:external | Add-Member ScriptMethod IsForeground {return $this.Foreground}
$script:icon = [pscustomobject]@{}
$script:icon | Add-Member ScriptMethod Dispose {$script:events.Add('icon-dispose')}
function Initialize-WorkspacePicker {$script:events.Add('initialize')}
function New-WorkspacePickerOwner([long]$Handle) {
    $script:events.Add('owner:' + $Handle)
    return @{Window = $script:window; External = __EXTERNAL__; Icon = $script:icon}
}
function New-WorkspacePathDialog([string]$Kind) {
    $script:events.Add('create:' + $Kind)
    return $script:dialog
}
function Set-WorkspacePickerForeground($Window) {$script:events.Add('foreground')}
$failure = $null
$output = $null
try {$output = Invoke-WorkspacePathPicker -Kind __KIND__ -OwnerHandle 1234}
catch {$failure = $_.Exception.Message}
@{output=$output; error=$failure; events=@($script:events); topmost=$script:window.TopMost} | ConvertTo-Json -Compress
""".replace("__KIND__", kind).replace("__RESULT__", result).replace("__ERROR__", error_at)
            .replace("__EXTERNAL__", "$script:external" if has_external else "$null"))

    def test_owned_folder_selection_raises_once_then_releases_topmost(self):
        value = self.scenario()
        self.assertIsNone(value["error"])
        self.assertEqual(json.loads(value["output"]), [r"C:\work\selected"])
        self.assertEqual(value["events"], [
            "initialize", "owner:1234", "create:folder", "show:owned", "front:True",
            "activate:True", "foreground", "dialog:False", "dialog-dispose", "close:False", "owner-dispose", "icon-dispose",
        ])
        self.assertFalse(value["topmost"])

    def test_files_return_all_selected_paths(self):
        value = self.scenario(kind="files")
        self.assertEqual(json.loads(value["output"]), [r"C:\work\a.pdf", r"C:\work\b.txt"])

    def test_cancel_returns_json_array_and_disposes_every_window(self):
        value = self.scenario(result="Cancel", has_external=False)
        self.assertEqual(value["output"], "[]")
        self.assertIn("show:fallback", value["events"])
        self.assertEqual(value["events"][-4:], ["dialog-dispose", "close:False", "owner-dispose", "icon-dispose"])

    def test_activation_dialog_and_cleanup_errors_still_dispose_owner(self):
        for stage in ("activate", "dialog", "dispose", "close"):
            with self.subTest(stage=stage):
                value = self.scenario(error_at=stage)
                self.assertTrue(value["error"])
                self.assertFalse(value["topmost"])
                self.assertEqual(value["events"][-2:], ["owner-dispose", "icon-dispose"])
                self.assertIn("dialog-dispose", value["events"])

    def test_another_foreground_app_is_not_raised_over(self):
        value = self.run_ps(r"""
$script:events = New-Object 'System.Collections.Generic.List[string]'
$window = [pscustomobject]@{TopMost = $false; ActivateWhenShown = $true}
$window | Add-Member ScriptMethod Show {param($owner); $script:events.Add('show')}
$window | Add-Member ScriptMethod BringToFront {throw 'must not raise'}
$window | Add-Member ScriptMethod Activate {throw 'must not activate'}
$external = [pscustomobject]@{}
$external | Add-Member ScriptMethod IsForeground {return $false}
Show-WorkspacePickerOwner @{Window=$window; External=$external}
@{events=@($script:events); topmost=$window.TopMost; activateWhenShown=$window.ActivateWhenShown} | ConvertTo-Json -Compress
""")
        self.assertEqual(value["events"], ["show"])
        self.assertFalse(value["topmost"])
        self.assertFalse(value["activateWhenShown"])

    def test_mta_fails_before_creating_any_dialog(self):
        value = self.run_ps(r"""
$failure = $null
try {Initialize-WorkspacePicker} catch {$failure=$_.Exception.Message}
@{error=$failure} | ConvertTo-Json -Compress
""", apartment="-MTA")
        self.assertIn("requires PowerShell -STA", value["error"])

    def test_native_helpers_compile_and_fallback_owner_is_visible_capable(self):
        value = self.run_ps(r"""
Initialize-WorkspacePicker
$context = New-WorkspacePickerOwner 0
try {
    $folder = New-WorkspacePathDialog 'folder'
    $files = New-WorkspacePathDialog 'files'
    try {
        @{
            opacity=$context.Window.Opacity;
            taskbar=$context.Window.ShowInTaskbar;
            topmost=$context.Window.TopMost;
            external=($null -eq $context.External);
            invalid=($null -eq [WorkspacePicker.NativeOwner]::FromHandle(-99999));
            multiselect=$files.Multiselect;
            folderType=$folder.GetType().Name
        } | ConvertTo-Json -Compress
    } finally {$folder.Dispose(); $files.Dispose()}
} finally {$context.Window.Dispose(); if ($null -ne $context.Icon) {$context.Icon.Dispose()}}
""")
        self.assertEqual(value["opacity"], 1)
        self.assertTrue(value["taskbar"])
        self.assertFalse(value["topmost"])
        self.assertTrue(value["external"])
        self.assertTrue(value["invalid"])
        self.assertTrue(value["multiselect"])
        self.assertEqual(value["folderType"], "NativePathDialog")


if __name__ == "__main__":
    unittest.main()
