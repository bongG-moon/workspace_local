// Reusable, anonymous GitLab installer. No credentials, bundled application or runtime downloader.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;

namespace AxWorkspace.OnlineInstaller
{
    public sealed class InstallerException : Exception
    {
        public InstallerException(string message) : base(message) { }
    }

    // Small strict parser: bounded input/depth, duplicate property rejection, integer-only metadata.
    internal sealed class Json
    {
        private readonly string text;
        private int position;
        private Json(byte[] bytes) { text = new UTF8Encoding(false, true).GetString(bytes).TrimStart('\ufeff'); }
        public static object Parse(byte[] bytes) {
            Json parser = new Json(bytes);
            object value = parser.Value(0);
            parser.Space();
            if (parser.position != parser.text.Length) throw Bad();
            return value;
        }
        internal static InstallerException Bad() { return new InstallerException("배포 정보의 형식이나 무결성을 확인하지 못했습니다. 배포 담당자에게 문의해 주세요."); }
        private void Space() { while (position < text.Length && " \r\n\t".IndexOf(text[position]) >= 0) position++; }
        private char Take() { if (position == text.Length) throw Bad(); return text[position++]; }
        private object Value(int depth) {
            if (depth > 24) throw Bad();
            Space();
            if (position == text.Length) throw Bad();
            char c = text[position];
            if (c == '"') return String();
            if (c == '{') {
                position++; Space();
                Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.Ordinal);
                if (position < text.Length && text[position] == '}') { position++; return result; }
                while (true) {
                    Space(); string key = String(); Space();
                    if (Take() != ':' || result.ContainsKey(key)) throw Bad();
                    result.Add(key, Value(depth + 1)); Space();
                    c = Take(); if (c == '}') return result; if (c != ',') throw Bad();
                }
            }
            if (c == '[') {
                position++; Space(); List<object> result = new List<object>();
                if (position < text.Length && text[position] == ']') { position++; return result; }
                while (true) {
                    if (result.Count > 1024) throw Bad();
                    result.Add(Value(depth + 1)); Space(); c = Take();
                    if (c == ']') return result; if (c != ',') throw Bad();
                }
            }
            foreach (string literal in new string[] { "true", "false", "null" }) {
                if (position + literal.Length <= text.Length && text.Substring(position, literal.Length) == literal) {
                    position += literal.Length;
                    return literal == "null" ? null : (object)(literal == "true");
                }
            }
            int start = position;
            if (c == '-') position++;
            while (position < text.Length && text[position] >= '0' && text[position] <= '9') position++;
            string number = text.Substring(start, position - start);
            long parsed;
            if (!Regex.IsMatch(number, @"\A-?(0|[1-9][0-9]*)\z") || !Int64.TryParse(number, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out parsed)) throw Bad();
            return parsed;
        }
        private string String() {
            if (Take() != '"') throw Bad();
            StringBuilder value = new StringBuilder();
            while (true) {
                char c = Take(); if (c == '"') return value.ToString(); if (c < 32) throw Bad();
                if (c == '\\') {
                    c = Take();
                    switch (c) {
                        case '"': case '\\': case '/': break;
                        case 'b': c = '\b'; break; case 'f': c = '\f'; break;
                        case 'n': c = '\n'; break; case 'r': c = '\r'; break; case 't': c = '\t'; break;
                        case 'u':
                            if (position + 4 > text.Length) throw Bad();
                            ushort code;
                            if (!UInt16.TryParse(text.Substring(position, 4), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out code)) throw Bad();
                            position += 4; c = (char)code; break;
                        default: throw Bad();
                    }
                }
                value.Append(c);
            }
        }
        internal static Dictionary<string, object> Object(object value) {
            Dictionary<string, object> result = value as Dictionary<string, object>;
            if (result == null) throw Bad(); return result;
        }
        internal static string Text(Dictionary<string, object> value, string key) {
            object item; if (!value.TryGetValue(key, out item) || !(item is string)) throw Bad(); return (string)item;
        }
        internal static long Number(Dictionary<string, object> value, string key) {
            object item; if (!value.TryGetValue(key, out item) || !(item is long)) throw Bad(); return (long)item;
        }
        internal static void Keys(Dictionary<string, object> value, params string[] keys) {
            if (value.Count != keys.Length) throw Bad();
            foreach (string key in keys) if (!value.ContainsKey(key)) throw Bad();
        }
    }

    public sealed class Source
    {
        public string BaseUrl { get; private set; }
        public string ProjectId { get; private set; }
        private readonly HashSet<string> origins = new HashSet<string>(StringComparer.Ordinal);
        public string RegistryRoot { get { return BaseUrl + "/api/v4/projects/" + ProjectId + "/packages/generic"; } }
        public string LatestUrl { get { return RegistryRoot + "/company-workspace-channel/0.0.0/latest.json"; } }
        public static bool IsVersion(string value) { return value != null && Regex.IsMatch(value, @"\A(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\z"); }
        public static Source Parse(byte[] bytes) {
            if (bytes == null || bytes.Length == 0 || bytes.Length > 16384) throw Json.Bad();
            Dictionary<string, object> config = Json.Object(Json.Parse(bytes));
            if (!config.ContainsKey("allowedDownloadOrigins")) config.Add("allowedDownloadOrigins", new List<object>());
            Json.Keys(config, "schema", "provider", "baseUrl", "projectId", "packageName", "channelPackage", "channelVersion", "allowedDownloadOrigins");
            if (Json.Number(config, "schema") != 1 || Json.Text(config, "provider") != "gitlab" ||
                Json.Text(config, "packageName") != "company-workspace" || Json.Text(config, "channelPackage") != "company-workspace-channel" ||
                Json.Text(config, "channelVersion") != "0.0.0") throw Json.Bad();
            string baseUrl = Json.Text(config, "baseUrl"); Uri uri = Https(baseUrl);
            int pathStart = baseUrl.IndexOf('/', "https://".Length);
            string rawPath = pathStart < 0 ? "" : baseUrl.Substring(pathStart);
            string path = uri.AbsolutePath.TrimEnd('/');
            if (uri.Query.Length != 0 || !Regex.IsMatch(path, @"\A(?:/[A-Za-z0-9._~-]+)*\z") ||
                !Regex.IsMatch(rawPath.TrimEnd('/'), @"\A(?:/[A-Za-z0-9._~-]+)*\z") ||
                Array.Exists(baseUrl.Split('/'), delegate(string part) { return part == "." || part == ".."; })) throw Json.Bad();
            Source result = new Source(); result.BaseUrl = Origin(uri) + path;
            result.ProjectId = Json.Text(config, "projectId");
            if (!Regex.IsMatch(result.ProjectId, @"\A[1-9][0-9]{0,19}\z")) throw Json.Bad();
            List<object> allowed = config["allowedDownloadOrigins"] as List<object>;
            if (allowed == null || allowed.Count > 12) throw Json.Bad();
            foreach (object entry in allowed) {
                if (!(entry is string)) throw Json.Bad(); Uri origin = Https((string)entry);
                if (origin.AbsolutePath != "/" || origin.Query.Length != 0) throw Json.Bad();
                result.origins.Add(Origin(origin));
            }
            return result;
        }
        internal static Uri Https(string value) {
            Uri uri;
            if (String.IsNullOrEmpty(value) || value.Length > 8192 || value.IndexOf('\\') >= 0 ||
                Regex.IsMatch(value, @"[\x00-\x20\x7f]") || !value.StartsWith("https://", StringComparison.Ordinal) ||
                !Uri.TryCreate(value, UriKind.Absolute, out uri) || uri.Scheme != "https" || String.IsNullOrEmpty(uri.Host) ||
                uri.UserInfo.Length != 0 || uri.Fragment.Length != 0 || uri.Authority.IndexOf('%') >= 0 || uri.Port < 1 || uri.Port > 65535) throw Json.Bad();
            return uri;
        }
        private static string Origin(Uri uri) { return uri.GetLeftPart(UriPartial.Authority).ToLowerInvariant(); }
        public string AssetUrl(string version, string name) {
            if (!IsVersion(version) || !(name == "SHA256SUMS.txt" || name == "AX-Workspace-" + version + "-Setup.exe")) throw Json.Bad();
            return RegistryRoot + "/company-workspace/" + version + "/" + name;
        }
        public void ValidateRedirect(string next, string initial) {
            Uri uri = Https(next); Https(initial);
            if (next != initial && !origins.Contains(Origin(uri))) throw Json.Bad();
        }
    }

    public sealed class TransferResponse : IDisposable
    {
        public int Status;
        public long ContentLength = -1;
        public string Encoding = "identity";
        public string Location;
        public Stream Body;
        public Action Close;
        public void Dispose() { try { if (Body != null) Body.Dispose(); } finally { if (Close != null) Close(); } }
    }
    public interface ITransport { TransferResponse Open(string url, CancellationToken cancel, DateTime deadline); }
    public interface IInstallerRunner { int Run(string filename, Action started); }
    public sealed class HttpsTransport : ITransport
    {
        public TransferResponse Open(string url, CancellationToken cancel, DateTime deadline) {
            Source.Https(url);
            Engine.Active(cancel, deadline);
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.AllowAutoRedirect = false; request.UseDefaultCredentials = false; request.Credentials = null;
            request.Proxy = WebRequest.GetSystemWebProxy(); if (request.Proxy != null) request.Proxy.Credentials = null;
            request.Timeout = Math.Max(1, Math.Min(10000, (int)(deadline - DateTime.UtcNow).TotalMilliseconds));
            request.ReadWriteTimeout = request.Timeout; request.AutomaticDecompression = DecompressionMethods.None;
            request.UserAgent = "AX-Workspace-Online-Installer"; request.Accept = "application/octet-stream";
            request.Headers[HttpRequestHeader.AcceptEncoding] = "identity";
            request.Headers[HttpRequestHeader.CacheControl] = "no-cache"; request.KeepAlive = false;
            CancellationTokenRegistration registration = cancel.Register(delegate { request.Abort(); });
            System.Threading.Timer timer = new System.Threading.Timer(delegate { request.Abort(); }, null,
                Math.Max(1, (int)(deadline - DateTime.UtcNow).TotalMilliseconds), Timeout.Infinite);
            try {
                HttpWebResponse response;
                try { response = (HttpWebResponse)request.GetResponse(); }
                catch (WebException e) { if (e.Response == null) throw; response = (HttpWebResponse)e.Response; }
                return new TransferResponse { Status = (int)response.StatusCode, ContentLength = response.ContentLength,
                    Location = response.Headers[HttpResponseHeader.Location], Encoding = response.ContentEncoding,
                    Body = response.GetResponseStream(), Close = delegate { response.Dispose(); timer.Dispose(); registration.Dispose(); } };
            } catch { timer.Dispose(); registration.Dispose(); cancel.ThrowIfCancellationRequested(); throw; }
        }
    }
    public sealed class NativeInstallerRunner : IInstallerRunner
    {
        public int Run(string filename, Action started) {
            using (Process child = Process.Start(new ProcessStartInfo(filename) { UseShellExecute = false, WorkingDirectory = Path.GetDirectoryName(filename) })) {
                if (child == null) throw new InstallerException("설치 창을 열지 못했습니다. 다시 시도해 주세요.");
                started(); child.WaitForExit(); return child.ExitCode;
            }
        }
    }
    public sealed class Release
    {
        public string Version { get; internal set; }
        public string Title { get; internal set; }
        public string Notes { get; internal set; }
        internal long ChecksumSize;
        internal string ChecksumHash;
    }

    internal sealed class OwnedDownload : IDisposable
    {
        public readonly string Filename;
        public FileStream File;
        private readonly string directory;
        private bool fileCreated, directoryCreated;
        private int parentLockCount;
        private readonly List<SafeFileHandle> locks = new List<SafeFileHandle>();
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        private void LockParents(string path) {
            List<string> parents = new List<string>();
            for (string current = Path.GetFullPath(path); current != null; current = Path.GetDirectoryName(current)) parents.Add(current);
            parents.Reverse();
            foreach (string current in parents) {
                if ((System.IO.File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw Json.Bad();
                SafeFileHandle handle = CreateFile(current, 0, 3, IntPtr.Zero, 3, 0x02000000 | 0x00200000, IntPtr.Zero);
                if (handle.IsInvalid) { handle.Dispose(); throw new IOException("다운로드 폴더를 보호하지 못했습니다."); }
                locks.Add(handle);
                if ((System.IO.File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw Json.Bad();
            }
        }
        public OwnedDownload(string root, string name) {
            directory = Path.Combine(Path.GetFullPath(root), Guid.NewGuid().ToString("N")); Filename = Path.Combine(directory, name);
            try {
                // Inspect every existing ancestor before creating anything; reject junctions/reparse paths.
                for (string current = Path.GetFullPath(root); current != null; current = Path.GetDirectoryName(current))
                    if (Directory.Exists(current) && (System.IO.File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw Json.Bad();
                Directory.CreateDirectory(root);
                LockParents(root);
                parentLockCount = locks.Count;
                DirectorySecurity security = new DirectorySecurity(); security.SetAccessRuleProtection(true, false);
                security.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User, FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
                if (Directory.Exists(directory)) throw new IOException();
                Directory.CreateDirectory(directory, security); directoryCreated = true;
                SafeFileHandle ownLock = CreateFile(directory, 0, 3, IntPtr.Zero, 3, 0x02000000 | 0x00200000, IntPtr.Zero);
                if (ownLock.IsInvalid) { ownLock.Dispose(); throw new IOException(); }
                locks.Add(ownLock);
                if ((System.IO.File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) throw Json.Bad();
                File = new FileStream(Filename, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.Read, 65536, FileOptions.SequentialScan);
                fileCreated = true;
            } catch { Dispose(); throw; }
        }
        public void Dispose() {
            if (File != null) { File.Dispose(); File = null; }
            // Delete only our exact downloaded file and an empty uniquely owned directory.
            try { if (fileCreated) System.IO.File.Delete(Filename); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            for (int i = locks.Count - 1; i >= parentLockCount; i--) locks[i].Dispose();
            try { if (directoryCreated) Directory.Delete(directory, false); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            for (int i = parentLockCount - 1; i >= 0; i--) locks[i].Dispose(); locks.Clear(); parentLockCount = 0;
        }
        public void Seal() {
            File.Flush(true); File.Dispose(); File = null;
            // Windows cannot map an executable while a writer is open. Reopen read-only BEFORE
            // hashing so any change in this narrow transition is detected; then retain the lock.
            File = new FileStream(Filename, FileMode.Open, FileAccess.Read, FileShare.Read, 65536, FileOptions.SequentialScan);
        }
    }

    public sealed class Engine
    {
        public const int MaxMetadata = 256 * 1024, MaxChecksums = 64 * 1024, MaxInstaller = 80 * 1024 * 1024;
        private readonly Source source;
        private readonly ITransport transport;
        private readonly IInstallerRunner runner;
        private readonly string downloadRoot;
        public Engine(Source source, ITransport transport, IInstallerRunner runner, string downloadRoot) {
            this.source = source; this.transport = transport; this.runner = runner; this.downloadRoot = downloadRoot;
        }
        internal static void Active(CancellationToken cancel, DateTime deadline) {
            cancel.ThrowIfCancellationRequested();
            if (DateTime.UtcNow >= deadline) throw new InstallerException("다운로드 시간이 초과되었습니다. 사내망 연결을 확인한 뒤 다시 시도해 주세요.");
        }
        private void Download(string initial, Stream destination, long limit, long expected, CancellationToken cancel, DateTime deadline, Action<long, long> progress) {
            string next = initial;
            for (int redirects = 0; redirects <= 5; redirects++) {
                Active(cancel, deadline); source.ValidateRedirect(next, initial);
                using (TransferResponse response = transport.Open(next, cancel, deadline)) {
                    if (response.Status == 301 || response.Status == 302 || response.Status == 303 || response.Status == 307 || response.Status == 308) {
                        if (redirects == 5 || String.IsNullOrEmpty(response.Location)) throw Json.Bad();
                        Uri target; if (!Uri.TryCreate(new Uri(next), response.Location, out target)) throw Json.Bad();
                        // Check raw header too: Uri normalizes backslashes and control characters.
                        if (response.Location.IndexOf('\\') >= 0 || Regex.IsMatch(response.Location, @"[\x00-\x20\x7f]")) throw Json.Bad();
                        next = target.AbsoluteUri; source.ValidateRedirect(next, initial); continue;
                    }
                    if (response.Status != 200) throw new InstallerException(response.Status == 404 ?
                        "최신 버전의 설치 파일을 찾지 못했습니다. 배포 담당자에게 NSIS 설치 파일 게시 여부를 확인해 주세요." :
                        "사내 배포 서버에서 파일을 내려받지 못했습니다. 사내망 연결과 패키지 익명 다운로드 설정을 확인해 주세요.");
                    if (response.Body == null || (!String.IsNullOrEmpty(response.Encoding) && response.Encoding != "identity") ||
                        response.ContentLength == 0 || response.ContentLength > limit || response.ContentLength < -1 ||
                        (expected > 0 && response.ContentLength >= 0 && response.ContentLength != expected)) throw Json.Bad();
                    byte[] buffer = new byte[65536]; long total = 0;
                    while (true) {
                        Active(cancel, deadline); int read = response.Body.Read(buffer, 0, (int)Math.Min(buffer.Length, limit + 1 - total)); Active(cancel, deadline);
                        if (read == 0) break; total += read;
                        if (total > limit || (expected > 0 && total > expected)) throw Json.Bad();
                        destination.Write(buffer, 0, read);
                        if (progress != null) progress(total, response.ContentLength);
                    }
                    if (total == 0 || (expected > 0 && total != expected) || (response.ContentLength >= 0 && total != response.ContentLength)) throw Json.Bad();
                    return;
                }
            }
            throw Json.Bad();
        }
        private byte[] Bytes(string url, int limit, long expected, CancellationToken cancel, DateTime deadline) {
            using (MemoryStream buffer = new MemoryStream()) { Download(url, buffer, limit, expected, cancel, deadline, null); return buffer.ToArray(); }
        }
        public Release Discover(CancellationToken cancel) {
            Dictionary<string, object> manifest = Json.Object(Json.Parse(Bytes(source.LatestUrl, MaxMetadata, -1, cancel, DateTime.UtcNow.AddSeconds(35))));
            Json.Keys(manifest, "schema", "channel", "version", "publishedAt", "title", "notes", "files");
            string version = Json.Text(manifest, "version");
            DateTimeOffset published;
            if (Json.Number(manifest, "schema") != 1 || Json.Text(manifest, "channel") != "stable" || !Source.IsVersion(version) ||
                !Regex.IsMatch(Json.Text(manifest, "publishedAt"), @"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)\z") ||
                !DateTimeOffset.TryParse(Json.Text(manifest, "publishedAt"), CultureInfo.InvariantCulture, DateTimeStyles.None, out published) || published.Offset != TimeSpan.Zero) throw Json.Bad();
            Release release = new Release { Version = version, Title = Json.Text(manifest, "title"), Notes = Json.Text(manifest, "notes") };
            if (release.Title.Length > 1000 || Encoding.UTF8.GetByteCount(release.Notes) > 32768) throw Json.Bad();
            List<object> files = manifest["files"] as List<object>; bool vbs = false;
            if (files == null || files.Count < 2 || files.Count > 3) throw Json.Bad();
            HashSet<string> names = new HashSet<string>(StringComparer.Ordinal);
            foreach (object entry in files) {
                Dictionary<string, object> item = Json.Object(entry); Json.Keys(item, "name", "size", "sha256");
                string name = Json.Text(item, "name"), hash = Json.Text(item, "sha256"); long size = Json.Number(item, "size");
                bool archive = name == "Company-Workspace-" + version + "-vbs.zip" || name == "Company-Workspace-" + version + "-exe.zip" ||
                    name == "AX-Workspace-" + version + "-vbs.zip" || name == "AX-Workspace-" + version + "-exe.zip";
                if ((!archive && name != "SHA256SUMS.txt") || !names.Add(name) || !Digest(hash) || size <= 0 || size > (archive ? MaxInstaller : MaxChecksums)) throw Json.Bad();
                if (name.EndsWith("-vbs.zip", StringComparison.Ordinal)) vbs = true;
                if (name == "SHA256SUMS.txt") { release.ChecksumHash = hash; release.ChecksumSize = size; }
            }
            if (!vbs || release.ChecksumHash == null) throw Json.Bad(); return release;
        }
        private static bool Digest(string value) { return Regex.IsMatch(value, @"\A[a-f0-9]{64}\z"); }
        private static string Hash(Stream stream) { using (SHA256 algorithm = SHA256.Create()) return BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
        private static string Hash(byte[] bytes) { using (MemoryStream stream = new MemoryStream(bytes, false)) return Hash(stream); }
        private static string SetupDigest(byte[] bytes, string filename) {
            string text = new UTF8Encoding(false, true).GetString(bytes).TrimStart('\ufeff'); string result = null;
            HashSet<string> names = new HashSet<string>(StringComparer.Ordinal);
            foreach (string line in text.Split(new char[] { '\n' })) {
                string clean = line.TrimEnd('\r'); if (String.IsNullOrWhiteSpace(clean)) continue;
                Match match = Regex.Match(clean, @"\A([A-Fa-f0-9]{64}) [ *]([^\r\n]+)\z");
                if (!match.Success || !names.Add(match.Groups[2].Value)) throw Json.Bad();
                if (match.Groups[2].Value == filename) result = match.Groups[1].Value.ToLowerInvariant();
            }
            if (result == null) throw new InstallerException("배포 목록에 NSIS 설치 파일이 없습니다. 배포 담당자에게 최신 소스로 설치 파일을 함께 게시해 달라고 요청해 주세요.");
            return result;
        }
        public int Install(Release release, CancellationToken cancel, Action<long, long> progress, Action started) {
            if (release == null || !Source.IsVersion(release.Version) || release.ChecksumSize <= 0 || release.ChecksumSize > MaxChecksums || !Digest(release.ChecksumHash ?? "")) throw Json.Bad();
            DateTime deadline = DateTime.UtcNow.AddMinutes(5);
            byte[] sums = Bytes(source.AssetUrl(release.Version, "SHA256SUMS.txt"), MaxChecksums, release.ChecksumSize, cancel, deadline);
            if (Hash(sums) != release.ChecksumHash) throw Json.Bad();
            string name = "AX-Workspace-" + release.Version + "-Setup.exe"; string expectedHash = SetupDigest(sums, name);
            using (OwnedDownload download = new OwnedDownload(downloadRoot, name)) {
                Download(source.AssetUrl(release.Version, name), download.File, MaxInstaller, -1, cancel, deadline, progress);
                download.Seal(); download.File.Position = 0;
                if (Hash(download.File) != expectedHash) throw Json.Bad();
                download.File.Position = 0;
                if (download.File.ReadByte() != 'M' || download.File.ReadByte() != 'Z') throw Json.Bad();
                FileVersionInfo info = FileVersionInfo.GetVersionInfo(download.Filename);
                if (info.FileVersion != release.Version + ".0" || info.ProductVersion != release.Version || info.ProductName != "AX Workspace") throw Json.Bad();
                Active(cancel, deadline);
                // The verified read handle grants read sharing only, denying replacement or edits until NSIS exits.
                return runner.Run(download.Filename, started ?? delegate { });
            }
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly Engine engine;
        private readonly Label status = new Label(), subtitle = new Label();
        private readonly TextBox notes = new TextBox();
        private readonly ProgressBar progress = new ProgressBar();
        private readonly Button action = new Button(), cancel = new Button();
        private CancellationTokenSource operation;
        private Release release;
        private bool busy, complete;
        private volatile bool installerStarted;
        public InstallerForm(Source source) {
            engine = new Engine(source, new HttpsTransport(), new NativeInstallerRunner(),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CompanyAgent", "installer-downloads"));
            SuspendLayout();
            // These controls are authored at 96 DPI. Without the explicit baseline, dynamically
            // constructed forms can keep 96-DPI row sizes while fonts scale on a 150/200% display.
            AutoScaleDimensions = new SizeF(96F, 96F); AutoScaleMode = AutoScaleMode.Dpi;
            Text = "AX Workspace 설치"; Font = new Font("맑은 고딕", 10F);
            ClientSize = new Size(580, 460); MinimumSize = new Size(560, 440); StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(247, 248, 253); MaximizeBox = false;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            TableLayoutPanel layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(28), ColumnCount = 1, RowCount = 6 };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize)); layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize)); layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            Label title = new Label { Text = "AX Workspace", AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 6),
                Font = new Font(Font.FontFamily, 21, FontStyle.Bold), ForeColor = Color.FromArgb(43, 47, 66) };
            subtitle.Text = "내 업무와 AI가 만나는 공간\r\n사내 GitLab에서 최신 설치 파일을 확인합니다."; subtitle.AutoSize = true; subtitle.Dock = DockStyle.Fill;
            subtitle.Margin = new Padding(0, 0, 0, 18); subtitle.ForeColor = Color.FromArgb(92, 100, 119);
            status.AutoSize = true; status.Dock = DockStyle.Fill; status.Margin = new Padding(0, 0, 0, 12);
            status.TextAlign = ContentAlignment.MiddleLeft; status.ForeColor = Color.FromArgb(92, 82, 174);
            notes.Multiline = true; notes.ReadOnly = true; notes.ScrollBars = ScrollBars.Vertical; notes.Dock = DockStyle.Fill;
            notes.BorderStyle = BorderStyle.None; notes.BackColor = BackColor; notes.ForeColor = Color.FromArgb(65, 70, 86); notes.TabStop = false;
            notes.MinimumSize = new Size(0, 100); notes.Margin = new Padding(0);
            progress.Dock = DockStyle.Fill; progress.Height = 12; progress.Margin = new Padding(0, 14, 0, 10);
            FlowLayoutPanel buttons = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                FlowDirection = FlowDirection.RightToLeft, WrapContents = false, Margin = new Padding(0), Padding = new Padding(0) };
            foreach (Button button in new Button[] { action, cancel }) {
                button.AutoSize = true; button.AutoSizeMode = AutoSizeMode.GrowAndShrink; button.MinimumSize = new Size(110, 38);
                button.Margin = new Padding(8, 0, 0, 0); button.FlatStyle = FlatStyle.Flat; button.FlatAppearance.BorderSize = 0; button.Padding = new Padding(12, 7, 12, 7);
            }
            action.BackColor = Color.FromArgb(112, 99, 211); action.ForeColor = Color.White; action.Text = "확인 중"; action.Enabled = false;
            cancel.Text = "취소"; cancel.BackColor = Color.FromArgb(231, 232, 242); cancel.ForeColor = Color.FromArgb(63, 69, 87);
            buttons.Controls.Add(action); buttons.Controls.Add(cancel);
            layout.Controls.Add(title); layout.Controls.Add(subtitle); layout.Controls.Add(status); layout.Controls.Add(notes); layout.Controls.Add(progress); layout.Controls.Add(buttons); Controls.Add(layout);
            action.Click += async delegate { if (complete) { Close(); return; } if (release == null) await Discover(); else await Install(); };
            cancel.Click += delegate { if (installerStarted) return; if (busy) { operation.Cancel(); status.Text = "취소하고 있습니다…"; } else Close(); };
            Shown += async delegate { await Discover(); };
            FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (installerStarted && busy) { e.Cancel = true; MessageBox.Show(this, "열린 설치 창에서 설치를 완료하거나 취소해 주세요.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information); }
                else if (busy) { e.Cancel = true; operation.Cancel(); status.Text = "취소하고 있습니다…"; }
            };
            ResumeLayout(true);
        }
        private void Begin(string message) {
            if (operation != null) operation.Dispose(); operation = new CancellationTokenSource(); busy = true; complete = false;
            action.Enabled = false; cancel.Enabled = true; cancel.Text = "취소"; status.Text = message; progress.Style = ProgressBarStyle.Marquee;
        }
        private void End() { busy = false; installerStarted = false; progress.Style = ProgressBarStyle.Blocks; action.Enabled = true; cancel.Enabled = true; }
        private void Failure(Exception error) {
            release = null; action.Text = "다시 확인";
            notes.Text = error is InstallerException ? error.Message + "\r\n\r\n" : "";
            notes.Text += installerStarted ? "설치 창과 Windows의 설치된 앱 목록을 확인한 뒤 다시 시도해 주세요." : "설치 프로그램은 실행하지 않았습니다.\r\n사내망에 연결한 뒤 다시 시도해 주세요.";
            if (error is OperationCanceledException) status.Text = "설치 준비를 취소했습니다.";
            else if (error is InstallerException) status.Text = "설치 파일을 확인하지 못했습니다.";
            else status.Text = "설치를 준비하지 못했습니다. 사내망 연결과 배포 상태를 확인해 주세요.";
        }
        private async Task Discover() {
            Begin("최신 버전을 확인하고 있습니다…"); notes.Text = "서버에 게시된 최신 버전만 설치합니다.\r\n이 설치 파일은 다음 버전에도 그대로 사용할 수 있습니다.";
            try {
                CancellationToken token = operation.Token; release = await Task.Run(delegate { return engine.Discover(token); });
                status.Text = "최신 버전 " + release.Version + " · 설치할 준비가 되었습니다";
                notes.Text = (String.IsNullOrWhiteSpace(release.Notes) ? "배포 설명이 없습니다." : release.Notes.Replace("\r\n", "\n").Replace('\r', '\n').Replace("\n", "\r\n")) +
                    "\r\n\r\n설치를 누르면 파일을 검증한 뒤 설치 창이 열립니다.\r\n최신 Claude Code 설치·인증과 Python 3.11 이상이 필요합니다.\r\n앱이 실행 중이면 알림 영역에서 완전 종료해 주세요.";
                action.Text = "최신 버전 설치";
            } catch (Exception error) { Failure(error); } finally { End(); }
        }
        private async Task Install() {
            Begin("최신 설치 파일을 내려받고 검증하고 있습니다…");
            try {
                CancellationToken token = operation.Token; Release selected = release;
                int code = await Task.Run(delegate {
                    long last = 0;
                    return engine.Install(selected, token, delegate(long bytes, long total) {
                        long now = Stopwatch.GetTimestamp();
                        if (now - last < Stopwatch.Frequency / 8 && bytes != total) return; last = now;
                        BeginInvoke((Action)delegate {
                            if (installerStarted) return;
                            status.Text = "설치 파일 다운로드 · " + (bytes / 1048576.0).ToString("0.0", CultureInfo.InvariantCulture) + " MB";
                            if (total > 0) { progress.Style = ProgressBarStyle.Blocks; progress.Value = (int)Math.Min(100, bytes * 100 / total); }
                        });
                    }, delegate {
                        installerStarted = true;
                        BeginInvoke((Action)delegate { status.Text = "열린 설치 창에서 설치를 진행해 주세요."; progress.Style = ProgressBarStyle.Marquee; cancel.Enabled = false; });
                    });
                });
                if (code == 0) { complete = true; status.Text = "AX Workspace " + selected.Version + " 설치를 마쳤습니다."; notes.Text = "시작 메뉴에서 AX Workspace를 열어 주세요.\r\n이 설치 파일은 다음 설치에도 그대로 사용할 수 있습니다."; action.Text = "닫기"; }
                else { release = null; status.Text = "설치가 취소되었거나 완료되지 않았습니다."; notes.Text = "설치 창에 나온 안내를 확인한 뒤 다시 시도해 주세요."; action.Text = "다시 확인"; }
            } catch (Exception error) { Failure(error); } finally { End(); }
        }
    }

    internal static class Program
    {
        [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr value);
        [DllImport("shcore.dll")] private static extern int SetProcessDpiAwareness(int value);
        internal static void InitializeDisplay() {
            try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) { try { SetProcessDpiAwareness(2); } catch { } } catch (DllNotFoundException) { }
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        }
        [STAThread] private static int Main() {
            InitializeDisplay();
            AppContext.SetSwitch("Switch.System.Net.DontEnableSchUseStrongCrypto", false);
            AppContext.SetSwitch("Switch.System.Net.DontEnableSystemDefaultTlsVersions", false);
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)0; // Use the Windows TLS policy.
            try {
                byte[] bytes;
                using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("WorkspaceUpdateSource.json")) {
                    if (stream == null || stream.Length > 16384) throw Json.Bad();
                    using (MemoryStream buffer = new MemoryStream()) { stream.CopyTo(buffer); bytes = buffer.ToArray(); }
                }
                Source source = Source.Parse(bytes); Application.Run(new InstallerForm(source)); return 0;
            } catch {
                MessageBox.Show("사내 배포 서버 설정을 확인하지 못했습니다. 배포 담당자가 만든 AX Workspace 설치 파일을 다시 받아 주세요.",
                    "AX Workspace 설치", MessageBoxButtons.OK, MessageBoxIcon.Error); return 2;
            }
        }
    }
}
