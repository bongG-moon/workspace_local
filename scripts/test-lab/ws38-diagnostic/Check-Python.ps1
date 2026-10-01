[CmdletBinding()]
param([string] $PythonCommand = 'auto', [switch] $Demo, [switch] $NoBrowser,
      [string] $StateRoot, [switch] $NormalTokenRelaunch)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
# Same PowerShell profile and production token transition as the app. The only
# Python workload is the pinned launcher's version/standard-library probe.
$toolRoot = $PSScriptRoot
$runId = [Guid]::NewGuid().ToString('N')
$reportRun = $null
$runDirectoryVerified = $false
$stage = 'source_check'
$sourceMatches = $false
$exitCode = 0
$report = [ordered]@{
    diagnosticVersion='ws38-1'; targetWorkspaceVersion='0.21.2'; runId=$runId
    createdUtc=[DateTime]::UtcNow.ToString('o'); status='checking'; sourceMatches=$false
    current=$null; production=$null; transition=$null; failure=$null
    scope='python_prerequisite_only'; originalAppFailureObserved=$false
}

function Write-DiagnosticJson {
    param([string] $Path, [object] $Value)
    $json = $Value | ConvertTo-Json -Depth 12
    $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json)
    if ($bytes.Length -gt 1048576) { throw 'DIAGNOSTIC:report_size' }
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}

function Get-DiagnosticFailure {
    param([object] $Record)
    $exception = $Record.Exception
    $code = $null; $nativeCode = $null; $reason = 'unavailable'
    # Never serialize exception text or stack frames: paths/environment/profile
    # output can be embedded in them. Only fixed categories cross this boundary.
    if ($exception.Message -match 'WORKSPACE_STARTUP:(\d{2})(?:\D|$)') { $code = 'WS-' + $Matches[1] }
    elseif ($exception.Message -match 'USER_CONTEXT_' -and (Get-Command Get-WorkspaceStartupCode -ErrorAction SilentlyContinue)) {
        $code = 'WS-' + (Get-WorkspaceStartupCode -Message $exception.Message)
    }
    elseif ($exception.Message -match '^DIAGNOSTIC:([a-z_]+)$') { $reason = $Matches[1] }
    while ($exception.InnerException) { $exception = $exception.InnerException }
    if ($exception -is [ComponentModel.Win32Exception]) { $nativeCode = $exception.NativeErrorCode }
    return [ordered]@{ status='failed'; code=$code; reason=$reason; nativeCode=$nativeCode }
}

function Assert-DiagnosticSource {
    $manifestPath = Join-Path $toolRoot 'manifest.json'
    if (-not [IO.File]::Exists($manifestPath) -or (Get-Item -LiteralPath $manifestPath).Length -gt 65536) { throw 'DIAGNOSTIC:manifest_missing' }
    $manifest = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($manifest.diagnosticVersion -ne 'ws38-1' -or $manifest.targetWorkspaceVersion -ne '0.21.2') { throw 'DIAGNOSTIC:manifest_version' }
    $files = @('Check-Python.cmd', 'Check-Python.ps1', 'CheckPython.Native.cs', 'README.txt',
        'deploy/CompanyWorkspace.Startup.ps1', 'deploy/CompanyWorkspace.NormalToken.cs',
        'deploy/CompanyAgent.UserContext.ps1', 'deploy/Start-CompanyWorkspace.ps1')
    foreach ($relative in $files) {
        $property = $manifest.sha256.PSObject.Properties[$relative]
        if (-not $property -or [string]$property.Value -notmatch '^[a-f0-9]{64}$') { throw 'DIAGNOSTIC:manifest_entry' }
        $path = Join-Path $toolRoot $relative
        if (-not [IO.File]::Exists($path) -or (Get-Item -LiteralPath $path).Length -gt 1048576) { throw 'DIAGNOSTIC:source_missing' }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = ([BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($path)))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($actual -ne [string]$property.Value) { throw 'DIAGNOSTIC:source_mismatch' }
    }
}

function Get-ProductionFunctionDefinitions {
    $tokens = $null; $errors = $null
    $path = Join-Path $toolRoot 'deploy/Start-CompanyWorkspace.ps1'
    $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if (@($errors).Count) { throw 'DIAGNOSTIC:source_parse' }
    foreach ($name in @('Invoke-WorkspacePythonQuery', 'Get-WorkspacePythonRegistryEntries',
            'Get-WorkspaceRegisteredPythonCandidates', 'Add-WorkspacePythonCheck', 'Get-WorkspacePythonExecutable')) {
        $definitions = @($ast.EndBlock.Statements | Where-Object {
            $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq $name
        })
        if ($definitions.Count -ne 1) { throw 'DIAGNOSTIC:source_function' }
        $definitions[0].Extent.Text
    }
}

function Get-DiagnosticContext {
    param([object] $Context, [string] $Kind)
    $value = [ordered]@{
        kind=$Kind; status='checking'; identityVerified=[bool]$Context.verified
        powershellVersion=$PSVersionTable.PSVersion.ToString(); process64Bit=[Environment]::Is64BitProcess
        token=$null; tokenStatus='unavailable'; nativeToken=$null; nativeTokenStatus='unavailable'
        uacEnabled=$null; pipe=$null; selfDuplicateAccess=$null; duplicatePipeHandle=$null
        productionQuery=[ordered]@{status='not_checked'; code=$null; selectedPython=$null; checks=@()}
        probePython=$null; redirected=$null; unredirected=$null
    }
    try {
        Initialize-WorkspaceNormalTokenApi
        $token = [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken()
        $value.token = [ordered]@{
            sameUser=($token.UserSid -eq $Context.sid); sameSession=($token.SessionId -eq $Context.sessionId)
            elevationType=$token.ElevationType; elevated=$token.IsElevated; integrity=$token.IntegrityRid
            administrator=$token.IsAdministrator; primary=$token.IsPrimary
        }
        $value.tokenStatus = 'accepted'
    } catch { $value.tokenStatus = 'unavailable' }
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
        $key = $null
        try {
            $key = $base.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', $false)
            if ($key) {
                $uac = $key.GetValue('EnableLUA', $null)
                if ($null -ne $uac -and [string]$uac -in @('0', '1')) { $value.uacEnabled = ([int]$uac -eq 1) }
            }
        } finally { if ($key) { $key.Dispose() }; $base.Dispose() }
    } catch {}
    try { $value.nativeToken = [WorkspaceTokenAudit]::CurrentToken(); $value.nativeTokenStatus = 'accepted' } catch {}
    foreach ($method in @('Pipe', 'SelfDuplicateAccess', 'DuplicatePipeHandle')) {
        $result = [ordered]@{status='unavailable'; nativeCode=$null}
        try {
            $nativeCode = switch ($method) {
                'Pipe' { [WorkspaceTokenAudit]::Pipe() }
                'SelfDuplicateAccess' { [WorkspaceTokenAudit]::SelfDuplicateAccess() }
                'DuplicatePipeHandle' { [WorkspaceTokenAudit]::DuplicatePipeHandle() }
            }
            $result.nativeCode = $nativeCode
            $result.status = if ($nativeCode -eq 0) { 'accepted' } else { 'failed' }
        } catch {}
        switch ($method) {
            'Pipe' { $value.pipe = $result }
            'SelfDuplicateAccess' { $value.selfDuplicateAccess = $result }
            'DuplicatePipeHandle' { $value.duplicatePipeHandle = $result }
        }
    }
    $script:WorkspacePythonChecks = @()
    try {
        $selected = Get-WorkspacePythonExecutable -Command $PythonCommand
        $value.productionQuery.selectedPython = $selected
        $value.productionQuery.status = 'accepted'
    } catch {
        $failure = Get-DiagnosticFailure $_
        $value.productionQuery.status = 'failed'; $value.productionQuery.code = $failure.code
    }
    # PS 5.1's array-expression binder can reject List[object]. Enumerate the
    # production check list through the pipeline instead of casting the list.
    $checks = @($script:WorkspacePythonChecks | ForEach-Object { $_ })
    $value.productionQuery.checks = @(ConvertTo-WorkspacePythonChecks -Checks $checks | ForEach-Object {
        # A command/function name or registry argument is never a report path.
        if (-not [IO.Path]::IsPathRooted($_.path) -or [IO.Path]::GetExtension($_.path) -ne '.exe') { $_.path = '' }
        $_
    })
    $candidate = $value.productionQuery.selectedPython
    if (-not $candidate) {
        $candidate = @($value.productionQuery.checks | Where-Object {
            $_.source -ne 'py_launcher' -and $_.path -and [IO.File]::Exists($_.path) -and
            [IO.Path]::GetFileName($_.path) -notin @('py.exe', 'pyw.exe')
        } | Select-Object -First 1 | ForEach-Object { $_.path })
        $candidate = if ($candidate.Count) { $candidate[0] } else { $null }
    }
    if ($candidate) {
        $value.probePython = $candidate
        try { $value.redirected = [WorkspaceTokenAudit]::ProbePython($candidate, $true, 4000) } catch {}
        try { $value.unredirected = [WorkspaceTokenAudit]::ProbePython($candidate, $false, 4000) } catch {}
    }
    $value.status = 'checked'
    return $value
}

try {
    Assert-DiagnosticSource
    $sourceMatches = $true; $report.sourceMatches = $true
    . (Join-Path $toolRoot 'deploy/CompanyWorkspace.Startup.ps1')
    # Definitions only, at script scope. The launcher's main body is never run.
    foreach ($definition in @(Get-ProductionFunctionDefinitions)) { . ([scriptblock]::Create($definition)) }
    Add-Type -Path (Join-Path $toolRoot 'CheckPython.Native.cs') -ErrorAction Stop
    $stage = 'report_directory'
    $runsRoot = Join-Path $toolRoot 'reportRun'
    if ($NormalTokenRelaunch) {
        if (-not $StateRoot) { throw 'DIAGNOSTIC:child_context' }
        $runId = [IO.Path]::GetFileName($StateRoot.TrimEnd('\'))
        if ($runId -notmatch '^[a-f0-9]{32}$') { throw 'DIAGNOSTIC:child_context' }
        $reportRun = [IO.Path]::GetFullPath((Join-Path $runsRoot $runId))
        if ([IO.Path]::GetFullPath($StateRoot) -ne $reportRun -or -not [IO.Directory]::Exists($reportRun)) { throw 'DIAGNOSTIC:child_context' }
    } else {
        if ($StateRoot) { throw 'DIAGNOSTIC:unexpected_state' }
        $null = [IO.Directory]::CreateDirectory($runsRoot)
        if (([IO.File]::GetAttributes($runsRoot) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'DIAGNOSTIC:report_directory' }
        $reportRun = Join-Path $runsRoot $runId
        if ([IO.Directory]::Exists($reportRun)) { throw 'DIAGNOSTIC:run_exists' }
        $null = [IO.Directory]::CreateDirectory($reportRun)
    }
    foreach ($directory in @($runsRoot, $reportRun)) {
        if (([IO.File]::GetAttributes($directory) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'DIAGNOSTIC:report_directory' }
    }
    $runDirectoryVerified = $true
    $report.runId = $runId
    $stage = 'identity'
    $context = Get-WorkspaceVerifiedContext
    if ($NormalTokenRelaunch) {
        $stage = 'normal_guard'
        $null = Get-WorkspaceLaunchAction -Context $context -Relaunched $true
        Assert-WorkspaceNormalProcess -Context $context
        $stage = 'python_probe'
        $result = Get-DiagnosticContext -Context $context -Kind 'production_child'
        Write-DiagnosticJson -Path (Join-Path $reportRun 'child.json') -Value ([ordered]@{
            diagnosticVersion='ws38-1'; runId=$runId; status='checked'; result=$result; failure=$null
        })
        exit 0
    }
    $stage = 'current_probe'
    $report.current = Get-DiagnosticContext -Context $context -Kind 'current_powershell'
    Write-DiagnosticJson -Path (Join-Path $reportRun 'parent.json') -Value $report.current
    $stage = 'normal_transition'
    $action = Get-WorkspaceLaunchAction -Context $context -Relaunched $false
    $report.transition = [ordered]@{action=$action; status='checking'; exitCode=$null}
    if ($action -eq 'run') {
        Assert-WorkspaceNormalProcess -Context $context
        $report.transition.status = 'same_context_already_normal'
        $report.production = $report.current
    } else {
        $childExit = Invoke-WorkspaceNormalTokenRelaunch -Context $context -ScriptPath $PSCommandPath -PythonCommand $PythonCommand -Demo $false -NoBrowser $true -StateRoot $reportRun
        $report.transition.exitCode = $childExit
        $childPath = Join-Path $reportRun 'child.json'
        if ([IO.File]::Exists($childPath) -and (Get-Item -LiteralPath $childPath).Length -le 1048576) {
            $child = [IO.File]::ReadAllText($childPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($child.diagnosticVersion -ne 'ws38-1' -or $child.runId -ne $runId) { throw 'DIAGNOSTIC:child_mismatch' }
            $report.production = $child.result; $report.failure = $child.failure
            $report.transition.status = $child.status
        } else { $report.transition.status = if ($childExit -eq 22) {'timed_out'} else {'child_report_unavailable'} }
        if ($childExit -ne 0 -or $report.transition.status -ne 'checked' -or -not $report.production) {
            $report.status = 'incomplete'; $exitCode = 1
        }
    }
    if ($report.status -eq 'checking') { $report.status = 'checked' }
} catch {
    $exitCode = 1
    $failure = Get-DiagnosticFailure $_
    $report.status = 'incomplete'; $report.failure = [ordered]@{stage=$stage; detail=$failure}
    if ($NormalTokenRelaunch) {
        if ($runDirectoryVerified -and $reportRun -and [IO.Directory]::Exists($reportRun)) {
            try { Write-DiagnosticJson -Path (Join-Path $reportRun 'child.json') -Value ([ordered]@{
                diagnosticVersion='ws38-1'; runId=$runId; status='failed'; result=$null; failure=$report.failure
            }) } catch {}
        }
        exit 1
    }
}

# The only user-facing output path is the final aggregate report. Intermediates
# are isolated per run, never overwritten, and never copied into release ZIPs.
try {
    $finalName = 'Workspace-Python-Diagnostic-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '-' + $runId.Substring(0, 8) + '.json'
    $finalPath = Join-Path $toolRoot $finalName
    Write-DiagnosticJson -Path $finalPath -Value $report
    Write-Host ''
    Write-Host 'Python diagnostic report:'
    Write-Host $finalPath
    Write-Host 'Please send this JSON file to the maintainer. No app or AI task was started.'
} catch {
    Write-Host 'DIAGNOSTIC:report_write_failed'
    $exitCode = 1
}
exit $exitCode
