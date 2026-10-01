[CmdletBinding()]
param([string]$PythonCommand = 'auto', [switch]$Demo, [switch]$NoBrowser, [string]$StateRoot,
      [switch]$NormalTokenRelaunch)
$ErrorActionPreference = 'Stop'
$workspaceMutex = $null
$workspaceLockHeld = $false
$workspacePythonAttemptStarted = [DateTime]::UtcNow
$appStateRoot = $null
$script:WorkspacePythonChecks = @()
$workspacePythonAttemptId = [Guid]::NewGuid().ToString('N')
if ($NormalTokenRelaunch -and $env:COMPANY_WORKSPACE_PYTHON_ATTEMPT -match '^[a-f0-9]{32}$') {
    $workspacePythonAttemptId = $env:COMPANY_WORKSPACE_PYTHON_ATTEMPT
    # Only the launcher handoff uses this marker; it is not passed to Python/Claude.
    Remove-Item -LiteralPath Env:COMPANY_WORKSPACE_PYTHON_ATTEMPT
}

function Invoke-WorkspacePythonQuery {
    param([string] $Executable, [string] $Arguments, [int] $TimeoutMilliseconds = 6000)
    # Some Windows python/py aliases are install managers. Disable automatic
    # installation only in this probe child, without editing the parent, PATH,
    # registry, profiles, or the eventual Workspace/Claude environment.
    $script:WorkspacePythonQueryStatus = 'start_failed'
    $script:WorkspacePythonQueryNativeCode = $null
    $process = New-Object Diagnostics.Process
    try {
        $queryStart = New-Object Diagnostics.ProcessStartInfo
        $queryStart.FileName = $Executable
        $queryStart.Arguments = $Arguments
        $queryStart.UseShellExecute = $false
        $queryStart.CreateNoWindow = $true
        $queryStart.RedirectStandardOutput = $true
        $queryStart.RedirectStandardError = $true
        $queryStart.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
        $queryStart.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
        # Initialize one child environment map before setting probe-only flags.
        $script:WorkspacePythonQueryStatus = 'environment_failed'
        $probeEnvironment = $queryStart.get_EnvironmentVariables()
        $probeEnvironment['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
        $probeEnvironment.Remove('PYLAUNCHER_ALLOW_INSTALL')
        $probeEnvironment.Remove('PYLAUNCHER_ALWAYS_INSTALL')
        $probeEnvironment['PYTHONUTF8'] = '1'
        $probeEnvironment['PYTHONIOENCODING'] = 'utf-8'
        $process.StartInfo = $queryStart
        $script:WorkspacePythonQueryStatus = 'start_failed'
        if (-not $process.Start()) { return $null }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([Math]::Max(1, [Math]::Min(6000, $TimeoutMilliseconds)))) {
            $script:WorkspacePythonQueryStatus = 'timed_out'
            # Only this bounded dependency probe is stopped, never a live app.
            try { $process.Kill(); $null = $process.WaitForExit(1000) } catch {}
            return $null
        }
        if (-not $stdout.Wait(1000) -or -not $stderr.Wait(1000)) {
            $script:WorkspacePythonQueryStatus = 'timed_out'
            return $null
        }
        $output = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $script:WorkspacePythonQueryStatus = 'exit_failed'
            $script:WorkspacePythonQueryNativeCode = $process.ExitCode
            return $null
        }
        if ($output.Length -gt 65536) { $script:WorkspacePythonQueryStatus = 'invalid_response'; return $null }
        $script:WorkspacePythonQueryStatus = 'accepted'
        return $output
    } catch {
        $failure = $_.Exception
        while ($failure.InnerException) { $failure = $failure.InnerException }
        if ($failure -is [ComponentModel.Win32Exception]) { $script:WorkspacePythonQueryNativeCode = $failure.NativeErrorCode }
        return $null
    }
    finally { $process.Dispose() }
}

function Get-WorkspacePythonRegistryEntries {
    param($Root, [string] $Source, [DateTime] $Deadline)
    # PEP 514: inspect only registered Python environments, never other users
    # or arbitrary directories. Registry values are read without expansion.
    foreach ($company in @($Root.GetSubKeyNames() | Select-Object -First 32)) {
        if ($company -eq 'PyLauncher' -or [DateTime]::UtcNow -ge $Deadline) { continue }
        $companyKey = $null
        try {
            $companyKey = $Root.OpenSubKey($company, $false)
            if (-not $companyKey) { continue }
            foreach ($tag in @($companyKey.GetSubKeyNames() | Select-Object -First 32)) {
                if ([DateTime]::UtcNow -ge $Deadline) { break }
                $install = $null
                try {
                    $install = $companyKey.OpenSubKey(($tag + '\InstallPath'), $false)
                    if (-not $install) { continue }
                    $path = $install.GetValue('ExecutablePath', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    if (-not $path -and $company -eq 'PythonCore') {
                        $directory = $install.GetValue('', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                        if ($directory -is [string] -and [IO.Path]::IsPathRooted($directory)) { $path = Join-Path $directory 'python.exe' }
                    }
                    if ($path -isnot [string] -or -not $path) { continue }
                    $arguments = $install.GetValue('ExecutableArguments', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    [pscustomobject]@{source=$Source;path=$path;unsupported=(-not [string]::IsNullOrWhiteSpace([string]$arguments));identity=($company + '\' + $tag);company=$company}
                } catch {} finally { if ($install) { $install.Dispose() } }
            }
        } catch {} finally { if ($companyKey) { $companyKey.Dispose() } }
    }
}

function Get-WorkspaceRegisteredPythonCandidates {
    param([DateTime] $Deadline)
    $locations = @(
        @{hive=[Microsoft.Win32.RegistryHive]::CurrentUser;view=[Microsoft.Win32.RegistryView]::Default;source='registry_current_user'},
        @{hive=[Microsoft.Win32.RegistryHive]::LocalMachine;view=[Microsoft.Win32.RegistryView]::Registry64;source='registry_local_machine_64'},
        @{hive=[Microsoft.Win32.RegistryHive]::LocalMachine;view=[Microsoft.Win32.RegistryView]::Registry32;source='registry_local_machine_32'}
    )
    $seenRegistrations = @{}
    foreach ($location in $locations) {
        if ([DateTime]::UtcNow -ge $Deadline) { break }
        $base = $null; $root = $null
        try {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($location.hive, $location.view)
            $root = $base.OpenSubKey('Software\Python', $false)
            if (-not $root) { continue }
            foreach ($entry in @(Get-WorkspacePythonRegistryEntries -Root $root -Source $location.source -Deadline $Deadline)) {
                # PythonCore retains pre-3.5 architecture compatibility; for
                # other vendors prefer per-user registrations of the same tag.
                if ($entry.company -ne 'PythonCore' -and $seenRegistrations.ContainsKey($entry.identity)) { continue }
                $seenRegistrations[$entry.identity] = $true
                $entry
            }
        } catch {} finally {
            if ($root) { $root.Dispose() }
            if ($base) { $base.Dispose() }
        }
    }
}

function Add-WorkspacePythonCheck {
    param([string] $Source, [string] $Path, [string] $Status, [string] $Version = '', $NativeCode = $null, [string[]] $MissingModules = @())
    $row = [ordered]@{source=$Source;path=$Path;status=$Status;version=$Version}
    if ($null -ne $NativeCode) { $row.nativeCode = [int]$NativeCode }
    if ($Status -eq 'missing_module') { $row.missingModules = @($MissingModules) }
    $script:WorkspacePythonChecks.Add([pscustomobject]$row)
}

function Get-WorkspacePythonExecutable {
    param([string] $Command)
    $script:WorkspacePythonChecks = New-Object 'System.Collections.Generic.List[object]'
    $script:WorkspacePythonSelected = ''
    $script:WorkspacePythonFailureKind = 'missing'
    $automatic = $Command -eq 'auto'
    $candidateName = if ($automatic) { 'python' } else { $Command }
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    $foundCommand = $false
    $seen = @{}
    $probeCode = @'
import json, sys
result = {"executable": sys.executable, "version": list(sys.version_info[:3]), "modules": True, "missingModules": []}
for name in ("http.server", "ssl", "ctypes", "subprocess", "pathlib", "threading", "zipfile", "urllib.request"):
    try:
        __import__(name)
    except Exception:
        result["modules"] = False
        result["missingModules"].append(name)
print("WORKSPACE_PYTHON_V1:" + json.dumps(result, ensure_ascii=True, separators=(",", ":")))
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probeCode))
    $probeArguments = '-B -X utf8 -c "import base64; exec(base64.b64decode(''' + $encoded + '''))"'
    # Never ask py to launch or install a version. Its legacy-compatible -0p
    # lists installed paths; only existing exact executables are then probed.
    for ($phase = 0; $phase -lt 3; $phase++) {
        $candidates = @()
        if ($phase -eq 0) {
            $explicitShellCommand = $false
            try {
                $resolvedCommand = Get-Command $candidateName -ErrorAction Stop | Select-Object -First 1
                if ($resolvedCommand.CommandType -eq 'Alias') {
                    $explicitShellCommand = -not $automatic
                    $target = $resolvedCommand.ResolvedCommand
                    if ($target -and $target.CommandType -eq 'Application') {
                        $candidates += [pscustomobject]@{source='alias';path=$target.Source;unsupported=$false}
                    } else {
                        Add-WorkspacePythonCheck -Source 'alias' -Path '' -Status 'unsupported_command'
                        $foundCommand = $true
                    }
                } elseif ($resolvedCommand.CommandType -in @('Function','Filter','ExternalScript','Cmdlet')) {
                    $explicitShellCommand = -not $automatic
                    Add-WorkspacePythonCheck -Source 'shell_command' -Path '' -Status 'unsupported_command'
                    $foundCommand = $true
                }
            } catch {}
            # -All is required: a stale, old, or WindowsApps entry must not
            # hide a usable interpreter later in the inherited PATH.
            try {
                if (-not $explicitShellCommand) {
                    foreach ($application in @(Get-Command $candidateName -CommandType Application -All -ErrorAction Stop | Select-Object -First 32)) {
                        $candidates += [pscustomobject]@{source=$(if ($automatic) {'path'} else {'explicit'});path=$application.Source;unsupported=$false}
                        if (-not $automatic) { break }
                    }
                }
            } catch {}
        } elseif ($phase -eq 1 -and $automatic -and [DateTime]::UtcNow -lt $deadline) {
            try {
                $launcher = (Get-Command py -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
                if ([IO.Path]::GetExtension($launcher) -ne '.exe') {
                    Add-WorkspacePythonCheck -Source 'py_launcher' -Path $launcher -Status 'unsupported_command'
                    continue
                }
                $remaining = [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds
                $listed = Invoke-WorkspacePythonQuery -Executable $launcher -Arguments '-0p' -TimeoutMilliseconds $remaining
                if ($null -eq $listed) { Add-WorkspacePythonCheck -Source 'py_launcher' -Path $launcher -Status $script:WorkspacePythonQueryStatus -NativeCode $script:WorkspacePythonQueryNativeCode }
                foreach ($line in @($listed -split '\r?\n')) {
                    if ($line -match '^\s*-(?:V:)?\S+\s+(?:\*\s+)?(?<path>"?(?:[A-Za-z]:[\\/]|\\\\).+?)\s*$') {
                        $candidate = $Matches['path'].Trim('"')
                        if ([IO.Path]::GetExtension($candidate) -eq '.exe' -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                            $candidates += [pscustomobject]@{source='py_listing';path=$candidate;unsupported=$false}
                        }
                    }
                    if ($candidates.Count -ge 8) { break }
                }
            } catch {}
        } elseif ($phase -eq 2 -and $automatic -and [DateTime]::UtcNow -lt $deadline) {
            $candidates = @(Get-WorkspaceRegisteredPythonCandidates -Deadline $deadline | Select-Object -First 64)
        }
        foreach ($candidate in $candidates) {
            $path = [string]$candidate.path
            if (-not $path -or $seen.ContainsKey($path) -or [DateTime]::UtcNow -ge $deadline) { continue }
            $seen[$path] = $true
            $foundCommand = $true
            if ($candidate.unsupported -or [IO.Path]::GetExtension($path) -ne '.exe') {
                Add-WorkspacePythonCheck -Source $candidate.source -Path $path -Status 'unsupported_command'
                continue
            }
            if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
                Add-WorkspacePythonCheck -Source $candidate.source -Path $path -Status 'missing'
                continue
            }
            $remaining = [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds
            $script:WorkspacePythonQueryStatus = 'invalid_response'; $script:WorkspacePythonQueryNativeCode = $null
            $output = Invoke-WorkspacePythonQuery -Executable $path -Arguments $probeArguments -TimeoutMilliseconds $remaining
            $status = 'invalid_response'; $version = ''; $exact = ''; $missingModules = @()
            if ($null -eq $output) { $status = $script:WorkspacePythonQueryStatus }
            else {
                $lines = @($output -split '\r?\n' | Where-Object { $_.StartsWith('WORKSPACE_PYTHON_V1:') })
                if ($lines.Count -eq 1) {
                    try {
                        $reply = $lines[0].Substring(20) | ConvertFrom-Json -ErrorAction Stop
                        $parts = @($reply.version)
                        if ($parts.Count -ne 3 -or @($parts | Where-Object { $_ -isnot [int] -and $_ -isnot [long] -or $_ -lt 0 -or $_ -gt 999 }).Count -ne 0) { throw 'Invalid version' }
                        $version = $parts -join '.'
                        $exact = [string]$reply.executable
                        if (-not [IO.Path]::IsPathRooted($exact) -or [IO.Path]::GetExtension($exact) -ne '.exe' -or -not (Test-Path -LiteralPath $exact -PathType Leaf) -or $reply.modules -isnot [bool]) { throw 'Invalid result' }
                        if ($parts[0] -lt 3 -or ($parts[0] -eq 3 -and $parts[1] -lt 11)) { $status = 'old_version' }
                        elseif (-not $reply.modules) {
                            $status = 'missing_module'
                            $missingModules = @($reply.missingModules | Where-Object { $_ -is [string] -and $_ -in @('http.server','ssl','ctypes','subprocess','pathlib','threading','zipfile','urllib.request') } | Select-Object -Unique)
                        }
                        else { $status = 'accepted' }
                    } catch { $status = 'invalid_response' }
                }
            }
            Add-WorkspacePythonCheck -Source $candidate.source -Path $path -Status $status -Version $version -NativeCode $script:WorkspacePythonQueryNativeCode -MissingModules $missingModules
            if ($status -eq 'accepted') {
                $script:WorkspacePythonSelected = $exact
                $script:WorkspacePythonFailureKind = ''
                return $exact
            }
        }
        if (-not $automatic) { break }
    }
    if ($script:WorkspacePythonChecks.Count -eq 0) {
        Add-WorkspacePythonCheck -Source $(if ($automatic) {'auto'} else {'explicit'}) -Path '' -Status 'missing'
    }
    if ([DateTime]::UtcNow -ge $deadline) { Add-WorkspacePythonCheck -Source 'auto' -Path '' -Status 'timed_out' }
    $script:WorkspacePythonFailureKind = $script:WorkspacePythonChecks[$script:WorkspacePythonChecks.Count - 1].status
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
        $previousAttempt = [Environment]::GetEnvironmentVariable('COMPANY_WORKSPACE_PYTHON_ATTEMPT', 'Process')
        try {
            [Environment]::SetEnvironmentVariable('COMPANY_WORKSPACE_PYTHON_ATTEMPT', $workspacePythonAttemptId, 'Process')
            $childCode = Invoke-WorkspaceNormalTokenRelaunch -Context $context -ScriptPath $PSCommandPath `
                -PythonCommand $PythonCommand -Demo ([bool]$Demo) -NoBrowser ([bool]$NoBrowser) -StateRoot $StateRoot
        } finally { [Environment]::SetEnvironmentVariable('COMPANY_WORKSPACE_PYTHON_ATTEMPT', $previousAttempt, 'Process') }
        if ($childCode -eq 0) { return }
        if ($childCode -notin @(22,30,31,32,33,34,35,36,37,38,39,40,41,42,45,46,47)) { $childCode = 35 }
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
                $sameWorkspaceRunning = $health.workspaceVersion -eq '0.21.4' -and $health.appRoot -eq $appRoot
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
        $canReuseWindow = $health.PSObject.Properties['window'] -and $health.window -and
            $health.window.PSObject.Properties['reopenSupported'] -and ($health.window.reopenSupported -eq $true)
        if (-not $NoBrowser) {
            if (-not $canReuseWindow) { throw 'WORKSPACE_STARTUP:39' }
            Open-WorkspaceWindow -Uri $liveWorkspaceUri -ReuseSupported $true
        }
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
    if (-not $workspaceReady) {
        $workspaceProcess.Refresh()
        if ($workspaceProcess.HasExited -and $workspaceProcess.ExitCode -in @(46,47)) {
            throw ('WORKSPACE_STARTUP:' + $workspaceProcess.ExitCode)
        }
        throw 'WORKSPACE_STARTUP:40'
    }
} catch {
    $code = 41
    $message = 'Workspace 실행 파일을 읽지 못했습니다. ZIP 전체를 새 폴더에 압축 해제한 뒤 다시 실행해 주세요.'
    if (Get-Command Get-WorkspaceStartupMessage -CommandType Function -ErrorAction SilentlyContinue) {
        $code = Get-WorkspaceStartupCode -Message $_.Exception.Message
        $message = Get-WorkspaceStartupMessage -Code $code
    }
    if ($code -in @(37, 38)) {
        # A restricted-token child writes only its own failed Python checks.
        # The parent may present that fresh report without launching Python again.
        $diagnosticState = if ($appStateRoot) { $appStateRoot } elseif ($StateRoot) { [IO.Path]::GetFullPath($StateRoot) }
            elseif ($context -and $context.localAppData) { Join-Path $context.localAppData 'CompanyAgent\local-ui' } else { $null }
        $diagnosticSource = Split-Path $PSScriptRoot -Parent
        $checks = @($script:WorkspacePythonChecks | ForEach-Object { $_ })
        $diagnostic = $null
        if ($checks.Count -gt 0 -and $null -ne $checks[0]) {
            $diagnostic = Write-WorkspacePythonDiagnostic -StateRoot $diagnosticState -AppRoot $diagnosticSource -Checks $checks -Code $code -AttemptId $workspacePythonAttemptId
        } else {
            $diagnostic = Read-WorkspaceRecentPythonDiagnostic -StateRoot $diagnosticState -AppRoot $diagnosticSource -Since $workspacePythonAttemptStarted -AttemptId $workspacePythonAttemptId -Code $code
        }
        $message += Get-WorkspacePythonDiagnosticText -Diagnostic $diagnostic -Checks $checks
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
