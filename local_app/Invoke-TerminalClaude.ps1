# Deliberately NOT an advanced script: PowerShell common-parameter binding would
# consume Claude's --verbose instead of forwarding it to the existing command.
$ErrorActionPreference = 'Stop'
try {
    if ($args.Count -lt 2 -or $args[0] -ne '-Entry') { throw 'Missing terminal command' }
    $Entry = [string]$args[1]
    $LoadProfiles = $args.Count -gt 2 -and $args[2] -eq '-LoadProfiles'
    $argumentOffset = if ($LoadProfiles) { 3 } else { 2 }
    $CliArguments = @($args | Select-Object -Skip $argumentOffset)
    $workspaceDirectory = Get-Location
    if ($LoadProfiles) {
        # Match the launching PowerShell host, without putting profile banners
        # into the CLI JSON channel. Never load a profile from a different user.
        $profilePaths = @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost,
                          $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost)
        foreach ($profilePath in ($profilePaths | Select-Object -Unique)) {
            if (Test-Path -LiteralPath $profilePath) { . $profilePath *> $null }
        }
        Set-Location -LiteralPath $workspaceDirectory
        $resolvedClaude = Get-Command $Entry -ErrorAction Stop | Select-Object -First 1
        while ($resolvedClaude.CommandType -eq 'Alias') { $resolvedClaude = $resolvedClaude.ResolvedCommand }
        if ($resolvedClaude.CommandType -ne 'Function') { throw 'Terminal function was not restored' }
        $definitionHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $definitionBytes = [Text.Encoding]::UTF8.GetBytes($resolvedClaude.Definition)
            $definitionHash = [BitConverter]::ToString($definitionHasher.ComputeHash($definitionBytes))
            if ($definitionHash -ne $env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH) { throw 'Terminal function has changed' }
        } finally { $definitionHasher.Dispose() }
    }
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
    # The underlying error may contain profile secrets. Leave details in the
    # user's original CLI; do not persist or print them in the local web app.
    [Console]::Error.WriteLine('The existing terminal Claude command could not start. No settings were changed.')
    exit 1
}
