# Deliberately NOT an advanced script: PowerShell common-parameter binding would
# consume Claude's --verbose instead of forwarding it to the existing command.
$ErrorActionPreference = 'Stop'
$failureCategory = 'cli_start'
$failureStage = 'process_start'
$commandType = 'Unknown'
$LoadProfiles = $false
try {
    if ($args.Count -lt 2 -or $args[0] -ne '-Entry') { throw 'Missing terminal command' }
    $Entry = [string]$args[1]
    $LoadProfiles = $args.Count -gt 2 -and $args[2] -eq '-LoadProfiles'
    $argumentOffset = if ($LoadProfiles) { 3 } else { 2 }
    $configOverride = $null
    if ($args.Count -gt ($argumentOffset + 1) -and $args[$argumentOffset] -ceq '-AxConfigDir') {
        $configOverride = $args[$argumentOffset + 1]
        $argumentOffset += 2
    }
    $CliArguments = @($args | Select-Object -Skip $argumentOffset)
    $workspaceDirectory = Get-Location
    if ($LoadProfiles) {
        $commandType = 'Function'
        $failureCategory = 'profile_load'
        $failureStage = 'profile_restore'
        # Match the launching PowerShell host, without putting profile banners
        # into the CLI JSON channel. Never load a profile from a different user.
        $profilePaths = @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost,
                          $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost)
        foreach ($profilePath in ($profilePaths | Select-Object -Unique)) {
            if (Test-Path -LiteralPath $profilePath) { . $profilePath *> $null }
        }
        Set-Location -LiteralPath $workspaceDirectory
        $failureCategory = 'command_not_found'
        $failureStage = 'command_resolution'
        $resolvedClaude = Get-Command $Entry -ErrorAction Stop | Select-Object -First 1
        $aliasDepth = 0
        while ($resolvedClaude -and $resolvedClaude.CommandType -eq 'Alias') {
            if (++$aliasDepth -gt 16) { throw 'Terminal alias resolution failed' }
            $resolvedClaude = $resolvedClaude.ResolvedCommand
        }
        $failureCategory = 'function_changed'
        $failureStage = 'function_restore'
        if ($resolvedClaude.CommandType -ne 'Function') { throw 'Terminal function was not restored' }
        $definitionHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $definitionBytes = [Text.Encoding]::UTF8.GetBytes($resolvedClaude.Definition)
            $definitionHash = [BitConverter]::ToString($definitionHasher.ComputeHash($definitionBytes))
            if ($definitionHash -ne $env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH) { throw 'Terminal function has changed' }
        } finally { $definitionHasher.Dispose() }
    }
    elseif ([IO.Path]::GetExtension($Entry) -eq '.ps1') { $commandType = 'ExternalScript' }
    else { $commandType = 'Application' }
    if ($configOverride) {
        $failureCategory = 'config_root'
        if (-not [IO.Directory]::Exists($configOverride)) { throw 'Configured directory unavailable' }
        $env:CLAUDE_CONFIG_DIR = $configOverride
    }
    $failureCategory = 'cli_start'
    $failureStage = 'process_start'
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $global:LASTEXITCODE = 0
    # Arguments are an array, never an evaluated command string. Prompts and
    # control responses stay on stdin, including while waiting for approvals.
    & $Entry @CliArguments
    if (-not $?) { exit 1 }
    exit $LASTEXITCODE
} catch {
    $failure = $_
    try {
        . (Join-Path $PSScriptRoot 'ClaudeLaunchDiagnostic.ps1')
        $marker = Get-WorkspaceClaudeFailureMarker -Failure $failure -Category $failureCategory -Stage $failureStage -CommandType $commandType -LoadProfiles $LoadProfiles
        [Console]::Error.WriteLine($marker)
    } catch {}
    exit 78
}
