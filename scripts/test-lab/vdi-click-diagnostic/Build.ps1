[CmdletBinding()]
param([string]$OutputDirectory, [string]$SdkPackage)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'build\vdi-click-diagnostic' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$workDirectory = Join-Path $OutputDirectory 'payload'
New-Item -ItemType Directory -Path $workDirectory -Force | Out-Null
$sdk = Get-Content -LiteralPath (Join-Path $repoRoot 'deploy\WebView2.lock.json') -Raw | ConvertFrom-Json
if ($sdk.version -ne '1.0.4258.31') { throw 'Review this diagnostic against the new product WebView2 SDK before rebuilding.' }
if (-not $SdkPackage) { $SdkPackage = Join-Path $repoRoot ('build\desktop-sdk\' + $sdk.version + '.nupkg') }
if (-not (Test-Path -LiteralPath $SdkPackage)) { throw 'Build the regular desktop host once to obtain the pinned SDK, or pass -SdkPackage.' }
if ((Get-FileHash -LiteralPath $SdkPackage -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sdk.sha256) { throw 'WebView2 SDK package hash mismatch.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$package = [IO.Compression.ZipFile]::OpenRead($SdkPackage)
try {
    foreach ($file in $sdk.files.PSObject.Properties) {
        $entry = $package.GetEntry($file.Value)
        if (-not $entry) { throw ('Missing SDK asset: ' + $file.Value) }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $workDirectory $file.Name), $true)
    }
} finally { $package.Dispose() }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$manifest = Join-Path $repoRoot 'deploy\CompanyWorkspace.Standalone.manifest'
$hostExe = Join-Path $workDirectory 'Diagnostic.Host.exe'
$common = @('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',('/win32manifest:' + $manifest),'/reference:System.dll','/reference:System.Core.dll','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll')
$arguments = $common + @('/reference:System.Drawing.dll',('/out:' + $hostExe),('/reference:' + (Join-Path $workDirectory 'Microsoft.Web.WebView2.Core.dll')),('/reference:' + (Join-Path $workDirectory 'Microsoft.Web.WebView2.WinForms.dll')),('/resource:' + (Join-Path $PSScriptRoot 'diagnostic.html') + ',Diagnostic.html'),(Join-Path $PSScriptRoot 'Diagnostic.cs'))
$compileOutput = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) { throw ($compileOutput -join [Environment]::NewLine) }
$hashes = [ordered]@{}
$names = @('Diagnostic.Host.exe') + @($sdk.files.PSObject.Properties.Name)
foreach ($name in $names) { $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $workDirectory $name) -Algorithm SHA256).Hash.ToLowerInvariant() }
$hashPath = Join-Path $workDirectory 'hashes.json'
[IO.File]::WriteAllText($hashPath, ($hashes | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
$exe = Join-Path $OutputDirectory 'AX-Workspace-VDI-Click-Diagnostic-1.0.0.exe'
$arguments = $common + @(('/out:' + $exe),('/resource:' + $hashPath + ',Payload.hashes.json'),(Join-Path $PSScriptRoot 'Bootstrap.cs'))
foreach ($name in $names) { $arguments += '/resource:' + (Join-Path $workDirectory $name) + ',Payload.' + $name }
$compileOutput = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) { throw ($compileOutput -join [Environment]::NewLine) }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.ko.md') -Destination (Join-Path $OutputDirectory 'AX-Workspace-VDI-Click-Diagnostic-README.ko.md') -Force
$report = [ordered]@{format=1; diagnosticVersion='1.0.0'; sdkVersion=$sdk.version; sdkSha256=$sdk.sha256; productManifestSha256=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant(); executableSha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant(); embedded=$hashes; sources=[ordered]@{}}
foreach ($name in @('Build.ps1','Bootstrap.cs','Diagnostic.cs','diagnostic.html','README.ko.md','test-page.mjs')) { $report.sources[$name] = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant() }
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'diagnostic-build.json'), ($report | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
Write-Output $exe
