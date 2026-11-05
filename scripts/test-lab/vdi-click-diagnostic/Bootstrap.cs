using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Ordinary offline packaging. No installation, elevation, policy changes or
// antivirus exclusions. Exact embedded files are verified before use.
internal static class Bootstrap
{
    private static readonly string[] Names = { "Diagnostic.Host.exe", "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll", "WebView2-LICENSE.txt", "WebView2-NOTICE.txt" };
    private static byte[] Resource(string name)
    {
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Payload." + name))
        {
            if (stream == null || stream.Length > 16 * 1024 * 1024) throw new InvalidDataException();
            using (var memory = new MemoryStream()) { stream.CopyTo(memory); return memory.ToArray(); }
        }
    }
    private static string Hash(byte[] bytes)
    { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    private static void CheckManagedDirectories(string path, string trustedRoot)
    {
        // Windows may redirect LocalAppData through an approved VDI profile
        // mount/junction. Trust that OS-provided root; validate only our own
        // descendant directories, never the user's profile ancestors.
        string root = Path.GetFullPath(trustedRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string full = Path.GetFullPath(path);
        if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("outside managed root");
        for (var current = new DirectoryInfo(full); current != null && !String.Equals(current.FullName.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), root, StringComparison.OrdinalIgnoreCase); current = current.Parent)
            if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("reparse directory");
    }
    private static void SafeDirectory(string path, string trustedRoot)
    {
        CheckManagedDirectories(path, trustedRoot);
        Directory.CreateDirectory(path);
        CheckManagedDirectories(path, trustedRoot);
    }
    private static bool FileMatches(string path, string expected, int length)
    {
        if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || new FileInfo(path).Length != length) return false;
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant() == expected;
    }
    [STAThread] private static int Main(string[] args)
    {
        bool test = args.Length == 1 && args[0] == "--self-test";
        if (args.Length != 0 && !test) return 2;
        try
        {
            byte[] manifest = Resource("hashes.json");
            var hashes = new JavaScriptSerializer().Deserialize<Dictionary<string, string>>(Encoding.UTF8.GetString(manifest));
            if (hashes == null || hashes.Count != Names.Length) throw new InvalidDataException();
            string trustedRoot = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string root = Path.Combine(trustedRoot, "CompanyAgent", "vdi-click-diagnostic", "runtime", Hash(manifest).Substring(0, 24));
            SafeDirectory(root, trustedRoot);
            foreach (string name in Names)
            {
                string expected;
                if (!hashes.TryGetValue(name, out expected)) throw new InvalidDataException();
                byte[] content = Resource(name);
                if (Hash(content) != expected) throw new InvalidDataException();
                string path = Path.Combine(root, name);
                if (File.Exists(path))
                {
                    if (!FileMatches(path, expected, content.Length)) throw new InvalidDataException();
                    continue;
                }
                string temporary = Path.Combine(root, Guid.NewGuid().ToString("N") + ".tmp");
                try
                {
                    using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) stream.Write(content, 0, content.Length);
                    try { File.Move(temporary, path); }
                    catch (IOException)
                    {
                        if (!FileMatches(path, expected, content.Length)) throw;
                    }
                }
                finally { if (File.Exists(temporary)) File.Delete(temporary); }
            }
            var info = new ProcessStartInfo(Path.Combine(root, "Diagnostic.Host.exe"), test ? "--self-test" : "") { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden };
            using (var process = Process.Start(info))
            {
                if (!test) return 0;
                if (!process.WaitForExit(60000)) return 5;
                return process.ExitCode;
            }
        }
        catch
        {
            if (!test) MessageBox.Show("진단 도구의 실행 자료를 준비하지 못했습니다. 다운로드한 파일이 온전한지 확인해 주세요. 회사 보안 프로그램이 파일을 차단했다면 담당자에게 확인해 주세요.", "AX Workspace 클릭 위치 진단", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 3;
        }
    }
}
