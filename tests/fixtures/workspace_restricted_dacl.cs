// Test fixture only. The real user's original token and every file ACL remain
// unchanged. AdminOnlyCopy runs inside a disposable restricted host created by
// this test and changes only that host's copied token after checking TokenId.
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

public static class WorkspaceRestrictedDaclFixture
{
    public static string TokenId(IntPtr token)
    {
        IntPtr stats = Query(token, 10);
        try { return Marshal.ReadInt64(stats).ToString("X16"); }
        finally { Marshal.FreeHGlobal(stats); }
    }

    public static void AdminOnlyCopy(string originalTokenId, IntPtr copy)
    {
        if (String.IsNullOrEmpty(originalTokenId) || originalTokenId.Length != 16 ||
            originalTokenId == TokenId(copy)) throw new InvalidOperationException("refusing_original_token");
        RawAcl acl = new RawAcl(2, 2);
        acl.InsertAce(0, new CommonAce(AceFlags.None, AceQualifier.AccessAllowed, 0x10000000,
            new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), false, null));
        acl.InsertAce(1, new CommonAce(AceFlags.None, AceQualifier.AccessAllowed, 0x10000000,
            new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), false, null));
        byte[] bytes = new byte[acl.BinaryLength];
        acl.GetBinaryForm(bytes, 0);
        IntPtr buffer = IntPtr.Zero, descriptor = IntPtr.Zero;
        try
        {
            buffer = Marshal.AllocHGlobal(bytes.Length);
            descriptor = Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.Copy(bytes, 0, buffer, bytes.Length);
            Marshal.WriteIntPtr(descriptor, buffer);
            if (!SetTokenInformation(copy, 6, descriptor, IntPtr.Size))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally
        {
            if (descriptor != IntPtr.Zero) Marshal.FreeHGlobal(descriptor);
            if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
        }
    }

    public static string DaclHash(IntPtr token)
    {
        using (SHA256 hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(Dacl(token))).Replace("-", "");
    }

    public static bool DaclHasUser(IntPtr token)
    {
        IntPtr user = Query(token, 1);
        try
        {
            SecurityIdentifier sid = new SecurityIdentifier(Marshal.ReadIntPtr(user));
            byte[] bytes = Dacl(token);
            if (bytes.Length == 0) return false;
            foreach (GenericAce item in new RawAcl(bytes, 0))
            {
                CommonAce ace = item as CommonAce;
                if (ace != null && ace.AceQualifier == AceQualifier.AccessAllowed &&
                    ace.SecurityIdentifier.Equals(sid)) return true;
            }
            return false;
        }
        finally { Marshal.FreeHGlobal(user); }
    }

    private static byte[] Dacl(IntPtr token)
    {
        IntPtr info = Query(token, 6);
        try
        {
            IntPtr acl = Marshal.ReadIntPtr(info);
            if (acl == IntPtr.Zero) return new byte[0];
            byte[] bytes = new byte[(ushort)Marshal.ReadInt16(acl, 2)];
            Marshal.Copy(acl, bytes, 0, bytes.Length);
            return bytes;
        }
        finally { Marshal.FreeHGlobal(info); }
    }

    private static IntPtr Query(IntPtr token, int kind)
    {
        int needed;
        GetTokenInformation(token, kind, IntPtr.Zero, 0, out needed);
        if (needed < 1 || needed > 65536) throw new InvalidOperationException("token_size");
        IntPtr buffer = Marshal.AllocHGlobal(needed);
        if (!GetTokenInformation(token, kind, buffer, needed, out needed))
        {
            int code = Marshal.GetLastWin32Error();
            Marshal.FreeHGlobal(buffer);
            throw new Win32Exception(code);
        }
        return buffer;
    }

    public static int Pipe()
    {
        IntPtr read = IntPtr.Zero, write = IntPtr.Zero;
        SecurityAttributes attributes = new SecurityAttributes { Length = Marshal.SizeOf(typeof(SecurityAttributes)), Inherit = true };
        if (!CreatePipe(out read, out write, ref attributes, 0)) return Marshal.GetLastWin32Error();
        try { return 0; }
        finally { CloseHandle(read); CloseHandle(write); }
    }

    public static int SelfDuplicateAccess()
    {
        IntPtr handle = OpenProcess(0x40, false, GetCurrentProcessId());
        if (handle == IntPtr.Zero) return Marshal.GetLastWin32Error();
        CloseHandle(handle);
        return 0;
    }

    public sealed class ProbeResult
    {
        public string Status = "start_failed";
        public int? NativeCode;
        public int? ExitCode;
        public string Output;
    }

    public static ProbeResult RedirectedPython(string python, string script)
    {
        ProbeResult result = new ProbeResult();
        using (Process process = new Process())
        {
            process.StartInfo = new ProcessStartInfo(python, "-B -X utf8 \"" + script + "\"");
            process.StartInfo.UseShellExecute = false;
            process.StartInfo.CreateNoWindow = true;
            process.StartInfo.RedirectStandardOutput = true;
            process.StartInfo.RedirectStandardError = true;
            process.StartInfo.StandardOutputEncoding = Encoding.UTF8;
            process.StartInfo.StandardErrorEncoding = Encoding.UTF8;
            process.StartInfo.EnvironmentVariables["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false";
            process.StartInfo.EnvironmentVariables.Remove("PYLAUNCHER_ALLOW_INSTALL");
            process.StartInfo.EnvironmentVariables.Remove("PYLAUNCHER_ALWAYS_INSTALL");
            try
            {
                if (!process.Start()) return result;
                var output = process.StandardOutput.ReadToEndAsync();
                var error = process.StandardError.ReadToEndAsync();
                if (!process.WaitForExit(12000))
                {
                    result.Status = "timed_out";
                    try { process.Kill(); } catch { }
                    return result;
                }
                if (!output.Wait(1000) || !error.Wait(1000)) { result.Status = "output_timeout"; return result; }
                result.ExitCode = process.ExitCode;
                result.Status = process.ExitCode == 0 ? "accepted" : "exit_failed";
                if (process.ExitCode == 0 && output.Result.Length <= 4096) result.Output = output.Result;
                return result;
            }
            catch (Win32Exception failure) { result.NativeCode = failure.NativeErrorCode; return result; }
        }
    }

    [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes
    {
        internal int Length;
        internal IntPtr Descriptor;
        [MarshalAs(UnmanagedType.Bool)] internal bool Inherit;
    }
    [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
    [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr OpenProcess(uint access, bool inherit, uint id);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr buffer, int size, out int needed);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool SetTokenInformation(IntPtr token, int kind, IntPtr buffer, int size);
}
