[CmdletBinding()]
param([string]$SdkPackage)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$lockPath = Join-Path $PSScriptRoot 'WebView2.lock.json'
$sdk = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$cache = Join-Path $repoRoot 'build\desktop-sdk'
$output = Join-Path $repoRoot 'build\desktop-host'
New-Item -ItemType Directory -Path $cache, $output -Force | Out-Null
if (-not $SdkPackage) { $SdkPackage = Join-Path $cache ($sdk.version + '.nupkg') }
if (-not (Test-Path -LiteralPath $SdkPackage)) {
    # Build-time download only. Shipped applications never download a runtime.
    Invoke-WebRequest -UseBasicParsing -Uri $sdk.url -OutFile $SdkPackage
}
if ((Get-FileHash -LiteralPath $SdkPackage -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sdk.sha256) {
    throw 'WebView2 SDK hash mismatch.'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The .NET Framework x64 compiler is required on the build PC.' }
$sourceFiles = @('deploy/Workspace.Desktop.cs','deploy/CompanyWorkspace.Standalone.manifest','deploy/WebView2.lock.json','deploy/New-WorkspaceDesktop.ps1','local_app/web/app-icon.ico')
$sources = [ordered]@{}
foreach ($relative in $sourceFiles) { $sources[$relative] = (Get-FileHash -LiteralPath (Join-Path $repoRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant() }
$sourceJson = $sources | ConvertTo-Json -Compress
$manifestPath = Join-Path $output 'desktop-build.json'
if (Test-Path -LiteralPath $manifestPath) {
    $old = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $valid = (($old.sources | ConvertTo-Json -Compress) -eq $sourceJson) -and ($old.sdkSha256 -eq $sdk.sha256)
    foreach ($file in $old.files.PSObject.Properties) {
        $path = Join-Path $output $file.Name
        if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { $valid = $false }
    }
    if ($valid -and @($old.files.PSObject.Properties).Count -eq 6) { return $output }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$package = [IO.Compression.ZipFile]::OpenRead($SdkPackage)
try {
    foreach ($file in $sdk.files.PSObject.Properties) {
        $entry = $package.GetEntry($file.Value)
        if ($null -eq $entry) { throw ('Missing SDK asset: ' + $file.Value) }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $output $file.Name), $true)
    }
} finally { $package.Dispose() }
$exe = Join-Path $output 'Workspace.Desktop.exe'
$arguments = @('/nologo','/target:winexe','/platform:x64','/optimize+','/codepage:65001',('/out:' + $exe),
    ('/win32manifest:' + (Join-Path $PSScriptRoot 'CompanyWorkspace.Standalone.manifest')),
    ('/win32icon:' + (Join-Path $repoRoot 'local_app\web\app-icon.ico')),
    '/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll',
    ('/reference:' + (Join-Path $output 'Microsoft.Web.WebView2.Core.dll')),
    ('/reference:' + (Join-Path $output 'Microsoft.Web.WebView2.WinForms.dll')),
    (Join-Path $PSScriptRoot 'Workspace.Desktop.cs'))
$compileOutput = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) { throw ($compileOutput -join [Environment]::NewLine) }
$files = [ordered]@{}
foreach ($name in @('Workspace.Desktop.exe') + @($sdk.files.PSObject.Properties.Name)) {
    $files[$name] = (Get-FileHash -LiteralPath (Join-Path $output $name) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$report = [ordered]@{format=1; sdkVersion=$sdk.version; sdkSha256=$sdk.sha256; sources=$sources; files=$files}
[IO.File]::WriteAllText($manifestPath, ($report | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
return $output
