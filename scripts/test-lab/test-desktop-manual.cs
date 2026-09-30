// Test the production URL policy without starting WebView2 or a browser.
using System;
using System.IO;
using System.Reflection;

internal static class TestDesktopManual
{
    private static int assertions;
    private static void Check(bool condition, string reason)
    {
        assertions++;
        if (!condition) throw new Exception(reason);
    }
    private static void Test(MethodInfo method, Uri home, string value, bool gesture, bool expected)
    {
        object[] inputs = { home, value, gesture, null };
        bool accepted = (bool)method.Invoke(null, inputs);
        Check(accepted == expected, "Unexpected manual URL decision: " + value);
        Uri target = inputs[3] as Uri;
        if (accepted)
        {
            Check(target != null && target.AbsoluteUri == "http://127.0.0.1:49152/manual/guide", "Noncanonical launch URL");
            Check(target.Fragment == "" && target.Query == "" && target.UserInfo == "", "Launch URL carries credentials or parameters");
        }
        else Check(target == null, "Rejected URL returned a launch target");
    }
    private static int Main(string[] args)
    {
        try
        {
            Check(args.Length == 1, "Usage: TestDesktopManual.exe <Workspace.Desktop.exe>");
            var assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
            var method = assembly.GetType("DesktopWindow", true).GetMethod("TryManualTarget", BindingFlags.Static | BindingFlags.NonPublic);
            Check(method != null, "Production manual URL policy missing");
            var home = new Uri("http://127.0.0.1:49152/#token=" + new String('a', 48));
            string allowed = "http://127.0.0.1:49152/manual/guide";
            Test(method, home, allowed, true, true);
            Test(method, home, allowed, false, false);
            foreach (string value in new string[] {
                null, "", "/manual/guide", allowed + "/", allowed + "?next=https://example.com", allowed + "#token=secret",
                allowed + "#usage", "http://127.0.0.1:49152/api/bootstrap", home.AbsoluteUri,
                "http://127.0.0.1:49153/manual/guide", "http://localhost:49152/manual/guide",
                "http://127.0.0.2:49152/manual/guide", "http://[::1]:49152/manual/guide",
                "http://user@127.0.0.1:49152/manual/guide", "https://127.0.0.1:49152/manual/guide",
                "http://127.0.0.1:49152/manual/part/../guide", "http://127.0.0.1:49152/manual/%67uide",
                "http://127.0.0.1:49152/manual/../api/bootstrap", "http://127.0.0.1:49152/manual/%2e%2e/api/bootstrap",
                "http://127.0.0.1:49152/manual/%252e%252e/api/bootstrap", "https://example.com/manual/guide",
                "http://127.0.0.1:49152.evil.test/manual/guide", "javascript:alert(1)", "file:///C:/manual/guide",
                "data:text/html,example", " " + allowed, allowed + "\r\n"
            }) Test(method, home, value, true, false);
            Test(method, null, allowed, true, false);
            Test(method, new Uri("http://127.0.0.1/"), "http://127.0.0.1/manual/guide", true, false);
            Test(method, new Uri("http://user@127.0.0.1:49152/"), "http://user@127.0.0.1:49152/manual/guide", true, false);
            Console.WriteLine("PASS: " + assertions + " production manual policy checks; token-free exact target; no browser launched.");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
