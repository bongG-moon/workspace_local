# Build-time version synchronization. The only editable version is
# WORKSPACE_VERSION in local_app/server.py. This file is never loaded by the app.
function Sync-WorkspaceVersion {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$RepoRoot, [switch]$Check)
    $root = [IO.Path]::GetFullPath($RepoRoot)
    $server = [IO.File]::ReadAllText((Join-Path $root 'local_app/server.py'))
    $canonical = [regex]::Matches($server, '(?m)^WORKSPACE_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$')
    if ($canonical.Count -ne 1) { throw 'Expected exactly one WORKSPACE_VERSION in local_app/server.py.' }
    $version = $canonical[0].Groups[1].Value
    # Each entry owns only the captured literal, not its surrounding source.
    $rules = @(
        @{file='deploy/Start-CompanyWorkspace.ps1'; pattern="-CurrentVersion '(?<value>[0-9.]+)'"; count=1},
        @{file='deploy/Start-CompanyWorkspace.ps1'; pattern="\`$targetVersion = \[version\]'(?<value>[0-9.]+)'"; count=1},
        @{file='deploy/CompanyWorkspace.Startup.ps1'; pattern="workspaceVersion='(?<value>[0-9.]+)'"; count=1},
        @{file='deploy/CompanyWorkspace.Startup.ps1'; pattern="\`$report.workspaceVersion -ne '(?<value>[0-9.]+)'"; count=1},
        @{file='Check-Workspace.ps1'; pattern="targetSource = 'workspace-(?<value>[0-9.]+)'"; count=1}
    )
    $original = @{}; $updated = @{}; $bom = @{}
    foreach ($rule in $rules) {
        if (-not $original.ContainsKey($rule.file)) {
            $path = Join-Path $root $rule.file
            $bytes = [IO.File]::ReadAllBytes($path)
            $bom[$rule.file] = $bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191
            $original[$rule.file] = [IO.File]::ReadAllText($path)
            $updated[$rule.file] = $original[$rule.file]
        }
        $text = $updated[$rule.file]
        $matches = [regex]::Matches($text, $rule.pattern)
        if ($matches.Count -ne $rule.count) { throw ('Version template changed: ' + $rule.file + '. Review its synchronization rule.') }
        for ($index = $matches.Count - 1; $index -ge 0; $index--) {
            $value = $matches[$index].Groups['value']
            $text = $text.Remove($value.Index, $value.Length).Insert($value.Index, $version)
        }
        $updated[$rule.file] = $text
    }
    # Refresh the diagnostic fingerprint from the generated source, preserving
    # the same BOM/CRLF normalization used by the read-only startup diagnostic.
    $diagnostic = $updated['Check-Workspace.ps1']
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($relative in @('deploy/Start-CompanyWorkspace.ps1', 'deploy/CompanyWorkspace.Startup.ps1',
                                'deploy/CompanyWorkspace.NormalToken.cs', 'deploy/CompanyAgent.UserContext.ps1')) {
            $source = if ($updated.ContainsKey($relative)) { $updated[$relative] } else { [IO.File]::ReadAllText((Join-Path $root $relative)) }
            $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($source.Replace("`r`n", "`n")))).Replace('-', '').ToLowerInvariant()
            $pattern = "'" + [regex]::Escape($relative) + "'\s*=\s*'(?<value>[a-f0-9]{64})'"
            $matches = [regex]::Matches($diagnostic, $pattern)
            if ($matches.Count -ne 1) { throw ('Diagnostic fingerprint template changed: ' + $relative) }
            $value = $matches[0].Groups['value']
            $diagnostic = $diagnostic.Remove($value.Index, $value.Length).Insert($value.Index, $digest)
        }
    } finally { $sha.Dispose() }
    $updated['Check-Workspace.ps1'] = $diagnostic
    $changed = @($updated.Keys | Where-Object { $original[$_] -cne $updated[$_] } | Sort-Object)
    if ($Check) {
        if ($changed.Count) {
            throw ('Generated version or diagnostic metadata is stale: ' + ($changed -join ', ') + '. Run scripts/Sync-WorkspaceVersion.ps1 before building.')
        }
        $notes = [IO.File]::ReadAllText((Join-Path $root 'deploy/workspace-release-notes.json')) | ConvertFrom-Json
        if ($notes.currentVersion -cne $version -or @($notes.releases | Where-Object { $_.version -ceq $version }).Count -ne 1) {
            throw ('Release notes do not contain current version ' + $version + '. Run scripts/write-workspace-release-notes.py with this version and the reviewed notes.')
        }
    } else {
        # Validate every template before any write. Keep original encoding and
        # line endings; unrelated edits and historical notes are not generated.
        foreach ($relative in $changed) {
            [IO.File]::WriteAllText((Join-Path $root $relative), $updated[$relative], (New-Object Text.UTF8Encoding([bool]$bom[$relative])))
        }
    }
    return $version
}
