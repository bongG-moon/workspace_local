# Private command protocol. User requests and CLI control frames stay on stdin.
# Do not add CmdletBinding: Claude's --verbose must remain a CLI argument.
$ErrorActionPreference = 'Stop'
$failureStage = 'profile_restore'
$failureCategory = 'profile_load'
$kind = 'Unknown'
try {
    $discover = $args.Count -eq 1 -and $args[0] -ceq '-Discover'
    if (-not $discover -and ($args.Count -lt 5 -or $args[0] -cne '-Entry')) { throw 'Invalid invocation' }
    $originalArgs = @($args)
    $work = Get-Location
    $explicitEntry = $env:COMPANY_AGENT_CLAUDE
    $previousOut = [Console]::Out
    $previousError = [Console]::Error
    try {
        # Resolve only in the user's shell profile context, never from a task.
        Set-Location -LiteralPath ([Environment]::GetFolderPath('UserProfile'))
        [Console]::SetOut([IO.TextWriter]::Null)
        [Console]::SetError([IO.TextWriter]::Null)
        $profiles = @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost,
                      $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost)
        foreach ($profilePath in ($profiles | Select-Object -Unique)) {
            if (Test-Path -LiteralPath $profilePath -PathType Leaf) { . $profilePath *> $null }
        }
    } finally {
        [Console]::SetOut($previousOut)
        [Console]::SetError($previousError)
    }
    $failureStage = 'command_resolution'
    $failureCategory = 'command_not_found'
    if (-not $explicitEntry) { $explicitEntry = $env:COMPANY_AGENT_CLAUDE }
    if ($explicitEntry) {
        # An explicitly configured launcher must take precedence over a PATH result.
        if (-not [IO.Path]::IsPathRooted($explicitEntry) -or -not [IO.File]::Exists($explicitEntry)) {
            throw 'Explicit command unavailable'
        }
        $commandInfo = Get-Command -Name ([Management.Automation.WildcardPattern]::Escape($explicitEntry)) -ErrorAction Stop | Select-Object -First 1
    } else {
        $commandInfo = Get-Command claude -ErrorAction Stop | Select-Object -First 1
    }
    $depth = 0
    while ($commandInfo -and $commandInfo.CommandType -eq 'Alias') {
        if (++$depth -gt 16) { throw 'Alias unavailable' }
        $commandInfo = $commandInfo.ResolvedCommand
    }
    if (-not $commandInfo) { throw 'Command unavailable' }
    $kind = $commandInfo.CommandType.ToString()
    $identity = if ($env:CLAUDE_CODE_USE_COWORK_PLUGINS) { 'coworkFile' } else { 'file' }
    if ($kind -eq 'Function') {
        $entry = 'claude'
        $hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes($commandInfo.Definition)
            $identity = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-', '')
        } finally { $hasher.Dispose() }
    } elseif ($kind -in @('Application', 'ExternalScript')) {
        $entry = $commandInfo.Path
        if (-not [IO.Path]::IsPathRooted($entry)) { throw 'Command path unavailable' }
    } else { throw 'Unsupported command' }
    $root = $env:CLAUDE_CONFIG_DIR
    if (-not $root) {
        $userDirectory = if ($env:USERPROFILE) { $env:USERPROFILE } else { [Environment]::GetFolderPath('UserProfile') }
        $root = Join-Path $userDirectory '.claude'
    }
    # The inspector, session imports and every task need the same profile.
    # PS 5.1's IsPathRooted also accepts C:relative and \drive-relative.
    $failureCategory = 'config_root'
    if ($root -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
        throw 'Workspace-dependent settings root'
    }
    $root = [IO.Path]::GetFullPath($root)
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    if ($discover) {
        # Never export the process environment, the profile source or its output.
        $metadata = @{entry=$entry;commandType=$kind;identity=$identity;configRoot=$root;shellVersion=$PSVersionTable.PSVersion.ToString()}
        [Console]::WriteLine(($metadata | ConvertTo-Json -Compress))
        exit 0
    }
    if ($originalArgs[4] -ne $root) { throw 'Configuration root changed' }
    $failureCategory = 'command_not_found'
    if ($kind -eq 'Function') { $failureStage = 'function_restore'; $failureCategory = 'function_changed' }
    if ($originalArgs[1] -ne $entry -or $originalArgs[2] -cne $kind -or $originalArgs[3] -cne $identity) {
        throw 'Command context changed'
    }
    $cliArgs = @($originalArgs | Select-Object -Skip 5)
    Set-Location -LiteralPath $work
    $failureStage = 'process_start'
    $failureCategory = 'cli_start'
    $global:LASTEXITCODE = 0
    & $commandInfo @cliArgs
    if (-not $?) { exit 1 }
    exit $LASTEXITCODE
} catch {
    $failure = $_
    . (Join-Path $PSScriptRoot 'ClaudeLaunchDiagnostic.ps1')
    $marker = Get-WorkspaceClaudeFailureMarker -Failure $failure -Stage $failureStage -Category $failureCategory -CommandType $kind -LoadProfiles $true
    [Console]::Error.WriteLine($marker)
    exit 78
}
