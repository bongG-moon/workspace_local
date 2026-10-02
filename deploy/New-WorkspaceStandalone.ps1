[CmdletBinding()]
param([string]$OutputDirectory, [string]$VerificationDirectory, [string]$UpdateConfig, [string]$ConfigPython = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$utf8 = New-Object Text.UTF8Encoding($false)
$versionMatch = [regex]::Match([IO.File]::ReadAllText((Join-Path $repoRoot 'local_app\server.py')), 'WORKSPACE_VERSION = "([0-9.]+)"')
if (-not $versionMatch.Success) { throw 'Workspace version not found.' }
$version = $versionMatch.Groups[1].Value
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'dist' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$exe = Join-Path $outputRoot ('Company-Workspace-' + $version + '.exe')
if (Test-Path -LiteralPath $exe) { throw 'Output EXE exists; choose another output directory instead of overwriting a running release.' }
$stage = Join-Path $repoRoot ('build\standalone-build-' + [Guid]::NewGuid().ToString('N'))
$bundleDirectory = Join-Path $stage 'bundle'
New-Item -ItemType Directory -Path $bundleDirectory -Force | Out-Null
& (Join-Path $PSScriptRoot 'New-WorkspaceBundle.ps1') -OutputDirectory $bundleDirectory -UpdateConfig $UpdateConfig -ConfigPython $ConfigPython | Out-Null
$bundle = @(Get-ChildItem -LiteralPath $bundleDirectory -Filter '*.zip')
if ($bundle.Count -ne 1) { throw 'Expected exactly one source bundle.' }
# Reuse the verified bundle directly. Extracting and recompressing another
# temporary tree repeats file-lock exposure without changing the payload.
$payloadZip = $bundle[0].FullName
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($payloadZip)
$sha = [Security.Cryptography.SHA256]::Create()
$names = @{}
try {
    $manifestRows = @(foreach ($entry in ($archive.Entries | Sort-Object FullName)) {
        if (-not $entry.Name) { continue }
        $relative = $entry.FullName.Replace('\','/')
        if (-not $relative.StartsWith('Company-Workspace/', [StringComparison]::Ordinal) -or
            $relative -match '(^|/)\.\.?(/|$)|[\x00-\x1f:]' -or $names.ContainsKey($relative)) {
            throw 'Invalid or duplicate application ZIP path.'
        }
        if ($relative.StartsWith('Company-Workspace/runtime/', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Application-only EXE must not contain an interpreter runtime.'
        }
        $names[$relative] = $true
        $entryStream = $entry.Open()
        try {
            ([BitConverter]::ToString($sha.ComputeHash($entryStream)).Replace('-','').ToLowerInvariant() + "`t" + $relative)
        } finally { $entryStream.Dispose() }
    })
    if ($manifestRows.Count -lt 1) { throw 'Empty application ZIP.' }
} finally { $sha.Dispose(); $archive.Dispose() }
$manifest = Join-Path $stage 'WorkspacePayload.manifest.tsv'
[IO.File]::WriteAllText($manifest, (($manifestRows -join "`n") + "`n"), $utf8)
$payloadHash = (Get-FileHash -LiteralPath $payloadZip -Algorithm SHA256).Hash.ToLowerInvariant()
$buildInfo = Join-Path $stage 'WorkspaceBuild.txt'
[IO.File]::WriteAllText($buildInfo, ($version + "`n" + $payloadHash + "`n"), $utf8)
$assembly = Join-Path $stage 'WorkspaceAssembly.cs'
[IO.File]::WriteAllText($assembly, ('using System.Reflection; [assembly: AssemblyTitle("Company Workspace")] [assembly: AssemblyProduct("Company Workspace")] [assembly: AssemblyVersion("' + $version + '.0")] [assembly: AssemblyFileVersion("' + $version + '.0")]'), $utf8)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4 x64 C# compiler is required to build the EXE.' }
$stagedExe = Join-Path $stage ([IO.Path]::GetFileName($exe))
$compileArgs = @('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',
    ('/out:' + $stagedExe),
    ('/win32icon:' + (Join-Path $repoRoot 'local_app\web\app-icon.ico')),
    ('/win32manifest:' + (Join-Path $PSScriptRoot 'CompanyWorkspace.Standalone.manifest')),
    '/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll','/reference:System.Windows.Forms.dll',
    ('/resource:' + $payloadZip + ',WorkspacePayload.zip'),
    ('/resource:' + $manifest + ',WorkspacePayload.manifest.tsv'),
    ('/resource:' + $buildInfo + ',WorkspaceBuild.txt'),
    (Join-Path $PSScriptRoot 'CompanyWorkspace.Standalone.cs'), $assembly)
& $compiler @compileArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stagedExe)) { throw 'Standalone EXE compilation failed.' }
# Exercise the exact embedded production archive, not only synthetic ZIPs.
# This prepares files only: no launcher, Claude, user state or settings are used.
$verifyCache = if ($VerificationDirectory) { [IO.Path]::GetFullPath($VerificationDirectory) } else { Join-Path $stage 'extraction-check' }
$verified = Start-Process -FilePath $stagedExe -ArgumentList @('--verify-only','--cache-root',('"' + $verifyCache + '"')) -WindowStyle Hidden -PassThru -Wait
if ($verified.ExitCode -ne 0) { throw ('Embedded payload verification failed: ' + $verified.ExitCode) }
Move-Item -LiteralPath $stagedExe -Destination $exe -ErrorAction Stop
$exeHash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($exe + '.sha256'), ($exeHash + '  ' + [IO.Path]::GetFileName($exe) + "`n"), $utf8)
$report = [ordered]@{ version=$version; exe=$exe; sha256=$exeHash; bytes=(Get-Item -LiteralPath $exe).Length; payloadSha256=$payloadHash; payloadFiles=$manifestRows.Count; embeddedPayloadVerified=$true; pythonBundled=$false; pythonRequirement='Existing Python 3.11 or later'; installsDependencies=$false; downloadsDependencies=$false; stage=$stage; existingVbsBundleModified=$false }
$reportPath = Join-Path $repoRoot ('build\workspace-standalone-' + $version + '-build.json')
[IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 4), $utf8)
$report | ConvertTo-Json -Depth 4
