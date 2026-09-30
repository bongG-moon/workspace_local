[CmdletBinding()]
param([string]$DesktopExecutable, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'build\qa-desktop-manual' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $DesktopExecutable) {
    $hostDirectory = & (Join-Path $repoRoot 'deploy\New-WorkspaceDesktop.ps1')
    $DesktopExecutable = Join-Path $hostDirectory 'Workspace.Desktop.exe'
}
$DesktopExecutable = [IO.Path]::GetFullPath($DesktopExecutable)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$testExe = Join-Path $OutputDirectory 'TestDesktopManual.exe'
$arguments = @('/nologo','/target:exe','/platform:x64','/codepage:65001',('/out:' + $testExe),
    '/reference:System.dll','/reference:System.Core.dll',
    (Join-Path $PSScriptRoot 'test-desktop-manual.cs'))
$compileOutput = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) { throw ($compileOutput -join [Environment]::NewLine) }
& $testExe $DesktopExecutable
if ($LASTEXITCODE -ne 0) { throw 'Native desktop manual URL policy checks failed.' }
