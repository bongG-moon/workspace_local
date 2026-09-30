// Native state checks on an isolated, noninteractive Windows desktop. The real
// DesktopWindow is loaded without starting WebView2, private pipes or the app.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

internal static class TestDesktopWindow
{
    private static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateDesktop(string name, IntPtr device, IntPtr mode, uint flags, uint access, IntPtr security);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetThreadDesktop(IntPtr desktop);
    [DllImport("user32.dll")]
    private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")]
    private static extern bool IsZoomed(IntPtr window);
    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);
    private static void Check(bool condition, string reason) { if (!condition) throw new Exception(reason); }
    private static void Pump() { Application.DoEvents(); }

    private static void CheckWindow(Assembly assembly)
    {
        var type = assembly.GetType("DesktopWindow", true);
        var config = new Dictionary<string, object> {
            { "url", "http://127.0.0.1:49152/#token=" + new String('a', 48) },
            { "profile", Path.GetFullPath(Path.Combine(Path.GetTempPath(), "workspace-window-unused-profile")) },
            { "background", true }
        };
        using (var window = (Form)Activator.CreateInstance(type, Private, null, new object[] { config }, null))
        {
            // Disable only external initialization; native Form display and the
            // production activation method run unchanged on this test desktop.
            type.GetField("controlReaderStarted", Private).SetValue(window, true);
            type.GetField("initializing", Private).SetValue(window, true);
            var activate = type.GetMethod("ActivateWindow", Private);
            Check(activate != null, "Production activation method missing");
            Check(window.WindowState == FormWindowState.Maximized, "Fresh form is not configured to maximize");
            window.Show(); Pump();
            IntPtr handle = window.Handle;
            Check(IsZoomed(handle) && window.WindowState == FormWindowState.Maximized, "Fresh native window is not maximized");
            Rectangle area = Screen.FromHandle(handle).WorkingArea;
            Rectangle client = window.RectangleToScreen(window.ClientRectangle);
            Check(area.Contains(client), "Maximized client escaped monitor work area");
            Check(client.Left == area.Left && client.Right == area.Right && client.Bottom == area.Bottom,
                "Maximized client did not fill the available work area");
            Check(window.FormBorderStyle == FormBorderStyle.Sizable && !window.TopMost,
                "Startup changed normal window chrome or made the app topmost");
            for (int index = 0; index < 4; index++)
            {
                activate.Invoke(window, null); Pump();
                Check(window.Handle == handle && IsZoomed(handle), "Repeated activation changed native window or startup state");
            }
            window.WindowState = FormWindowState.Minimized; Pump();
            Check(IsIconic(handle), "Maximized test window did not minimize");
            activate.Invoke(window, null); Pump();
            Check(IsZoomed(handle), "Activation lost the prior maximized state");
            window.Hide(); Pump();
            Check(!IsWindowVisible(handle), "Maximized window did not hide");
            activate.Invoke(window, null); Pump();
            Check(IsWindowVisible(handle) && IsZoomed(handle), "Tray reopen lost maximized state");
            window.WindowState = FormWindowState.Normal; Pump();
            Rectangle restored = window.Bounds;
            Check(!IsZoomed(handle) && !IsIconic(handle) && restored.Width < area.Width,
                "User restore did not retain a sensible normal window size");
            activate.Invoke(window, null); Pump();
            Check(window.WindowState == FormWindowState.Normal && window.Bounds == restored,
                "Activation overrode the user's normal window preference");
            window.WindowState = FormWindowState.Minimized; Pump();
            Check(IsIconic(handle), "Normal test window did not minimize");
            activate.Invoke(window, null); Pump();
            Check(window.WindowState == FormWindowState.Normal && window.Bounds == restored,
                "Activation lost the prior normal window state or bounds");
            window.Close(); Pump(); // Production titlebar-close handler hides in background mode.
            Check(!window.IsDisposed && !IsWindowVisible(handle), "Background close did not hide the existing window");
            activate.Invoke(window, null); Pump();
            Check(window.Handle == handle && IsWindowVisible(handle) && window.WindowState == FormWindowState.Normal
                && window.Bounds == restored, "Tray reopen lost normal state or created another window");
            window.WindowState = FormWindowState.Minimized; Pump();
            window.Hide(); Pump();
            activate.Invoke(window, null); Pump();
            Check(IsWindowVisible(handle) && window.WindowState == FormWindowState.Normal && window.Bounds == restored,
                "Hidden minimized window lost the normal restore preference");
            window.WindowState = FormWindowState.Maximized; Pump();
            window.WindowState = FormWindowState.Minimized; Pump();
            window.Hide(); Pump();
            activate.Invoke(window, null); Pump();
            Check(IsZoomed(handle) && IsWindowVisible(handle), "Hidden minimized window lost the maximized restore preference");
            type.GetField("exiting", Private).SetValue(window, true);
            window.Close(); Pump();
        }
    }

    private static int Main(string[] args)
    {
        IntPtr desktop = IntPtr.Zero;
        try
        {
            Check(args.Length == 1, "Usage: TestDesktopWindow.exe <Workspace.Desktop.exe>");
            desktop = CreateDesktop("WorkspaceWindowTest-" + Guid.NewGuid().ToString("N"), IntPtr.Zero, IntPtr.Zero,
                0, 0x0083, IntPtr.Zero); // Read/write objects and create windows only.
            Check(desktop != IntPtr.Zero, "Could not create isolated native test desktop: " + Marshal.GetLastWin32Error());
            Exception failure = null;
            var thread = new Thread(delegate()
            {
                try
                {
                    Check(SetThreadDesktop(desktop), "Could not attach isolated native test desktop: " + Marshal.GetLastWin32Error());
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    CheckWindow(Assembly.LoadFrom(Path.GetFullPath(args[0])));
                }
                catch (Exception error) { failure = error; }
            });
            // STA setup creates a COM helper HWND before the thread body and
            // pins it to the input desktop. These Form-only checks need no COM.
            thread.SetApartmentState(ApartmentState.MTA);
            thread.IsBackground = true;
            thread.Start();
            Check(thread.Join(30000), "Native window checks timed out");
            if (failure != null) throw failure;
            Console.WriteLine("PASS: startup work-area maximize; same HWND activation; maximized/normal minimize restore; tray hide/reopen; hidden minimized restore.");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        finally
        {
            if (desktop != IntPtr.Zero) CloseDesktop(desktop);
        }
    }
}
