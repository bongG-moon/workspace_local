[CmdletBinding()]
param([string]$StandaloneExe, [string]$OutputDirectory, [string]$UpdateConfig, [string]$ConfigPython = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$version = [regex]::Match([IO.File]::ReadAllText((Join-Path $repoRoot 'local_app\server.py')), 'WORKSPACE_VERSION = "([0-9.]+)"').Groups[1].Value
if (-not $version) { throw 'Workspace version not found.' }
if (-not $StandaloneExe) { $StandaloneExe = Join-Path $repoRoot ('dist\Company-Workspace-' + $version + '.exe') }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot ('dist\workspace-release-' + $version) }
$StandaloneExe = (Resolve-Path -LiteralPath $StandaloneExe).Path
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$vbsZip = Join-Path $outputRoot ('Company-Workspace-' + $version + '-vbs.zip')
$exeZip = Join-Path $outputRoot ('Company-Workspace-' + $version + '-exe.zip')
$checksums = Join-Path $outputRoot 'SHA256SUMS.txt'
foreach ($target in @($vbsZip, $exeZip, $checksums)) {
    if (Test-Path -LiteralPath $target) { throw ('Output already exists: ' + $target) }
}
if ([Reflection.AssemblyName]::GetAssemblyName($StandaloneExe).Version.ToString() -ne ($version + '.0')) {
    throw 'Standalone EXE version differs from current source.'
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stage = Join-Path $repoRoot ('build\workspace-release-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage, $outputRoot -Force | Out-Null
& (Join-Path $PSScriptRoot 'New-WorkspaceBundle.ps1') -OutputDirectory (Join-Path $stage 'vbs') -UpdateConfig $UpdateConfig -ConfigPython $ConfigPython | Out-Null
$vbsSource = @(Get-ChildItem -LiteralPath (Join-Path $stage 'vbs') -Filter '*.zip')
if ($vbsSource.Count -ne 1) { throw 'Expected one VBS source bundle.' }
# Read resources only. Loading this local assembly does not run its entry point.
$assembly = [Reflection.Assembly]::LoadFile($StandaloneExe)
$stream = $assembly.GetManifestResourceStream('WorkspacePayload.zip')
if ($null -eq $stream) { throw 'Standalone payload resource is missing.' }
$embedded = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read)
$portable = [IO.Compression.ZipFile]::OpenRead($vbsSource[0].FullName)
$sha = [Security.Cryptography.SHA256]::Create()
$compared = 0
try {
    $embeddedFiles = @{}
    foreach ($entry in $embedded.Entries) { $embeddedFiles[$entry.FullName.Replace('\','/')] = $entry }
    $embeddedFileCount = @($embedded.Entries | Where-Object { $_.Name }).Count
    foreach ($name in $embeddedFiles.Keys) {
        if ($name.StartsWith('Company-Workspace/runtime/', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The EXE contains a Python runtime; rebuild the prerequisite-checking edition.'
        }
    }
    foreach ($entry in $portable.Entries) {
        if (-not $entry.Name) { continue }
        $name = $entry.FullName.Replace('\','/')
        if (-not $embeddedFiles.ContainsKey($name)) { throw ('Missing EXE source: ' + $name) }
        $left = $entry.Open(); $right = $embeddedFiles[$name].Open()
        try {
            $a = [BitConverter]::ToString($sha.ComputeHash($left))
            $b = [BitConverter]::ToString($sha.ComputeHash($right))
            if ($a -ne $b) { throw ('EXE is stale; rebuild before release: ' + $name) }
        } finally { $left.Dispose(); $right.Dispose() }
        $compared++
    }
    if ($compared -lt 1) { throw 'Empty VBS source bundle.' }
    if ($embeddedFileCount -ne $compared) { throw 'EXE contains files outside the VBS application payload.' }
} finally { $portable.Dispose(); $embedded.Dispose(); $stream.Dispose(); $sha.Dispose() }
$exeStage = Join-Path $stage 'exe'
New-Item -ItemType Directory -Path $exeStage -Force | Out-Null
$exeName = 'Company-Workspace-' + $version + '.exe'
Copy-Item -LiteralPath $StandaloneExe -Destination (Join-Path $exeStage $exeName)
$exeHash = (Get-FileHash -LiteralPath $StandaloneExe -Algorithm SHA256).Hash.ToLowerInvariant()
$utf8 = New-Object Text.UTF8Encoding($false)
$releaseGuide = if ($UpdateConfig) { 'Open Settings > App update inside the app for internal update notes and downloads.' } else { 'https://github.com/bongG-moon/workspace_local/releases/tag/v' + $version }
$instructions = @"
Company Workspace $version - single EXE edition

1. Extract this ZIP, then double-click $exeName.
2. Existing Python 3.11 or newer is required. This EXE does not include,
   download or install Python. Missing prerequisites are reported at startup.
3. AI features use the current Windows user's existing Claude Code installation
   and authentication. Claude and Company Agent are not installed by this ZIP.
4. Launching the same or an older version reuses the running app. A newer
   version waits for current work, then closes and restarts normally. Releases
   before 0.21.5 require one confirmation after saving unsent drafts yourself.
   From 0.21.5, drafts are preserved automatically. Review queues and schedules
   after restarting to resume them.
5. Existing account, Windows security policy and personal Claude settings are
   preserved. Company policy may block scripts or executables; this package
   does not bypass those restrictions.

Windows 10/11 x64, Windows PowerShell, .NET Framework 4.6.2+ and the existing
Microsoft Edge WebView2 Evergreen Runtime are required. The dedicated app
window embeds WebView2; it does not open an Edge or Chrome browser window.
This package does not download or install the WebView2 Runtime.
The EXE extracts only its application files to a per-user cache on first run.
Keep the EXE as the entry point; README and hash are for reference.
Existing Python and Claude remain installed and managed separately on the PC.

Korean guide and the alternative VBS ZIP:
$releaseGuide
"@
[IO.File]::WriteAllText((Join-Path $exeStage 'README.txt'), $instructions, $utf8)
[IO.File]::WriteAllText((Join-Path $exeStage ($exeName + '.sha256')), ($exeHash + '  ' + $exeName + "`n"), $utf8)
Copy-Item -LiteralPath $vbsSource[0].FullName -Destination $vbsZip
& $ConfigPython -X utf8 (Join-Path $repoRoot 'scripts\create-workspace-archive.py') --source $exeStage --output $exeZip --contents-only
if ($LASTEXITCODE -ne 0) { throw 'EXE ZIP creation failed. Check the file and retry details above; release checksums were not published.' }
$rows = @($vbsZip, $exeZip) | ForEach-Object {
    (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($_)
}
[IO.File]::WriteAllText($checksums, (($rows -join "`n") + "`n"), $utf8)
$report = [ordered]@{version=$version; vbsZip=$vbsZip; exeZip=$exeZip; checksums=$checksums;
    identicalSourceFiles=$compared; pythonBundled=$false; exeSha256=$exeHash; stage=$stage}
$json = $report | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $repoRoot ('build\workspace-release-' + $version + '.json')), $json, $utf8)
$json
