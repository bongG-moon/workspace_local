[CmdletBinding()]
param([string]$PythonCommand = 'python', [switch]$Demo, [switch]$NoBrowser, [string]$StateRoot,
      [switch]$NormalTokenRelaunch)
$ErrorActionPreference = 'Stop'
$workspaceMutex = $null
$workspaceLockHeld = $false
try {
    $startupHelper = Join-Path $PSScriptRoot 'CompanyWorkspace.Startup.ps1'
    if (-not (Test-Path -LiteralPath $startupHelper -PathType Leaf)) { throw 'WORKSPACE_STARTUP:41' }
    . $startupHelper
    $context = Get-WorkspaceVerifiedContext
    if ((Get-WorkspaceLaunchAction -Context $context -Relaunched ([bool]$NormalTokenRelaunch)) -eq 'relaunch') {
        # Reduce privileges BEFORE mutex/state/CLI/Python creation. The child
        # rechecks identity and privileges; this flag never skips a check.
        $childCode = Invoke-WorkspaceNormalTokenRelaunch -Context $context -ScriptPath $PSCommandPath `
            -PythonCommand $PythonCommand -Demo ([bool]$Demo) -NoBrowser ([bool]$NoBrowser) -StateRoot $StateRoot
        if ($childCode -eq 0) { return }
        if ($childCode -notin @(22,30,31,32,33,34,35,36,37,38,39,40,41,42,45)) { $childCode = 35 }
        throw ('WORKSPACE_STARTUP:' + $childCode)
    }
    Assert-WorkspaceNormalProcess -Context $context
    $mutexName = 'Local\CompanyWorkspace-' + $context.sid
    if ($Demo) { $mutexName += '-demo' }
    $workspaceMutex = New-Object Threading.Mutex($false, $mutexName)
    $workspaceLockHeld = $workspaceMutex.WaitOne(0)
    if (-not $workspaceLockHeld) { return }
    $appRoot = Split-Path $PSScriptRoot -Parent
    $appStateRoot = if ($StateRoot) { [IO.Path]::GetFullPath($StateRoot) } else { Join-Path $context.localAppData 'CompanyAgent\local-ui' }
    $runtimeStateRoot = $appStateRoot
    if ($Demo) { $runtimeStateRoot = Join-Path $appStateRoot 'demo' }
    $runtimePath = Join-Path $runtimeStateRoot 'runtime.json'
    # Reuse a live local server; do not start another CLI or abandon active work.
    $liveWorkspaceUri = $null
    $sameWorkspaceRunning = $false
    $workspaceClosing = $false
    if (Test-Path -LiteralPath $runtimePath) {
        try {
            $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $uri = [Uri]$runtime.url
            if ($uri.Scheme -ne 'http' -or $uri.Host -ne '127.0.0.1' -or $uri.AbsolutePath -ne '/' -or $uri.Fragment -notmatch '^#token=([A-Za-z0-9_-]{40,100})$') { throw 'Invalid local endpoint' }
            $auth = $Matches[1]
            $origin = $uri.GetLeftPart([UriPartial]::Authority)
            $health = Invoke-RestMethod -Uri ($origin + '/api/bootstrap') -Headers @{ Authorization = ('Bearer ' + $auth) } -TimeoutSec 2
            if ($health.application -eq 'company-workspace' -and [bool]$health.demo -eq [bool]$Demo) {
                $liveWorkspaceUri = $uri
                $workspaceClosing = $health.closing -eq $true
                $sameWorkspaceRunning = $health.workspaceVersion -eq '0.11.4' -and $health.appRoot -eq $appRoot
            }
        } catch { # Stale runtime records never authorize process termination.
        }
    }
    if ($liveWorkspaceUri -and $workspaceClosing) {
        if (-not (Wait-WorkspaceShutdown -Origin $origin -Auth $auth -RuntimePath $runtimePath)) {
            throw 'WORKSPACE_STARTUP:39'
        }
        $liveWorkspaceUri = $null
    }
    if ($liveWorkspaceUri) {
        # Closing the app window leaves its connection alive. A previous build
        # must remain reachable so the user can end it through Settings.
        # Open only the authenticated, validated endpoint above; never kill it.
        if (-not $NoBrowser) { Open-WorkspaceWindow -Uri $liveWorkspaceUri }
        if (-not $sameWorkspaceRunning) { throw 'WORKSPACE_STARTUP:39' }
        return
    }
    if (-not $Demo -and -not $env:COMPANY_AGENT_CLAUDE) {
        # Resolve exactly what `claude` means in this user's shell. Do not silently
        # replace a profile alias/company wrapper with another executable on PATH.
        try { $terminalClaude = Get-Command claude -ErrorAction Stop | Select-Object -First 1 }
        catch { throw 'WORKSPACE_STARTUP:36' }
        while ($terminalClaude.CommandType -eq 'Alias') { $terminalClaude = $terminalClaude.ResolvedCommand }
        if ($terminalClaude.CommandType -in @('Application', 'ExternalScript')) {
            $env:COMPANY_WORKSPACE_CLAUDE_ENTRY = $terminalClaude.Source
            $env:COMPANY_WORKSPACE_CLAUDE_PROFILE = '0'
        } elseif ($terminalClaude.CommandType -eq 'Function') {
            # Functions are reloaded from the same trusted shell profiles; no
            # function body or credential is copied into the app's state directory.
            $env:COMPANY_WORKSPACE_CLAUDE_ENTRY = 'claude'
            $env:COMPANY_WORKSPACE_CLAUDE_PROFILE = '1'
            $definitionHasher = [Security.Cryptography.SHA256]::Create()
            try {
                $definitionBytes = [Text.Encoding]::UTF8.GetBytes($terminalClaude.Definition)
                $env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH = [BitConverter]::ToString($definitionHasher.ComputeHash($definitionBytes))
            } finally { $definitionHasher.Dispose() }
        } else { throw 'WORKSPACE_STARTUP:36' }
        $env:COMPANY_WORKSPACE_SHELL = (Get-Process -Id $PID).Path
    }
    try { $resolvedPython = (Get-Command $PythonCommand -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
    catch { throw 'WORKSPACE_STARTUP:37' }
    try { $probe = @(& $resolvedPython -X utf8 -c "import sys; print(sys.executable); print(int(sys.version_info >= (3, 11)))") }
    catch { throw 'WORKSPACE_STARTUP:38' }
    if ($LASTEXITCODE -ne 0 -or $probe.Count -ne 2 -or $probe[-1] -ne '1') { throw 'WORKSPACE_STARTUP:38' }
    $resolvedPython = [string]$probe[0]
    $windowless = Join-Path (Split-Path $resolvedPython -Parent) 'pythonw.exe'
    if (Test-Path -LiteralPath $windowless) { $resolvedPython = $windowless }
    $arguments = @('-X', 'utf8', '-m', 'local_app.server')
    if ($Demo) { $arguments += '--demo' }
    if ($NoBrowser) { $arguments += '--no-browser' }
    if ($StateRoot) { $arguments += @('--state', ('"' + $appStateRoot + '"')) }
    try { $workspaceProcess = Start-Process -FilePath $resolvedPython -ArgumentList $arguments -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru }
    catch { throw 'WORKSPACE_STARTUP:40' }
    $workspaceReady = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    while ([DateTime]::UtcNow -lt $deadline -and -not $workspaceProcess.HasExited) {
        if (Test-Path -LiteralPath $runtimePath) {
            try {
                $startedRuntime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($startedRuntime.pid -eq $workspaceProcess.Id) { $workspaceReady = $true; break }
            } catch {}
        }
        Start-Sleep -Milliseconds 200
        $workspaceProcess.Refresh()
    }
    if (-not $workspaceReady) { throw 'WORKSPACE_STARTUP:40' }
} catch {
    $code = 41
    $message = 'Workspace 실행 파일을 읽지 못했습니다. ZIP 전체를 새 폴더에 압축 해제한 뒤 다시 실행해 주세요.'
    if (Get-Command Get-WorkspaceStartupMessage -CommandType Function -ErrorAction SilentlyContinue) {
        $code = Get-WorkspaceStartupCode -Message $_.Exception.Message
        $message = Get-WorkspaceStartupMessage -Code $code
    }
    $message += [Environment]::NewLine + ('오류 코드: WS-' + $code)
    # One UI owner: a relaunched child returns a code without opening a modal.
    # Parent presents it once; VBS recognizes 20 as already explained.
    if ($NoBrowser -or $NormalTokenRelaunch) {
        [Console]::Error.WriteLine($message)
        exit $code
    }
    else {
        # A dismissed warning is not required to launch again. Keep the lock
        # around startup work only, not while the user reads a blocking dialog.
        if ($workspaceLockHeld -and $workspaceMutex) {
            $workspaceMutex.ReleaseMutex()
            $workspaceLockHeld = $false
        }
        try {
            Show-WorkspaceStartupDialog -Message $message
            exit 20
        }
        catch { exit $code }
    }
} finally {
    if ($workspaceLockHeld -and $workspaceMutex) { $workspaceMutex.ReleaseMutex() }
    if ($workspaceMutex) { $workspaceMutex.Dispose() }
}
