[CmdletBinding()]
param([string]$PythonCommand = 'auto', [switch]$Demo, [switch]$NoBrowser, [string]$StateRoot,
      [switch]$NormalTokenRelaunch)
$ErrorActionPreference = 'Stop'
$workspaceMutex = $null
$workspaceLockHeld = $false

function Invoke-WorkspacePythonQuery {
    param([string] $Executable, [string] $Arguments, [int] $TimeoutMilliseconds = 6000)
    # Some Windows python/py aliases are install managers. Disable automatic
    # installation only in this probe child, without editing the parent, PATH,
    # registry, profiles, or the eventual Workspace/Claude environment.
    $process = New-Object Diagnostics.Process
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = $Arguments
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $start.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    $start.EnvironmentVariables.Remove('PYLAUNCHER_ALLOW_INSTALL')
    $start.EnvironmentVariables.Remove('PYLAUNCHER_ALWAYS_INSTALL')
    $start.EnvironmentVariables['PYTHONUTF8'] = '1'
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { return $null }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([Math]::Max(1, [Math]::Min(6000, $TimeoutMilliseconds)))) {
            # Only this bounded dependency probe is stopped, never a live app.
            try { $process.Kill(); $null = $process.WaitForExit(1000) } catch {}
            return $null
        }
        if (-not $stdout.Wait(1000) -or -not $stderr.Wait(1000)) { return $null }
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0 -or $output.Length -gt 65536) { return $null }
        return $output
    } catch { return $null }
    finally { $process.Dispose() }
}

function Get-WorkspacePythonExecutable {
    param([string] $Command)
    $automatic = $Command -eq 'auto'
    $candidateName = if ($automatic) { 'python' } else { $Command }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $foundCommand = $false
    $seen = @{}
    $probeCode = @'
import json, sys
print(json.dumps(sys.executable, ensure_ascii=True))
try:
    import http.server, ssl, ctypes, subprocess, pathlib, threading, zipfile, urllib.request
    print(int(sys.version_info >= (3, 11)))
except Exception:
    print(2)
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probeCode))
    $probeArguments = '-B -X utf8 -c "import base64; exec(base64.b64decode(''' + $encoded + '''))"'
    # Never ask py to launch or install a version. Its legacy-compatible -0p
    # lists installed paths; only existing exact executables are then probed.
    for ($phase = 0; $phase -lt 2; $phase++) {
        $candidates = @()
        if ($phase -eq 0) {
            try {
                $candidate = (Get-Command $candidateName -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
                if ($candidate) { $candidates += $candidate; $foundCommand = $true }
            } catch {}
        } elseif ($automatic -and [DateTime]::UtcNow -lt $deadline) {
            try {
                $launcher = (Get-Command py -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
                $remaining = [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds
                $listed = Invoke-WorkspacePythonQuery -Executable $launcher -Arguments '-0p' -TimeoutMilliseconds $remaining
                foreach ($line in @($listed -split '\r?\n')) {
                    if ($line -match '^\s*-(?:V:)?\S+\s+(?:\*\s+)?(?<path>(?:[A-Za-z]:[\\/]|\\\\).+?)\s*$') {
                        $candidate = $Matches['path'].Trim('"')
                        if ([IO.Path]::GetExtension($candidate) -eq '.exe' -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                            $candidates += $candidate
                            $foundCommand = $true
                        }
                    }
                    if ($candidates.Count -ge 8) { break }
                }
            } catch {}
        }
        foreach ($candidate in $candidates) {
            if ($seen.ContainsKey($candidate) -or [DateTime]::UtcNow -ge $deadline) { continue }
            $seen[$candidate] = $true
            $remaining = [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds
            $output = Invoke-WorkspacePythonQuery -Executable $candidate -Arguments $probeArguments -TimeoutMilliseconds $remaining
            $lines = @($output -split '\r?\n' | Where-Object { $_ -ne '' })
            if ($lines.Count -ne 2 -or $lines[1] -ne '1') { continue }
            # ASCII JSON avoids corrupting Korean paths in hidden CP949 shells.
            try { $exact = [string]($lines[0] | ConvertFrom-Json -ErrorAction Stop) }
            catch { continue }
            if ([IO.Path]::IsPathRooted($exact) -and (Test-Path -LiteralPath $exact -PathType Leaf)) { return $exact }
        }
        if (-not $automatic) { break }
    }
    if ($foundCommand) { throw 'WORKSPACE_STARTUP:38' }
    throw 'WORKSPACE_STARTUP:37'
}

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
                $sameWorkspaceRunning = $health.workspaceVersion -eq '0.12.10' -and $health.appRoot -eq $appRoot
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
        # Never reuse stale launcher evidence inherited from another process.
        foreach ($name in @('COMPANY_WORKSPACE_CLAUDE_ENTRY', 'COMPANY_WORKSPACE_CLAUDE_PROFILE',
                            'COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH', 'COMPANY_WORKSPACE_SHELL')) {
            Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
        }
        try {
            try {
                $terminalClaude = Get-Command claude -ErrorAction Stop | Select-Object -First 1
            } catch [System.Management.Automation.CommandNotFoundException] {
                # Explorer can keep an older PATH after the native installer has
                # completed. Check only its documented location in the already
                # verified current user's profile. Never replace a found alias,
                # wrapper, function, or a command that failed for another reason.
                $nativeClaude = Join-Path $context.userProfile '.local\bin\claude.exe'
                if (-not (Test-Path -LiteralPath $nativeClaude -PathType Leaf)) { throw }
                $terminalClaude = Get-Command -Name $nativeClaude -CommandType Application -ErrorAction Stop | Select-Object -First 1
            }
            $aliasDepth = 0
            while ($terminalClaude -and $terminalClaude.CommandType -eq 'Alias') {
                if (++$aliasDepth -gt 16) { throw 'WORKSPACE_STARTUP:36' }
                $terminalClaude = $terminalClaude.ResolvedCommand
            }
            if (-not $terminalClaude) { throw 'WORKSPACE_STARTUP:36' }
            if ($terminalClaude.CommandType -in @('Application', 'ExternalScript')) {
                if ([string]::IsNullOrWhiteSpace($terminalClaude.Source)) { throw 'WORKSPACE_STARTUP:36' }
                $env:COMPANY_WORKSPACE_CLAUDE_ENTRY = $terminalClaude.Source
                $env:COMPANY_WORKSPACE_CLAUDE_PROFILE = '0'
            } elseif ($terminalClaude.CommandType -eq 'Function') {
                # Reload the same trusted profiles, without copying their body or secrets.
                $definitionHasher = [Security.Cryptography.SHA256]::Create()
                try {
                    $definitionBytes = [Text.Encoding]::UTF8.GetBytes($terminalClaude.Definition)
                    $definitionHash = [BitConverter]::ToString($definitionHasher.ComputeHash($definitionBytes))
                } finally { $definitionHasher.Dispose() }
                $env:COMPANY_WORKSPACE_CLAUDE_ENTRY = 'claude'
                $env:COMPANY_WORKSPACE_CLAUDE_PROFILE = '1'
                $env:COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH = $definitionHash
            } else { throw 'WORKSPACE_STARTUP:36' }
            $env:COMPANY_WORKSPACE_SHELL = (Get-Process -Id $PID).Path
            Remove-Item -LiteralPath Env:COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE -ErrorAction SilentlyContinue
        } catch {
            # Claude availability is an AI connection issue, not an app-start gate.
            # A fixed marker prevents the server from silently choosing another shim.
            foreach ($name in @('COMPANY_WORKSPACE_CLAUDE_ENTRY', 'COMPANY_WORKSPACE_CLAUDE_PROFILE',
                                'COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH', 'COMPANY_WORKSPACE_SHELL')) {
                Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
            }
            $env:COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE = '1'
        }
    } elseif ($env:COMPANY_AGENT_CLAUDE) {
        # An explicit wrapper still needs this shell, not stale internal
        # launcher evidence inherited from an earlier Workspace process.
        foreach ($name in @('COMPANY_WORKSPACE_CLAUDE_ENTRY', 'COMPANY_WORKSPACE_CLAUDE_PROFILE',
                            'COMPANY_WORKSPACE_CLAUDE_DEFINITION_HASH')) {
            Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue
        }
        $env:COMPANY_WORKSPACE_SHELL = (Get-Process -Id $PID).Path
        Remove-Item -LiteralPath Env:COMPANY_WORKSPACE_CLAUDE_UNAVAILABLE -ErrorAction SilentlyContinue
    }
    $resolvedPython = Get-WorkspacePythonExecutable -Command $PythonCommand
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
