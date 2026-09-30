// Diagnostic only. Reads the current token and creates disposable probe pipes
// and a fixed Python process. Never edits tokens, ACLs, policy, or user settings.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading.Tasks;

public static class WorkspaceTokenAudit
{
    public sealed class TokenSummary
    {
        public bool HasRestrictions;
        public int IntegrityRid;
        public int ElevationType;
        public bool IsElevated;
        public bool IsPrimary;
        public bool AdminGroupEnabled;
        public bool AdminGroupDenyOnly;
        public bool OwnerIsUser;
        public bool OwnerIsAdministrators;
        public bool DefaultDaclPresent;
        public int AceCount;
        // These describe direct allow ACEs, not an effective access verdict.
        public bool DirectUserAllowAce;
        public bool DirectAdministratorsAllowAce;
        public bool DirectSystemAllowAce;
    }

    public sealed class ProbeResult
    {
        public string status;
        public int? nativeCode;
        public string stage;
        public int? exitCode;
    }

    public sealed class NativeProbeException : InvalidOperationException
    {
        public int NativeErrorCode { get; private set; }
        internal NativeProbeException(int code) : base("Native diagnostic unavailable")
        {
            NativeErrorCode = code;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes
    {
        internal int Length;
        internal IntPtr Descriptor;
        [MarshalAs(UnmanagedType.Bool)] internal bool Inherit;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SidAndAttributes
    {
        internal IntPtr Sid;
        internal uint Attributes;
    }

    public static TokenSummary CurrentToken()
    {
        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), 0x8, out token))
            throw new NativeProbeException(Marshal.GetLastWin32Error());
        try { return ReadToken(token); }
        finally { CloseHandle(token); }
    }

    private static TokenSummary ReadToken(IntPtr token)
    {
        IntPtr userBuffer = IntPtr.Zero, ownerBuffer = IntPtr.Zero;
        IntPtr daclBuffer = IntPtr.Zero, groupsBuffer = IntPtr.Zero, labelBuffer = IntPtr.Zero;
        try
        {
            userBuffer = Query(token, 1);
            ownerBuffer = Query(token, 4);
            daclBuffer = Query(token, 6);
            groupsBuffer = Query(token, 2);
            labelBuffer = Query(token, 25);
            SecurityIdentifier user = new SecurityIdentifier(Marshal.ReadIntPtr(userBuffer));
            SecurityIdentifier owner = new SecurityIdentifier(Marshal.ReadIntPtr(ownerBuffer));
            string integrity = new SecurityIdentifier(Marshal.ReadIntPtr(labelBuffer)).Value;
            int integrityRid;
            if (!integrity.StartsWith("S-1-16-", StringComparison.Ordinal) ||
                !Int32.TryParse(integrity.Substring(7), out integrityRid))
                throw new NativeProbeException(13);
            TokenSummary result = new TokenSummary
            {
                HasRestrictions = QueryInt(token, 21) != 0,
                IntegrityRid = integrityRid,
                ElevationType = QueryInt(token, 18),
                IsElevated = QueryInt(token, 20) != 0,
                IsPrimary = QueryInt(token, 8) == 1,
                OwnerIsUser = owner.Equals(user),
                OwnerIsAdministrators = owner.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid)
            };
            int groupCount = Marshal.ReadInt32(groupsBuffer);
            if (groupCount < 0 || groupCount > 4096) throw new NativeProbeException(13);
            int offset = IntPtr.Size == 8 ? 8 : 4;
            int stride = Marshal.SizeOf(typeof(SidAndAttributes));
            for (int index = 0; index < groupCount; index++)
            {
                SidAndAttributes group = (SidAndAttributes)Marshal.PtrToStructure(
                    IntPtr.Add(groupsBuffer, offset + index * stride), typeof(SidAndAttributes));
                if (new SecurityIdentifier(group.Sid).IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid))
                {
                    result.AdminGroupEnabled = (group.Attributes & 0x4) != 0;
                    result.AdminGroupDenyOnly = (group.Attributes & 0x10) != 0;
                }
            }
            IntPtr acl = Marshal.ReadIntPtr(daclBuffer);
            result.DefaultDaclPresent = acl != IntPtr.Zero;
            if (acl != IntPtr.Zero)
            {
                int size = (ushort)Marshal.ReadInt16(acl, 2);
                if (size < 8) throw new NativeProbeException(13);
                byte[] bytes = new byte[size];
                Marshal.Copy(acl, bytes, 0, size);
                RawAcl parsed = new RawAcl(bytes, 0);
                result.AceCount = parsed.Count;
                foreach (GenericAce entry in parsed)
                {
                    CommonAce ace = entry as CommonAce;
                    if (ace == null || ace.AceQualifier != AceQualifier.AccessAllowed) continue;
                    result.DirectUserAllowAce |= ace.SecurityIdentifier.Equals(user);
                    result.DirectAdministratorsAllowAce |= ace.SecurityIdentifier.IsWellKnown(WellKnownSidType.BuiltinAdministratorsSid);
                    result.DirectSystemAllowAce |= ace.SecurityIdentifier.IsWellKnown(WellKnownSidType.LocalSystemSid);
                }
            }
            return result;
        }
        finally
        {
            Free(userBuffer); Free(ownerBuffer); Free(daclBuffer); Free(groupsBuffer); Free(labelBuffer);
        }
    }

    private static IntPtr Query(IntPtr token, int kind)
    {
        int needed;
        GetTokenInformation(token, kind, IntPtr.Zero, 0, out needed);
        if (needed < 1 || needed > 1024 * 1024)
            throw new NativeProbeException(Marshal.GetLastWin32Error());
        IntPtr buffer = Marshal.AllocHGlobal(needed);
        if (!GetTokenInformation(token, kind, buffer, needed, out needed))
        {
            int error = Marshal.GetLastWin32Error();
            Marshal.FreeHGlobal(buffer);
            throw new NativeProbeException(error);
        }
        return buffer;
    }

    private static int QueryInt(IntPtr token, int kind)
    {
        // Fixed classes can reject a zero-length sizing request with BAD_LENGTH.
        IntPtr buffer = Marshal.AllocHGlobal(4);
        try
        {
            int needed;
            if (!GetTokenInformation(token, kind, buffer, 4, out needed))
                throw new NativeProbeException(Marshal.GetLastWin32Error());
            return Marshal.ReadInt32(buffer);
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    private static void Free(IntPtr pointer)
    {
        if (pointer != IntPtr.Zero) Marshal.FreeHGlobal(pointer);
    }

    // These operations match the setup needed by .NET Framework's redirected
    // Process.Start, without starting any executable or changing a descriptor.
    public static int Pipe()
    {
        return PipeCore(false);
    }

    public static int DuplicatePipeHandle()
    {
        return PipeCore(true);
    }

    private static int PipeCore(bool duplicate)
    {
        IntPtr read = IntPtr.Zero, write = IntPtr.Zero, copied = IntPtr.Zero;
        try
        {
            SecurityAttributes attributes = new SecurityAttributes
            {
                Length = Marshal.SizeOf(typeof(SecurityAttributes)), Inherit = true
            };
            if (!CreatePipe(out read, out write, ref attributes, 0))
            {
                int error = Marshal.GetLastWin32Error();
                // Failed CreatePipe output handles are indeterminate. Only
                // handles from a successful creation belong to this probe.
                read = IntPtr.Zero;
                write = IntPtr.Zero;
                return error;
            }
            if (duplicate && !DuplicateHandle(GetCurrentProcess(), read, GetCurrentProcess(),
                out copied, 0, false, 2))
            {
                int error = Marshal.GetLastWin32Error();
                copied = IntPtr.Zero;
                return error;
            }
            return 0;
        }
        finally
        {
            if (copied != IntPtr.Zero) CloseHandle(copied);
            if (read != IntPtr.Zero) CloseHandle(read);
            if (write != IntPtr.Zero) CloseHandle(write);
        }
    }

    public static int SelfDuplicateAccess()
    {
        IntPtr process = OpenProcess(0x40, false, GetCurrentProcessId());
        if (process == IntPtr.Zero) return Marshal.GetLastWin32Error();
        CloseHandle(process);
        return 0;
    }

    // Both variants perform only the same fixed stdlib/version check. The
    // unredirected variant is diagnostic evidence, never an app launch fallback.
    public static ProbeResult ProbePython(string path, bool redirect, int timeoutMs = 4000)
    {
        ProbeResult result = new ProbeResult { status = "start_failed", stage = "process_start" };
        if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path) ||
            !String.Equals(Path.GetExtension(path), ".exe", StringComparison.OrdinalIgnoreCase) ||
            !File.Exists(path))
        {
            result.status = "missing";
            return result;
        }
        string code = "import sys\ntry:\n import http.server,ssl,ctypes,subprocess,pathlib,threading,zipfile,urllib.request\nexcept Exception:\n sys.exit(82)\nsys.exit(0 if sys.version_info >= (3,11) else 81)";
        string encoded = Convert.ToBase64String(Encoding.UTF8.GetBytes(code));
        using (Process process = new Process())
        {
            bool started = false;
            try
            {
                ProcessStartInfo start = new ProcessStartInfo(path,
                    "-B -X utf8 -c \"import base64;exec(base64.b64decode('" + encoded + "'))\"");
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.RedirectStandardOutput = redirect;
                start.RedirectStandardError = redirect;
                result.stage = "environment";
                start.EnvironmentVariables["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false";
                start.EnvironmentVariables.Remove("PYLAUNCHER_ALLOW_INSTALL");
                start.EnvironmentVariables.Remove("PYLAUNCHER_ALWAYS_INSTALL");
                start.EnvironmentVariables["PYTHONUTF8"] = "1";
                start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                process.StartInfo = start;
                result.stage = "process_start";
                Stopwatch clock = Stopwatch.StartNew();
                int budget = Math.Max(1, Math.Min(4000, timeoutMs));
                started = process.Start();
                if (!started) return result;
                Task<string> stdout = redirect ? process.StandardOutput.ReadToEndAsync() : null;
                Task<string> stderr = redirect ? process.StandardError.ReadToEndAsync() : null;
                result.stage = "process_wait";
                if (!process.WaitForExit(Math.Max(1, budget - (int)clock.ElapsedMilliseconds)))
                {
                    result.status = "timed_out";
                    // Only this fixed disposable probe; never a user task/server.
                    try { process.Kill(); } catch { }
                    return result;
                }
                if (redirect && (!stdout.Wait(Math.Max(0, budget - (int)clock.ElapsedMilliseconds)) ||
                    !stderr.Wait(Math.Max(0, budget - (int)clock.ElapsedMilliseconds))))
                {
                    result.status = "timed_out";
                    return result;
                }
                result.exitCode = process.ExitCode;
                result.status = process.ExitCode == 0 ? "accepted" :
                    process.ExitCode == 81 ? "old_version" :
                    process.ExitCode == 82 ? "missing_module" : "exit_failed";
                return result;
            }
            catch (Exception exception)
            {
                while (exception.InnerException != null) exception = exception.InnerException;
                System.ComponentModel.Win32Exception native = exception as System.ComponentModel.Win32Exception;
                if (native != null) result.nativeCode = native.NativeErrorCode;
                if (result.stage == "environment") result.status = "environment_failed";
                else if (!started) result.stage = StartFailureStage(exception);
                else result.status = "query_failed";
                return result;
            }
        }
    }

    private static string StartFailureStage(Exception exception)
    {
        // Only allowlisted method names leave this boundary. Never return a
        // stack trace, framework exception message, file contents or arguments.
        StackFrame[] frames = new StackTrace(exception, false).GetFrames();
        if (frames != null)
        {
            foreach (StackFrame frame in frames)
            {
                System.Reflection.MethodBase method = frame.GetMethod();
                if (method == null || method.DeclaringType != typeof(Process)) continue;
                if (method.Name == "CreatePipeWithSecurityAttributes") return "pipe_create";
                if (method.Name == "CreatePipe") return "pipe_duplicate";
                // StartWithCreateProcess also contains setup steps, and JIT
                // inlining can omit inner frames. It cannot prove a failed
                // CreateProcess call, so retain the general process_start.
            }
        }
        return "process_start";
    }

    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CreatePipe(
        out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool DuplicateHandle(
        IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target,
        uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint options);
    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool OpenProcessToken(
        IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetTokenInformation(
        IntPtr token, int kind, IntPtr buffer, int size, out int needed);
}
