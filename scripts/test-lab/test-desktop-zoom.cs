// Isolated WebView2 check: only fixture HTML, a fresh profile, and owned form.
// No Claude session, live app state, web-to-native bridge or system input.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;

internal static class TestDesktopZoom
{
    private static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private delegate bool ChildWindow(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, ChildWindow callback, IntPtr data);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder name, int count);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private static void Check(bool ok, string reason) { if (!ok) throw new Exception(reason); }
    private static async Task Wait(Func<Task<bool>> ready, string label)
    {
        for (int n = 0; n < 200; n++) { if (await ready()) return; await Task.Delay(50); }
        throw new Exception("Timed out: " + label);
    }
    private static async Task<bool> Is(WebView2 view, string condition)
    { return await view.CoreWebView2.ExecuteScriptAsync("Boolean(" + condition + ")") == "true"; }
    private static async Task<string> Read(WebView2 view, string expression)
    { return await view.CoreWebView2.ExecuteScriptAsync(expression); }

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        var assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
        var type = assembly.GetType("DesktopWindow", true);
        var config = new Dictionary<string, object> {
            {"url", args[1]}, {"profile", Path.GetFullPath(args[2])}, {"background", false}, {"theme", "dark"}
        };
        var report = new Dictionary<string, object>();
        Exception failure = null;
        using (var window = (Form)Activator.CreateInstance(type, Private, null, new object[] {config}, null))
        {
            type.GetField("controlReaderStarted", Private).SetValue(window, true);
            window.WindowState = FormWindowState.Normal;
            window.StartPosition = FormStartPosition.Manual;
            window.Location = new System.Drawing.Point(-16000, -16000);
            window.Size = new System.Drawing.Size(1000, 720);
            window.ShowInTaskbar = false;
            window.Shown += async delegate
            {
                try
                {
                    WebView2 view = null;
                    await Wait(async delegate {
                        view = (WebView2)type.GetField("view", Private).GetValue(window);
                        report["hasView"] = view != null;
                        if (view == null || view.CoreWebView2 == null) return false;
                        report["nativeFactor"] = view.ZoomFactor;
                        report["sourcePath"] = view.CoreWebView2.Source.Split('#')[0];
                        report["initialState"] = await Read(view, "JSON.stringify({ready:document.readyState,feedback:!!globalThis.WorkspaceZoomFeedback,percent:globalThis.WorkspaceZoomFeedback?.percent})");
                        return await Is(view, "globalThis.WorkspaceZoomFeedback && WorkspaceZoomFeedback.percent===100");
                    }, "initial native zoom publication");
                    Check(!view.CoreWebView2.Settings.IsWebMessageEnabled, "Generic native bridge was enabled");
                    Check(await Is(view, "!document.querySelector('#workspace-zoom-feedback')"), "Startup opened a popup");
                    await Read(view, "document.querySelector('#editor').focus()");
                    var samples = new List<object>();
                    foreach (double factor in new[] {1.25, 1.5, .8, 1.0})
                    {
                        view.ZoomFactor = factor;
                        type.GetMethod("PublishZoomFeedback", Private).Invoke(window, new object[] {true});
                        int expected = (int)Math.Round(factor * 100);
                        await Wait(() => Is(view, "WorkspaceZoomFeedback.percent===" + expected), "factor " + expected);
                        Check(await Is(view, "document.querySelector('#workspace-zoom-feedback').matches(':popover-open')"), "Popup not in top layer");
                        Check(await Is(view, "document.activeElement.id==='editor'"), "Zoom changed input focus");
                        samples.Add(new {nativeFactor=view.ZoomFactor, pagePercent=expected});
                    }
                    report["samples"] = samples;
                    await Read(view, "document.querySelector('#fixture-dialog').showModal();document.querySelector('#dialog-input').focus()");
                    view.ZoomFactor = 3;
                    type.GetMethod("PublishZoomFeedback", Private).Invoke(window, new object[] {true});
                    await Wait(() => Is(view, "WorkspaceZoomFeedback.percent===300"), "dialog zoom");
                    Check(await Is(view, "document.activeElement.id==='dialog-input' && document.querySelector('#fixture-dialog').open"), "Zoom changed modal focus/lifetime");
                    Check(await Is(view, "(()=>{const r=document.querySelector('#workspace-zoom-feedback').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight})()"), "Popup escaped zoomed viewport");
                    report["dialogAt300Percent"] = true;
                    await Task.Delay(2200);
                    Check(await Is(view, "document.querySelector('#workspace-zoom-feedback').hidden && !document.querySelector('#workspace-zoom-feedback').matches(':popover-open')"), "Popup did not dismiss");
                    Check(await Is(view, "document.activeElement.id==='dialog-input'"), "Dismissal changed focus");
                    report["dismissalPreservesFocus"] = true;
                    // The DevTools input command drives this test's renderer only;
                    // it never sends global Windows input or enables devtools UI.
                    await Read(view, "document.querySelector('#fixture-dialog').close()");
                    view.ZoomFactor = 1;
                    type.GetMethod("PublishZoomFeedback", Private).Invoke(window, new object[] {false});
                    await Wait(() => Is(view, "WorkspaceZoomFeedback.percent===100"), "wheel baseline");
                    await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent",
                        "{\"type\":\"mouseWheel\",\"x\":180,\"y\":160,\"deltaX\":0,\"deltaY\":-120,\"modifiers\":2}");
                    await Task.Delay(400);
                    report["syntheticCtrlWheelNativeFactor"] = view.ZoomFactor;
                    report["syntheticCtrlWheelPagePercent"] = await Read(view, "WorkspaceZoomFeedback.percent");
                    if (Math.Abs(view.ZoomFactor - 1) < .001)
                    {
                        // Target only this offscreen fixture's child HWND. No
                        // global keyboard state or mouse position is changed.
                        var targets = new List<IntPtr>();
                        EnumChildWindows(view.Handle, delegate(IntPtr child, IntPtr data) {
                            var name = new StringBuilder(256); GetClassName(child, name, name.Capacity);
                            if (name.ToString() == "Chrome_RenderWidgetHostHWND") targets.Add(child);
                            return true;
                        }, IntPtr.Zero);
                        foreach (var target in targets) PostMessage(target, 0x020A, new IntPtr((120 << 16) | 8), IntPtr.Zero);
                        await Task.Delay(400);
                        report["targetedCtrlWheelWindows"] = targets.Count;
                        report["targetedCtrlWheelNativeFactor"] = view.ZoomFactor;
                    }
                    if (Math.Abs(view.ZoomFactor - 1) > .001)
                    {
                        int expectedWheel = (int)Math.Round(view.ZoomFactor * 100);
                        await Wait(() => Is(view, "WorkspaceZoomFeedback.percent===" + expectedWheel), "user zoom event");
                        report["userZoomEventVerified"] = true;
                    }
                    else
                    {
                        // Out-of-range factors are normalized by the controller
                        // and emit the same production ZoomFactorChanged event.
                        view.ZoomFactor = 1000;
                        await Wait(() => Is(view, "WorkspaceZoomFeedback.percent===" + (int)Math.Round(view.ZoomFactor * 100)), "normalized native zoom event");
                        report["normalizedNativeZoomEventFactor"] = view.ZoomFactor;
                    }
                    report["ok"] = true;
                }
                catch (Exception error) { failure = error; report["ok"] = false; report["error"] = error.ToString(); }
                finally
                {
                    File.WriteAllText(args[3], Json.Serialize(report));
                    type.GetField("exiting", Private).SetValue(window, true);
                    window.Close();
                }
            };
            Application.Run(window);
        }
        Console.WriteLine(Json.Serialize(report));
        return failure == null ? 0 : 1;
    }
}
