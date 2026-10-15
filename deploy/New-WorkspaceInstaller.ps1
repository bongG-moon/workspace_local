[CmdletBinding()]
param(
    [string]$StandaloneExe,
    [string]$OutputDirectory,
    [string]$NsisCompiler,
    [ValidatePattern('^[a-z0-9-]{1,40}$')][string]$TestIdentity
)
$ErrorActionPreference = 'Stop'
function Get-InstallerDigest([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}
$repoRoot = Split-Path $PSScriptRoot -Parent
$version = [regex]::Match([IO.File]::ReadAllText((Join-Path $repoRoot 'local_app\server.py')), 'WORKSPACE_VERSION = "([0-9.]+)"').Groups[1].Value
if (-not $version) { throw 'Workspace version not found.' }
if (-not $StandaloneExe) { $StandaloneExe = Join-Path $repoRoot ('dist\AX-Workspace-' + $version + '.exe') }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot ('dist\workspace-installer-' + $version) }
$StandaloneExe = (Resolve-Path -LiteralPath $StandaloneExe).Path
if ([Reflection.AssemblyName]::GetAssemblyName($StandaloneExe).Version.ToString() -ne ($version + '.0')) {
    throw 'Standalone EXE version differs from current source.'
}
if (-not $NsisCompiler) { $NsisCompiler = $env:AX_WORKSPACE_NSIS }
if (-not $NsisCompiler) {
    $found = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($found) { $NsisCompiler = $found.Source }
}
if (-not $NsisCompiler) {
    $candidates = @((Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe'), (Join-Path $env:ProgramFiles 'NSIS\makensis.exe'))
    $localTools = Join-Path $repoRoot 'build\nsis'
    if (Test-Path -LiteralPath $localTools) {
        foreach ($pattern in @('makensis.exe', '*\makensis.exe', '*\*\makensis.exe')) {
            $candidates += @(Get-ChildItem -Path (Join-Path $localTools $pattern) -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
        }
    }
    $NsisCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $NsisCompiler) {
    throw 'NSIS 3 compiler not found. Prepare the official portable ZIP or an approved installation, then pass -NsisCompiler <makensis.exe> or set AX_WORKSPACE_NSIS. Nothing was downloaded.'
}
$NsisCompiler = (Resolve-Path -LiteralPath $NsisCompiler).Path
$nsisVersion = (& $NsisCompiler /VERSION | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $nsisVersion -notmatch '^v3\.') { throw 'NSIS 3 Unicode compiler is required.' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$installer = Join-Path $outputRoot ('AX-Workspace-' + $version + '-Setup.exe')
if ($TestIdentity) { $installer = Join-Path $outputRoot ('AX-Workspace-' + $version + '-Test-' + $TestIdentity + '.exe') }
if ((Test-Path -LiteralPath $installer) -or (Test-Path -LiteralPath ($installer + '.sha256'))) { throw ('Output already exists: ' + $installer) }
$stage = Join-Path $repoRoot ('build\installer-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage, $outputRoot -Force | Out-Null
$support = Join-Path $stage 'InstallerSupport.exe'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 ('/out:' + $support) ('/win32manifest:' + (Join-Path $PSScriptRoot 'CompanyWorkspace.Standalone.manifest')) (Join-Path $PSScriptRoot 'Workspace.InstallerSupport.cs')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $support)) { throw 'Installer helper compilation failed.' }
$stagedInstaller = Join-Path $stage ([IO.Path]::GetFileName($installer))
$productId = 'AX.Workspace.Desktop'
$productName = 'AX Workspace'
if ($TestIdentity) { $productId += '.Test.' + $TestIdentity; $productName += ' Installer Test ' + $TestIdentity }
$arguments = @('/NOCONFIG', '/V3', '/INPUTCHARSET', 'UTF8',
    ('/DAPP_VERSION=' + $version), ('/DPRODUCT_ID=' + $productId), ('/DPRODUCT_NAME=' + $productName),
    ('/DSTANDALONE_EXE=' + $StandaloneExe), ('/DSUPPORT_EXE=' + $support),
    ('/DOUTPUT_FILE=' + $stagedInstaller), ('/DAPP_ICON=' + (Join-Path $repoRoot 'local_app\web\app-icon.ico')),
    ('/DINSTALL_KB=' + [math]::Ceiling(((Get-Item -LiteralPath $StandaloneExe).Length + 512KB) / 1KB)),
    (Join-Path $PSScriptRoot 'AX-Workspace.nsi'))
& $NsisCompiler @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stagedInstaller)) { throw 'NSIS installer compilation failed.' }
$fileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($stagedInstaller).FileVersion
if ($fileVersion -ne ($version + '.0')) { throw 'Installer version verification failed.' }
Move-Item -LiteralPath $stagedInstaller -Destination $installer
$hash = Get-InstallerDigest $installer
$report = [ordered]@{version=$version; installer=$installer; sha256=$hash; bytes=(Get-Item -LiteralPath $installer).Length;
    standaloneSha256=(Get-InstallerDigest $StandaloneExe);
    nsisVersion=$nsisVersion; productId=$productId; productName=$productName; testMode=[bool]$TestIdentity;
    perUser=$true; preservesUserData=$true; installsDependencies=$false; stage=$stage}
$utf8 = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText(($installer + '.sha256'), ($hash + '  ' + [IO.Path]::GetFileName($installer) + "`n"), $utf8)
[IO.File]::WriteAllText((Join-Path $repoRoot ('build\workspace-installer-' + $version + '.json')), ($report | ConvertTo-Json), $utf8)
$report | ConvertTo-Json
