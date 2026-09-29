# Workspace startup policy only. No credentials, settings, UAC, or permissions
# are changed. The installer identity helper is read-only here; its allowance
# of same-user elevation does NOT authorize an elevated Workspace process.
# UAC-disabled starts may use a separately restricted, verified normal token;
# the source token and Windows policy are never changed.
function Get-WorkspaceStartupMessage {
    param([int] $Code)
    switch ($Code) {
        22 { return '실행 확인 시간이 초과되었습니다. 창이 이미 열렸을 수 있으므로 반복 실행하지 말고 확인해 주세요. 진행 중인 작업은 강제로 종료하지 않았습니다.' }
        30 { return '현재 Windows 로그인 사용자를 확인하지 못했습니다. 본인 계정의 바탕화면에서 다시 실행해 주세요. 계속되면 담당자에게 알려 주세요. 설정은 변경하지 않았습니다.' }
        31 { return '로그인한 사용자와 실행 계정이 다르거나 서비스 환경에서 실행되었습니다. 본인 계정의 바탕화면에서 실행해 주세요. 다른 사용자의 설정은 사용하지 않았습니다.' }
        32 { return 'Windows 사용자 폴더와 실행 환경의 폴더가 일치하지 않습니다. 담당자에게 실행 환경 확인을 요청해 주세요. 기존 Claude 설정은 변경하지 않았습니다.' }
        33 { return '같은 계정의 일반 권한 실행 환경을 확인하지 못했습니다. 일반 권한 연결 토큰 또는 제한된 실행 토큰이 검증되지 않았습니다. Check-Workspace.cmd로 진단 결과를 확인해 주세요. PC 보안 설정과 개인 Claude 설정은 바꾸지 않았습니다.' }
        34 { return '일반 권한으로 다시 실행한 뒤에도 관리자 권한으로 감지되어 중단했습니다. 재시도는 한 번만 수행하며 관리자 권한으로 AI를 실행하지 않습니다.' }
        35 { return '일반 권한 실행 요청을 Windows가 처리하지 못했습니다. 담당자에게 실행 정책을 확인해 달라고 요청해 주세요. 다른 계정이나 관리자 권한으로 대신 실행하지 않았습니다.' }
        36 { return '이 실행 환경에서 기존 Claude Code 명령을 찾거나 실행 경로를 확인하지 못했습니다. 평소 Claude가 동작하는 Windows PowerShell 환경인지 확인해 주세요. 로그인과 모델 설정은 변경하지 않았습니다.' }
        37 { return '이 실행 환경에서 Python을 찾거나 시작하지 못했습니다. 회사에서 승인한 Python 실행 경로를 확인해 주세요. 자동 설치나 PATH 변경은 하지 않았습니다.' }
        38 { return 'Python 실행 확인에 실패했거나 버전이 3.11 미만입니다. 회사에서 승인한 Python 3.11 이상을 사용해 주세요. 기존 설치는 변경하지 않았습니다.' }
        39 { return '이전 버전 또는 다른 폴더의 Workspace 연결이 남아 있거나 종료 처리 중입니다. 실행 중인 창에서 [설정 → 앱 종료]를 선택하고 종료가 끝난 뒤 다시 실행해 주세요. 창의 X 버튼은 창만 닫고 연결은 유지합니다. 진행 중인 작업은 강제로 종료하지 않았습니다.' }
        40 { return 'Workspace 창을 여는 데 필요한 서버 준비를 확인하지 못했습니다. 창이 열렸는지 먼저 확인해 주세요. 담당자에게 실행 경로와 오류 코드를 전달해 주세요. 기존 Claude 설정은 변경하지 않았습니다.' }
        41 { return 'Workspace 실행 파일이 누락되었거나 읽을 수 없습니다. ZIP 전체를 새 폴더에 압축 해제한 뒤 실행해 주세요. 파일 한 개만 복사하면 실행할 수 없습니다.' }
        42 { return '일반 권한 실행에 전달할 경로 또는 인수가 너무 길거나 유효하지 않습니다. 압축 해제한 Workspace 폴더와 지정한 경로를 확인해 주세요. 값을 잘라서 실행하지 않았습니다.' }
        default { return 'Workspace 실행을 완료하지 못했습니다. ZIP 전체를 압축 해제했는지 확인하고, 담당자에게 아래 오류 코드를 전달해 주세요. 기존 로그인과 개인 설정은 변경하지 않았습니다.' }
    }
}

function Get-WorkspaceStartupCode {
    param([string] $Message)
    if ($Message -match 'WORKSPACE_STARTUP:(\d{2})(?:\D|$)') { return [int]$Matches[1] }
    if ($Message -match 'USER_CONTEXT_(DIFFERENT_ACCOUNT|NONINTERACTIVE)') { return 31 }
    if ($Message -match 'USER_CONTEXT_(PATH_MISMATCH|INVALID_PATH)') { return 32 }
    if ($Message -match 'USER_CONTEXT_') { return 30 }
    return 45
}

function Initialize-WorkspaceStartupDisplay {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    # Startup errors need the same scaling as the file picker. This helper is
    # optional here: a missing/damaged install must still show its warning.
    try {
        if (-not ('WorkspacePicker.DisplayScaling' -as [type])) {
            $pickerSource = Join-Path (Split-Path $PSScriptRoot -Parent) 'local_app\WorkspacePicker.cs'
            if (Test-Path -LiteralPath $pickerSource -PathType Leaf) {
                Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition ([IO.File]::ReadAllText($pickerSource)) -ErrorAction Stop
            }
        }
        if ('WorkspacePicker.DisplayScaling' -as [type]) { [WorkspacePicker.DisplayScaling]::Initialize() }
    } catch { # Display improvements must not suppress the original startup error.
    }
    try { [Windows.Forms.Application]::EnableVisualStyles() } catch {}
}

function Show-WorkspaceStartupDialog {
    param([string] $Message)
    Initialize-WorkspaceStartupDisplay
    [Windows.Forms.MessageBox]::Show($Message, 'Company Workspace', 'OK', 'Warning') | Out-Null
}


function Wait-WorkspaceShutdown {
    param([string] $Origin, [string] $Auth, [string] $RuntimePath, [int] $TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 200
        try {
            # An early /quit acknowledgement may precede CLI cleanup. Wait for
            # both the old endpoint and its owned runtime record to disappear.
            $null = Invoke-RestMethod -Uri ($Origin + '/api/bootstrap') -Headers @{ Authorization = ('Bearer ' + $Auth) } -TimeoutSec 2
        } catch {
            if (-not (Test-Path -LiteralPath $RuntimePath)) { return $true }
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Open-WorkspaceWindow {
    param([Uri] $Uri)
    # Only an authenticated local Workspace URL is passed by the launcher.
    try {
        # Match the initial window's search in local_app.server.open_window.
        foreach ($edgeRoot in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
            if (-not $edgeRoot) { continue }
            $edge = Join-Path $edgeRoot 'Microsoft\Edge\Application\msedge.exe'
            if (Test-Path -LiteralPath $edge -PathType Leaf) {
                Start-Process -FilePath $edge -ArgumentList @('--new-window', ('--app=' + $Uri.AbsoluteUri)) -WindowStyle Normal | Out-Null
                return
            }
        }
        Start-Process $Uri.AbsoluteUri | Out-Null
    } catch { throw 'WORKSPACE_STARTUP:40' }
}

function Get-WorkspaceVerifiedContext {
    $identityHelper = Join-Path $PSScriptRoot 'CompanyAgent.UserContext.ps1'
    if (-not (Test-Path -LiteralPath $identityHelper -PathType Leaf)) { throw 'WORKSPACE_STARTUP:41' }
    . $identityHelper
    # Never pass SkipAdminCheck or take SID/profile evidence from command-line
    # arguments. Resolve verifies the exact current WTS session, not the first
    # Explorer process or the active console (which may be another RDP user).
    $context = Resolve-SetupUserContext
    if (-not $context.verified) { throw 'WORKSPACE_STARTUP:30' }
    return $context
}

function Get-WorkspaceLaunchAction {
    param([object] $Context, [bool] $Relaunched)
    if (-not $Context -or -not $Context.verified -or -not $Context.sid -or $Context.sessionId -le 0) {
        throw 'WORKSPACE_STARTUP:30'
    }
    if (-not $Context.isAdministrator) { return 'run' }
    if ($Relaunched) { throw 'WORKSPACE_STARTUP:34' }
    return 'relaunch'
}

function Initialize-WorkspaceNormalTokenApi {
    $nativeSource = Join-Path $PSScriptRoot 'CompanyWorkspace.NormalToken.cs'
    if (-not (Test-Path -LiteralPath $nativeSource -PathType Leaf)) { throw 'WORKSPACE_STARTUP:41' }
    if (-not ('CompanyAgent.WorkspaceNormalToken' -as [type])) {
        Add-Type -Path $nativeSource -ErrorAction Stop
    }
}

function Assert-WorkspaceNormalProcess {
    param([object] $Context)
    # Membership alone is not proof of a medium/non-elevated token.
    Initialize-WorkspaceNormalTokenApi
    $snapshot = [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken()
    if (-not [CompanyAgent.WorkspaceNormalToken]::ValidateNormalProcess($snapshot, $Context.sid, [int]$Context.sessionId)) {
        throw 'WORKSPACE_STARTUP:33'
    }
}

function Invoke-WorkspaceNormalTokenRelaunch {
    param([object] $Context, [string] $ScriptPath, [string] $PythonCommand,
          [bool] $Demo, [bool] $NoBrowser, [string] $StateRoot)
    Initialize-WorkspaceNormalTokenApi
    try {
        return [CompanyAgent.WorkspaceNormalToken]::Relaunch(
            $ScriptPath, $PythonCommand, $Demo, $NoBrowser, $StateRoot,
            $Context.sid, [int]$Context.sessionId)
    }
    catch {
        # Only stable categories leave this boundary. Native exception text can
        # include implementation details; never relay inherited environment.
        $nativeError = $_.Exception
        while ($nativeError.InnerException) { $nativeError = $nativeError.InnerException }
        $reasonProperty = $nativeError.PSObject.Properties['ReasonCode']
        $reason = if ($reasonProperty) { [string]$reasonProperty.Value } else { '' }
        if ($reason -in @('invalid_argument', 'command_too_long', 'invalid_identity')) { throw 'WORKSPACE_STARTUP:42' }
        if ($reason -eq 'missing_launcher') { throw 'WORKSPACE_STARTUP:41' }
        if ($reason -match '^(source_not_same_user_split_token|linked_|restricted_token_|primary_token_not_normal|current_session_mismatch|child_session_mismatch|child_token_not_normal|integrity_label|membership_)') {
            throw 'WORKSPACE_STARTUP:33'
        }
        throw 'WORKSPACE_STARTUP:35'
    }
}
