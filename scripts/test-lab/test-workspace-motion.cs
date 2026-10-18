// Isolated WebView2 rendering benchmark. Only generated fixture HTML and a fresh
// profile are used; no live app state, Claude, native bridge or system input.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class TestWorkspaceMotion
{
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 10000000 };
    private static async Task<object> Evaluate(WebView2 view, string expression)
    {
        var result = Json.Deserialize<Dictionary<string, object>>(await view.CoreWebView2.CallDevToolsProtocolMethodAsync(
            "Runtime.evaluate", Json.Serialize(new { expression, awaitPromise = true, returnByValue = true })));
        if (result.ContainsKey("exceptionDetails")) throw new Exception(Json.Serialize(result["exceptionDetails"]));
        return ((Dictionary<string, object>)result["result"])["value"];
    }
    private static async Task<object> Sample(WebView2 view, CoreWebView2Environment environment)
    {
        await view.CoreWebView2.CallDevToolsProtocolMethodAsync("HeapProfiler.collectGarbage", "{}");
        long privateBytes = 0, workingSet = 0;
        foreach (var info in environment.GetProcessInfos())
        {
            try { using (var process = Process.GetProcessById(info.ProcessId)) {
                privateBytes += process.PrivateMemorySize64; workingSet += process.WorkingSet64;
            }} catch (ArgumentException) { }
        }
        return new {
            metrics = Json.DeserializeObject(await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Performance.getMetrics", "{}")),
            dom = Json.DeserializeObject(await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Memory.getDOMCounters", "{}")),
            processPrivateBytes = privateBytes, processWorkingSet = workingSet,
            activeAnimations = await Evaluate(view, "document.getAnimations().length")
        };
    }
    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        var report = new Dictionary<string, object>(); Exception failure = null;
        using (var form = new Form()) using (var view = new WebView2())
        {
            form.Size = new System.Drawing.Size(1280, 800); form.ShowInTaskbar = false;
            form.StartPosition = FormStartPosition.Manual; form.Location = new System.Drawing.Point(-16000, -16000);
            view.Dock = DockStyle.Fill; form.Controls.Add(view);
            form.Shown += async delegate {
                try {
                    // Keep this offscreen test rendering; do not change the installed app.
                    var options = new CoreWebView2EnvironmentOptions("--disable-background-timer-throttling --disable-renderer-backgrounding --disable-features=CalculateNativeWinOcclusion");
                    var environment = await CoreWebView2Environment.CreateAsync(null, Path.GetFullPath(args[1]), options);
                    await view.EnsureCoreWebView2Async(environment);
                    view.CoreWebView2.Settings.IsWebMessageEnabled = false;
                    var loaded = new TaskCompletionSource<bool>();
                    view.CoreWebView2.NavigationCompleted += (sender, e) => loaded.TrySetResult(e.IsSuccess);
                    view.Source = new Uri(Path.GetFullPath(args[0]));
                    if (await Task.WhenAny(loaded.Task, Task.Delay(15000)) != loaded.Task || !await loaded.Task)
                        throw new Exception("Fixture navigation failed");
                    await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Performance.enable", "{}");
                    await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setEmulatedMedia", "{\"features\":[{\"name\":\"prefers-reduced-motion\",\"value\":\"no-preference\"}]}");
                    report["runtime"] = environment.BrowserVersionString;
                    report["checks"] = await Evaluate(view, "motionChecks()");
                    await Evaluate(view, "motionBatch(false,12)");
                    await Evaluate(view, "motionBatch(true,12)");
                    var rounds = new List<object>();
                    foreach (bool enabled in new[] { false, true, true, true, true, false }) {
                        var before = await Sample(view, environment);
                        var timing = await Evaluate(view, "motionBatch(" + (enabled ? "true" : "false") + ",60)");
                        var after = await Sample(view, environment);
                        rounds.Add(new { enabled, before, timing, after });
                    }
                    report["rounds"] = rounds;
                    await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Emulation.setEmulatedMedia", "{\"features\":[{\"name\":\"prefers-reduced-motion\",\"value\":\"reduce\"}]}");
                    report["reducedMotion"] = await Evaluate(view, "motionReducedCheck()");
                    report["success"] = true;
                } catch (Exception error) { failure = error; report["success"] = false; report["error"] = error.ToString(); }
                finally { File.WriteAllText(args[2], Json.Serialize(report)); form.Close(); }
            };
            Application.Run(form);
        }
        Console.WriteLine(Json.Serialize(report)); return failure == null ? 0 : 1;
    }
}
