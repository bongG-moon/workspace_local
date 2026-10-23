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
        private static string stage = "실행 정보 확인";
        private static string failureDetail = "";
        private static string failurePath = "";
        [System.Runtime.InteropServices.DllImport("shell32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
        private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

        [STAThread]
        private static int Main(string[] args) {
            SetCurrentProcessExplicitAppUserModelID("AX.Workspace.Desktop");
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
                        stage = "실행 자료 폴더 접근";
                        EnsureNoReparse(cacheRoot);
                        EnsureNoReparse(target);
                        if (Directory.Exists(target)) {
                            stage = "기존 실행 자료 읽기 및 검사";
                            VerifyFiles(target, manifest);
                        } else {
                            Extract(cacheRoot, target, manifest, archiveHash);
                        }
                    } finally { if (held) mutex.ReleaseMutex(); }
                }
                if (options.VerifyOnly) return 0;
                stage = "앱 실행기 시작";
                return Launch(Path.Combine(target, "Company-Workspace"), options, version);
            } catch (StandaloneFailure error) { return Report(error.Code); }
            catch (Exception error) {
                failureDetail = "실패 단계: " + stage + Environment.NewLine +
                    "진단: " + error.GetType().Name + " / 0x" + error.HResult.ToString("X8");
                if (failurePath.Length != 0) failureDetail += Environment.NewLine + "대상 위치: " + failurePath;
                return Report(ClassifyFailure(error));
            }
        }

        private static int ClassifyFailure(Exception error) {
            int native = error.HResult & 0xffff;
            if (error is UnauthorizedAccessException || error is System.Security.SecurityException || native == 5) return 60;
            if (error is PathTooLongException) return 63;
            if (error is InvalidDataException) return 64;
            if (error is IOException) {
                if (native == 112 || native == 39) return 61;
                if (native == 32 || native == 33) return 62;
                if (native == 3 || native == 2 || native == 53 || native == 67 || native == 267 || native == 183 || native == 80) return 65;
                return 66;
            }
            return 59;
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
                    if (++i >= args.Length || (args[i] != "auto" && args[i] != "normal" && args[i] != "administrator")) throw new StandaloneFailure(50);
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
            stage = "실행 자료 저장 폴더 생성";
            Directory.CreateDirectory(cacheRoot);
            EnsureNoReparse(cacheRoot);
            string staging = Path.Combine(cacheRoot, ".staging-" + expectedHash.Substring(0, 16) + "-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(staging);
            try {
                stage = "내장 실행 자료 압축 해제";
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
                stage = "압축 해제한 자료 검사";
                VerifyFiles(staging, manifest);
                EnsureNoReparse(target);
                stage = "실행 자료 폴더 확정";
                failurePath = target;
                FinalizeExtraction(staging, target, manifest);
                failurePath = "";
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

        private static void FinalizeExtraction(string staging, string target, Dictionary<string, string> manifest) {
            // A VDI/antivirus reader may allow extraction and hashing but briefly
            // deny renaming the directory (including ERROR_ACCESS_DENIED).
            // Retry only publication, never unpack again or weaken permissions.
            int[] retryDelays = { 100, 250, 500, 1000, 1500 };
            for (int attempt = 0; ; attempt++) {
                EnsureNoReparse(staging);
                EnsureNoReparse(target);
                // The named mutex is local to a Windows login session. Another
                // session sharing a profile may have published this same bundle.
                // Reuse it only after full verification; never replace its files.
                if (Directory.Exists(target)) {
                    stage = "기존 실행 자료 검사";
                    VerifyFiles(target, manifest);
                    return;
                }
                try {
                    Directory.Move(staging, target);
                    return;
                } catch (IOException error) {
                    int native = error.HResult & 0xffff;
                    if (attempt >= retryDelays.Length ||
                        (native != 5 && native != 32 && native != 33 && native != 183 && native != 80)) throw;
                } catch (UnauthorizedAccessException) {
                    if (attempt >= retryDelays.Length) throw;
                }
                Thread.Sleep(retryDelays[attempt]);
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

        private static int Launch(string appRoot, Options options, string version) {
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
            // Read-only display evidence survives managed app-file forwarding.
            // A new bootstrap always overwrites inherited hints. This is never
            // used as authorization or as an executable path from the app.
            start.EnvironmentVariables["COMPANY_WORKSPACE_ENTRY_VERSION"] = version;
            start.EnvironmentVariables["COMPANY_WORKSPACE_ENTRY_PROTOCOL"] = "1";
            // Pin the real entry EXE, never the private WebView host or its URL.
            // Reconstruct only parsed launch options; no session/auth command line.
            string entry = Assembly.GetExecutingAssembly().Location;
            string relaunch = Quote(entry);
            if (options.StateRoot != null) relaunch += " --state " + Quote(options.StateRoot);
            if (options.CacheRoot != null) relaunch += " --cache-root " + Quote(options.CacheRoot);
            if (options.PythonPath != null) relaunch += " --python " + Quote(options.PythonPath);
            if (options.Demo) relaunch += " --demo";
            // Privilege is selected by Windows on each launch, not stored in a pin.
            start.EnvironmentVariables["COMPANY_WORKSPACE_RELAUNCH_COMMAND"] = relaunch;
            start.EnvironmentVariables["COMPANY_WORKSPACE_RELAUNCH_ICON"] = entry;

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
                if (!WaitForLauncher(process, 90000)) throw new StandaloneFailure(57);
                int code = process.ExitCode;
                // 20 means the existing launcher already displayed its reason.
                if (code == 0 || code == 20) return code;
                if (options.NoBrowser && code >= 22 && code <= 48) {
                    Console.Error.WriteLine("AX Workspace 실행 환경을 확인하지 못했습니다. 오류 코드: WS-" + code);
                    return code;
                }
                throw new StandaloneFailure(58);
            }
        }

        private static bool WaitForLauncher(Process process, int timeoutMilliseconds) {
            if (process.WaitForExit(timeoutMilliseconds)) return true;
            // This retained handle belongs only to our startup PowerShell. A
            // timed-out profile/launcher must not accumulate after each retry.
            // Do not terminate a process tree: the app server and an upgrade
            // coordinator deliberately outlive their short startup helper.
            try {
                if (!process.HasExited) process.Kill();
                process.WaitForExit(2000);
            } catch (InvalidOperationException) { }
              catch (System.ComponentModel.Win32Exception) { }
            return false;
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
                case 60: message = stage == "실행 자료 폴더 확정"
                    ? "실행 자료 저장과 검사는 완료했지만 폴더 이름 변경이 거부되었습니다. 잠시 기다린 뒤 재시도했으나 해결되지 않았습니다. 담당자에게 아래 대상 위치의 폴더 이름 변경 권한과 VDI·보안 프로그램의 파일 잠금 또는 차단 기록을 확인해 달라고 요청해 주세요."
                    : "실행 자료 폴더 또는 파일에 접근할 권한이 없습니다. 사용자 폴더의 읽기·쓰기 권한과 보안 프로그램 차단 기록을 확인해 주세요."; break;
                case 61: message = "실행 자료를 저장할 디스크 공간이 부족합니다. 실행 캐시가 저장되는 드라이브의 여유 공간을 확보해 주세요."; break;
                case 62: message = "실행 자료 파일을 다른 프로그램이 사용 중입니다. 실행 중인 AX Workspace를 종료한 뒤 다시 시도해 주세요. 계속되면 보안 프로그램의 파일 검사 여부를 확인해 주세요."; break;
                case 63: message = "실행 자료의 파일 경로가 너무 깁니다. 더 짧은 실행 캐시 경로를 사용해 주세요."; break;
                case 64: message = "내장 실행 자료의 압축을 읽지 못했습니다. 설치파일을 다시 받아 확인해 주세요."; break;
                case 65: message = "실행 자료 경로를 사용할 수 없습니다. 드라이브 연결 상태와 같은 이름의 파일 또는 폴더가 있는지 확인해 주세요."; break;
                case 66: message = "실행 자료를 읽거나 저장하는 중 입출력 오류가 발생했습니다. 디스크 상태와 보안 프로그램 차단 기록을 확인해 주세요."; break;
                default: message = "실행 자료를 준비하지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요. 개인 설정은 변경하지 않았습니다."; break;
            }
            message += Environment.NewLine + "오류 코드: EXE-" + code;
            if (failureDetail.Length != 0) message += Environment.NewLine + failureDetail;
            if (quiet) Console.Error.WriteLine(message);
            else MessageBox.Show(message, "AX Workspace", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return code;
        }
    }
}
