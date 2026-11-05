using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class DiagnosticProgram
{
    internal const string Version = "1.0.0";
    internal static readonly string DataRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CompanyAgent", "vdi-click-diagnostic");
    [STAThread] private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--self-test") return DiagnosticWindow.SelfTest();
        if (args.Length != 0) return 2;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new DiagnosticWindow());
        return 0;
    }
}

internal sealed class DiagnosticWindow : Form
{
    private const string DocumentUrl = "https://ax-workspace-diagnostic.invalid/";
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 2 * 1024 * 1024 };
    private readonly List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
    private readonly Label status = new Label();
    private readonly Button start = new Button(), skip = new Button(), save = new Button();
    private WebView2 view;
    private bool loaded, closing;
    private int round, sequence, discarded;
    private string runtime = "unknown", lastCommand = "";
    private long lastCommandAt;
    private readonly DateTime began = DateTime.UtcNow;
    private readonly string runId = Guid.NewGuid().ToString("N");
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern int GetAwarenessFromDpiAwarenessContext(IntPtr value);

    internal DiagnosticWindow()
    {
        Text = "AX Workspace 클릭 위치 진단 1.0.0";
        SuspendLayout();
        AutoScaleDimensions = new SizeF(96F, 96F);
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = new Font("Segoe UI", 10);
        BackColor = Color.FromArgb(245, 246, 250);
        MinimumSize = new Size(800, 600);
        Size = new Size(1100, 800);
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        KeyPreview = true;
        var toolbar = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Padding = new Padding(18, 10, 18, 8), ColumnCount = 1, RowCount = 3, BackColor = Color.White };
        toolbar.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        toolbar.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        toolbar.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        var title = new Label { AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(3, 4, 3, 7), Text = "클릭 위치 확인  |  창을 연 상태 그대로 검사한 뒤, 창 크기를 바꿔 다시 비교하세요.", TextAlign = ContentAlignment.MiddleLeft };
        var commands = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = true, FlowDirection = FlowDirection.LeftToRight };
        Configure(start, "새 검사 (F6)", delegate { Command("start"); });
        Configure(skip, "건너뛰기 (F8)", delegate { Command("skip"); });
        Configure(save, "결과 저장 (Ctrl+S)", delegate { Command("save"); });
        var folder = new Button();
        Configure(folder, "결과 폴더 열기", delegate { OpenResults(); });
        commands.Controls.AddRange(new Control[] { start, skip, save, folder });
        status.Dock = DockStyle.Fill;
        status.AutoSize = true;
        status.Margin = new Padding(3, 4, 3, 4);
        status.TextAlign = ContentAlignment.MiddleLeft;
        status.ForeColor = Color.FromArgb(93, 99, 119);
        status.Text = "WebView2 테스트 화면을 준비하고 있습니다. 기존 AX Workspace와 업무 데이터는 사용하지 않습니다.";
        toolbar.Controls.Add(title, 0, 0); toolbar.Controls.Add(commands, 0, 1); toolbar.Controls.Add(status, 0, 2);
        Controls.Add(toolbar);
        start.Enabled = skip.Enabled = false;
        Shown += async delegate { await Initialize(); };
        FormClosed += delegate { closing = true; if (view != null) view.Dispose(); };
        ResumeLayout(true);
    }
    private void Configure(Button button, string text, EventHandler click)
    {
        button.Text = text; button.AutoSize = true; button.Height = 33; button.Padding = new Padding(8, 2, 8, 2);
        button.FlatStyle = FlatStyle.Flat; button.FlatAppearance.BorderColor = Color.FromArgb(211, 213, 229);
        button.BackColor = Color.White; button.Margin = new Padding(0, 3, 9, 3); button.Click += click;
    }
    private async System.Threading.Tasks.Task Initialize()
    {
        string stage = "runtime_discovery";
        try
        {
            runtime = CoreWebView2Environment.GetAvailableBrowserVersionString();
            Directory.CreateDirectory(DiagnosticProgram.DataRoot);
            stage = "environment_creation";
            var options = new CoreWebView2EnvironmentOptions(); options.ExclusiveUserDataFolderAccess = true;
            var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(DiagnosticProgram.DataRoot, "webview"), options);
            if (closing) return;
            runtime = environment.BrowserVersionString;
            view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor, AllowExternalDrop = false };
            Controls.Add(view); view.BringToFront();
            // The same System-DPI manifest, WinForms AutoScaleMode.Dpi and Dock.Fill
            // as the product host. No input remapping or experimental GPU flags.
            stage = "controller_creation";
            await view.EnsureCoreWebView2Async(environment);
            if (closing) return;
            var core = view.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = true;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs e) { e.State = CoreWebView2PermissionState.Deny; };
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e) { e.Cancel = true; };
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { if (e.Uri != DocumentUrl) e.Cancel = true; };
            core.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { e.Cancel = true; };
            byte[] page;
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Diagnostic.html"))
            using (var memory = new MemoryStream()) { stream.CopyTo(memory); page = memory.ToArray(); }
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += delegate(object sender, CoreWebView2WebResourceRequestedEventArgs e)
            {
                // A fixed synthetic origin is served entirely from embedded
                // bytes. No DNS or HTTP request is delegated to the network.
                if (e.Request.Uri == DocumentUrl && e.ResourceContext == CoreWebView2WebResourceContext.Document)
                    e.Response = environment.CreateWebResourceResponse(new MemoryStream(page, false), 200, "OK", "Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n");
                else e.Response = environment.CreateWebResourceResponse(null, 403, "Blocked", "");
            };
            core.WebMessageReceived += Receive;
            await core.AddScriptToExecuteOnDocumentCreatedAsync("addEventListener('error',function(){chrome.webview.postMessage({kind:'page_error'});});addEventListener('unhandledrejection',function(){chrome.webview.postMessage({kind:'page_error'});});");
            core.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                Add(new Dictionary<string, object> { { "kind", "navigation" }, { "success", e.IsSuccess }, { "webErrorStatus", (int)e.WebErrorStatus } });
                if (!e.IsSuccess) status.Text = "테스트 문서를 열지 못했습니다. 결과를 저장해 담당자에게 전달해 주세요. 문서 코드: " + (int)e.WebErrorStatus;
                else if (!loaded)
                {
                    status.Text = "테스트 문서를 열었습니다. 클릭 검사 화면의 준비 응답을 기다리고 있습니다.";
                    try
                    {
                        string ready = await core.ExecuteScriptAsync("typeof Diagnostic==='object'&&Diagnostic.ready()===true");
                        if (closing) return;
                        if (ready == "true") { loaded = true; start.Enabled = skip.Enabled = true; status.Text = "준비 완료. 새 검사를 누른 뒤 보라색 십자 중심 5곳을 한 번씩 클릭하세요."; }
                        else { Add(new Dictionary<string, object> { { "kind", "page_not_ready" } }); status.Text = "테스트 문서의 실행을 확인하지 못했습니다. 결과를 저장해 담당자에게 전달해 주세요."; }
                    }
                    catch { if (!closing) status.Text = "테스트 문서의 응답을 확인하지 못했습니다. 결과를 저장해 주세요."; }
                }
            };
            core.ProcessFailed += delegate { loaded = false; start.Enabled = skip.Enabled = false; status.Text = "테스트 화면이 종료되었습니다. 결과를 저장하고 진단 도구를 다시 열어 주세요."; Add(new Dictionary<string, object> { { "kind", "process_failed" } }); };
            core.Navigate(DocumentUrl);
        }
        catch (Exception e)
        {
            Add(new Dictionary<string, object> { { "kind", "startup_error" }, { "stage", stage }, { "hresult", e.HResult } });
            status.Text = "테스트 창을 준비하지 못했습니다. 결과 저장으로 진단 정보를 남겨 주세요. 단계: " + stage + " / " + e.HResult.ToString("X8");
        }
    }
    private void Receive(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (closing || e.Source != DocumentUrl) return;
        try
        {
            string raw = e.WebMessageAsJson;
            if (raw.Length > 8192) return;
            var source = json.Deserialize<Dictionary<string, object>>(raw);
            object kindValue;
            if (!source.TryGetValue("kind", out kindValue) || !(kindValue is string)) return;
            string kind = (string)kindValue;
            if (kind == "command")
            {
                object command; if (source.TryGetValue("command", out command) && command is string)
                    BeginInvoke((Action)delegate { Command((string)command); });
                return;
            }
            var safe = Sanitize(source); if (safe == null) return;
            Add(safe);
            if (kind == "ready") { loaded = true; start.Enabled = skip.Enabled = true; status.Text = "준비 완료. ‘새 검사’를 누른 뒤 보라색 십자 중심 5곳을 한 번씩 클릭하세요. 업무 내용은 기록하지 않습니다."; }
            if (kind == "complete") status.Text = round + "회 검사 완료. 결과 저장을 누르거나, 창 크기를 바꾼 뒤 새 검사로 비교하세요.";
            if (kind == "interrupted") status.Text = "검사 중 창 크기가 바뀌었습니다. 새 검사를 눌러 다시 시작하세요. 이전 기록도 저장됩니다.";
            if (kind == "page_error") { loaded = false; start.Enabled = skip.Enabled = false; status.Text = "테스트 화면 실행 중 오류가 발생했습니다. 결과를 저장해 담당자에게 전달해 주세요."; }
        }
        catch { /* Reject malformed page telemetry without interrupting testing. */ }
    }
    internal static Dictionary<string, object> Sanitize(Dictionary<string, object> source)
    {
        object kind;
        if (source == null || !source.TryGetValue("kind", out kind) || !(kind is string)) return null;
        if (Array.IndexOf(new[] { "ready", "start", "complete", "skip", "interrupted", "pointerdown", "pointerup", "pointercancel", "click", "page_error" }, (string)kind) < 0) return null;
        var safe = new Dictionary<string, object> { { "kind", kind } };
        foreach (string name in new[] { "round", "target", "ms", "clientX", "clientY", "targetX", "targetY", "deltaX", "deltaY", "x", "y", "width", "height", "dpr", "visualScale", "buttons", "pointerId", "measured" })
        {
            object value;
            if (!source.TryGetValue(name, out value) || !(value is int || value is long || value is double || value is decimal)) continue;
            double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
            if (!Double.IsNaN(number) && !Double.IsInfinity(number) && Math.Abs(number) <= 1e9) safe[name] = number;
        }
        foreach (string name in new[] { "geometryChanged", "paired", "duplicate" })
        { object value; if (source.TryGetValue(name, out value) && value is bool) safe[name] = value; }
        return safe;
    }
    private Dictionary<string, object> Snapshot()
    {
        uint dpi = 0; int awareness = -1;
        try { dpi = GetDpiForWindow(Handle); awareness = GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(Handle)); } catch { }
        var fields = new Dictionary<string, object> { { "dpi", dpi }, { "awareness", awareness }, { "windowState", (int)WindowState }, { "formWidth", ClientSize.Width }, { "formHeight", ClientSize.Height } };
        if (view != null && !view.IsDisposed)
        {
            fields["viewLeft"] = view.Left; fields["viewTop"] = view.Top; fields["viewWidth"] = view.Width; fields["viewHeight"] = view.Height;
            if (view.CoreWebView2 != null) fields["zoomFactor"] = view.ZoomFactor;
        }
        return fields;
    }
    private void Add(Dictionary<string, object> row)
    {
        row["sequence"] = ++sequence;
        row["host"] = Snapshot();
        if (rows.Count >= 2000) { rows.RemoveAt(0); discarded++; }
        rows.Add(row);
    }
    private async void Command(string command)
    {
        if (closing || (command != "start" && command != "skip" && command != "save")) return;
        long now = Stopwatch.GetTimestamp();
        if (command == lastCommand && (now - lastCommandAt) * 1000.0 / Stopwatch.Frequency < 300) return;
        lastCommand = command; lastCommandAt = now;
        if (command == "save") { Save(); return; }
        if (!loaded || view == null || view.CoreWebView2 == null) return;
        try
        {
            if (command == "start") { round++; await view.CoreWebView2.ExecuteScriptAsync("Diagnostic.start(" + round.ToString(CultureInfo.InvariantCulture) + ");"); status.Text = round + "회 검사 중. 십자 중심을 클릭하세요. 클릭이 반응하지 않으면 F8로 건너뛰세요."; }
            else await view.CoreWebView2.ExecuteScriptAsync("Diagnostic.skip();");
            view.Focus();
        }
        catch { status.Text = "테스트 화면과 연결하지 못했습니다. 결과를 저장하고 도구를 다시 열어 주세요."; }
    }
    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == Keys.F6) { Command("start"); return true; }
        if (keyData == Keys.F8) { Command("skip"); return true; }
        if (keyData == (Keys.Control | Keys.S)) { Command("save"); return true; }
        return base.ProcessCmdKey(ref msg, keyData);
    }
    private void Save()
    {
        try
        {
            string directory = Path.Combine(DiagnosticProgram.DataRoot, "results"); Directory.CreateDirectory(directory);
            string path = Path.Combine(directory, "AX-Workspace-VDI-Click-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + "-" + runId.Substring(0, 8) + ".json");
            var report = new Dictionary<string, object> { { "format", 1 }, { "diagnosticVersion", DiagnosticProgram.Version }, { "sdkVersion", "1.0.4258.31" }, { "runtimeVersion", runtime }, { "windowsVersion", Environment.OSVersion.Version.ToString() }, { "startedUtc", began.ToString("o") }, { "savedUtc", DateTime.UtcNow.ToString("o") }, { "runId", runId }, { "rounds", round }, { "discardedEvents", discarded }, { "coordinateSpace", "DOM CSS pixels. Native sizes are separate context, not a pixel-perfect physical pointer measurement." }, { "interpretation", "Target-center error assumes the user aimed at the visible cross center. It does not identify the root cause or record physical client-side coordinates." }, { "events", rows } };
            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
            using (var writer = new StreamWriter(stream, new UTF8Encoding(false))) writer.Write(json.Serialize(report));
            status.Text = "저장 완료: " + path;
            status.ForeColor = Color.FromArgb(43, 113, 82);
        }
        catch { status.Text = "결과 파일을 저장하지 못했습니다. 사용자 폴더의 쓰기 권한을 확인해 주세요."; }
    }
    private void OpenResults()
    {
        try { string directory = Path.Combine(DiagnosticProgram.DataRoot, "results"); Directory.CreateDirectory(directory); Process.Start(new ProcessStartInfo(directory) { UseShellExecute = true }); }
        catch { status.Text = "결과 폴더를 열지 못했습니다. 저장된 경로를 파일 탐색기에서 확인해 주세요."; }
    }
    internal static int SelfTest()
    {
        var input = new Dictionary<string, object> { { "kind", "click" }, { "clientX", 24.5 }, { "deltaY", -5 }, { "key", "must-not-export" }, { "secret", "must-not-export" }, { "dpr", Double.NaN }, { "height", Double.PositiveInfinity }, { "width", 1e12 }, { "geometryChanged", true } };
        var result = Sanitize(input);
        if (result == null || result.Count != 4 || result.ContainsKey("secret") || result.ContainsKey("key") || result.ContainsKey("dpr") || result.ContainsKey("height") || result.ContainsKey("width")) return 1;
        if (Sanitize(new Dictionary<string, object> { { "kind", "unknown" } }) != null) return 2;
        if (Sanitize(new Dictionary<string, object> { { "kind", "click" }, { "clientX", "42" } }).ContainsKey("clientX")) return 3;
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Diagnostic.html")) if (stream == null) return 4;
        return 0;
    }
}
