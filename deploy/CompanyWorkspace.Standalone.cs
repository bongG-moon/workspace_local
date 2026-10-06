// Windows single-file delivery. The application and Claude transport remain in
// the existing, verified launcher; this bootstrap never changes user settings.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace CompanyAgent {
    internal sealed class StandaloneFailure : Exception {
        internal readonly int Code;
        internal StandaloneFailure(int code) { Code = code; }
    }

    internal static class WorkspaceStandalone {
        private const string PayloadPrefix = "Company-Workspace/";
        private static bool quiet;

        [STAThread]
        private static int Main(string[] args) {
            quiet = Array.IndexOf(args, "--no-browser") >= 0 || Array.IndexOf(args, "--verify-only") >= 0;
            try {
                Options options = ParseOptions(args);
                string[] build = ResourceText("WorkspaceBuild.txt", 1024).Replace("\r", "").TrimEnd('\n').Split('\n');
                if (build.Length != 2 || !Regex.IsMatch(build[0], @"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$") || !IsHash(build[1]))
                    throw new StandaloneFailure(51);
                string version = build[0], archiveHash = build[1].ToLowerInvariant();
                Dictionary<string, string> manifest = ParseManifest(ResourceText("WorkspacePayload.manifest.tsv", 4 * 1024 * 1024));
                string cacheRoot = options.CacheRoot ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CompanyAgent", "workspace-runtime");
                cacheRoot = AbsolutePath(cacheRoot);
                string target = Path.Combine(cacheRoot, version + "-" + archiveHash.Substring(0, 16));
                string sid = WindowsIdentity.GetCurrent().User.Value;
                string mutexName = @"Local\CompanyWorkspaceExtract-" + sid + "-" + archiveHash;
                using (Mutex mutex = new Mutex(false, mutexName)) {
                    bool held = false;
                    try {
                        try { held = mutex.WaitOne(TimeSpan.FromSeconds(60)); }
                        catch (AbandonedMutexException) { held = true; }
                        if (!held) throw new StandaloneFailure(55);
                        EnsureNoReparse(cacheRoot);
                        EnsureNoReparse(target);
                        if (Directory.Exists(target)) {
                            VerifyFiles(target, manifest);
                        } else {
                            Extract(cacheRoot, target, manifest, archiveHash);
                        }
                    } finally { if (held) mutex.ReleaseMutex(); }
                }
                if (options.VerifyOnly) return 0;
                return Launch(Path.Combine(target, "Company-Workspace"), options);
            } catch (StandaloneFailure error) { return Report(error.Code); }
            catch { return Report(59); }
        }

        private sealed class Options {
            internal string StateRoot, CacheRoot, PythonPath, ExecutionMode;
            internal bool NoBrowser, Demo, VerifyOnly;
        }

        private static Options ParseOptions(string[] args) {
            Options value = new Options();
            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < args.Length; i++) {
                string name = args[i];
                if (!seen.Add(name)) throw new StandaloneFailure(50);
                if (name == "--no-browser") value.NoBrowser = true;
                else if (name == "--demo") value.Demo = true;
                else if (name == "--verify-only") value.VerifyOnly = true;
                else if (name == "--execution-mode") {
                    if (++i >= args.Length || (args[i] != "normal" && args[i] != "administrator")) throw new StandaloneFailure(50);
                    value.ExecutionMode = args[i];
                }
                else if (name == "--state" || name == "--cache-root" || name == "--python") {
                    if (++i >= args.Length) throw new StandaloneFailure(50);
                    string path = AbsolutePath(args[i]);
                    if (name == "--state") value.StateRoot = path;
                    else if (name == "--cache-root") value.CacheRoot = path;
                    else {
                        if (!String.Equals(Path.GetExtension(path), ".exe", StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
                            throw new StandaloneFailure(50);
                        value.PythonPath = path;
                    }
                } else throw new StandaloneFailure(50);
            }
            return value;
        }

        private static string AbsolutePath(string path) {
            try {
                if (String.IsNullOrWhiteSpace(path) || path.IndexOf('"') >= 0 || path.IndexOf('\0') >= 0 || !Path.IsPathRooted(path))
                    throw new StandaloneFailure(50);
                // C:folder and \folder are rooted but depend on the caller's drive.
                if (!(path.StartsWith(@"\\", StringComparison.Ordinal) || (path.Length >= 3 && path[1] == ':' && (path[2] == '\\' || path[2] == '/'))))
                    throw new StandaloneFailure(50);
                return Path.GetFullPath(path);
            }
            catch (ArgumentException) { throw new StandaloneFailure(50); }
            catch (NotSupportedException) { throw new StandaloneFailure(50); }
            catch (PathTooLongException) { throw new StandaloneFailure(50); }
        }

        private static Stream Resource(string name) {
            Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
            if (stream == null) throw new StandaloneFailure(51);
            return stream;
        }

        private static string ResourceText(string name, int limit) {
            using (Stream stream = Resource(name)) {
                if (stream.Length > limit) throw new StandaloneFailure(51);
                byte[] data = new byte[(int)stream.Length];
                int read = 0;
                while (read < data.Length) {
                    int count = stream.Read(data, read, data.Length - read);
                    if (count == 0) throw new StandaloneFailure(51);
                    read += count;
                }
                return new UTF8Encoding(false, true).GetString(data);
            }
        }

        private static bool IsHash(string value) { return Regex.IsMatch(value, "^[0-9a-fA-F]{64}$"); }

        private static Dictionary<string, string> ParseManifest(string text) {
            Dictionary<string, string> entries = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string raw in text.Split('\n')) {
                string line = raw.TrimEnd('\r');
                if (line.Length == 0) continue;
                string[] fields = line.Split('\t');
                if (fields.Length != 2 || !IsHash(fields[0])) throw new StandaloneFailure(51);
                string name = ValidateRelative(fields[1], false);
                if (entries.ContainsKey(name)) throw new StandaloneFailure(51);
                entries.Add(name, fields[0]);
            }
            foreach (string required in new[] { "deploy/Start-CompanyWorkspace.ps1", "local_app/server.py" })
                if (!entries.ContainsKey(PayloadPrefix + required)) throw new StandaloneFailure(51);
            return entries;
        }

        private static string ValidateRelative(string name, bool directory) {
            if (String.IsNullOrEmpty(name) || name.IndexOf('\\') >= 0 || !name.StartsWith(PayloadPrefix, StringComparison.Ordinal))
                throw new StandaloneFailure(51);
            string path = directory ? name.TrimEnd('/') : name;
            foreach (string part in path.Split('/')) {
                if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".", StringComparison.Ordinal) || part.EndsWith(" ", StringComparison.Ordinal) ||
                    part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                    Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", RegexOptions.IgnoreCase))
                    throw new StandaloneFailure(51);
            }
            if (!directory && !path.StartsWith(PayloadPrefix, StringComparison.Ordinal)) throw new StandaloneFailure(51);
            return path;
        }

        private static void EnsureNoReparse(string path) {
            string current = Path.GetFullPath(path);
            while (!String.IsNullOrEmpty(current)) {
                try {
                    FileAttributes attributes = File.GetAttributes(current);
                    if ((attributes & FileAttributes.ReparsePoint) != 0) throw new StandaloneFailure(53);
                } catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                string parent = Path.GetDirectoryName(current);
                if (parent == current) break;
                current = parent;
            }
        }

        private static string Hash(Stream stream) {
            using (SHA256 algorithm = SHA256.Create())
                return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        private static void VerifyFiles(string root, Dictionary<string, string> manifest) {
            EnsureNoReparse(root);
            foreach (KeyValuePair<string, string> item in manifest) {
                string file = Path.Combine(root, item.Key.Replace('/', Path.DirectorySeparatorChar));
                EnsureNoReparse(file);
                if (!File.Exists(file)) throw new StandaloneFailure(54);
                using (FileStream stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
                    if (!String.Equals(Hash(stream), item.Value, StringComparison.OrdinalIgnoreCase)) throw new StandaloneFailure(54);
            }
        }

        private static void Extract(string cacheRoot, string target, Dictionary<string, string> manifest, string expectedHash) {
            Directory.CreateDirectory(cacheRoot);
            EnsureNoReparse(cacheRoot);
            string staging = Path.Combine(cacheRoot, ".staging-" + expectedHash.Substring(0, 16) + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(staging);
            try {
                using (Stream resource = Resource("WorkspacePayload.zip")) {
                    if (!String.Equals(Hash(resource), expectedHash, StringComparison.OrdinalIgnoreCase)) throw new StandaloneFailure(51);
                    resource.Position = 0;
                    using (ZipArchive archive = new ZipArchive(resource, ZipArchiveMode.Read, false)) {
                        HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                        int fileCount = 0;
                        foreach (ZipArchiveEntry entry in archive.Entries) {
                            // Windows PowerShell Compress-Archive writes some
                            // directory records with backslashes even when its
                            // file records use '/'. Canonicalize ZIP separators
                            // before the same strict containment/duplicate rules;
                            // the embedded manifest itself remains slash-only.
                            string archiveName = entry.FullName.Replace('\\', '/');
                            bool directory = archiveName.EndsWith("/", StringComparison.Ordinal);
                            string relative = ValidateRelative(archiveName, directory);
                            if (!seen.Add(relative)) throw new StandaloneFailure(51);
                            if (directory) {
                                bool expectedDirectory = false;
                                foreach (string name in manifest.Keys)
                                    if (name.StartsWith(relative + "/", StringComparison.OrdinalIgnoreCase)) { expectedDirectory = true; break; }
                                if (!expectedDirectory || entry.Length != 0) throw new StandaloneFailure(51);
                                continue;
                            }
                            if (!manifest.ContainsKey(relative) || entry.Length > 256L * 1024 * 1024) throw new StandaloneFailure(51);
                            string file = Path.Combine(staging, relative.Replace('/', Path.DirectorySeparatorChar));
                            EnsureNoReparse(file);
                            Directory.CreateDirectory(Path.GetDirectoryName(file));
                            using (Stream input = entry.Open())
                            using (FileStream output = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                                input.CopyTo(output);
                            fileCount++;
                        }
                        if (fileCount != manifest.Count) throw new StandaloneFailure(51);
                    }
                }
                VerifyFiles(staging, manifest);
                EnsureNoReparse(target);
                Directory.Move(staging, target);
            } finally {
                // Only our uncommitted staging tree is eligible for cleanup.
                // Never remove a completed cache or files used by a running app.
                try {
                    if (Directory.Exists(staging)) {
                        CheckCleanupTree(staging);
                        Directory.Delete(staging, true);
                    }
                } catch { }
            }
        }

        private static void CheckCleanupTree(string root) {
            EnsureNoReparse(root);
            foreach (string path in Directory.GetFileSystemEntries(root)) {
                EnsureNoReparse(path);
                if (Directory.Exists(path)) CheckCleanupTree(path);
            }
        }

        // Windows argv quoting, including a trailing backslash before a quote.
        private static string Quote(string value) {
            StringBuilder result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') result.Append('\\', slashes * 2 + 1);
                else result.Append('\\', slashes);
                result.Append(c);
                slashes = 0;
            }
            result.Append('\\', slashes * 2);
            return result.Append('"').ToString();
        }

        private static int Launch(string appRoot, Options options) {
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            if (!File.Exists(powershell)) throw new StandaloneFailure(56);
            // The shared launcher checks the user's existing Python. The EXE
            // carries application files only; it never provides an interpreter
            // or installs/downloads missing prerequisites.
            string command = "-NoLogo -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(Path.Combine(appRoot, @"deploy\Start-CompanyWorkspace.ps1"));
            if (options.PythonPath != null) command += " -PythonCommand " + Quote(options.PythonPath);
            if (options.NoBrowser) command += " -NoBrowser";
            if (options.Demo) command += " -Demo";
            if (options.StateRoot != null) command += " -StateRoot " + Quote(options.StateRoot);
            if (options.ExecutionMode != null) command += " -ExecutionMode " + options.ExecutionMode;
            ProcessStartInfo start = new ProcessStartInfo(powershell, command);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.WorkingDirectory = appRoot;
            // Profiles may emit private diagnostics. Drain both streams without
            // forwarding, recording, or putting them in the UI. Environment is
            // inherited unchanged; notably no PATH/Python/config overrides.
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            using (Process process = new Process()) {
                process.StartInfo = start;
                process.OutputDataReceived += delegate { };
                process.ErrorDataReceived += delegate { };
                try { if (!process.Start()) throw new StandaloneFailure(56); }
                catch (System.ComponentModel.Win32Exception) { throw new StandaloneFailure(56); }
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                if (!process.WaitForExit(90000)) throw new StandaloneFailure(57);
                int code = process.ExitCode;
                // 20 means the existing launcher already displayed its reason.
                if (code == 0 || code == 20) return code;
                if (options.NoBrowser && code >= 22 && code <= 47) {
                    Console.Error.WriteLine("Company Workspace 실행 환경을 확인하지 못했습니다. 오류 코드: WS-" + code);
                    return code;
                }
                throw new StandaloneFailure(58);
            }
        }

        private static int Report(int code) {
            string message;
            switch (code) {
                case 50: message = "실행 옵션이나 경로를 확인해 주세요. 평소에는 EXE 파일을 더블클릭하면 됩니다."; break;
                case 51: message = "실행 파일에 포함된 자료를 확인하지 못했습니다. EXE 파일을 다시 받아 실행해 주세요."; break;
                case 53: message = "실행 자료를 저장할 폴더에 연결 경로가 있어 시작하지 못했습니다. 기존 자료와 설정은 변경하지 않았습니다."; break;
                case 54: message = "보관된 실행 자료가 없거나 변경되어 시작하지 못했습니다. 실행 중인 업무를 종료한 뒤 담당자에게 실행 캐시 확인을 요청해 주세요. 기존 자료는 삭제하지 않았습니다."; break;
                case 55: message = "다른 실행기에서 자료를 준비하고 있습니다. 잠시 뒤 다시 열어 주세요."; break;
                case 56: message = "Windows PowerShell을 찾거나 시작하지 못했습니다. PC의 기존 PowerShell 실행 환경을 확인해 주세요. 설치나 설정 변경은 하지 않았습니다."; break;
                case 57: message = "실행 준비 시간이 길어지고 있습니다. 준비 중인 프로세스는 종료하지 않았습니다. 잠시 뒤 앱이 열리는지 확인해 주세요."; break;
                case 58: message = "앱 실행을 완료하지 못했습니다. 실행 캐시의 Company-Workspace 폴더에 있는 Check-Workspace.cmd로 진단하거나 이 오류 코드를 담당자에게 전달해 주세요."; break;
                default: message = "실행 자료를 준비하지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요. 개인 설정은 변경하지 않았습니다."; break;
            }
            message += Environment.NewLine + "오류 코드: EXE-" + code;
            if (quiet) Console.Error.WriteLine(message);
            else MessageBox.Show(message, "Company Workspace", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return code;
        }
    }
}
