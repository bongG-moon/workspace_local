// Short-lived installer helper. No app/Claude process or personal data is changed.
using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

internal static class InstallerSupport
{
    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey { public Guid format; public uint id; }
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct PropVariant {
        [FieldOffset(0)] public ushort type;
        [FieldOffset(8)] public IntPtr pointer;
    }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }
    private static object Get(object item, string name) {
        return item.GetType().InvokeMember(name, BindingFlags.GetProperty, null, item, null);
    }
    private static void Put(object item, string name, object value) {
        item.GetType().InvokeMember(name, BindingFlags.SetProperty, null, item, new object[] {value});
    }
    private static void SafePath(string path) {
        for (string current = Path.GetFullPath(path); !String.IsNullOrEmpty(current); current = Path.GetDirectoryName(current)) {
            if ((File.Exists(current) || Directory.Exists(current)) &&
                (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new IOException();
        }
    }
    private static void CheckDirectory(string directory, string identity, bool uninstall) {
        directory = Path.GetFullPath(directory);
        if (directory.TrimEnd('\\') == Path.GetPathRoot(directory).TrimEnd('\\')) throw new IOException();
        SafePath(directory);
        string marker = Path.Combine(directory, "AX-Workspace.install-id");
        SafePath(marker);
        if (File.Exists(marker)) {
            if (new FileInfo(marker).Length > 256 || File.ReadAllText(marker).Trim() != identity) throw new IOException();
        } else if (uninstall || (Directory.Exists(directory) && Directory.GetFileSystemEntries(directory).Length != 0)) {
            throw new IOException();
        }
        foreach (string file in new string[] {"AX-Workspace.exe", "Uninstall.exe", "AX-Workspace.install-id"})
            SafePath(Path.Combine(directory, file));
    }
    private static void Shortcut(string linkPath, string target, string appId, bool remove) {
        SafePath(linkPath);
        object shell = null, link = null, native = null;
        try {
            shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
            link = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] {linkPath});
            if (File.Exists(linkPath)) {
                string previous = (string)Get(link, "TargetPath");
                if (!String.Equals(Path.GetFullPath(previous), Path.GetFullPath(target), StringComparison.OrdinalIgnoreCase)) {
                    if (remove) return; // Never delete an unrelated shortcut the user replaced.
                    throw new IOException();
                }
            } else if (remove) return;
            if (remove) { File.Delete(linkPath); return; }
            Put(link, "TargetPath", target);
            Put(link, "Arguments", "");
            Put(link, "WorkingDirectory", Path.GetDirectoryName(target));
            Put(link, "IconLocation", target + ",0");
            Put(link, "Description", "AX Workspace - 내 업무와 AI가 만나는 공간");
            link.GetType().InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            native = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
            ((IPersistFile)native).Load(linkPath, 2);
            var key = new PropertyKey {format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), id = 5};
            var value = new PropVariant {type = 31, pointer = Marshal.StringToCoTaskMemUni(appId)};
            try { ((IPropertyStore)native).SetValue(ref key, ref value); ((IPropertyStore)native).Commit(); }
            finally { Marshal.FreeCoTaskMem(value.pointer); }
            ((IPersistFile)native).Save(linkPath, true);
        } finally {
            if (native != null) Marshal.FinalReleaseComObject(native);
            if (link != null) Marshal.FinalReleaseComObject(link);
            if (shell != null) Marshal.FinalReleaseComObject(shell);
        }
    }
    [STAThread]
    private static int Main(string[] args) {
        try {
            if (args.Length == 3 && (args[0] == "check" || args[0] == "check-uninstall"))
                CheckDirectory(args[1], args[2], args[0] == "check-uninstall");
            else if (args.Length == 4 && (args[0] == "shortcut" || args[0] == "remove-shortcut"))
                Shortcut(args[1], args[2], args[3], args[0] == "remove-shortcut");
            else return 2;
            return 0;
        } catch { return 1; }
    }
}
