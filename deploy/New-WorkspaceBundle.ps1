[CmdletBinding()]
param([string]$OutputDirectory, [string]$UpdateConfig, [string]$ConfigPython = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'dist' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $repoRoot ('build\workspace-bundle-' + [Guid]::NewGuid().ToString('N'))
$payload = Join-Path $stage 'Company-Workspace'
New-Item -ItemType Directory -Path (Join-Path $payload 'local_app\web') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload 'deploy') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload 'docs') -Force | Out-Null
$files = @(
    'local_app\session_visibility.py', 'local_app\path_browser.py', 'local_app\web\path-picker.js', 'local_app\web\path-picker.css',
    'local_app\native_window.py', 'local_app\ui_health.py', 'deploy\Workspace.Desktop.cs', 'deploy\WebView2.lock.json',
    'deploy\New-WorkspaceDesktop.ps1', 'deploy\CompanyWorkspace.Standalone.manifest',
    'docs\WORKSPACE_0.18.0_NATIVE_WINDOW.md',
    'docs\WORKSPACE_0.20.0_NOTIFICATIONS.md',
    'Company-Workspace.vbs',
    'Check-Workspace.cmd', 'Check-Workspace.ps1',
    'deploy\Start-CompanyWorkspace.ps1',
    'deploy\CompanyWorkspace.Startup.ps1',
    'deploy\CompanyWorkspace.NormalToken.cs',
    'deploy\CompanyAgent.UserContext.ps1',
    'docs\WORKSPACE_0.16.0_PRODUCTIVITY.md', 'docs\WORKSPACE_0.17.0_RELIABILITY.md',
    'docs\LOCAL_WORKSPACE.md', 'docs\WORKSPACE_USER_GUIDE.html', 'docs\WORKSPACE_0.15.0_CLAUDE.md',
    'docs\WORKSPACE_0.13.0_DESKTOP.md', 'docs\WORKSPACE_0.14.0_CONTINUITY.md',
    'docs\WORKSPACE_STARTUP_DIAGNOSTIC.md',
    'docs\WORKSPACE_0.12.1_COMPATIBILITY.md', 'docs\WORKSPACE_0.12.2_INPUT.md', 'docs\WORKSPACE_0.12.3_AUTOCOMPLETE.md', 'docs\WORKSPACE_0.12.4_CONTROLS.md', 'docs\WORKSPACE_STANDALONE_EXE.md',
                        'local_app\__init__.py', 'local_app\bridge.py', 'local_app\server.py', 'local_app\demo.py',
    'local_app\claude_inventory.py', 'local_app\session_order.py', 'local_app\file_diff.py', 'local_app\conversation_fork.py', 'local_app\history.py', 'local_app\artifacts.py', 'local_app\capabilities.py', 'local_app\skill_inventory.py', 'local_app\html_preview.py',
        'local_app\Pick-Path.ps1', 'local_app\WorkspacePicker.cs', 'local_app\Invoke-TerminalClaude.ps1',
    'local_app\startup.py', 'local_app\picker_protocol.py', 'local_app\windows_paths.py',
    'local_app\windows_process.py', 'local_app\picker_channel.py',
    'local_app\choices.py', 'local_app\hook_status.py', 'local_app\permission_contract.py',
    'local_app\external_apps.py', 'local_app\completions.py', 'local_app\attention.py', 'local_app\app_window.py',
    'local_app\session_import.py', 'local_app\desktop_notifications.py', 'local_app\window_theme.py', 'local_app\tray.py', 'local_app\attachments.py', 'local_app\work_queue.py', 'local_app\app_dispatch.py',
    'local_app\web\fonts\NotoSansKR-Variable.woff', 'local_app\web\fonts\OFL.txt', 'local_app\web\fonts\SOURCE.json',
    'local_app\web\index.html', 'local_app\web\app.css', 'local_app\web\app.js', 'local_app\web\capabilities.js',
    'local_app\web\composer.js', 'local_app\web\inline-controls.js', 'local_app\web\attention.js',
    'local_app\web\input-keys.js', 'local_app\web\chat-shortcuts.js', 'local_app\web\chat-shortcuts.css',
    'local_app\web\startup-health.js', 'local_app\web\startup-health.css',
    'local_app\web\desktop.js', 'local_app\web\session-import.js', 'local_app\web\rendering.js', 'local_app\web\attachments.js', 'local_app\web\workflow.js', 'local_app\web\productivity.js', 'local_app\web\palette.js', 'local_app\web\layout.js', 'local_app\web\productivity.css', 'local_app\web\review.css',
    'docs\WORKSPACE_0.21.0_RICH_CHAT.md',
    'local_app\file_preview.py', 'local_app\executions.py', 'local_app\tool_activity.py',
    'local_app\web\tool-activity.js', 'local_app\web\tool-activity.css',
    'local_app\upgrade_handoff.py', 'local_app\upgrade_launcher.py',
    'local_app\app_updates.py', 'local_app\update_install.py', 'local_app\update_source.py',
    'local_app\web\app-updates.js', 'local_app\web\app-updates.css',
    'docs\WORKSPACE_APP_UPDATES.md',
    'docs\WORKSPACE_GITLAB_PUBLISHER.md',
    'local_app\web\upgrade-handoff.js', 'local_app\web\upgrade-handoff.css',
    'local_app\web\rich-content.js', 'local_app\web\rich-content.css', 'local_app\web\execution-view.js', 'local_app\web\execution-view.css',
    'local_app\web\icon.svg', 'local_app\web\app-icon.ico',
    'local_app\web\app-icon-192.png', 'local_app\web\app-icon-512.png'
)
foreach ($relative in $files) {
    $sourceFile = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) { throw ('Missing bundle source: ' + $relative) }
    New-Item -ItemType Directory -Path (Split-Path (Join-Path $payload $relative) -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $payload $relative) -ErrorAction Stop
    if (-not (Test-Path -LiteralPath (Join-Path $payload $relative) -PathType Leaf)) { throw ('Bundle copy missing: ' + $relative) }
}
if ($UpdateConfig) {
    $configInput = (Resolve-Path -LiteralPath $UpdateConfig).Path
    & $ConfigPython -X utf8 (Join-Path $repoRoot 'scripts\prepare-workspace-update-source.py') --input $configInput --output (Join-Path $payload 'workspace-update-source.json')
    if ($LASTEXITCODE -ne 0) { throw 'Invalid GitLab update settings. No application bundle was created.' }
}
$desktopBuild = & (Join-Path $PSScriptRoot 'New-WorkspaceDesktop.ps1')
$desktopPayload = Join-Path $payload 'desktop'
New-Item -ItemType Directory -Path $desktopPayload -Force | Out-Null
foreach ($name in @('Workspace.Desktop.exe','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll','WebView2-LICENSE.txt','WebView2-NOTICE.txt','desktop-build.json')) {
    Copy-Item -LiteralPath (Join-Path $desktopBuild $name) -Destination (Join-Path $desktopPayload $name)
}
$zip = Join-Path $outputRoot ('company-workspace-preview-0.23.4-' + $stamp + '.zip')
if (Test-Path -LiteralPath $zip) { throw 'Output already exists; refusing to overwrite.' }
& $ConfigPython -X utf8 (Join-Path $repoRoot 'scripts\create-workspace-archive.py') --source $payload --output $zip
if ($LASTEXITCODE -ne 0) { throw 'Application ZIP creation failed. Check the file and retry details above; no completed ZIP was published.' }
Get-FileHash -LiteralPath $zip -Algorithm SHA256 | Select-Object Path, Hash
# Keep staging for verification; no broad recursive deletion.
