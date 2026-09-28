Set-StrictMode -Version 2.0

# Read-only Windows identity discovery. Never infer a user from the first
# explorer.exe process or WTSGetActiveConsoleSessionId: those may belong to
# another RDP/fast-user-switching session. No token launch or elevation occurs.
# API references:
# https://learn.microsoft.com/windows/win32/api/wtsapi32/nf-wtsapi32-wtsquerysessioninformationw
# https://learn.microsoft.com/windows/win32/api/userenv/nf-userenv-getuserprofiledirectoryw
# https://learn.microsoft.com/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath
# https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getlongpathnamew

function Initialize-SetupUserContextNativeApi {
    if ('CompanyAgent.SetupUserContextNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace CompanyAgent {
    public static class SetupUserContextNative {
        [DllImport("wtsapi32.dll", EntryPoint = "WTSQuerySessionInformationW", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool WTSQuerySessionInformation(IntPtr server, int sessionId, int informationClass, out IntPtr buffer, out int bytesReturned);
        [DllImport("wtsapi32.dll")]
        private static extern void WTSFreeMemory(IntPtr buffer);
        [DllImport("userenv.dll", EntryPoint = "GetUserProfileDirectoryW", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetUserProfileDirectory(IntPtr token, StringBuilder path, ref uint characters);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        private static extern int SHGetKnownFolderPath(ref Guid folderId, uint flags, IntPtr token, out IntPtr path);
        [DllImport("kernel32.dll", EntryPoint = "GetLongPathNameW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetLongPathName(string shortPath, StringBuilder longPath, uint characters);

        public static string SessionText(int sessionId, int informationClass) {
            IntPtr buffer = IntPtr.Zero;
            int bytes;
            try {
                if (!WTSQuerySessionInformation(IntPtr.Zero, sessionId, informationClass, out buffer, out bytes))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot query the current Windows session.");
                if (buffer == IntPtr.Zero || bytes < 2) return String.Empty;
                return Marshal.PtrToStringUni(buffer) ?? String.Empty;
            } finally {
                if (buffer != IntPtr.Zero) WTSFreeMemory(buffer);
            }
        }

        public static string UserProfile(IntPtr token) {
            uint characters = 0;
            GetUserProfileDirectory(token, null, ref characters);
            if (characters == 0 || characters > 32768)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot determine the current token's profile.");
            StringBuilder path = new StringBuilder((int)characters);
            if (!GetUserProfileDirectory(token, path, ref characters))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot read the current token's profile.");
            return path.ToString();
        }

        public static string LocalAppData() {
            Guid folder = new Guid("F1B32785-6FBA-4FCF-9D55-7B8E7F157091");
            IntPtr path = IntPtr.Zero;
            try {
                // DONT_VERIFY is read-only and respects the current redirected
                // folder. Do not use CREATE or DEFAULT_PATH (which ignores it).
                int result = SHGetKnownFolderPath(ref folder, 0x00004000, IntPtr.Zero, out path);
                if (result < 0) Marshal.ThrowExceptionForHR(result);
                return Marshal.PtrToStringUni(path) ?? String.Empty;
            } finally {
                if (path != IntPtr.Zero) Marshal.FreeCoTaskMem(path);
            }
        }

        public static string CanonicalExistingAncestor(string fullPath) {
            string candidate = fullPath;
            List<string> suffix = new List<string>();
            while (!String.IsNullOrEmpty(candidate)) {
                StringBuilder longPath = new StringBuilder(32768);
                uint length = GetLongPathName(candidate, longPath, (uint)longPath.Capacity);
                if (length > 0) {
                    if (length >= longPath.Capacity)
                        throw new Win32Exception(206, "Windows path exceeds the supported length.");
                    string resolved = longPath.ToString();
                    for (int index = suffix.Count - 1; index >= 0; index--)
                        resolved = Path.Combine(resolved, suffix[index]);
                    return Path.GetFullPath(resolved);
                }
                int error = Marshal.GetLastWin32Error();
                // Permission/IO errors are not evidence of non-existence.
                // In particular, never ascend past ACCESS_DENIED.
                if (error != 2 && error != 3)
                    throw new Win32Exception(error, "Cannot safely resolve the Windows directory spelling.");
                string parent = Path.GetDirectoryName(candidate);
                if (String.IsNullOrEmpty(parent) || String.Equals(candidate, parent, StringComparison.OrdinalIgnoreCase))
                    throw new Win32Exception(error, "No existing Windows path ancestor could be verified.");
                suffix.Add(Path.GetFileName(candidate));
                candidate = parent;
            }
            throw new InvalidOperationException("No existing Windows path ancestor could be verified.");
        }
    }
}
'@ -ErrorAction Stop
}

function Get-SetupUserContextObservation {
    # This function is the isolated test boundary. Production never accepts a
    # supplied SID, session name, or an environment flag as identity evidence.
    Initialize-SetupUserContextNativeApi
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
        $sid = $identity.User.Value
        $groupSids = @($identity.Groups | ForEach-Object { $_.Value })
        $isService = $identity.IsSystem -or $sid -in @('S-1-5-18', 'S-1-5-19', 'S-1-5-20') -or
            $groupSids -contains 'S-1-5-6'
        $sessionAccount = ''
        $sessionSid = ''
        if ($sessionId -gt 0 -and -not $isService) {
            $sessionUser = [CompanyAgent.SetupUserContextNative]::SessionText($sessionId, 5) # WTSUserName
            $sessionDomain = [CompanyAgent.SetupUserContextNative]::SessionText($sessionId, 7) # WTSDomainName
            if (-not [string]::IsNullOrWhiteSpace($sessionUser)) {
                $sessionAccount = $(if ([string]::IsNullOrWhiteSpace($sessionDomain)) { $sessionUser } else { $sessionDomain + '\' + $sessionUser })
                $account = New-Object Security.Principal.NTAccount($sessionAccount)
                $sessionSid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
            }
        }
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return [pscustomobject]@{
            sid = $sid
            accountName = $identity.Name
            sessionId = $sessionId
            sessionSid = $sessionSid
            sessionAccountName = $sessionAccount
            isService = [bool]$isService
            isInteractive = [Environment]::UserInteractive
            isAuthenticated = $identity.IsAuthenticated -and -not $identity.IsAnonymous
            isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
            userProfile = [CompanyAgent.SetupUserContextNative]::UserProfile($identity.Token)
            localAppData = [CompanyAgent.SetupUserContextNative]::LocalAppData()
            environmentUserProfile = $env:USERPROFILE
            environmentLocalAppData = $env:LOCALAPPDATA
        }
    }
    finally { $identity.Dispose() }
}

function ConvertTo-SetupUserContextPath {
    param([string] $Path, [string] $Name)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)' -or
        $Path.StartsWith('\\?\') -or $Path.StartsWith('\\.\') -or $Path.IndexOf([char]0) -ge 0) {
        throw "USER_CONTEXT_INVALID_PATH: $Name must identify an absolute Windows folder. No installation settings were changed."
    }
    try {
        $fullPath = [IO.Path]::GetFullPath($Path)
        $pathTail = $fullPath.Substring([IO.Path]::GetPathRoot($fullPath).Length)
        if ($pathTail -match '[. ](?:[\\/]|$)' -or $pathTail.Contains(':')) {
            throw 'Ambiguous Windows directory spelling or alternate data stream.'
        }
        if ($fullPath -ieq [IO.Path]::GetPathRoot($fullPath)) { return $fullPath }
        return $fullPath.TrimEnd([char[]]@('\', '/'))
    }
    catch { throw "USER_CONTEXT_INVALID_PATH: $Name is not a valid Windows folder. No installation settings were changed." }
}

function Assert-SetupUserContextPathMatch {
    param([string] $Candidate, [string] $Expected, [string] $Name)
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return }
    $normalized = ConvertTo-SetupUserContextPath -Path $Candidate -Name $Name
    if (-not [string]::Equals($normalized, $Expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "USER_CONTEXT_PATH_MISMATCH: $Name does not match the verified Windows account folder. Close this installer and open it from your own signed-in desktop. Do not change or delete your Claude settings."
    }
}

function Resolve-SetupUserContext {
    [CmdletBinding()]
    param(
        [string] $InvokingUserProfile,
        [string] $InvokingLocalAppData,
        [switch] $SkipAdminCheck
    )

    # Existing explicitly opted-in isolated tests supply temporary roots. This
    # escape is not an automatic recovery path, and its result is NOT verified.
    if ($SkipAdminCheck) {
        if ([string]::IsNullOrWhiteSpace($InvokingUserProfile)) { $InvokingUserProfile = $env:USERPROFILE }
        if ([string]::IsNullOrWhiteSpace($InvokingLocalAppData)) { $InvokingLocalAppData = $env:LOCALAPPDATA }
        return [pscustomobject]@{
            userProfile = ConvertTo-SetupUserContextPath -Path $InvokingUserProfile -Name 'InvokingUserProfile'
            localAppData = ConvertTo-SetupUserContextPath -Path $InvokingLocalAppData -Name 'InvokingLocalAppData'
            accountName = 'isolated-test-context'; sid = ''; sessionId = -1
            isAdministrator = $false; verified = $false
        }
    }

    try { $observation = Get-SetupUserContextObservation }
    catch {
        throw ('USER_CONTEXT_UNVERIFIED: Windows could not verify the signed-in account for this installer session. No installation settings were changed. Open the installer from your own signed-in desktop; if this repeats, contact the package owner. Details: ' + $_.Exception.Message)
    }
    if ($null -eq $observation -or [string]::IsNullOrWhiteSpace([string]$observation.sid) -or
        [string]::IsNullOrWhiteSpace([string]$observation.accountName) -or
        -not $observation.isAuthenticated) {
        throw 'USER_CONTEXT_UNVERIFIED: The current Windows account could not be verified. No installation settings were changed.'
    }
    if ($observation.isService -or $observation.sid -in @('S-1-5-18', 'S-1-5-19', 'S-1-5-20') -or
        [int]$observation.sessionId -le 0 -or -not $observation.isInteractive) {
        throw 'USER_CONTEXT_NONINTERACTIVE: Run the installer from your own signed-in Windows desktop, not a system or background service account. No installation settings were changed.'
    }
    if ([string]::IsNullOrWhiteSpace([string]$observation.sessionSid) -or
        [string]::IsNullOrWhiteSpace([string]$observation.sessionAccountName)) {
        throw 'USER_CONTEXT_UNVERIFIED: Windows could not identify the signed-in user of this exact session. No installation settings were changed.'
    }
    if ([string]$observation.sid -cne [string]$observation.sessionSid) {
        throw 'USER_CONTEXT_DIFFERENT_ACCOUNT: This installer is running as a different account from the user signed into this Windows session. Close it and open it from your own desktop without choosing another account. No installation settings were changed.'
    }

    $profile = ConvertTo-SetupUserContextPath -Path $observation.userProfile -Name 'Windows user profile'
    $localData = ConvertTo-SetupUserContextPath -Path $observation.localAppData -Name 'Windows LocalAppData'
    Assert-SetupUserContextPathMatch -Candidate $observation.environmentUserProfile -Expected $profile -Name 'USERPROFILE'
    Assert-SetupUserContextPathMatch -Candidate $observation.environmentLocalAppData -Expected $localData -Name 'LOCALAPPDATA'
    Assert-SetupUserContextPathMatch -Candidate $InvokingUserProfile -Expected $profile -Name 'InvokingUserProfile'
    Assert-SetupUserContextPathMatch -Candidate $InvokingLocalAppData -Expected $localData -Name 'InvokingLocalAppData'
    return [pscustomobject]@{
        userProfile = $profile; localAppData = $localData
        accountName = [string]$observation.accountName; sid = [string]$observation.sid
        sessionId = [int]$observation.sessionId
        isAdministrator = [bool]$observation.isAdministrator
        verified = $true
    }
}

function Get-SetupRegisteredUserProfileRoots {
    # Read only ProfileList metadata; never enumerate another user's files.
    # Read the native registry view even when the caller is 32-bit PowerShell.
    $view = $(if ([Environment]::Is64BitOperatingSystem) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Default })
    $machine = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
    $windows = $null
    $profiles = $null
    try {
        $windows = $machine.OpenSubKey('SOFTWARE\Microsoft\Windows NT\CurrentVersion', $false)
        if ($null -eq $windows) { throw 'Windows metadata is unavailable.' }
        $systemRoot = [string]$windows.GetValue('SystemRoot', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $systemRoot = ConvertTo-SetupUserContextPath -Path $systemRoot -Name 'Windows registry SystemRoot'
        $systemDrive = [IO.Path]::GetPathRoot($systemRoot).TrimEnd('\')
        $profiles = $machine.OpenSubKey('SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList', $false)
        if ($null -eq $profiles) { throw 'Windows profile metadata is unavailable.' }
        foreach ($keyName in $profiles.GetSubKeyNames()) {
            $profileKey = $profiles.OpenSubKey($keyName, $false)
            try {
                if ($null -eq $profileKey) { throw 'A Windows profile registration could not be read.' }
                $raw = [string]$profileKey.GetValue('ProfileImagePath', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                if ([string]::IsNullOrWhiteSpace($raw)) { throw 'A Windows profile registration has no profile path.' }
                # Expand only machine roots read from registry, not caller-
                # controlled USERPROFILE or SystemRoot environment variables.
                $raw = [regex]::Replace($raw, '(?i)%SystemRoot%|%windir%', [Text.RegularExpressions.MatchEvaluator]{ param($match) return $systemRoot })
                $raw = [regex]::Replace($raw, '(?i)%SystemDrive%', [Text.RegularExpressions.MatchEvaluator]{ param($match) return $systemDrive })
                if ($raw -match '%[^%]+%') { throw 'A Windows profile registration has an unresolved path variable.' }
                [pscustomobject]@{ sid = $keyName; path = ConvertTo-SetupUserContextPath -Path $raw -Name 'Windows registered profile' }
            }
            finally { if ($null -ne $profileKey) { $profileKey.Dispose() } }
        }
    }
    finally {
        if ($null -ne $profiles) { $profiles.Dispose() }
        if ($null -ne $windows) { $windows.Dispose() }
        $machine.Dispose()
    }
}

function ConvertTo-SetupUserContextCanonicalPath {
    param([Parameter(Mandatory = $true)] [string] $Path)
    $fullPath = ConvertTo-SetupUserContextPath -Path $Path -Name 'Installation target'
    try {
        Initialize-SetupUserContextNativeApi
        $canonical = [CompanyAgent.SetupUserContextNative]::CanonicalExistingAncestor($fullPath)
        return ConvertTo-SetupUserContextPath -Path $canonical -Name 'Canonical installation target'
    }
    catch {
        throw ('USER_CONTEXT_TARGET_UNVERIFIED: The selected directory or registered profile path could not be resolved safely. No installation settings were changed. Details: ' + $_.Exception.Message)
    }
}

function Get-SetupComparableUserProfileRoot {
    param([string] $ProfilePath, [string] $CanonicalTarget)
    $fullProfile = ConvertTo-SetupUserContextPath -Path $ProfilePath -Name 'Windows registered profile'
    $root = [IO.Path]::GetPathRoot($fullProfile)
    $prefix = $root
    $parts = @('') + @($fullProfile.Substring($root.Length).Split([char]'\') | Where-Object { $_ -ne '' })
    foreach ($part in $parts) {
        if ($part) { $prefix = Join-Path $prefix $part }
        $canonicalPrefix = ConvertTo-SetupUserContextCanonicalPath -Path $prefix
        if (-not [string]::Equals($CanonicalTarget, $canonicalPrefix, [StringComparison]::OrdinalIgnoreCase) -and
            -not $CanonicalTarget.StartsWith($canonicalPrefix.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            # This already-resolved ancestor cannot contain the target. Do not
            # read deeper into unrelated service/other-user directories: their
            # permissions may prohibit GetLongPathName even for normal installs.
            return $null
        }
    }
    return $canonicalPrefix
}

function Assert-SetupUserProfileTarget {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [Parameter(Mandatory = $true)] [object] $Context
    )
    if (-not $Context.verified) { return } # Existing explicit isolated fixtures.
    # The installer performs its no-reparse-point check before this ownership
    # guard. GetLongPathName closes 8.3 aliases; it is not a symlink policy.
    $target = ConvertTo-SetupUserContextCanonicalPath -Path $Path
    try { $registeredProfiles = @(Get-SetupRegisteredUserProfileRoots) }
    catch {
        throw ('USER_CONTEXT_TARGET_UNVERIFIED: Windows profile ownership metadata could not be checked. No installation settings were changed. Details: ' + $_.Exception.Message)
    }
    if ($registeredProfiles.Count -eq 0) {
        throw 'USER_CONTEXT_TARGET_UNVERIFIED: No Windows profile ownership metadata was available. No installation settings were changed.'
    }
    foreach ($profile in $registeredProfiles) {
        if ([string]$profile.sid -ceq [string]$Context.sid) { continue }
        $profileRoot = Get-SetupComparableUserProfileRoot -ProfilePath $profile.path -CanonicalTarget $target
        if ([string]::IsNullOrWhiteSpace($profileRoot)) { continue }
        $descendantPrefix = $profileRoot.TrimEnd('\') + '\'
        if ([string]::Equals($target, $profileRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $target.StartsWith($descendantPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'USER_CONTEXT_FOREIGN_PROFILE: The selected folder belongs to another Windows account or a system/service profile. Choose your own Claude settings, personal storage, or project folder. No installation settings were changed.'
        }
    }
}
