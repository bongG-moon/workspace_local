# Loaded only when the launcher or the CLI wrapper observes a failure.
function Test-WorkspaceProfileUtf8 {
    param([string]$Path)
    if ($PSVersionTable.PSVersion.Major -ne 5 -or -not $Path -or
        $Path -notmatch '^[A-Za-z]:[\\/]' -or [IO.Path]::GetExtension($Path) -notin @('.ps1', '.psm1')) { return $false }
    $stream = $null
    try {
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Path))
        if ($drive.DriveType -ne [IO.DriveType]::Fixed) { return $false }
        $stream = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
        $length = $stream.Length
        if ($length -lt 2 -or $length -gt 262144) { return $false }
        $reader = New-Object IO.BinaryReader($stream)
        $bytes = $reader.ReadBytes([int]$length)
        if ($bytes.Length -ne $length -or ($length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)) { return $false }
        $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
        $source = $strictUtf8.GetString($bytes)
        if ($source -notmatch '[^\x00-\x7f]') { return $false }
        # Parse the very same bytes under both interpretations; never execute them.
        $tokens = $null; $nativeErrors = $null; $utf8Errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$nativeErrors)
        if (-not $nativeErrors.Count) { return $false }
        $null = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$utf8Errors)
        return (-not $utf8Errors.Count)
    } catch { return $false }
    finally { if ($stream) { $stream.Dispose() } }
}

function Get-WorkspaceClaudeFailureMarker {
    param($Failure, [string]$Category, [string]$Stage,
          [string]$CommandType = 'Unknown', [bool]$LoadProfiles = $false)
    $line = 0; $encodingHint = 'none'
    if ($CommandType -notin @('Application','ExternalScript','Function')) { $CommandType = 'Unknown' }
    if ($Failure -and $Stage -in @('profile_restore', 'launcher_profile')) {
        $exception = $Failure.Exception
        for ($depth = 0; $exception -and $depth -lt 5; $depth++) {
            if ($exception -is [Management.Automation.ParseException] -and $exception.Errors.Count) {
                $Category = 'profile_parse'
                $extent = $exception.Errors[0].Extent
                $line = [Math]::Max(0, [Math]::Min(9999999, [int]$extent.StartLineNumber))
                if (Test-WorkspaceProfileUtf8 $extent.File) { $encodingHint = 'utf8_without_bom' }
                break
            }
            $exception = $exception.InnerException
        }
    }
    $version = $PSVersionTable.PSVersion
    return ('AXWS_LAUNCH/1|{0}|{1}|{2}.{3}|{4}|{5}|{6}|{7}' -f $Category,$Stage,
        $version.Major,$version.Minor,$CommandType,([int]$LoadProfiles),$line,$encodingHint)
}
