[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$UpdateConfig,
    [string]$OutputDirectory,
    [string]$ConfigPython = 'python'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$version = [regex]::Match([IO.File]::ReadAllText((Join-Path $repoRoot 'local_app\server.py')), 'WORKSPACE_VERSION = "([0-9.]+)"').Groups[1].Value
if (-not $version) { throw 'Workspace version not found.' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot ('dist\workspace-online-installer-' + $version) }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$installer = Join-Path $outputRoot 'AX-Workspace-Install.exe'
if ((Test-Path -LiteralPath $installer) -or (Test-Path -LiteralPath ($installer + '.sha256'))) {
    throw ('Output already exists: ' + $installer)
}
$stage = Join-Path $repoRoot ('build\online-installer-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$configPayload = Join-Path $stage 'WorkspaceUpdateSource.json'
& $ConfigPython -X utf8 (Join-Path $repoRoot 'scripts\prepare-workspace-update-source.py') --input $UpdateConfig --output $configPayload
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $configPayload)) {
    throw 'A valid credential-free GitLab update source is required. No installer was created.'
}
$identityScript = 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from local_app.update_source import from_bytes; print(from_bytes(Path(sys.argv[2]).read_bytes()).identity)'
$sourceIdentity = (& $ConfigPython -X utf8 -c $identityScript $repoRoot $configPayload | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceIdentity -notmatch '^[a-f0-9]{64}$') { throw 'Could not verify GitLab source identity.' }
$utf8 = New-Object Text.UTF8Encoding($false)
$assemblyInfo = Join-Path $stage 'OnlineInstallerAssembly.cs'
[IO.File]::WriteAllText($assemblyInfo, ('using System.Reflection; using System.Runtime.Versioning; ' +
    '[assembly: AssemblyTitle("AX Workspace latest installer")] [assembly: AssemblyProduct("AX Workspace")] ' +
    '[assembly: AssemblyVersion("' + $version + '.0")] [assembly: AssemblyFileVersion("' + $version + '.0")] ' +
    '[assembly: TargetFramework(".NETFramework,Version=v4.6.2")]'), $utf8)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4 x64 C# compiler is required.' }
$stagedInstaller = Join-Path $stage 'AX-Workspace-Install.exe'
$arguments = @('/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
    ('/out:' + $stagedInstaller),
    ('/win32icon:' + (Join-Path $repoRoot 'local_app\web\app-icon.ico')),
    ('/win32manifest:' + (Join-Path $PSScriptRoot 'CompanyWorkspace.Standalone.manifest')),
    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
    '/reference:System.Core.dll',
    ('/resource:' + $configPayload + ',WorkspaceUpdateSource.json'),
    (Join-Path $PSScriptRoot 'Workspace.OnlineInstaller.cs'), $assemblyInfo)
& $compiler @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stagedInstaller)) { throw 'Online installer compilation failed.' }
if ([Reflection.AssemblyName]::GetAssemblyName($stagedInstaller).Version.ToString() -ne ($version + '.0')) {
    throw 'Online installer version verification failed.'
}
# Reading manifest resources does not execute the installer or contact GitLab.
$assembly = [Reflection.Assembly]::Load([IO.File]::ReadAllBytes($stagedInstaller))
$stream = $assembly.GetManifestResourceStream('WorkspaceUpdateSource.json')
if ($null -eq $stream) { throw 'Online installer update source is missing.' }
$reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
try { $embedded = $reader.ReadToEnd() } finally { $reader.Dispose(); $stream.Dispose() }
if ($embedded -cne [IO.File]::ReadAllText($configPayload, [Text.Encoding]::UTF8)) {
    throw 'Online installer update source differs from the validated configuration.'
}
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
Move-Item -LiteralPath $stagedInstaller -Destination $installer -ErrorAction Stop
$inputStream = [IO.File]::OpenRead($installer)
$sha = [Security.Cryptography.SHA256]::Create()
try { $digest = [BitConverter]::ToString($sha.ComputeHash($inputStream)).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose(); $inputStream.Dispose() }
$report = [ordered]@{schema=1; version=$version; installer=$installer; sha256=$digest;
    bytes=(Get-Item -LiteralPath $installer).Length; updateSource=($embedded | ConvertFrom-Json);
    sourceIdentity=$sourceIdentity; productId='AX.Workspace.OnlineInstaller'; protocol=1;
    embeddedConfigVerified=$true; appPayloadBundled=$false; pythonRequiredToInstall=$false; stage=$stage}
[IO.File]::WriteAllText(($installer + '.sha256'), ($digest + '  AX-Workspace-Install.exe' + "`n"), $utf8)
$json = $report | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $repoRoot ('build\workspace-online-installer-' + $version + '.json')), $json, $utf8)
$json
