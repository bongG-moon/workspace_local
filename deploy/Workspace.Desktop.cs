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
        Shown += async delegate { await InitializeView(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (exiting || e.CloseReason != CloseReason.UserClosing) return;
            e.Cancel = true;
            if (background) { Hide(); DesktopProgram.Emit(new { type = "hidden" }); }
            else DesktopProgram.Emit(new { type = "close_requested" });
        };
        FormClosed += delegate { if (view != null) view.Dispose(); };
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
                    hwnd = Handle.ToInt64(), runtime = runtimeVersion });
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
                    if (command == "activate") { Show(); if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Activate(); if (view != null) view.Focus(); }
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
