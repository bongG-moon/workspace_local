// Dedicated Windows host. No browser profile, credentials, installer or generic
// web-to-native bridge. The parent owns its lifetime through private stdio pipes.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class DesktopProgram
{
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16384 };
    private static readonly object OutputLock = new object();
    internal static void Emit(object value)
    {
        lock (OutputLock)
        {
            try { Console.Out.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
            catch (IOException) { /* Parent pipe has closed. EOF ends the host. */ }
        }
    }
    internal static string ReadLine()
    {
        var line = new StringBuilder();
        for (int n; (n = Console.In.Read()) != -1; )
        {
            if (n == '\n') return line.ToString().TrimEnd('\r');
            if (line.Length >= 16384) throw new InvalidDataException();
            line.Append((char)n);
        }
        return null;
    }
    [STAThread]
    private static int Main()
    {
        try
        {
            // A winexe has no console code page. Set stream encodings without
            // calling SetConsoleCP, which fails for redirected GUI processes.
            Console.SetIn(new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false)));
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var input = ReadLine();
            if (input == null) return 0;
            var config = Json.Deserialize<Dictionary<string, object>>(input);
            Application.Run(new DesktopWindow(config));
            return 0;
        }
        catch (Exception e)
        {
            // No exception text, URL, environment, auth fragment or file paths.
            Emit(new { type = "error", code = 47, hresult = e.HResult });
            return 47;
        }
    }
}

internal sealed class DesktopWindow : Form
{
    private readonly Uri home;
    private readonly string profile;
    private readonly bool background;
    private WebView2 view;
    private Panel recovery;
    private bool exiting, started, initializing, controlReaderStarted;
    private string runtimeVersion;
    private WorkspaceNotificationCard notificationCard;

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);

    internal DesktopWindow(Dictionary<string, object> config)
    {
        home = new Uri((string)config["url"]);
        if (home.Scheme != "http" || home.Host != "127.0.0.1" || home.IsDefaultPort ||
            home.AbsolutePath != "/" || home.Query != "" || home.UserInfo != "" ||
            !Regex.IsMatch(home.Fragment, "^#token=[A-Za-z0-9_-]{40,100}$")) throw new InvalidDataException();
        profile = (string)config["profile"];
        if (!Path.IsPathRooted(profile) || profile != Path.GetFullPath(profile)) throw new InvalidDataException();
        background = config.ContainsKey("background") && (bool)config["background"];
        Text = "Workspace";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.FromArgb(240, 242, 248);
        Font = new Font("Segoe UI", 10);
        MinimumSize = new Size(720, 520);
        var area = Screen.PrimaryScreen.WorkingArea;
        Size = new Size(Math.Min(1440, area.Width - 32), Math.Min(940, area.Height - 32));
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        Shown += async delegate { await InitializeView(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (exiting || e.CloseReason != CloseReason.UserClosing) return;
            e.Cancel = true;
            if (background) { Hide(); DesktopProgram.Emit(new { type = "hidden" }); }
            else DesktopProgram.Emit(new { type = "close_requested" });
        };
        FormClosed += delegate
        {
            if (notificationCard != null) notificationCard.Finish(false);
            if (view != null) view.Dispose();
        };
        HandleCreated += delegate
        {
            if (controlReaderStarted) return;
            controlReaderStarted = true;
            var reader = new Thread(ReadCommands) { IsBackground = true, Name = "Workspace desktop control" };
            reader.Start();
        };
    }

    private bool SameOrigin(string value)
    {
        Uri uri;
        return Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Scheme == home.Scheme &&
            uri.Host == home.Host && uri.Port == home.Port && uri.UserInfo == "";
    }

    private async Task InitializeView()
    {
        if (initializing || exiting) return;
        initializing = true;
        try
        {
            runtimeVersion = CoreWebView2Environment.GetAvailableBrowserVersionString();
            var options = new CoreWebView2EnvironmentOptions();
            options.ExclusiveUserDataFolderAccess = true;
            var environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            if (exiting) return;
            if (view != null) { Controls.Remove(view); view.Dispose(); }
            view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor, AllowExternalDrop = true };
            Controls.Add(view);
            await view.EnsureCoreWebView2Async(environment);
            if (exiting) return;
            var core = view.CoreWebView2;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.IsBuiltInErrorPageEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            // Keep editing/IME/Tab/Shift+Tab shortcuts in the page; suppress
            // browser-specific refresh/devtools/navigation accelerators.
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs e)
            { e.State = CoreWebView2PermissionState.Deny; };
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            { if (!SameOrigin(e.Uri) || new Uri(e.Uri).AbsolutePath != "/") e.Cancel = true; };
            core.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            { if (!SameOrigin(e.Uri) && e.Uri != "about:blank" && e.Uri != "about:srcdoc") e.Cancel = true; };
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            {
                e.Handled = true;
                Uri target;
                if (e.IsUserInitiated && Uri.TryCreate(e.Uri, UriKind.Absolute, out target) &&
                    (target.Scheme == "https" || target.Scheme == "http") && target.UserInfo == "" &&
                    !target.IsLoopback && !SameOrigin(e.Uri))
                {
                    try { Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true }); }
                    catch { DesktopProgram.Emit(new { type = "external_link_failed" }); }
                }
            };
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += delegate(object sender, CoreWebView2WebResourceRequestedEventArgs e)
            {
                var uri = e.Request.Uri;
                if (!SameOrigin(uri) && !uri.StartsWith("data:", StringComparison.Ordinal) &&
                    !uri.StartsWith("blob:" + home.GetLeftPart(UriPartial.Authority) + "/", StringComparison.Ordinal) &&
                    uri != "about:blank" && uri != "about:srcdoc")
                    e.Response = environment.CreateWebResourceResponse(null, 403, "Blocked", "");
            };
            core.DownloadStarting += Download;
            core.ProcessFailed += delegate { ShowRecovery(); };
            core.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (!e.IsSuccess) { ShowRecovery(); return; }
                if (recovery != null) { Controls.Remove(recovery); recovery.Dispose(); recovery = null; }
                view.BringToFront();
                DesktopProgram.Emit(new { type = "loaded" });
            };
            // Ready is deliberately BEFORE navigation completes: Python starts
            // serving HTTP after the host is initialized. No startup deadlock.
            if (!started)
            {
                DesktopProgram.Emit(new { type = "ready", pid = Process.GetCurrentProcess().Id,
                    hwnd = Handle.ToInt64(), runtime = runtimeVersion, notificationCards = true });
                started = true;
            }
            core.Navigate(home.AbsoluteUri);
        }
        catch (WebView2RuntimeNotFoundException)
        { DesktopProgram.Emit(new { type = "error", code = 46 }); if (!started) Exit(); else ShowRecovery(); }
        catch (Exception e)
        { DesktopProgram.Emit(new { type = "error", code = 47, hresult = e.HResult }); if (!started) Exit(); else ShowRecovery(); }
        finally { initializing = false; }
    }

    private void Download(object sender, CoreWebView2DownloadStartingEventArgs e)
    {
        e.Handled = true;
        var uri = e.DownloadOperation.Uri;
        if (!SameOrigin(uri) && !uri.StartsWith("blob:" + home.GetLeftPart(UriPartial.Authority) + "/", StringComparison.Ordinal))
        { e.Cancel = true; return; }
        // Do not enter a modal message loop inside a WebView2 callback.
        // Defer completion, then open the owned save dialog on the UI context.
        var deferral = e.GetDeferral();
        SynchronizationContext.Current.Post(delegate
        {
            using (deferral)
            {
                if (exiting || IsDisposed) { e.Cancel = true; return; }
                try
                {
                    using (var dialog = new SaveFileDialog { FileName = Path.GetFileName(e.ResultFilePath), OverwritePrompt = true })
                    {
                        if (dialog.ShowDialog(this) != DialogResult.OK) e.Cancel = true;
                        else e.ResultFilePath = dialog.FileName;
                    }
                }
                catch { e.Cancel = true; }
            }
        }, null);
    }

    private void ShowRecovery()
    {
        if (exiting || recovery != null) return;
        recovery = new Panel { Dock = DockStyle.Fill, BackColor = BackColor, Padding = new Padding(48) };
        var label = new Label { Dock = DockStyle.Top, Height = 100,
            Text = "화면 연결이 잠시 끊겼어요.\n진행 중인 업무를 다시 요청하지 않고 화면만 다시 열 수 있어요." };
        var retry = new Button { Dock = DockStyle.Top, Height = 48, Text = "화면 다시 열기" };
        retry.Click += async delegate { await InitializeView(); };
        recovery.Controls.Add(retry); recovery.Controls.Add(label);
        Controls.Add(recovery); recovery.BringToFront();
        DesktopProgram.Emit(new { type = "recovery" });
    }

    private void Notify(Dictionary<string, object> input, int id)
    {
        object receiptValue, kindValue, titleValue, messageValue;
        input.TryGetValue("notificationId", out receiptValue);
        input.TryGetValue("kind", out kindValue);
        input.TryGetValue("title", out titleValue);
        input.TryGetValue("message", out messageValue);
        var receipt = receiptValue as string;
        var kind = kindValue as string;
        string reason = null;
        if (receipt == null || !Regex.IsMatch(receipt, @"\A[a-f0-9]{64}\z") ||
            (kind != "completed" && kind != "attention" && kind != "error") ||
            !WorkspaceNotificationCard.ValidText(titleValue, 100) || !WorkspaceNotificationCard.ValidText(messageValue, 255)) reason = "invalid";
        else if (exiting || IsDisposed) reason = "unavailable";
        else if (notificationCard != null) reason = "busy";
        else if (!WorkspaceNotificationCard.NotificationsAllowed()) reason = "suppressed";
        if (reason == null)
        {
            WorkspaceNotificationCard card = null;
            try
            {
                card = new WorkspaceNotificationCard(receipt, kind, (string)titleValue, (string)messageValue,
                    delegate(WorkspaceNotificationCard finished, bool opened)
                    {
                        if (ReferenceEquals(notificationCard, finished)) notificationCard = null;
                        DesktopProgram.Emit(new { type = opened ? "notification_opened" : "notification_dismissed",
                            notificationId = finished.Receipt });
                    });
                notificationCard = card;
                card.Present(Handle);
            }
            catch
            {
                if (ReferenceEquals(notificationCard, card)) notificationCard = null;
                if (card != null) { try { card.Dispose(); } catch { } }
                reason = "unavailable";
            }
        }
        DesktopProgram.Emit(new { type = "ack", id = id, ok = true,
            notificationAccepted = reason == null, notificationReason = reason });
    }

    private void ActivateWindow()
    {
        Show();
        // SW_RESTORE keeps Windows' normal/maximized state from before minimize.
        if (WindowState == FormWindowState.Minimized) ShowWindow(Handle, 9);
        Activate();
        if (view != null) view.Focus();
    }

    private void ReadCommands()
    {
        try
        {
            string line;
            while ((line = DesktopProgram.ReadLine()) != null)
            {
                var input = new JavaScriptSerializer { MaxJsonLength = 16384 }.Deserialize<Dictionary<string, object>>(line);
                var command = (string)input["command"];
                var id = Convert.ToInt32(input["id"]);
                BeginInvoke((Action)delegate
                {
                    if (command == "close") { Exit(); return; }
                    if (command == "notify") { Notify(input, id); return; }
                    if (command == "activate") ActivateWindow();
                    else if (command == "hide" && background) Hide();
                    else { DesktopProgram.Emit(new { type = "ack", id = id, ok = false }); return; }
                    DesktopProgram.Emit(new { type = "ack", id = id, ok = true, visible = Visible });
                });
            }
        }
        catch { /* A broken/invalid private parent pipe ends only this host. */ }
        try { BeginInvoke((Action)Exit); } catch { }
    }
    private void Exit() { exiting = true; Close(); }
}

// Two embedded static font faces, loaded only when the first card is arranged.
// GDI+ retains these private families directly: no installed font, registry entry,
// GDI font-name mapping or extra renderer is involved.
internal sealed class WorkspaceNotificationFonts : IDisposable
{
    private const int MaximumFontBytes = 32 * 1024 * 1024;
    private static readonly Lazy<WorkspaceNotificationFonts> Cache = new Lazy<WorkspaceNotificationFonts>(Create);
    private readonly Face regular, semibold;
    private readonly FontFamily fallback;
    private bool disposed;

    private static WorkspaceNotificationFonts Create()
    {
        var fonts = new WorkspaceNotificationFonts();
        Application.ApplicationExit += delegate { fonts.Dispose(); };
        return fonts;
    }
    private WorkspaceNotificationFonts()
    {
        regular = Load("Workspace.NotoSansKR.Regular.ttf.gz");
        semibold = Load("Workspace.NotoSansKR.SemiBold.ttf.gz");
        try { fallback = new FontFamily("Malgun Gothic"); }
        catch
        {
            try { fallback = new FontFamily("Segoe UI"); }
            catch { fallback = new FontFamily(FontFamily.GenericSansSerif.Name); }
        }
    }
    internal static Font CreateFont(float pixels, bool emphasized)
    {
        var fonts = Cache.Value;
        var face = emphasized ? fonts.semibold : fonts.regular;
        if (!fonts.disposed && face != null)
            return new Font(face.Family, Math.Max(1, pixels), FontStyle.Regular, GraphicsUnit.Pixel);
        return new Font(fonts.fallback, Math.Max(1, pixels), emphasized ? FontStyle.Bold : FontStyle.Regular, GraphicsUnit.Pixel);
    }
    internal static bool EmbeddedAvailable { get { return Cache.Value.regular != null && Cache.Value.semibold != null; } }

    private static Face Load(string resource)
    {
        Face face = null;
        try
        {
            using (var source = typeof(WorkspaceNotificationFonts).Assembly.GetManifestResourceStream(resource))
            {
                if (source == null) return null;
                using (var zip = new System.IO.Compression.GZipStream(source, System.IO.Compression.CompressionMode.Decompress))
                using (var bytes = new MemoryStream())
                {
                    var buffer = new byte[32768];
                    int count;
                    while ((count = zip.Read(buffer, 0, buffer.Length)) != 0)
                    {
                        if (bytes.Length + count > MaximumFontBytes) throw new InvalidDataException();
                        bytes.Write(buffer, 0, count);
                    }
                    if (bytes.Length == 0) throw new InvalidDataException();
                    face = new Face();
                    face.Memory = System.Runtime.InteropServices.Marshal.AllocHGlobal((int)bytes.Length);
                    System.Runtime.InteropServices.Marshal.Copy(bytes.GetBuffer(), 0, face.Memory, (int)bytes.Length);
                    face.Collection = new System.Drawing.Text.PrivateFontCollection();
                    face.Collection.AddMemoryFont(face.Memory, (int)bytes.Length);
                    var families = face.Collection.Families;
                    if (families.Length == 0) throw new InvalidDataException();
                    face.Family = families[0];
                    for (int index = 1; index < families.Length; index++) families[index].Dispose();
                    if (!face.Family.IsStyleAvailable(FontStyle.Regular)) throw new InvalidDataException();
                    return face;
                }
            }
        }
        catch
        {
            if (face != null) face.Dispose();
            return null;
        }
    }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (regular != null) regular.Dispose();
        if (semibold != null) semibold.Dispose();
        if (fallback != null) fallback.Dispose();
    }
    private sealed class Face : IDisposable
    {
        internal IntPtr Memory;
        internal System.Drawing.Text.PrivateFontCollection Collection;
        internal FontFamily Family;
        public void Dispose()
        {
            if (Family != null) { Family.Dispose(); Family = null; }
            if (Collection != null) { Collection.Dispose(); Collection = null; }
            if (Memory != IntPtr.Zero) { System.Runtime.InteropServices.Marshal.FreeHGlobal(Memory); Memory = IntPtr.Zero; }
        }
    }
}

// Lightweight notification surface: no renderer, profile or additional process.
// The private parent protocol supplies only a receipt and bounded display text.
internal sealed class WorkspaceNotificationCard : Form
{
    private readonly string receipt, kind;
    private readonly Action<WorkspaceNotificationCard, bool> completed;
    private readonly Label statusLabel, titleLabel;
    private readonly NotificationButton openButton, closeButton;
    private readonly System.Windows.Forms.Timer lifetime;
    private readonly Stopwatch elapsed = Stopwatch.StartNew();
    private readonly List<Font> ownedFonts = new List<Font>();
    private long lastTick;
    private double remaining = 12000;
    private float scale = 1;
    private bool finished, arranging;
    private readonly bool highContrast;
    private readonly Color surface, ink, muted, accent, border;

    [System.Runtime.InteropServices.DllImport("shell32.dll")]
    private static extern int SHQueryUserNotificationState(out int state);
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);
    [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    internal string Receipt { get { return receipt; } }
    internal static bool NotificationsAllowed()
    {
        try { int state; return SHQueryUserNotificationState(out state) == 0 && state == 5; }
        catch { return false; }
    }
    internal static bool ValidText(object value, int maximum)
    {
        var text = value as string;
        if (String.IsNullOrWhiteSpace(text) || text.Length > maximum * 2) return false;
        int characters = 0;
        for (int index = 0; index < text.Length; index++)
        {
            char c = text[index];
            if (Char.IsControl(c) || Char.GetUnicodeCategory(c) == System.Globalization.UnicodeCategory.Format) return false;
            if (Char.IsHighSurrogate(c))
            {
                if (index + 1 >= text.Length || !Char.IsLowSurrogate(text[index + 1])) return false;
                index++;
            }
            else if (Char.IsLowSurrogate(c)) return false;
            if (++characters > maximum) return false;
        }
        return true;
    }

    internal WorkspaceNotificationCard(string notificationId, string notificationKind,
        string title, string message, Action<WorkspaceNotificationCard, bool> onCompleted)
    {
        receipt = notificationId; kind = notificationKind; completed = onCompleted;
        highContrast = SystemInformation.HighContrast;
        surface = highContrast ? SystemColors.Window : Color.FromArgb(248, 249, 253);
        ink = highContrast ? SystemColors.WindowText : Color.FromArgb(44, 49, 67);
        muted = highContrast ? SystemColors.GrayText : Color.FromArgb(111, 119, 141);
        accent = highContrast ? SystemColors.Highlight : Color.FromArgb(113, 105, 211);
        border = highContrast ? SystemColors.WindowText : Color.FromArgb(222, 225, 239);
        AutoScaleMode = AutoScaleMode.None;
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        BackColor = surface;
        Padding = Padding.Empty;
        DoubleBuffered = true;
        Text = "Workspace 알림";
        AccessibleName = kind == "completed" ? "작업 완료 알림" : kind == "attention" ? "응답 대기 알림" : "작업 확인 알림";
        AccessibleDescription = title + ". " + message;
        statusLabel = MakeLabel(kind == "completed" ? "작업 완료" : kind == "attention" ? "응답 대기" : "확인 필요", muted);
        titleLabel = MakeLabel(title, ink);
        titleLabel.AutoEllipsis = true;
        titleLabel.AccessibleName = title;
        openButton = new NotificationButton(kind == "completed" ? "결과" : "열기", accent,
            highContrast ? SystemColors.HighlightText : Color.White, false);
        closeButton = new NotificationButton("", surface, muted, true);
        openButton.AccessibleName = kind == "completed" ? "완료된 업무의 결과 보기" : "해당 업무 열기";
        closeButton.AccessibleName = "알림 닫기";
        openButton.Click += delegate { Finish(true); };
        closeButton.Click += delegate { Finish(false); };
        Controls.Add(openButton); Controls.Add(closeButton);
        lifetime = new System.Windows.Forms.Timer { Interval = 250 };
        lifetime.Tick += delegate
        {
            long now = elapsed.ElapsedMilliseconds;
            if (!Bounds.Contains(Cursor.Position)) remaining -= now - lastTick;
            lastTick = now;
            if (remaining <= 0 || now >= 60000 || !NotificationsAllowed()) Finish(false);
        };
        FormClosed += delegate { Complete(false); };
    }

    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams
    {
        get
        {
            var value = base.CreateParams;
            value.ExStyle |= 0x08000000 | 0x00000080; // WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            value.ClassStyle &= ~0x00020000; // One app-owned contour, no legacy CS_DROPSHADOW.
            return value;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        DisableSystemFrame();
    }
    private void DisableSystemFrame()
    {
        // Apply before the first visible frame. Region owns the only curve on
        // both Windows 10 and 11; unsupported DWM attributes are harmless.
        try
        {
            int disabled = 1, noBorder = unchecked((int)0xfffffffe);
            DwmSetWindowAttribute(Handle, 2, ref disabled, 4); // DWMNCRP_DISABLED
            DwmSetWindowAttribute(Handle, 33, ref disabled, 4); // DWMWCP_DONOTROUND
            DwmSetWindowAttribute(Handle, 34, ref noBorder, 4); // DWMWA_COLOR_NONE
        }
        catch { }
    }

    private Label MakeLabel(string text, Color color)
    {
        var label = new NotificationLabel { Text = text, ForeColor = color, BackColor = surface,
            AutoSize = false, UseMnemonic = false, TabStop = false, UseCompatibleTextRendering = true,
            TextAlign = ContentAlignment.MiddleLeft };
        Controls.Add(label);
        return label;
    }

    private int Px(float value) { return Math.Max(1, (int)Math.Round(value * scale)); }
    private void SetFont(Control control, float pixels, bool emphasized)
    {
        var font = WorkspaceNotificationFonts.CreateFont(Math.Max(1, pixels * scale), emphasized);
        ownedFonts.Add(font); control.Font = font;
    }
    private static System.Drawing.Drawing2D.GraphicsPath Rounded(RectangleF box, float radius)
    {
        var path = new System.Drawing.Drawing2D.GraphicsPath();
        float diameter = Math.Min(radius * 2, Math.Min(box.Width, box.Height));
        if (diameter < 1) { path.AddRectangle(box); return path; }
        path.AddArc(box.X, box.Y, diameter, diameter, 180, 90);
        path.AddArc(box.Right - diameter, box.Y, diameter, diameter, 270, 90);
        path.AddArc(box.Right - diameter, box.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(box.X, box.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure(); return path;
    }

    internal void Present(IntPtr referenceWindow)
    {
        uint dpi = 96;
        try { dpi = GetDpiForWindow(referenceWindow); } catch { }
        Arrange(Screen.FromHandle(referenceWindow).WorkingArea, dpi == 0 ? 96 : dpi);
        // Do not assign an owner: a hidden/minimized main window must not hide
        // this notification. Its lifetime is explicitly owned by DesktopWindow.
        Show();
        elapsed.Restart(); lastTick = 0; lifetime.Start();
    }

    private void Arrange(Rectangle area, uint dpi)
    {
        if (arranging || IsDisposed) return;
        arranging = true;
        SuspendLayout();
        try
        {
            float intended = Math.Max(.25f, dpi / 96f);
            int gap = Math.Max(0, Math.Min((int)Math.Round(16 * intended), Math.Min(area.Width, area.Height) / 12));
            scale = Math.Max(.01f, Math.Min(intended, Math.Min((area.Width - gap * 2) / 340f, (area.Height - gap * 2) / 96f)));
            ClientSize = new Size(Math.Min(area.Width, Px(340)), Math.Min(area.Height, Px(96)));
            Location = new Point(Math.Max(area.Left, area.Right - Width - gap), Math.Max(area.Top, area.Bottom - Height - gap));
            var previousFonts = ownedFonts.ToArray(); ownedFonts.Clear();
            SetFont(statusLabel, 11, false); SetFont(titleLabel, 14, true); SetFont(openButton, 12, true);
            foreach (var font in previousFonts) font.Dispose();
            statusLabel.SetBounds(Px(56), Px(20), Px(232), Px(18));
            titleLabel.SetBounds(Px(56), Px(44), Px(194), Px(26));
            openButton.SetBounds(Px(260), Px(43), Px(64), Px(28));
            closeButton.SetBounds(Px(304), Px(10), Px(20), Px(20));
            openButton.CornerRadius = Px(7); closeButton.CornerRadius = Px(5);
            using (var path = Rounded(new RectangleF(0, 0, Width, Height), highContrast ? 0 : Px(10)))
            {
                var previousRegion = Region;
                Region = new Region(path);
                if (previousRegion != null) previousRegion.Dispose();
            }
            Invalidate();
        }
        finally { ResumeLayout(false); arranging = false; }
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == 0x0021) { message.Result = new IntPtr(3); return; } // MA_NOACTIVATE
        if (message.Msg == 0x02E0 && !arranging) // WM_DPICHANGED
        {
            uint dpi = unchecked((uint)message.WParam.ToInt64()) & 0xffff;
            var suggested = (NativeRect)System.Runtime.InteropServices.Marshal.PtrToStructure(message.LParam, typeof(NativeRect));
            var screen = Screen.FromRectangle(Rectangle.FromLTRB(suggested.Left, suggested.Top, suggested.Right, suggested.Bottom));
            Arrange(screen.WorkingArea, dpi == 0 ? 96 : dpi); message.Result = IntPtr.Zero; return;
        }
        base.WndProc(ref message);
        if (message.Msg == 0x031E && IsHandleCreated) DisableSystemFrame(); // DWM composition changed
        if ((message.Msg == 0x007E || message.Msg == 0x001A) && IsHandleCreated && !finished && !arranging)
        {
            uint dpi = (uint)Math.Round(scale * 96);
            try { dpi = GetDpiForWindow(Handle); } catch { }
            Arrange(Screen.FromHandle(Handle).WorkingArea, dpi == 0 ? 96 : dpi);
        }
    }
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct NativeRect { internal int Left, Top, Right, Bottom; }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        float stroke = Math.Max(1, (float)Math.Round(scale)), inset = stroke / 2;
        // Keep the stroke's arc centers identical to the single outer Region.
        using (var outline = Rounded(new RectangleF(inset, inset, Width - stroke, Height - stroke), highContrast ? 0 : Px(10) - inset))
        using (var pen = new Pen(border, stroke)) g.DrawPath(pen, outline);
        Color tone = highContrast ? accent : kind == "completed" ? Color.FromArgb(87, 141, 121)
            : kind == "error" ? Color.FromArgb(176, 116, 88) : accent;
        using (var badge = Rounded(new RectangleF(Px(16), Px(32), Px(28), Px(28)), Px(8)))
        using (var brush = new SolidBrush(highContrast ? SystemColors.Window : Color.FromArgb(238, 237, 248))) g.FillPath(brush, badge);
        using (var pen = new Pen(tone, Math.Max(1, 1.8f * scale)))
        {
            pen.StartCap = pen.EndCap = System.Drawing.Drawing2D.LineCap.Round;
            if (kind == "completed") g.DrawLines(pen, new[] { new Point(Px(23), Px(46)), new Point(Px(28), Px(51)), new Point(Px(37), Px(41)) });
            else
            {
                g.DrawEllipse(pen, Px(22), Px(38), Px(16), Px(16));
                g.DrawLine(pen, Px(30), Px(42), Px(30), Px(46));
                g.DrawLine(pen, Px(30), Px(50), Px(30), Px(50.3f));
            }
        }
    }

    private void Complete(bool opened)
    {
        if (finished) return;
        finished = true;
        if (lifetime != null) lifetime.Stop();
        if (completed != null) completed(this, opened);
    }
    internal void Finish(bool opened)
    {
        if (finished) return;
        Complete(opened);
        Close();
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            Complete(false);
            if (lifetime != null) lifetime.Dispose();
            var previousRegion = Region;
            Region = null;
            if (previousRegion != null) previousRegion.Dispose();
        }
        base.Dispose(disposing);
        if (disposing)
        {
            foreach (var font in ownedFonts) font.Dispose();
            ownedFonts.Clear();
        }
    }

    private sealed class NotificationLabel : Label
    {
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            base.OnPaint(e);
        }
    }

    private sealed class NotificationButton : Button
    {
        private readonly Color fill, text;
        private readonly bool quiet;
        private bool hover, pressed;
        internal int CornerRadius = 10;
        internal NotificationButton(string label, Color background, Color foreground, bool isQuiet)
        {
            Text = label; fill = background; text = foreground; quiet = isQuiet;
            TabStop = false; Cursor = Cursors.Hand; UseMnemonic = false;
            FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0;
            // A notification action must not move keyboard focus from the active app.
            SetStyle(ControlStyles.Selectable, false);
            SetStyle(ControlStyles.UserMouse, true);
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            MouseEnter += delegate { hover = true; Invalidate(); };
            MouseLeave += delegate { hover = false; pressed = false; Invalidate(); };
            MouseDown += delegate { pressed = true; Invalidate(); };
            MouseUp += delegate { pressed = false; Invalidate(); };
        }
        protected override AccessibleObject CreateAccessibilityInstance()
        { return new NotificationButtonAccessibleObject(this); }
        private sealed class NotificationButtonAccessibleObject : Control.ControlAccessibleObject
        {
            private readonly NotificationButton button;
            internal NotificationButtonAccessibleObject(NotificationButton owner) : base(owner) { button = owner; }
            public override AccessibleRole Role { get { return AccessibleRole.PushButton; } }
            public override string DefaultAction { get { return button.AccessibleName; } }
            public override void DoDefaultAction()
            {
                // Button.PerformClick checks CanSelect. This surface is deliberately
                // non-selectable; accessibility still invokes the same click action.
                if (button.Enabled && !button.IsDisposed) button.OnClick(EventArgs.Empty);
            }
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            e.Graphics.Clear(Parent.BackColor);
            Color background = hover ? ControlPaint.Dark(fill, pressed ? .12f : .04f) : fill;
            if (!quiet || hover)
                using (var path = Rounded(new RectangleF(0, 0, Width - 1, Height - 1), CornerRadius))
                using (var brush = new SolidBrush(background)) e.Graphics.FillPath(brush, path);
            float unit = Height / (quiet ? 20f : 28f);
            if (quiet)
            {
                float centerX = Width / 2f, centerY = Height / 2f, half = 3.5f * unit;
                using (var pen = new Pen(text, Math.Max(1, 1.3f * unit)))
                {
                    pen.StartCap = pen.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                    e.Graphics.DrawLine(pen, centerX - half, centerY - half, centerX + half, centerY + half);
                    e.Graphics.DrawLine(pen, centerX + half, centerY - half, centerX - half, centerY + half);
                }
                return;
            }
            e.Graphics.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center,
                Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
            using (var brush = new SolidBrush(text))
                e.Graphics.DrawString(Text, Font, brush, new RectangleF(8 * unit, 0, Width - 29 * unit, Height), format);
            using (var pen = new Pen(text, Math.Max(1, 1.25f * unit)))
            {
                pen.StartCap = pen.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                float right = Width - 10 * unit, middle = Height / 2f;
                e.Graphics.DrawLine(pen, right - 8 * unit, middle, right, middle);
                e.Graphics.DrawLines(pen, new[] { new PointF(right - 3.5f * unit, middle - 3.5f * unit),
                    new PointF(right, middle), new PointF(right - 3.5f * unit, middle + 3.5f * unit) });
            }
        }
    }
}
