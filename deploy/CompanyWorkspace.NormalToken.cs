// Windows PowerShell 5.1 / .NET Framework: compile with Add-Type -Path.
// Prefer the current user's limited UAC token. On a verified UAC-disabled host,
// only a separately created, strictly validated restricted copy is eligible.
// Never modify the source token, Windows policy, another process, or credentials.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32.SafeHandles;
using Microsoft.Win32;

namespace CompanyAgent
{
    public sealed class WorkspaceTokenSnapshot
    {
        public string UserSid { get; private set; }
        public int SessionId { get; private set; }
        public int ElevationType { get; private set; }
        public bool IsElevated { get; private set; }
        public int IntegrityRid { get; private set; }
        public bool IsAdministrator { get; private set; }
        public bool IsPrimary { get; private set; }

        public WorkspaceTokenSnapshot(string userSid, int sessionId, int elevationType,
            bool isElevated, int integrityRid, bool isAdministrator, bool isPrimary)
        {
            UserSid = userSid;
            SessionId = sessionId;
            ElevationType = elevationType;
            IsElevated = isElevated;
            IntegrityRid = integrityRid;
            IsAdministrator = isAdministrator;
            IsPrimary = isPrimary;
        }
    }

    public sealed class WorkspaceNormalTokenException : InvalidOperationException
    {
        public string ReasonCode { get; private set; }
        public int NativeErrorCode { get; private set; }

        internal WorkspaceNormalTokenException(string reasonCode, int nativeErrorCode)
            : base("Workspace normal-token relaunch: " + reasonCode +
                " (Win32 " + BoundedError(nativeErrorCode).ToString(
                    System.Globalization.CultureInfo.InvariantCulture) + ").")
        {
            ReasonCode = reasonCode;
            NativeErrorCode = BoundedError(nativeErrorCode);
        }

        private static int BoundedError(int value)
        {
            return value >= 0 && value <= 65535 ? value : 0;
        }
    }

    public static class WorkspaceNormalToken
    {
        public const int StatusUncertainExitCode = 22;
        private const uint TokenQuery = 0x0008;
        private const uint TokenDuplicate = 0x0002;
        private const uint TokenAssignPrimary = 0x0001;
        private const uint TokenAdjustDefault = 0x0080;
        private const uint CreateSuspended = 0x00000004;
        private const uint CreateUnicodeEnvironment = 0x00000400;
        private const uint StartfUseShowWindow = 0x00000001;
        private const uint WaitObject0 = 0;
        private const uint LauncherWaitMilliseconds = 60000;
        private const int MediumIntegrityRid = 0x2000;

        // These pure predicates are also used by validation fixtures. They never
        // authorize relaunch on their own; production always queries real tokens.
        public static bool ValidateSourceToken(WorkspaceTokenSnapshot token,
            string expectedSid, int expectedSession)
        {
            return MatchesIdentity(token, expectedSid, expectedSession) &&
                token.IsPrimary && token.ElevationType == 2 &&
                token.IsElevated && token.IsAdministrator;
        }

        public static bool ValidateNormalTokenCandidate(WorkspaceTokenSnapshot token,
            string expectedSid, int expectedSession)
        {
            return ValidateNormalProcess(token, expectedSid, expectedSession) &&
                token.ElevationType == 3;
        }

        public static bool ValidateRestrictedSource(WorkspaceTokenSnapshot token,
            string expectedSid, int expectedSession, bool uacDisabled)
        {
            // Do not reinterpret a failed linked-token launch as permission to
            // use a different policy. Only the unsplit, UAC-disabled case qualifies.
            return uacDisabled && MatchesIdentity(token, expectedSid, expectedSession) &&
                token.IsPrimary && token.ElevationType == 1 && token.IsElevated &&
                token.IsAdministrator && token.IntegrityRid == 0x3000;
        }

        public static bool IsUacDisabled()
        {
            // Missing, inaccessible, or malformed policy is never an opt-in.
            try {
                using (RegistryKey machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                using (RegistryKey policy = machine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System", false))
                {
                    object value = policy == null ? null : policy.GetValue("EnableLUA", null);
                    return value is Int32 && (Int32)value == 0;
                }
            } catch { return false; }
        }

        // Ordinary starts may use a standard user's unlinked token. Relaunch
        // candidates above must specifically be the existing limited UAC token.
        public static bool ValidateNormalProcess(WorkspaceTokenSnapshot token,
            string expectedSid, int expectedSession)
        {
            return MatchesIdentity(token, expectedSid, expectedSession) &&
                token.IsPrimary && (token.ElevationType == 1 || token.ElevationType == 3) &&
                !token.IsElevated && token.IntegrityRid == MediumIntegrityRid &&
                !token.IsAdministrator;
        }

        private static bool MatchesIdentity(WorkspaceTokenSnapshot token,
            string expectedSid, int expectedSession)
        {
            return token != null && !String.IsNullOrEmpty(expectedSid) &&
                expectedSession > 0 && token.SessionId == expectedSession &&
                String.Equals(token.UserSid, expectedSid, StringComparison.OrdinalIgnoreCase);
        }

        // A read-only probe: no linked token lookup or child creation occurs here.
        public static WorkspaceTokenSnapshot InspectCurrentToken()
        {
            SafeNativeHandle token;
            if (!OpenProcessToken(GetCurrentProcess(), TokenQuery | TokenDuplicate, out token))
            {
                int error = Marshal.GetLastWin32Error();
                if (token != null) token.Dispose();
                throw Failure("current_token", error);
            }
            using (token) { return ReadToken(token); }
        }

        // Windows argv quoting, not a PowerShell expression or nested command.
        // Always quote; double trailing slashes and slashes preceding a quote.
        public static string QuoteWindowsArgument(string value)
        {
            if (value == null || value.IndexOf('\0') >= 0 ||
                value.IndexOf('\r') >= 0 || value.IndexOf('\n') >= 0)
                throw Failure("invalid_argument", 0);

            StringBuilder result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\') { slashes++; continue; }
                if (character == '"')
                {
                    result.Append('\\', slashes * 2 + 1);
                    result.Append('"');
                }
                else
                {
                    result.Append('\\', slashes);
                    result.Append(character);
                }
                slashes = 0;
            }
            result.Append('\\', slashes * 2);
            result.Append('"');
            return result.ToString();
        }

        public static string BuildCommandLine(string scriptPath, string pythonCommand,
            bool demo, bool noBrowser, string stateRoot)
        {
            if (String.IsNullOrWhiteSpace(scriptPath) || String.IsNullOrWhiteSpace(pythonCommand))
                throw Failure("invalid_argument", 0);
            StringBuilder command = new StringBuilder(QuoteWindowsArgument(PowerShellPath()));
            // Only this process tree; registry and Group Policy are unchanged.
            command.Append(" -NoLogo -ExecutionPolicy Bypass -WindowStyle Hidden -File ");
            command.Append(QuoteWindowsArgument(scriptPath));
            command.Append(" -NormalTokenRelaunch -PythonCommand ");
            command.Append(QuoteWindowsArgument(pythonCommand));
            if (demo) command.Append(" -Demo");
            if (noBrowser) command.Append(" -NoBrowser");
            if (!String.IsNullOrEmpty(stateRoot))
            {
                command.Append(" -StateRoot ");
                command.Append(QuoteWindowsArgument(stateRoot));
            }
            // CreateProcessWithTokenW documents a 1024-character maximum,
            // including the terminating NUL. Never fall back to a command file.
            if (command.Length >= 1024) throw Failure("command_too_long", 0);
            return command.ToString();
        }

        public static int Relaunch(string scriptPath, string pythonCommand, bool demo,
            bool noBrowser, string stateRoot, string expectedSid, int expectedSession)
        {
            try
            {
                return RelaunchCore(scriptPath, pythonCommand, demo, noBrowser,
                    stateRoot, expectedSid, expectedSession);
            }
            catch (WorkspaceNormalTokenException) { throw; }
            catch (Exception)
            {
                // Framework exception text can contain user paths or arguments.
                // The parent owns the only user-facing startup dialog.
                throw Failure("runtime_validation", 0);
            }
        }

        private static int RelaunchCore(string scriptPath, string pythonCommand, bool demo,
            bool noBrowser, string stateRoot, string expectedSid, int expectedSession)
        {
            if (String.IsNullOrWhiteSpace(scriptPath) || !Path.IsPathRooted(scriptPath) ||
                !String.Equals(Path.GetExtension(scriptPath), ".ps1", StringComparison.OrdinalIgnoreCase) ||
                String.IsNullOrEmpty(expectedSid) || expectedSession <= 0)
                throw Failure("invalid_argument", 0);

            string exactScript = Path.GetFullPath(scriptPath);
            string executable = PowerShellPath();
            if (!File.Exists(exactScript) || !File.Exists(executable))
                throw Failure("missing_launcher", 0);
            // Parse the supplied SID before any native launch operation.
            if (!String.Equals(new SecurityIdentifier(expectedSid).Value, expectedSid,
                StringComparison.OrdinalIgnoreCase)) throw Failure("invalid_identity", 0);
            string commandLine = BuildCommandLine(exactScript, pythonCommand, demo, noBrowser, stateRoot);

            SafeNativeHandle current;
            if (!OpenProcessToken(GetCurrentProcess(), TokenQuery | TokenDuplicate |
                TokenAssignPrimary | TokenAdjustDefault, out current))
            {
                int error = Marshal.GetLastWin32Error();
                if (current != null) current.Dispose();
                throw Failure("current_token", error);
            }
            using (current)
            {
                WorkspaceTokenSnapshot source = ReadToken(current);
                bool linkedSource = ValidateSourceToken(source, expectedSid, expectedSession);
                bool restrictedSource = ValidateRestrictedSource(source, expectedSid, expectedSession, IsUacDisabled());
                if (!linkedSource && !restrictedSource)
                    throw Failure("source_not_same_user_split_token", 0);
                uint currentSession;
                if (!ProcessIdToSessionId(GetCurrentProcessId(), out currentSession))
                    throw LastFailure("current_session");
                if (currentSession != (uint)expectedSession)
                    throw Failure("current_session_mismatch", 0);

                if (restrictedSource)
                {
                    using (SafeNativeHandle restricted = CreateRestrictedNormalToken(current))
                    {
                        ValidateRestrictedCandidate(restricted, expectedSid, expectedSession);
                        return StartAndWait(restricted, executable, commandLine, expectedSid, expectedSession, true);
                    }
                }

                using (SafeNativeHandle linked = ReadLinkedToken(current))
                {
                    if (!ValidateNormalTokenCandidate(ReadToken(linked), expectedSid, expectedSession))
                        throw Failure("linked_token_not_normal", 0);
                    SafeNativeHandle primary;
                    if (!DuplicateTokenEx(linked, TokenQuery | TokenDuplicate | TokenAssignPrimary,
                        IntPtr.Zero, 2, 1, out primary))
                    {
                        int error = Marshal.GetLastWin32Error();
                        if (primary != null) primary.Dispose();
                        throw Failure("linked_primary_token", error);
                    }
                    using (primary)
                    {
                        if (!ValidateNormalTokenCandidate(ReadToken(primary), expectedSid, expectedSession))
                            throw Failure("primary_token_not_normal", 0);
                        return StartAndWait(primary, executable, commandLine, expectedSid, expectedSession);
                    }
                }
            }
        }

        private static string PowerShellPath()
        {
            // A 32-bit caller on 64-bit Windows would redirect System32. Reject
            // that ambiguous context instead of silently loading another profile.
            if (Environment.Is64BitOperatingSystem && !Environment.Is64BitProcess)
                throw Failure("native_powershell_required", 0);
            return Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe");
        }

        private static int StartAndWait(SafeNativeHandle primary, string executable,
            string commandLine, string expectedSid, int expectedSession, bool restricted = false)
        {
            IntPtr environment = IntPtr.Zero;
            SafeNativeHandle process = null;
            SafeNativeHandle thread = null;
            bool createdSuspended = false;
            bool childMayBeRunning = false;
            try
            {
                // Preserve the exact current process environment (including
                // provider/auth variables) only in this in-memory Unicode block.
                environment = GetEnvironmentStringsW();
                if (environment == IntPtr.Zero) throw LastFailure("environment_block");
                StartupInfo startup = new StartupInfo();
                startup.cb = Marshal.SizeOf(typeof(StartupInfo));
                startup.dwFlags = StartfUseShowWindow;
                startup.wShowWindow = 0;
                ProcessInformation information;
                // A restricted version of our own primary token is eligible for
                // CreateProcessAsUser without assigning a different user's token.
                // Required privileges are never enabled manually; an API denial
                // remains a startup failure, without an elevated fallback.
                bool created = restricted ? CreateProcessAsUserW(primary, executable,
                    new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, false,
                    CreateSuspended | CreateUnicodeEnvironment, environment, null, ref startup, out information)
                    : CreateProcessWithTokenW(primary, 0, executable,
                    new StringBuilder(commandLine), CreateSuspended | CreateUnicodeEnvironment,
                    environment, null, ref startup, out information);
                int createError = created ? 0 : Marshal.GetLastWin32Error();
                process = new SafeNativeHandle(information.hProcess);
                thread = new SafeNativeHandle(information.hThread);
                if (!created) throw Failure("create_process", createError);
                createdSuspended = true;

                uint childSession;
                if (!ProcessIdToSessionId(information.dwProcessId, out childSession))
                    throw LastFailure("child_session");
                if (childSession != (uint)expectedSession)
                    throw Failure("child_session_mismatch", 0);
                SafeNativeHandle childToken;
                if (!OpenProcessToken(process.DangerousGetHandle(), TokenQuery | TokenDuplicate, out childToken))
                {
                    int error = Marshal.GetLastWin32Error();
                    if (childToken != null) childToken.Dispose();
                    throw Failure("child_token", error);
                }
                using (childToken)
                {
                    if (restricted) ValidateRestrictedCandidate(childToken, expectedSid, expectedSession);
                    else if (!ValidateNormalTokenCandidate(ReadToken(childToken), expectedSid, expectedSession))
                        throw Failure("child_token_not_normal", 0);
                }

                uint resumeResult = ResumeThread(thread);
                if (resumeResult == UInt32.MaxValue) throw LastFailure("resume_thread");
                // More than one previous suspension means our single resume
                // left this child suspended; retire that failed child below.
                if (resumeResult > 1) throw Failure("resume_count", 0);
                childMayBeRunning = true;
                if (resumeResult != 1) return StatusUncertainExitCode;
                // No running process or tree is terminated on timeout/failure.
                // Retrying could start another UI while this launcher completes.
                if (WaitForSingleObject(process, LauncherWaitMilliseconds) != WaitObject0)
                    return StatusUncertainExitCode;
                uint exitCode;
                if (!GetExitCodeProcess(process, out exitCode)) return StatusUncertainExitCode;
                return unchecked((int)exitCode);
            }
            finally
            {
                // This is only our newly-created, never-resumed failed child.
                if (createdSuspended && !childMayBeRunning && process != null && !process.IsInvalid)
                    TerminateProcess(process, 21);
                if (thread != null) thread.Dispose();
                if (process != null) process.Dispose();
                if (environment != IntPtr.Zero) FreeEnvironmentStringsW(environment);
            }
        }

        private static SafeNativeHandle CreateRestrictedNormalToken(SafeNativeHandle source)
        {
            // DISABLE_MAX_PRIVILEGE | LUA_TOKEN. SANDBOX_INERT is deliberately
            // absent: AppLocker and Software Restriction Policies still apply.
            SecurityIdentifier administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
            byte[] sidBytes = new byte[administrators.BinaryLength];
            administrators.GetBinaryForm(sidBytes, 0);
            using (TokenBuffer sid = new TokenBuffer(sidBytes.Length))
            {
                Marshal.Copy(sidBytes, 0, sid.Pointer, sidBytes.Length);
                SidAndAttributes[] disabled = { new SidAndAttributes { Sid = sid.Pointer, Attributes = 0 } };
                SafeNativeHandle restricted;
                if (!CreateRestrictedToken(source, 0x1 | 0x4, 1, disabled, 0, IntPtr.Zero,
                    0, IntPtr.Zero, out restricted))
                {
                    int error = Marshal.GetLastWin32Error();
                    if (restricted != null) restricted.Dispose();
                    throw Failure("restricted_token_create", error);
                }
                try
                {
                    SecurityIdentifier medium = new SecurityIdentifier("S-1-16-8192");
                    byte[] labelBytes = new byte[medium.BinaryLength];
                    medium.GetBinaryForm(labelBytes, 0);
                    using (TokenBuffer labelSid = new TokenBuffer(labelBytes.Length))
                    {
                        Marshal.Copy(labelBytes, 0, labelSid.Pointer, labelBytes.Length);
                        SidAndAttributes label = new SidAndAttributes { Sid = labelSid.Pointer, Attributes = 0x20 };
                        if (!SetTokenInformation(restricted, 25, ref label,
                            Marshal.SizeOf(typeof(SidAndAttributes)) + labelBytes.Length))
                            throw LastFailure("restricted_token_integrity");
                    }
                    return restricted;
                }
                catch { restricted.Dispose(); throw; }
            }
        }

        private static void ValidateRestrictedCandidate(SafeNativeHandle token, string expectedSid, int expectedSession)
        {
            // A filtered token is not accepted merely because filtering returned
            // success. Requery its actual identity, integrity, elevation, groups,
            // and privileges, and do so again for the suspended child.
            if (!ValidateNormalProcess(ReadToken(token), expectedSid, expectedSession) ||
                ReadTokenInt(token, 21) == 0)
                throw Failure("restricted_token_not_normal", 0);
            using (TokenBuffer groups = QueryToken(token, 2))
            {
                int count = Marshal.ReadInt32(groups.Pointer);
                if (count < 0 || count > 4096) throw Failure("restricted_token_groups", 0);
                int offset = IntPtr.Size == 8 ? 8 : 4;
                int size = Marshal.SizeOf(typeof(SidAndAttributes));
                for (int index = 0; index < count; index++)
                {
                    SidAndAttributes group = (SidAndAttributes)Marshal.PtrToStructure(
                        IntPtr.Add(groups.Pointer, offset + index * size), typeof(SidAndAttributes));
                    if (new SecurityIdentifier(group.Sid).IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid) &&
                        ((group.Attributes & 0x10) == 0 || (group.Attributes & 0x4) != 0))
                        throw Failure("restricted_token_groups", 0);
                }
            }
            Luid notify;
            if (!LookupPrivilegeValueW(null, "SeChangeNotifyPrivilege", out notify))
                throw LastFailure("restricted_token_privileges");
            using (TokenBuffer privileges = QueryToken(token, 3))
            {
                int count = Marshal.ReadInt32(privileges.Pointer);
                if (count < 0 || count > 256) throw Failure("restricted_token_privileges", 0);
                int size = Marshal.SizeOf(typeof(LuidAndAttributes));
                for (int index = 0; index < count; index++)
                {
                    LuidAndAttributes privilege = (LuidAndAttributes)Marshal.PtrToStructure(
                        IntPtr.Add(privileges.Pointer, 4 + index * size), typeof(LuidAndAttributes));
                    // Reject even disabled administrative privileges: retaining
                    // one could let a child re-enable it later.
                    if (privilege.Luid.LowPart != notify.LowPart || privilege.Luid.HighPart != notify.HighPart)
                        throw Failure("restricted_token_privileges", 0);
                }
            }
        }

        private static WorkspaceTokenSnapshot ReadToken(SafeNativeHandle token)
        {
            string sid;
            using (TokenBuffer user = QueryToken(token, 1))
                sid = new SecurityIdentifier(Marshal.ReadIntPtr(user.Pointer)).Value;
            int integrity;
            using (TokenBuffer label = QueryToken(token, 25))
            {
                string integritySid = new SecurityIdentifier(Marshal.ReadIntPtr(label.Pointer)).Value;
                if (!integritySid.StartsWith("S-1-16-", StringComparison.Ordinal) ||
                    !Int32.TryParse(integritySid.Substring(7), out integrity))
                    throw Failure("integrity_label", 0);
            }
            return new WorkspaceTokenSnapshot(sid, ReadTokenInt(token, 12),
                ReadTokenInt(token, 18), ReadTokenInt(token, 20) != 0, integrity,
                IsAdministrator(token), ReadTokenInt(token, 8) == 1);
        }

        private static bool IsAdministrator(SafeNativeHandle token)
        {
            // CheckTokenMembership requires an impersonation token. Duplication
            // only changes the token type; this thread never impersonates it.
            SafeNativeHandle membershipToken;
            if (!DuplicateTokenEx(token, TokenQuery, IntPtr.Zero, 2, 2, out membershipToken))
            {
                int error = Marshal.GetLastWin32Error();
                if (membershipToken != null) membershipToken.Dispose();
                throw Failure("membership_token", error);
            }
            using (membershipToken)
            {
                SecurityIdentifier administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
                byte[] sidBytes = new byte[administrators.BinaryLength];
                administrators.GetBinaryForm(sidBytes, 0);
                bool isMember;
                if (!CheckTokenMembership(membershipToken, sidBytes, out isMember))
                    throw LastFailure("membership_check");
                return isMember;
            }
        }

        private static SafeNativeHandle ReadLinkedToken(SafeNativeHandle token)
        {
            using (TokenBuffer buffer = QueryToken(token, 19))
            {
                SafeNativeHandle linked = new SafeNativeHandle(Marshal.ReadIntPtr(buffer.Pointer));
                if (linked.IsInvalid)
                {
                    linked.Dispose();
                    throw Failure("linked_token_missing", 0);
                }
                return linked;
            }
        }

        private static int ReadTokenInt(SafeNativeHandle token, int informationClass)
        {
            using (TokenBuffer buffer = QueryToken(token, informationClass))
                return Marshal.ReadInt32(buffer.Pointer);
        }

        private static TokenBuffer QueryToken(SafeNativeHandle token, int informationClass)
        {
            int needed = informationClass == 19 ? IntPtr.Size : 4;
            int error;
            // Fixed-size classes (notably TokenSessionId) can report
            // ERROR_BAD_LENGTH instead of a sizing response for a zero buffer.
            if (informationClass == 1 || informationClass == 2 || informationClass == 3 || informationClass == 25)
            {
                bool measured = GetTokenInformation(token, informationClass, IntPtr.Zero, 0, out needed);
                error = Marshal.GetLastWin32Error();
                if ((!measured && error != 122) || needed <= 0 || needed > 1024 * 1024)
                    throw Failure("token_information", error);
            }
            TokenBuffer buffer = new TokenBuffer(needed);
            if (!GetTokenInformation(token, informationClass, buffer.Pointer, needed, out needed))
            {
                error = Marshal.GetLastWin32Error();
                buffer.Dispose();
                throw Failure(informationClass == 19 ? "linked_token_unavailable" : "token_information", error);
            }
            return buffer;
        }

        private static WorkspaceNormalTokenException LastFailure(string reason)
        {
            return Failure(reason, Marshal.GetLastWin32Error());
        }

        private static WorkspaceNormalTokenException Failure(string reason, int error)
        {
            return new WorkspaceNormalTokenException(reason, error);
        }

        private sealed class TokenBuffer : IDisposable
        {
            internal IntPtr Pointer { get; private set; }
            internal TokenBuffer(int bytes) { Pointer = Marshal.AllocHGlobal(bytes); }
            public void Dispose()
            {
                if (Pointer != IntPtr.Zero) Marshal.FreeHGlobal(Pointer);
                Pointer = IntPtr.Zero;
            }
        }

        private sealed class SafeNativeHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            public SafeNativeHandle() : base(true) { }
            internal SafeNativeHandle(IntPtr handle) : base(true) { SetHandle(handle); }
            protected override bool ReleaseHandle() { return CloseHandle(handle); }
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct StartupInfo
        {
            internal int cb;
            internal string lpReserved;
            internal string lpDesktop;
            internal string lpTitle;
            internal uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
            internal uint dwFillAttribute, dwFlags;
            internal short wShowWindow, cbReserved2;
            internal IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation
        {
            internal IntPtr hProcess, hThread;
            internal uint dwProcessId, dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SidAndAttributes { internal IntPtr Sid; internal uint Attributes; }
        [StructLayout(LayoutKind.Sequential)]
        private struct Luid { internal uint LowPart; internal int HighPart; }
        [StructLayout(LayoutKind.Sequential)]
        private struct LuidAndAttributes { internal Luid Luid; internal uint Attributes; }

        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool OpenProcessToken(
            IntPtr process, uint access, out SafeNativeHandle token);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetTokenInformation(
            SafeNativeHandle token, int informationClass, IntPtr information, int length, out int needed);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool DuplicateTokenEx(
            SafeNativeHandle existing, uint access, IntPtr attributes, int impersonationLevel,
            int tokenType, out SafeNativeHandle duplicate);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CheckTokenMembership(
            SafeNativeHandle token, byte[] sid, [MarshalAs(UnmanagedType.Bool)] out bool isMember);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateRestrictedToken(
            SafeNativeHandle existing, uint flags, uint disableCount, [In] SidAndAttributes[] disabled,
            uint deleteCount, IntPtr deleted, uint restrictedCount, IntPtr restrictedSids, out SafeNativeHandle token);
        [DllImport("advapi32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetTokenInformation(
            SafeNativeHandle token, int informationClass, ref SidAndAttributes information, int length);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool LookupPrivilegeValueW(
            string system, string name, out Luid luid);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool ProcessIdToSessionId(
            uint processId, out uint sessionId);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr GetEnvironmentStringsW();
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool FreeEnvironmentStringsW(IntPtr environment);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcessWithTokenW(
            SafeNativeHandle token, uint logonFlags, string application, StringBuilder commandLine,
            uint creationFlags, IntPtr environment, string currentDirectory,
            ref StartupInfo startupInfo, out ProcessInformation information);
        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreateProcessAsUserW(
            SafeNativeHandle token, string application, StringBuilder commandLine, IntPtr processAttributes,
            IntPtr threadAttributes, [MarshalAs(UnmanagedType.Bool)] bool inheritHandles, uint creationFlags,
            IntPtr environment, string currentDirectory, ref StartupInfo startupInfo, out ProcessInformation information);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(SafeNativeHandle thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(SafeNativeHandle handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetExitCodeProcess(
            SafeNativeHandle process, out uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool TerminateProcess(
            SafeNativeHandle process, uint exitCode);
    }
}
