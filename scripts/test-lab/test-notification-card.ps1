[CmdletBinding()]
param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'build\qa-notification-card' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$hostDirectory = & (Join-Path $repoRoot 'deploy\New-WorkspaceDesktop.ps1')
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$testExe = Join-Path $OutputDirectory 'TestNotificationCard.exe'
$source = Join-Path $PSScriptRoot 'test-notification-card.cs'
$arguments = @('/nologo','/target:exe','/platform:x64','/codepage:65001',('/out:' + $testExe),
    '/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll','/reference:Accessibility.dll',$source)
$compileOutput = & $compiler @arguments 2>&1
if ($LASTEXITCODE -ne 0) { throw ($compileOutput -join [Environment]::NewLine) }
& $testExe (Join-Path $hostDirectory 'Workspace.Desktop.exe') $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw 'Native notification card checks failed.' }
