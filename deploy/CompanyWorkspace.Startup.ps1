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
        37 { return '이 실행 환경에서 설치된 Python을 찾지 못했습니다. 기존 Python 3.11 이상과 실행 경로를 확인해 주세요. Python을 포함하거나 자동으로 설치하지 않으며 PATH와 PC 설정은 변경하지 않았습니다.' }
        38 { return '설치된 Python을 찾았지만 앱 실행 조건을 확인하지 못했습니다. 아래 검사 경로와 원인을 확인해 주세요. EXE는 --python "기존 python.exe의 전체 경로"로 직접 지정할 수 있습니다. Python 재설치나 PATH 변경은 하지 않았습니다.' }
        39 { return '이전 버전 또는 다른 폴더의 Workspace 연결이 남아 있거나 종료 처리 중입니다. 실행 중인 창에서 [설정 → 앱 종료]를 선택하고 종료가 끝난 뒤 다시 실행해 주세요. 창의 X 버튼은 창만 닫고 연결은 유지합니다. 진행 중인 작업은 강제로 종료하지 않았습니다.' }
        40 { return 'Workspace 창을 여는 데 필요한 서버 준비를 확인하지 못했습니다. 창이 열렸는지 먼저 확인해 주세요. 담당자에게 실행 경로와 오류 코드를 전달해 주세요. 기존 Claude 설정은 변경하지 않았습니다.' }
        41 { return 'Workspace 실행 파일이 누락되었거나 읽을 수 없습니다. ZIP 전체를 새 폴더에 압축 해제한 뒤 실행해 주세요. 파일 한 개만 복사하면 실행할 수 없습니다.' }
        42 { return '일반 권한 실행에 전달할 경로 또는 인수가 너무 길거나 유효하지 않습니다. 압축 해제한 Workspace 폴더와 지정한 경로를 확인해 주세요. 값을 잘라서 실행하지 않았습니다.' }
        46 { return '전용 앱 창에 필요한 Microsoft Edge WebView2 Runtime을 찾지 못했습니다. 회사에서 허용한 WebView2 Runtime이 설치되어 있는지 담당자에게 확인해 주세요. 브라우저 실행, 자동 설치 또는 PC 설정 변경은 하지 않았습니다.' }
        47 { return '전용 앱 창을 준비하지 못했습니다. 배포 파일이 모두 있는지, Microsoft Edge WebView2 Runtime 실행이 회사 정책에서 허용되는지 확인해 주세요. Python이나 Claude 인증 문제로 단정하지 않았습니다. 기존 설정은 변경하지 않았습니다.' }
        default { return 'Workspace 실행을 완료하지 못했습니다. ZIP 전체를 압축 해제했는지 확인하고, 담당자에게 아래 오류 코드를 전달해 주세요. 기존 로그인과 개인 설정은 변경하지 않았습니다.' }
    }
}

function ConvertTo-WorkspacePythonChecks {
    param([object[]] $Checks)
    $allowed = @('missing', 'unsupported_command', 'start_failed', 'exit_failed', 'timed_out',
        'invalid_response', 'old_version', 'missing_module', 'accepted', 'output_timeout', 'output_limit', 'not_found', 'environment_failed')
    $modules = @('http.server', 'ssl', 'ctypes', 'subprocess', 'pathlib', 'threading', 'zipfile', 'urllib.request')
    $count = 0
    foreach ($check in $Checks) {
        if ($null -eq $check -or $count -ge 40) { continue }
        $fields = @{}
        foreach ($name in @('source', 'path', 'status', 'version', 'nativeCode', 'missingModules')) {
            if ($check -is [Collections.IDictionary]) { $fields[$name] = $check[$name] }
            else {
                $property = $check.PSObject.Properties[$name]
                $fields[$name] = if ($property) { $property.Value } else { $null }
            }
        }
        $check = $fields
        $status = [string]$check.status
        if ($status -notin $allowed) { $status = 'invalid_response' }
        $path = ([string]$check.path -replace '[\x00-\x1f\x7f]', '')
        if ($path.Length -gt 2048) { $path = $path.Substring(0, 2048) }
        $source = ([string]$check.source -replace '[^a-zA-Z0-9_.:-]', '')
        if ($source.Length -gt 80) { $source = $source.Substring(0, 80) }
        $version = @($check.version) -join '.'
        if ($version -notmatch '^\d{1,3}\.\d{1,3}(?:\.\d{1,3})?$') { $version = $null }
        $nativeCode = $null
        if ($null -ne $check.nativeCode -and [string]$check.nativeCode -match '^-?\d{1,10}$') { $nativeCode = [long]$check.nativeCode }
        $missing = @($check.missingModules | Where-Object { $_ -in $modules } | Select-Object -Unique)
        [pscustomobject]@{ source=$source; path=$path; status=$status; version=$version; nativeCode=$nativeCode; missingModules=$missing }
        $count++
    }
}

function Write-WorkspacePythonDiagnostic {
    param([string] $StateRoot, [string] $AppRoot, [object[]] $Checks, [int] $Code, [string] $AttemptId)
    if (-not $StateRoot -or -not $AppRoot -or $Code -notin @(37, 38) -or $AttemptId -notmatch '^[a-f0-9]{32}$') { return $null }
    try {
        $clean = @(ConvertTo-WorkspacePythonChecks -Checks $Checks)
        $directory = Join-Path $StateRoot 'diagnostics'
        $null = [IO.Directory]::CreateDirectory($directory)
        if (([IO.File]::GetAttributes($directory) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
        $path = Join-Path $directory ('python-check-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8) + '.json')
        $report = [ordered]@{ diagnosticVersion='python-1'; workspaceVersion='0.23.0';
            createdUtc=[DateTime]::UtcNow.ToString('o'); sourceRoot=[IO.Path]::GetFullPath($AppRoot);
            powershellVersion=$PSVersionTable.PSVersion.ToString(); attemptId=$AttemptId; code=('WS-' + $Code); checks=$clean }
        # Allowlisted metadata only: no stderr, wrapper/profile bodies, auth,
        # environment values, or Claude settings. Never overwrite an old file.
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($report | ConvertTo-Json -Depth 5))
        $stream = New-Object IO.FileStream($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        return [pscustomobject]@{ path=$path; checks=$clean }
    } catch { return $null }
}

function Read-WorkspaceRecentPythonDiagnostic {
    param([string] $StateRoot, [string] $AppRoot, [DateTime] $Since, [string] $AttemptId, [int] $Code)
    if (-not $StateRoot -or -not $AppRoot -or $AttemptId -notmatch '^[a-f0-9]{32}$' -or $Code -notin @(37, 38)) { return $null }
    try {
        $directory = Join-Path $StateRoot 'diagnostics'
        if (-not [IO.Directory]::Exists($directory)) { return $null }
        if (([IO.File]::GetAttributes($directory) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
        $files = @(Get-ChildItem -LiteralPath $directory -Filter 'python-check-*.json' -File |
            Where-Object { $_.LastWriteTimeUtc -ge $Since -and $_.Length -le 262144 -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 8)
        foreach ($file in $files) {
            try {
                $report = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) | ConvertFrom-Json
                if ($report.diagnosticVersion -ne 'python-1' -or $report.workspaceVersion -ne '0.23.0' -or
                    $report.sourceRoot -ne [IO.Path]::GetFullPath($AppRoot) -or $report.code -ne ('WS-' + $Code) -or $report.attemptId -ne $AttemptId) { continue }
                return [pscustomobject]@{ path=$file.FullName; checks=@(ConvertTo-WorkspacePythonChecks -Checks $report.checks) }
            } catch {}
        }
    } catch {}
    return $null
}

function Get-WorkspacePythonDiagnosticText {
    param([object] $Diagnostic, [object[]] $Checks)
    $clean = @(ConvertTo-WorkspacePythonChecks -Checks $Checks)
    if ($Diagnostic) { $clean = @($Diagnostic.checks) }
    $labels = @{
        missing='실행 파일을 찾지 못함'; not_found='실행 파일을 찾지 못함'; unsupported_command='직접 확인할 수 없는 명령 형식';
        start_failed='프로세스를 시작하지 못함'; environment_failed='검사용 프로세스 환경을 구성하지 못함'; exit_failed='Python 실행 오류'; timed_out='검사 시간 초과';
        invalid_response='Python 검사 응답을 확인하지 못함'; old_version='Python 3.11 미만';
        missing_module='필수 기본 모듈 확인 실패'; accepted='사용 가능한 Python';
        output_timeout='검사 출력 대기 시간 초과'; output_limit='검사 출력 크기 초과'
    }
    $lines = @()
    foreach ($check in @($clean | Select-Object -Last 3)) {
        $label = $labels[[string]$check.status]
        if ($check.version) { $label += ' (Python ' + $check.version + ')' }
        if ($check.nativeCode) { $label += ' [Windows/종료 코드 ' + $check.nativeCode + ']' }
        if (@($check.missingModules).Count) { $label += ' [' + ($check.missingModules -join ', ') + ']' }
        $displayPath = [string]$check.path
        if ($displayPath.Length -gt 150) { $displayPath = $displayPath.Substring(0, 45) + ' ... ' + $displayPath.Substring($displayPath.Length - 100) }
        $lines += ($displayPath + ' : ' + $label)
    }
    if ($Diagnostic -and $Diagnostic.path) { $lines += ('상세 진단 파일: ' + $Diagnostic.path) }
    if ($lines.Count) { return ([Environment]::NewLine + [Environment]::NewLine + ($lines -join [Environment]::NewLine)) }
    return ''
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

function Confirm-WorkspaceLegacyUpgrade {
    Initialize-WorkspaceStartupDisplay
    $message = '새 버전으로 전환할 준비가 됐어요. 이전 버전은 보내지 않은 입력을 자동으로 옮길 수 없습니다. 작성 중인 내용이 있다면 먼저 복사해 두세요.' + [Environment]::NewLine + [Environment]::NewLine +
        '확인을 누르면 이전 앱을 종료하고 새 버전을 자동으로 엽니다. 대화와 파일은 유지되며, 예약과 이어 할 일은 새 앱에서 확인 후 다시 이어 실행할 수 있어요.'
    $owner = New-Object Windows.Forms.Form
    try {
        $owner.ShowInTaskbar = $false
        $owner.TopMost = $true
        $owner.Opacity = 0
        $owner.StartPosition = 'CenterScreen'
        $owner.Show()
        return [Windows.Forms.MessageBox]::Show($owner, $message, '새 버전으로 전환', 'OKCancel', 'Information') -eq [Windows.Forms.DialogResult]::OK
    } finally { $owner.Dispose() }
}

function Show-WorkspaceUpgradeWaiting {
    Initialize-WorkspaceStartupDisplay
    $notice = New-Object Windows.Forms.NotifyIcon
    try {
        $iconPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'local_app\web\app-icon.ico'
        $notice.Icon = New-Object Drawing.Icon($iconPath)
        $notice.Visible = $true
        $notice.BalloonTipTitle = '새 버전 전환 대기 중'
        $notice.BalloonTipText = '진행 중인 업무와 승인·질문을 마치면 전환을 안내합니다. 기존 앱에서 계속 작업할 수 있어요.'
        $notice.ShowBalloonTip(5000)
        Start-Sleep -Seconds 5
    } finally { $notice.Dispose() }
    return $true
}

function Show-WorkspaceUpgradeFailure {
    Initialize-WorkspaceStartupDisplay
    [Windows.Forms.MessageBox]::Show('새 버전으로 전환을 마치지 못했어요. 진행 중인 업무를 강제로 종료하지 않았습니다. 기존 앱의 작업 상태를 확인한 뒤 새 실행 파일을 다시 열어 주세요.', 'Company Workspace', 'OK', 'Information') | Out-Null
    return $true
}


function Wait-WorkspaceShutdown {
    param([string] $Origin, [string] $Auth, [string] $RuntimePath, [int] $TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 200
        try {
            # An early /quit acknowledgement may precede CLI cleanup. Wait for
            # both the old endpoint and its owned runtime record to disappear.
            $null = Invoke-RestMethod -Uri ($Origin + '/api/bootstrap') -Headers @{ Authorization = ('Bearer ' + $Auth) } -TimeoutSec 5 -MaximumRedirection 0
        } catch {
            $failure = $_.Exception
            while ($failure.InnerException) { $failure = $failure.InnerException }
            $refused = $failure -is [Net.Sockets.SocketException] -and $failure.SocketErrorCode -eq [Net.Sockets.SocketError]::ConnectionRefused
            if ($refused -and -not (Test-Path -LiteralPath $RuntimePath)) { return $true }
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Open-WorkspaceWindow {
    param([Uri] $Uri, [bool] $ReuseSupported = $false)
    # Only an authenticated local Workspace URL is passed by the launcher.
    try {
        if ($Uri.Scheme -ne 'http' -or $Uri.Host -ne '127.0.0.1' -or $Uri.IsDefaultPort -or $Uri.UserInfo -or $Uri.Query -or $Uri.AbsolutePath -ne '/' -or $Uri.Fragment -notmatch '^#token=([A-Za-z0-9_-]{40,100})$') { throw 'Invalid local endpoint' }
        $windowAuth = $Matches[1]
        if ($ReuseSupported) {
            $opened = Invoke-RestMethod -Method Post -Uri ($Uri.GetLeftPart([UriPartial]::Authority) + '/api/window/open') -Headers @{ Authorization = ('Bearer ' + $windowAuth) } -ContentType 'application/json' -Body '{}' -TimeoutSec 10 -MaximumRedirection 0
            if ($opened.ok -eq $true) { return }
            throw 'Window response was not accepted'
        }
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
