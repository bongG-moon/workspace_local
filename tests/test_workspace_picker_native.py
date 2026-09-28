"""Compile and exercise real Windows APIs; no dialog is shown or automated."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

from tests import test_workspace_picker as lifecycle


@unittest.skipUnless(os.name == "nt" and lifecycle.POWERSHELL, "Windows PowerShell required")
class NativePickerTests(unittest.TestCase):
    run_ps = lifecycle.PickerLifecycleTests.run_ps

    def test_picker_thread_and_unshown_owner_use_native_dpi_context(self):
        value = self.run_ps(r"""
Initialize-WorkspacePicker
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DpiProbe {
    [DllImport("user32.dll")] public static extern IntPtr GetWindowDpiAwarenessContext(IntPtr window);
    [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr window);
}
'@
$context = New-WorkspacePickerOwner 0
try {
    $window = $context.Window
    $handle = $window.Handle
    @{
        threadMode = [WorkspacePicker.DisplayScaling]::CurrentMode;
        windowV2 = [DpiProbe]::AreDpiAwarenessContextsEqual([DpiProbe]::GetWindowDpiAwarenessContext($handle), [IntPtr](-4));
        windowDpi = [DpiProbe]::GetDpiForWindow($handle);
        visible = $window.Visible;
        font = $window.Font.Name;
        scaling = [string]$window.AutoScaleMode;
        visualStyles = [Windows.Forms.Application]::RenderWithVisualStyles;
        apartment = [string][Threading.Thread]::CurrentThread.ApartmentState
    } | ConvertTo-Json -Compress
} finally {$context.Window.Dispose(); if ($null -ne $context.Icon) {$context.Icon.Dispose()}}
""") if sys.getwindowsversion().build >= 15063 else None
        if value is None:
            self.skipTest("Per-monitor v2 requires Windows 10 build 15063")
        self.assertEqual(value["threadMode"], "PerMonitorV2")
        self.assertTrue(value["windowV2"])
        self.assertGreaterEqual(value["windowDpi"], 96)
        self.assertFalse(value["visible"])
        self.assertEqual(value["font"], "Segoe UI")
        self.assertEqual(value["scaling"], "Dpi")
        self.assertTrue(value["visualStyles"])
        self.assertEqual(value["apartment"], "STA")

    def test_real_common_item_dialog_retains_required_options_without_show(self):
        value = self.run_ps(r"""
Initialize-WorkspacePicker
$results = @()
foreach ($kind in @('folder', 'files')) {
    $dialog = New-WorkspacePathDialog $kind
    try {
        $native = $dialog.GetType().GetField('dialog', 'Instance,NonPublic').GetValue($dialog)
        $results += @{
            kind = $kind;
            isCom = [Runtime.InteropServices.Marshal]::IsComObject($native);
            options = $dialog.NativeOptions;
            multiselect = $dialog.Multiselect;
            empty = ($dialog.FileNames.Length -eq 0 -and $dialog.SelectedPath -eq '')
        }
    } finally {$dialog.Dispose()}
    $dialog.Dispose()
    $disposedError = $null
    try {$unused = $dialog.GetType().GetProperty('NativeOptions').GetValue($dialog, $null)} catch {$disposedError = $_.Exception.ToString()}
    if ($null -eq $disposedError -or $disposedError -notmatch 'ObjectDisposedException') {throw 'Disposed COM dialog stayed usable'}
}
ConvertTo-Json -InputObject @($results) -Compress
""")
        self.assertEqual(len(value), 2)
        for dialog in value:
            flags = dialog["options"]
            self.assertTrue(dialog["isCom"])
            self.assertTrue(dialog["empty"])
            self.assertEqual(flags & 0x1848, 0x1848)  # existing filesystem item/path, no cwd mutation
            if dialog["kind"] == "folder":
                self.assertEqual(flags & 0x20, 0x20)
                self.assertEqual(flags & 0x200, 0)  # exactly one folder
                self.assertFalse(dialog["multiselect"])
            else:
                self.assertEqual(flags & 0x1200, 0x1200)
                self.assertEqual(flags & 0x20, 0)
                self.assertTrue(dialog["multiselect"])

    def test_real_shell_item_returns_exact_unicode_folder_and_file_paths(self):
        with tempfile.TemporaryDirectory(prefix="workspace-picker-업무-") as directory:
            folder = Path(directory) / "자료 모음 [검토]"
            folder.mkdir()
            file = folder / "한글 문서 🗂.txt"
            file.write_text("picker path probe", encoding="utf-8")
            paths = [str(folder), str(file)]
            literals = ",".join("'" + path.replace("'", "''") + "'" for path in paths)
            value = self.run_ps(r"""
Initialize-WorkspacePicker
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ShellPathProbe {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
    public static extern int SHCreateItemFromParsingName(string path, IntPtr context, ref Guid iid, out IntPtr item);
}
'@
$interface = [WorkspacePicker.NativePathDialog].GetNestedType('IShellItem', 'NonPublic')
$readPath = [WorkspacePicker.NativePathDialog].GetMethod('ReadPath', [Reflection.BindingFlags]'Static,NonPublic')
$results = @()
foreach ($path in @(__PATHS__)) {
    $pointer = [IntPtr]::Zero
    $item = $null
    try {
        $iid = $interface.GUID
        $status = [ShellPathProbe]::SHCreateItemFromParsingName($path, [IntPtr]::Zero, [ref]$iid, [ref]$pointer)
        [Runtime.InteropServices.Marshal]::ThrowExceptionForHR($status)
        $item = [Runtime.InteropServices.Marshal]::GetTypedObjectForIUnknown($pointer, $interface)
        $results += $readPath.Invoke($null, [object[]]@($item))
    } finally {
        if ($null -ne $item) {[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item)}
        if ($pointer -ne [IntPtr]::Zero) {[void][Runtime.InteropServices.Marshal]::Release($pointer)}
    }
}
ConvertTo-Json -InputObject @($results) -Compress
""".replace("__PATHS__", literals))
        self.assertEqual(value, paths)


if __name__ == "__main__":
    unittest.main()
