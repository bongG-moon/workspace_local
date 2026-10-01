[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$report = [ordered]@{
    diagnosticVersion = 'ws33-37'
    targetSource = 'workspace-0.21.10'
    status = 'checking'
    sourceMatches = $false
    files = [ordered]@{}
    identityVerified = $false
    current = $null
    linked = $null
    predictedGuard = 'not_checked'
    uacEnabled = $null
    process64Bit = [Environment]::Is64BitProcess
    stage = 'source_check'
    reason = $null
    nativeCode = $null
    executionPolicy = [ordered]@{ effective=$null; MachinePolicy=$null; UserPolicy=$null; Process=$null; CurrentUser=$null; LocalMachine=$null }
    scriptEvidence = [ordered]@{}
    originalLaunchObserved = $false
    nextStep = 'compare_original_launch_context'
    notTested = @('original_vbs_process', 'original_profile_startup', 'original_launch_failure', 'primary_token_duplication', 'restricted_token_creation', 'child_process_launch', 'claude_or_python')
}
$expected = [ordered]@{
    'deploy/Start-CompanyWorkspace.ps1' = 'a848dd8c031bb6d38932cc7007abe37fa4971e1fc2f3ce0635eb692a209614c0'
    'deploy/CompanyWorkspace.Startup.ps1' = '861ffc26b5d6c52ffeb460bd96d4f3e376ff0cda2a0239b23a2e18bc65738b28'
    'deploy/CompanyWorkspace.NormalToken.cs' = 'fa0798916b9e761b0f54633a2d13263f3a98824a0e10809c9de5c372ec12dae8'
    'deploy/CompanyAgent.UserContext.ps1' = 'a687f50745c3b4e4917fee050be001fa50f7406f7036cc189e21b05de60a8f5f'
}
function Safe-Snapshot($Snapshot, $Context) {
    return [ordered]@{
        sameUser = ($Snapshot.UserSid -eq $Context.sid)
        sameSession = ($Snapshot.SessionId -eq $Context.sessionId)
        elevationType = $Snapshot.ElevationType
        elevated = $Snapshot.IsElevated
        integrity = $Snapshot.IntegrityRid
        administrator = $Snapshot.IsAdministrator
        primary = $Snapshot.IsPrimary
    }
}
function Read-WorkspaceExecutionEvidence {
    # Scope values are allowlisted. No policy setter, profile replay, exception
    # text, profile path/content, Zone URL, or authentication data is exported.
    $allowedPolicies = @('Undefined', 'Restricted', 'AllSigned', 'RemoteSigned', 'Unrestricted', 'Bypass', 'Default')
    try {
        # A parent PowerShell 7 PSModulePath can select incompatible modules in
        # Windows PowerShell 5.1. Load this host's own built-in module only.
        $securityModule = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
        Import-Module -Name $securityModule -ErrorAction Stop | Out-Null
    } catch { # Query failures below stay null; do not modify PSModulePath.
    }
    foreach ($scope in @('effective', 'MachinePolicy', 'UserPolicy', 'Process', 'CurrentUser', 'LocalMachine')) {
        try {
            $value = if ($scope -eq 'effective') { [string](Get-ExecutionPolicy -ErrorAction Stop) }
                     else { [string](Get-ExecutionPolicy -Scope $scope -ErrorAction Stop) }
            if ($value -in $allowedPolicies) { $report.executionPolicy[$scope] = $value }
        } catch { # Unavailable remains null, never an inferred policy failure.
        }
    }
    foreach ($relative in @('Company-Workspace.vbs', 'Check-Workspace.ps1',
                            'deploy/Start-CompanyWorkspace.ps1', 'deploy/CompanyWorkspace.Startup.ps1',
                            'deploy/CompanyAgent.UserContext.ps1', 'local_app/Pick-Path.ps1',
                            'local_app/Invoke-TerminalClaude.ps1')) {
        $evidence = [ordered]@{ exists=$null; downloadMarkPresent=$null; parseStatus='not_checked'; parseErrorCount=$null }
        $path = Join-Path $PSScriptRoot $relative
        try {
            $evidence.exists = [bool](Test-Path -LiteralPath $path -PathType Leaf -ErrorAction Stop)
            if ($evidence.exists) {
                try {
                    $streams = @(Get-Item -LiteralPath $path -Stream * -ErrorAction Stop)
                    $evidence.downloadMarkPresent = [bool]($streams | Where-Object { $_.Stream -eq 'Zone.Identifier' })
                } catch {}
                if ([IO.Path]::GetExtension($path) -eq '.ps1') {
                    $parseTokens = $null; $parseErrors = $null
                    # Parse only: no dot-source or invocation of a candidate file.
                    if ((Get-Item -LiteralPath $path -ErrorAction Stop).Length -gt 524288) {
                        $evidence.parseStatus = 'unavailable'
                    } else {
                        $null = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$parseTokens, [ref]$parseErrors)
                        $evidence.parseErrorCount = [Math]::Min(1000, @($parseErrors).Count)
                        $evidence.parseStatus = if ($evidence.parseErrorCount) { 'invalid' } else { 'valid' }
                    }
                }
            }
        } catch { $evidence.parseStatus = 'unavailable' }
        $report.scriptEvidence[$relative] = $evidence
    }
}
Read-WorkspaceExecutionEvidence
Write-Host 'Workspace 실행·권한 상태를 읽기 전용으로 확인합니다. 영구 정책 변경이나 관리자 권한 요청은 하지 않습니다.'
Write-Host 'Claude와 Workspace 업무 서버는 실행하지 않습니다.'
Write-Host '원래 VBS 종료 코드 1의 원인을 재현하는 검사는 아닙니다. 프로필과 오류 원문은 저장하지 않습니다.'
try {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $allMatch = $true
        foreach ($entry in $expected.GetEnumerator()) {
            $file = Join-Path $PSScriptRoot $entry.Key
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
                $report.files[$entry.Key] = 'missing'
                $allMatch = $false
                continue
            }
            $source = [IO.File]::ReadAllText($file, (New-Object Text.UTF8Encoding($false, $true))).Replace("`r`n", "`n")
            $digest = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($source))).Replace('-', '').ToLowerInvariant()
            $match = $digest -eq $entry.Value
            $report.files[$entry.Key] = $(if ($match) { 'matched' } else { 'different_version' })
            if (-not $match) { $allMatch = $false }
        }
        $report.sourceMatches = $allMatch
    } finally { $hasher.Dispose() }
    if (-not $report.sourceMatches) { throw 'DIAGNOSTIC_SOURCE_MISMATCH' }

    if (('CompanyAgent.WorkspaceNormalToken' -as [type]) -or ('CompanyAgent.SetupUserContextNative' -as [type])) {
        throw 'DIAGNOSTIC_ALREADY_LOADED_TYPE'
    }

    # Only the exact reviewed release helpers are loaded. Never run the launcher.
    . (Join-Path $PSScriptRoot 'deploy/CompanyWorkspace.Startup.ps1')
    $report.stage = 'identity_check'
    $context = Get-WorkspaceVerifiedContext
    $report.identityVerified = [bool]$context.verified
    $report.stage = 'current_token'
    Initialize-WorkspaceNormalTokenApi
    $current = [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken()
    $report.current = Safe-Snapshot $current $context
    $normal = [CompanyAgent.WorkspaceNormalToken]::ValidateNormalProcess($current, $context.sid, $context.sessionId)
    $sourceAllowed = [CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken($current, $context.sid, $context.sessionId)
    $restrictedSource = [CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($current, $context.sid, $context.sessionId, [CompanyAgent.WorkspaceNormalToken]::IsUacDisabled())
    if (-not $context.isAdministrator) {
        $report.predictedGuard = $(if ($normal) { 'normal_process_accepted' } else { 'WS33_current_token_rejected' })
    } elseif ($restrictedSource) {
        $report.predictedGuard = 'restricted_candidate_required_launch_not_tested'
    } elseif (-not $sourceAllowed) {
        $report.predictedGuard = 'WS33_source_not_same_user_split_token'
    } else {
        # Query current and linked tokens only. No primary-token creation,
        # impersonation, privilege change, or process launch is performed.
        $report.stage = 'linked_token'
        $native = [CompanyAgent.WorkspaceNormalToken]
        $flags = [Reflection.BindingFlags]'Static,NonPublic'
        $currentHandle = $null
        $linkedHandle = $null
        try {
            $pseudoHandle = $native.GetMethod('GetCurrentProcess', $flags).Invoke($null, $null)
            $tokenArgs = [object[]]@($pseudoHandle, [uint32]10, $null)
            $opened = $native.GetMethod('OpenProcessToken', $flags).Invoke($null, $tokenArgs)
            $currentHandle = $tokenArgs[2]
            if (-not $opened) {
                $report.nativeCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
                throw 'DIAGNOSTIC_TOKEN_OPEN_FAILED'
            }
            $linkedHandle = $native.GetMethod('ReadLinkedToken', $flags).Invoke($null, [object[]]@($currentHandle))
            $linked = $native.GetMethod('ReadToken', $flags).Invoke($null, [object[]]@($linkedHandle))
            $report.linked = Safe-Snapshot $linked $context
            $linkedAccepted = [CompanyAgent.WorkspaceNormalToken]::ValidateNormalTokenCandidate($linked, $context.sid, $context.sessionId)
            $report.predictedGuard = $(if ($linkedAccepted) { 'linked_candidate_accepted_launch_not_tested' } else { 'WS33_linked_token_not_normal' })
        } finally {
            if ($linkedHandle) { $linkedHandle.Dispose() }
            if ($currentHandle) { $currentHandle.Dispose() }
        }
    }
    $report.status = 'observed'
} catch {
    $failure = $_.Exception
    while ($failure.InnerException) { $failure = $failure.InnerException }
    $reasonProperty = $failure.PSObject.Properties['ReasonCode']
    $codeProperty = $failure.PSObject.Properties['NativeErrorCode']
    if ($reasonProperty -and [string]$reasonProperty.Value -match '^[a-z_]{1,64}$') {
        $report.reason = [string]$reasonProperty.Value
    } elseif ($failure.Message -match '(USER_CONTEXT_[A-Z_]+|WORKSPACE_STARTUP:[0-9]{2}|DIAGNOSTIC_[A-Z_]+)') {
        $report.reason = $Matches[1]
    } else { $report.reason = 'diagnostic_unavailable' }
    if ($codeProperty -and [int]$codeProperty.Value -ge 0 -and [int]$codeProperty.Value -le 65535) {
        $report.nativeCode = [int]$codeProperty.Value
    }
    $report.status = 'incomplete'
}
try {
    $policy = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA
    $report.uacEnabled = [int]$policy.EnableLUA
} catch { $report.uacEnabled = $null }

if (-not $report.sourceMatches) { $report.nextStep = 'use_matching_workspace_bundle' }
elseif (@($report.scriptEvidence.Values | Where-Object { $_.parseStatus -eq 'invalid' }).Count) {
    $report.nextStep = 'replace_invalid_workspace_scripts'
} elseif ($report.predictedGuard -like 'WS33_*' -or $report.reason -like 'USER_CONTEXT_*') {
    $report.nextStep = 'review_current_user_and_token'
} elseif (($report.executionPolicy.MachinePolicy -and $report.executionPolicy.MachinePolicy -ne 'Undefined') -or
          ($report.executionPolicy.UserPolicy -and $report.executionPolicy.UserPolicy -ne 'Undefined')) {
    $report.nextStep = 'review_effective_group_policy'
}

# Only these allowlisted booleans/numbers and fixed reason tags leave memory.
# No account name, SID, original path, auth value, or exception text is saved.
$json = $report | ConvertTo-Json -Depth 5
$reportName = 'Workspace-Diagnostic-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,6) + '.json'
try {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot $reportName), $json, (New-Object Text.UTF8Encoding($false)))
    Write-Host ''
    Write-Host ('결과 파일: ' + $reportName)
    Write-Host '이 파일을 보내 주세요. 프로그램 설치·로그인·보안 설정은 변경하지 않았습니다.'
    if (-not $report.sourceMatches) {
        Write-Host '진단 파일 두 개를 Company-Workspace.vbs 옆에 놓았는지 확인해 주세요. 다른 버전이면 임의 코드를 실행하지 않습니다.'
    }
    Write-Output $json
    exit 0
} catch {
    Write-Host '결과 파일을 저장하지 못했습니다. 아래 내용이 보이도록 화면을 캡처해 보내 주세요.'
    Write-Output $json
    exit 1
}
