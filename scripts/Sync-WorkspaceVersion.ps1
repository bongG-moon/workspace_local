[CmdletBinding()]
param([switch]$Check)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $repoRoot 'deploy/Workspace.Version.ps1')
$version = Sync-WorkspaceVersion -RepoRoot $repoRoot -Check:$Check
if ($Check) { Write-Output ('Workspace version metadata verified: ' + $version) }
else { Write-Output ('Workspace version metadata synchronized: ' + $version + '. Review the diff and update release notes before building.') }
