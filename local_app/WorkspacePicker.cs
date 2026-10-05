using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

// Compiled by Windows PowerShell 5.1 in the short-lived picker process.
// No registry, compatibility setting, or installed runtime is changed.
namespace WorkspacePicker {
    public static class DisplayScaling {
        private static readonly IntPtr PerMonitorV2 = new IntPtr(-4);
        private static readonly IntPtr PerMonitor = new IntPtr(-3);
        private static readonly IntPtr SystemAware = new IntPtr(-2);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("user32.dll")] private static extern IntPtr GetThreadDpiAwarenessContext();
        [DllImport("user32.dll")] private static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
        [DllImport("shcore.dll")] private static extern int SetProcessDpiAwareness(int awareness);
        [DllImport("shcore.dll")] private static extern int GetProcessDpiAwareness(IntPtr process, out int awareness);
        [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")] private static extern bool IsProcessDPIAware();

        // PowerShell may have a manifest-fixed process mode. The thread override
        // still gives every subsequently created picker window its own PMv2 mode.
        public static void Initialize() {
            try { SetProcessDpiAwarenessContext(PerMonitorV2); }
            catch (EntryPointNotFoundException) { }
            try {
                if (SetThreadDpiAwarenessContext(PerMonitorV2) != IntPtr.Zero) return;
            } catch (EntryPointNotFoundException) { }
            try { SetProcessDpiAwareness(2); }
            catch (DllNotFoundException) { }
            catch (EntryPointNotFoundException) { }
            try {
                if (SetThreadDpiAwarenessContext(PerMonitor) != IntPtr.Zero) return;
            } catch (EntryPointNotFoundException) { }
            // Windows 7 fallback, only in this disposable helper process.
            SetProcessDPIAware();
        }

        public static string CurrentMode {
            get {
                try {
                    IntPtr context = GetThreadDpiAwarenessContext();
                    if (AreDpiAwarenessContextsEqual(context, PerMonitorV2)) return "PerMonitorV2";
                    if (AreDpiAwarenessContextsEqual(context, PerMonitor)) return "PerMonitor";
                    if (AreDpiAwarenessContextsEqual(context, SystemAware)) return "SystemAware";
                    return "Unaware";
                } catch (EntryPointNotFoundException) { }
                try {
                    int awareness;
                    if (GetProcessDpiAwareness(IntPtr.Zero, out awareness) >= 0) {
                        return awareness == 2 ? "PerMonitor" : awareness == 1 ? "SystemAware" : "Unaware";
                    }
                } catch (DllNotFoundException) { }
                catch (EntryPointNotFoundException) { }
                return IsProcessDPIAware() ? "SystemAware" : "Unaware";
            }
        }
    }

    public sealed class OwnerForm : Form {
        private readonly Font pickerFont = new Font("Segoe UI", 9F);
        public OwnerForm() {
            Font = pickerFont;
            AutoScaleDimensions = new SizeF(96F, 96F);
            AutoScaleMode = AutoScaleMode.Dpi;
        }
        public bool ActivateWhenShown { get; set; }
        protected override bool ShowWithoutActivation { get { return !ActivateWhenShown; } }
        protected override void Dispose(bool disposing) {
            try { base.Dispose(disposing); }
            finally { if (disposing) pickerFont.Dispose(); }
        }
    }

    public sealed class NativeOwner : IWin32Window {
        public IntPtr Handle { get; private set; }
        private NativeOwner(IntPtr handle) { Handle = handle; }

        [StructLayout(LayoutKind.Sequential)]
        private struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr handle);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr handle);
        [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr handle, uint flags);
        [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr handle, out Rect rect);
        [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr handle);

        // Revalidate the caller's foreground Workspace handle after startup.
        public static NativeOwner FromHandle(long value) {
            IntPtr handle = new IntPtr(value);
            if (handle == IntPtr.Zero || !IsWindow(handle) || !IsWindowVisible(handle)) return null;
            handle = GetAncestor(handle, 2); // GA_ROOT
            return handle == IntPtr.Zero ? null : new NativeOwner(handle);
        }
        public Rectangle Bounds() {
            Rect rect;
            if (!GetWindowRect(Handle, out rect)) return Rectangle.Empty;
            return Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom);
        }
        public static void TryActivate(IntPtr handle) { SetForegroundWindow(handle); }
        public bool IsForeground() { return GetForegroundWindow() == Handle; }
    }

    public sealed class NativePathDialog : IDisposable {
        private const uint PickFolders = 0x20;
        private const uint ForceFileSystem = 0x40;
        private const uint AllowMultiSelect = 0x200;
        private const uint PathMustExist = 0x800;
        private const uint FileMustExist = 0x1000;
        private const uint NoChangeDirectory = 0x8;
        private const int Cancelled = unchecked((int)0x800704C7);
        private const uint FileSystemPath = 0x80058000;
        private IFileOpenDialog dialog;

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        private static extern void SHCreateItemFromParsingName(string path, IntPtr context, ref Guid iid,
                                                               [MarshalAs(UnmanagedType.Interface)] out IShellItem item);

        public bool IsFolderPicker { get; private set; }
        public bool Multiselect { get { return !IsFolderPicker; } }
        public string SelectedPath { get; private set; }
        public string[] FileNames { get; private set; }
        public uint NativeOptions {
            get { EnsureOpen(); uint flags; dialog.GetOptions(out flags); return flags; }
        }

        public NativePathDialog(string kind) {
            if (kind != "folder" && kind != "files") throw new ArgumentException("Unknown picker kind.", "kind");
            if (Thread.CurrentThread.GetApartmentState() != ApartmentState.STA)
                throw new InvalidOperationException("The path picker requires an STA thread.");
            IsFolderPicker = kind == "folder";
            SelectedPath = String.Empty;
            FileNames = new string[0];
            try {
                dialog = (IFileOpenDialog)new FileOpenDialog();
                uint flags;
                dialog.GetOptions(out flags);
                flags |= ForceFileSystem | PathMustExist | FileMustExist | NoChangeDirectory;
                if (IsFolderPicker) {
                    flags = (flags | PickFolders) & ~AllowMultiSelect;
                    dialog.SetTitle("Company Workspace - 업무 폴더 선택");
                    dialog.SetOkButtonLabel("이 폴더 선택");
                    dialog.SetFileNameLabel("폴더:");
                } else {
                    flags = (flags | AllowMultiSelect | FileMustExist) & ~PickFolders;
                    dialog.SetTitle("Company Workspace - 자료 추가");
                    dialog.SetOkButtonLabel("자료 추가");
                    dialog.SetFileNameLabel("파일 이름:");
                    // Selection only. The server revalidates references; no file is executed or extracted.
                    FilterSpec[] filters = new FilterSpec[] {
                        new FilterSpec("지원하는 모든 자료", "*.7z;*.adoc;*.alz;*.bash;*.bat;*.bz2;*.c;*.cab;*.cc;*.cfg;*.cjs;*.cmd;*.conf;*.cpp;*.cs;*.css;*.csv;*.cts;*.cxx;*.dart;*.docx;*.egg;*.ex;*.exe;*.exs;*.fs;*.fsx;*.go;*.gql;*.graphql;*.gz;*.h;*.hpp;*.htm;*.html;*.hxx;*.ini;*.ipynb;*.java;*.jpeg;*.jpg;*.js;*.json;*.jsonc;*.jsx;*.kt;*.kts;*.less;*.log;*.lua;*.lz;*.lz4;*.lzma;*.md;*.mjs;*.mts;*.pdf;*.php;*.pl;*.png;*.pptx;*.properties;*.proto;*.ps1;*.psd1;*.psm1;*.py;*.pyi;*.pyw;*.r;*.rar;*.rb;*.rmd;*.rs;*.rst;*.sass;*.scss;*.sh;*.sql;*.svelte;*.swift;*.tar;*.tbz;*.tbz2;*.tex;*.tgz;*.toml;*.ts;*.tsv;*.tsx;*.txt;*.txz;*.tzst;*.vb;*.vbs;*.vue;*.webp;*.xlsx;*.xml;*.xz;*.yaml;*.yml;*.zip;*.zsh;*.zst"),
                        new FilterSpec("문서", "*.pptx;*.docx;*.xlsx;*.csv;*.tsv;*.pdf;*.txt;*.md;*.html;*.htm"),
                        new FilterSpec("이미지", "*.png;*.jpg;*.jpeg;*.webp"),
                        new FilterSpec("압축 파일", "*.7z;*.alz;*.bz2;*.cab;*.egg;*.gz;*.lz;*.lz4;*.lzma;*.rar;*.tar;*.tbz;*.tbz2;*.tgz;*.txz;*.tzst;*.xz;*.zip;*.zst"),
                        new FilterSpec("실행 파일 (EXE)", "*.exe")
                    };
                    dialog.SetFileTypes((uint)filters.Length, filters);
                    dialog.SetFileTypeIndex(1);
                }
                dialog.SetOptions(flags);
                // Separate recent locations for folders and attachments.
                Guid client = IsFolderPicker
                    ? new Guid("446432A6-181F-4F99-9A88-1DA450942AAA")
                    : new Guid("457833A5-7BD3-409C-A576-ECC14159A83D");
                dialog.SetClientGuid(ref client);
            } catch {
                Dispose();
                throw;
            }
        }

        public void SetInitialDirectory(string path) {
            EnsureOpen();
            if (String.IsNullOrWhiteSpace(path) || !System.IO.Path.IsPathRooted(path) || !System.IO.Directory.Exists(path))
                throw new ArgumentException("The initial directory must exist.", "path");
            IShellItem item = null;
            try {
                Guid iid = typeof(IShellItem).GUID;
                SHCreateItemFromParsingName(path, IntPtr.Zero, ref iid, out item);
                dialog.SetFolder(item);
            } finally { Release(item); }
        }

        public DialogResult ShowDialog(IWin32Window owner) {
            EnsureOpen();
            if (owner == null) throw new ArgumentNullException("owner");
            SelectedPath = String.Empty;
            FileNames = new string[0];
            int result = dialog.Show(owner.Handle);
            if (result == Cancelled) return DialogResult.Cancel;
            Marshal.ThrowExceptionForHR(result);
            if (IsFolderPicker) {
                IShellItem item = null;
                try { dialog.GetResult(out item); SelectedPath = ReadPath(item); }
                finally { Release(item); }
            } else {
                IShellItemArray items = null;
                try {
                    dialog.GetResults(out items);
                    uint count;
                    items.GetCount(out count);
                    string[] paths = new string[count];
                    for (uint index = 0; index < count; index++) {
                        IShellItem item = null;
                        try { items.GetItemAt(index, out item); paths[index] = ReadPath(item); }
                        finally { Release(item); }
                    }
                    FileNames = paths;
                } finally { Release(items); }
            }
            return DialogResult.OK;
        }

        private static string ReadPath(IShellItem item) {
            IntPtr value = IntPtr.Zero;
            try {
                item.GetDisplayName(FileSystemPath, out value);
                string path = Marshal.PtrToStringUni(value);
                if (String.IsNullOrEmpty(path)) throw new InvalidOperationException("The selection has no filesystem path.");
                return path;
            } finally { if (value != IntPtr.Zero) Marshal.FreeCoTaskMem(value); }
        }
        private void EnsureOpen() {
            if (dialog == null) throw new ObjectDisposedException("NativePathDialog");
        }
        private static void Release(object value) {
            if (value != null && Marshal.IsComObject(value)) Marshal.FinalReleaseComObject(value);
        }
        public void Dispose() {
            IFileOpenDialog value = dialog;
            dialog = null;
            Release(value);
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct FilterSpec {
            [MarshalAs(UnmanagedType.LPWStr)] public string Name;
            [MarshalAs(UnmanagedType.LPWStr)] public string Pattern;
            public FilterSpec(string name, string pattern) { Name = name; Pattern = pattern; }
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct PropertyKey { public Guid Format; public uint Id; }

        [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"), ClassInterface(ClassInterfaceType.None)]
        private class FileOpenDialog { }

        // The complete inherited IModalWindow + IFileDialog vtable must precede
        // GetResults. IntPtr parameters belong only to interfaces never called.
        [ComImport, Guid("D57C7288-D4AD-4768-BE02-9D969532D960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileOpenDialog {
            [PreserveSig] int Show(IntPtr owner);
            void SetFileTypes(uint count, [MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 0)] FilterSpec[] filters);
            void SetFileTypeIndex(uint index);
            void GetFileTypeIndex(out uint index);
            void Advise(IntPtr events, out uint cookie);
            void Unadvise(uint cookie);
            void SetOptions(uint options);
            void GetOptions(out uint options);
            void SetDefaultFolder(IShellItem item);
            void SetFolder(IShellItem item);
            void GetFolder(out IShellItem item);
            void GetCurrentSelection(out IShellItem item);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetFileName(out IntPtr name);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void GetResult(out IShellItem item);
            void AddPlace(IShellItem item, int alignment);
            void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
            void Close(int result);
            void SetClientGuid(ref Guid guid);
            void ClearClientData();
            void SetFilter(IntPtr filter);
            void GetResults(out IShellItemArray items);
            void GetSelectedItems(out IShellItemArray items);
        }
        [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem {
            void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr value);
            void GetParent(out IShellItem item);
            void GetDisplayName(uint name, out IntPtr value);
            void GetAttributes(uint mask, out uint attributes);
            void Compare(IShellItem other, uint hint, out int order);
        }
        [ComImport, Guid("B63EA76D-1F85-456F-A19C-48159EFA858B"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItemArray {
            void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr value);
            void GetPropertyStore(int flags, ref Guid iid, out IntPtr value);
            void GetPropertyDescriptionList(ref PropertyKey key, ref Guid iid, out IntPtr value);
            void GetAttributes(uint flags, uint mask, out uint attributes);
            void GetCount(out uint count);
            void GetItemAt(uint index, out IShellItem item);
            void EnumItems(out IntPtr items);
        }
    }
}
