// Offline native component checks. No app, live desktop input, Claude or account
// state is opened. Reflection constructs the shipped notification control only.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class TestNotificationCard
{
    private static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static Type cardType;
    private static Delegate completed;
    private static int opened, dismissed;
    private static int nativeNcChecks, nativeCornerChecks, nativeBorderChecks;
    [DllImport("user32.dll")]
    private static extern int GetGuiResources(IntPtr process, uint flags);
    [DllImport("dwmapi.dll")]
    private static extern int DwmGetWindowAttribute(IntPtr window, int attribute, out int value, int size);
    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr window, out NativeRect value);
    [DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr window, out NativeRect value);
    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect { internal int Left, Top, Right, Bottom; }
    private static void Check(bool condition, string reason) { if (!condition) throw new Exception(reason); }
    private static void Completed(object card, bool wasOpened) { if (wasOpened) opened++; else dismissed++; }
    private static Form Card(string kind, string title = "9월 실적 보고서 정리 · 긴 업무 제목 확인용 알림 카드입니다")
    {
        return (Form)Activator.CreateInstance(cardType, Private, null, new object[] {
            new String('a', 64), kind, title,
            kind == "attention" ? "승인 또는 답변이 필요해요. 클릭하면 해당 업무를 열어요."
                : kind == "error" ? "작업 상태를 확인해 주세요. 클릭하면 해당 업무를 열어요."
                : "작업이 완료됐어요. 클릭하면 결과를 볼 수 있어요.", completed }, null);
    }
    private static void Arrange(Form card, Rectangle area, uint dpi)
    {
        cardType.GetMethod("Arrange", Private).Invoke(card, new object[] { area, dpi });
        Check(area.Contains(card.Bounds), "Card escaped monitor working area");
        foreach (Control child in card.Controls) Check(card.ClientRectangle.Contains(child.Bounds), "Child escaped card: " + child.Text);
    }
    private static int U16(byte[] data, int offset)
    {
        Check(offset >= 0 && offset <= data.Length - 2, "Truncated font data");
        return (data[offset] << 8) | data[offset + 1];
    }
    private static uint U32(byte[] data, int offset)
    {
        Check(offset >= 0 && offset <= data.Length - 4, "Truncated font data");
        return ((uint)U16(data, offset) << 16) | (uint)U16(data, offset + 2);
    }
    private static int Table(byte[] font, string tag)
    {
        int count = U16(font, 4);
        for (int index = 0; index < count; index++)
        {
            int entry = 12 + 16 * index;
            Check(entry <= font.Length - 16, "Invalid font table directory");
            if (font[entry] == tag[0] && font[entry + 1] == tag[1] && font[entry + 2] == tag[2] && font[entry + 3] == tag[3])
            {
                int offset = checked((int)U32(font, entry + 8)), size = checked((int)U32(font, entry + 12));
                Check(offset >= 0 && size >= 0 && offset <= font.Length - size, "Invalid font table bounds");
                return offset;
            }
        }
        throw new Exception("Required font table missing: " + tag);
    }
    private static bool HasGlyph(byte[] font, int cmap, int codepoint)
    {
        for (int index = 0, count = U16(font, cmap + 2); index < count; index++)
        {
            int entry = cmap + 4 + 8 * index, platform = U16(font, entry), encoding = U16(font, entry + 2);
            if (platform != 0 && !(platform == 3 && (encoding == 1 || encoding == 10))) continue;
            int mapping = checked(cmap + (int)U32(font, entry + 4)), format = U16(font, mapping);
            if (format == 12)
            {
                int groups = checked((int)U32(font, mapping + 12));
                for (int group = 0; group < groups; group++)
                {
                    int range = mapping + 16 + 12 * group;
                    uint first = U32(font, range), last = U32(font, range + 4);
                    if (codepoint < first) break;
                    if (codepoint <= last) return U32(font, range + 8) + (uint)codepoint - first != 0;
                }
            }
            else if (format == 4 && codepoint <= 0xffff)
            {
                int segments = U16(font, mapping + 6) / 2, ends = mapping + 14;
                int starts = ends + segments * 2 + 2, deltas = starts + segments * 2, offsets = deltas + segments * 2;
                for (int segment = 0; segment < segments; segment++)
                {
                    if (codepoint > U16(font, ends + segment * 2)) continue;
                    int first = U16(font, starts + segment * 2);
                    if (codepoint < first) break;
                    int delta = U16(font, deltas + segment * 2), offsetAt = offsets + segment * 2;
                    int distance = U16(font, offsetAt);
                    int glyph = distance == 0 ? codepoint : U16(font, offsetAt + distance + 2 * (codepoint - first));
                    return (distance == 0 || glyph != 0) && ((glyph + delta) & 0xffff) != 0;
                }
            }
        }
        return false;
    }
    private static void FontResource(Assembly assembly, string name, int weight)
    {
        byte[] font;
        using (var resource = assembly.GetManifestResourceStream(name))
        {
            Check(resource != null, "Missing private font resource: " + name);
            using (var decompressed = new GZipStream(resource, CompressionMode.Decompress))
            using (var bytes = new MemoryStream())
            { decompressed.CopyTo(bytes); font = bytes.ToArray(); }
        }
        Check(U32(font, 0) == 0x00010000, "Expected static TrueType font resource");
        Check(U16(font, Table(font, "OS/2") + 4) == weight, "Unexpected real font weight: " + name);
        int cmap = Table(font, "cmap");
        Check(HasGlyph(font, cmap, 'A') && HasGlyph(font, cmap, '0'), "Missing basic Latin glyphs: " + name);
        for (int codepoint = 0xac00; codepoint <= 0xd7a3; codepoint++)
            Check(HasGlyph(font, cmap, codepoint), "Incomplete Hangul font: " + name + " U+" + codepoint.ToString("X4"));
    }
    private static long Ink(Font font)
    {
        using (var image = new Bitmap(320, 60))
        {
            using (var graphics = Graphics.FromImage(image))
            {
                graphics.Clear(Color.White);
                graphics.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                graphics.DrawString("가나다ABC 123", font, Brushes.Black, 0, 0, StringFormat.GenericTypographic);
            }
            long ink = 0;
            for (int y = 0; y < image.Height; y++)
                for (int x = 0; x < image.Width; x++) ink += 255 - image.GetPixel(x, y).R;
            return ink;
        }
    }
    private static void PrivateFonts(Assembly assembly)
    {
        var type = assembly.GetType("WorkspaceNotificationFonts", true);
        var flags = BindingFlags.Static | BindingFlags.NonPublic;
        Check((bool)type.GetProperty("EmbeddedAvailable", flags).GetValue(null, null), "Private fonts fell back to installed fonts");
        var create = type.GetMethod("CreateFont", flags);
        using (var regular = (Font)create.Invoke(null, new object[] { 20f, false }))
        using (var semibold = (Font)create.Invoke(null, new object[] { 20f, true }))
        {
            Check(regular.FontFamily.Name.StartsWith("Noto Sans KR", StringComparison.Ordinal)
                && semibold.FontFamily.Name.StartsWith("Noto Sans KR", StringComparison.Ordinal), "Unexpected private font families");
            Check(regular.Style == FontStyle.Regular && semibold.Style == FontStyle.Regular, "Emphasis must use real 600 face, not synthesized bold");
            Check(regular.Unit == GraphicsUnit.Pixel && semibold.Unit == GraphicsUnit.Pixel, "Font size does not follow manual DPI layout");
            long normalInk = Ink(regular), emphasizedInk = Ink(semibold);
            Check(normalInk > 0 && emphasizedInk > normalInk * 1.03, "Regular and semibold faces render the same weight");
        }
    }
    private static void NativeFrame(Form card)
    {
        IntPtr window = card.Handle; // Hidden HWND only; no desktop activation or input.
        int value;
        if (DwmGetWindowAttribute(window, 1, out value, 4) >= 0)
        { nativeNcChecks++; Check(value == 0, "System non-client rendering adds an extra frame"); }
        if (DwmGetWindowAttribute(window, 33, out value, 4) >= 0)
        { nativeCornerChecks++; Check(value == 1, "System corner rounding conflicts with manual region"); }
        if (DwmGetWindowAttribute(window, 34, out value, 4) >= 0)
        { nativeBorderChecks++; Check(value == unchecked((int)0xfffffffe), "System border color was not disabled"); }
        NativeRect outer = new NativeRect(), client = new NativeRect();
        Check(GetWindowRect(window, out outer) && GetClientRect(window, out client), "Hidden native geometry unavailable");
        Check(outer.Right - outer.Left == client.Right - client.Left && outer.Bottom - outer.Top == client.Bottom - client.Top,
            "Native window contains an extra non-client frame");
    }
    private static void CompactLayout(Form card, uint dpi)
    {
        float scale = dpi / 96f;
        Check(card.ClientSize == new Size((int)Math.Round(340 * scale), (int)Math.Round(96 * scale)), "Unexpected compact card dimensions");
        int labels = 0, buttons = 0;
        foreach (Control control in card.Controls)
        {
            if (control is Label)
            {
                labels++;
                Check(((Label)control).UseCompatibleTextRendering, "Private GDI+ font rendered through GDI label");
            }
            if (control is Button) buttons++;
            if (control.Text.Length != 0)
                Check(control.Font.FontFamily.Name.StartsWith("Noto Sans KR", StringComparison.Ordinal), "Private Noto font unavailable: " + control.Text);
        }
        Check(labels == 2 && buttons == 2, "Compact card must contain only status/title and open/close controls");
        var status = (Label)cardType.GetField("statusLabel", Private).GetValue(card);
        var title = (Label)cardType.GetField("titleLabel", Private).GetValue(card);
        var action = (Button)cardType.GetField("openButton", Private).GetValue(card);
        Check(Math.Abs(status.Font.Size - 11 * scale) < .01f && Math.Abs(title.Font.Size - 14 * scale) < .01f
            && Math.Abs(action.Font.Size - 12 * scale) < .01f, "Typography hierarchy or DPI sizing changed");
        Check(card.Region != null, "Missing uniform manual window region");
        float radius = 10 * scale;
        if (!SystemInformation.HighContrast)
        {
            Check(!card.Region.IsVisible(.15f * radius, .15f * radius), "Rounded region has square corner");
            Check(card.Region.IsVisible(.35f * radius, .35f * radius), "Rounded region radius exceeds 10 DIP");
        }
        Check(card.Region.IsVisible(card.Width / 2f, 1) && card.Region.IsVisible(1, card.Height / 2f), "Region clips straight borders");
    }
    private static void Render(Form card, string target)
    {
        using (var image = new Bitmap(card.Width, card.Height))
        {
            card.DrawToBitmap(image, card.ClientRectangle);
            // Hidden parents omit child controls in DrawToBitmap. Composite each
            // control's own painting at its actual layout without showing a Form.
            using (var graphics = Graphics.FromImage(image))
                foreach (Control child in card.Controls)
                    using (var childImage = new Bitmap(child.Width, child.Height))
                    { child.DrawToBitmap(childImage, child.ClientRectangle); graphics.DrawImageUnscaled(childImage, child.Location); }
            image.Save(target);
        }
    }
    private static void Collect() { GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect(); }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            var assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
            cardType = assembly.GetType("WorkspaceNotificationCard", true);
            FontResource(assembly, "Workspace.NotoSansKR.Regular.ttf.gz", 400);
            FontResource(assembly, "Workspace.NotoSansKR.SemiBold.ttf.gz", 600);
            completed = Delegate.CreateDelegate(typeof(Action<,>).MakeGenericType(cardType, typeof(bool)),
                typeof(TestNotificationCard).GetMethod("Completed", BindingFlags.Static | BindingFlags.NonPublic));
            var valid = cardType.GetMethod("ValidText", BindingFlags.Static | BindingFlags.NonPublic);
            Check((bool)valid.Invoke(null, new object[] { "한글 제목 😀", 100 }), "Valid title rejected");
            foreach (string value in new [] { "", "\ninvalid", "a\u202e", "\ud800", "\udc00", new String('가', 101) })
                Check(!(bool)valid.Invoke(null, new object[] { value, 100 }), "Invalid text accepted");
            string emoji = Char.ConvertFromUtf32(0x1F600), hundredEmoji = "";
            for (int index = 0; index < 100; index++) hundredEmoji += emoji;
            Check((bool)valid.Invoke(null, new object[] { hundredEmoji, 100 }), "Scalar count does not match Python title contract");
            Check(!(bool)valid.Invoke(null, new object[] { hundredEmoji + emoji, 100 }), "Overlong scalar title accepted");
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            PrivateFonts(assembly);
            string output = Path.GetFullPath(args[1]); Directory.CreateDirectory(output);
            foreach (string kind in new[] { "completed", "attention", "error" })
                using (var card = Card(kind))
                {
                    Check(!card.ShowInTaskbar && card.TopMost && card.FormBorderStyle == FormBorderStyle.None, "Unexpected window presentation");
                    Check((bool)cardType.GetProperty("ShowWithoutActivation", Private).GetValue(card, null), "Window would activate on show");
                    var parameters = (CreateParams)cardType.GetProperty("CreateParams", Private).GetValue(card, null);
                    Check((parameters.ExStyle & 0x08000080) == 0x08000080, "Missing non-activation/toolwindow styles");
                    Check((parameters.ClassStyle & 0x00020000) == 0, "Legacy class shadow adds a second outline");
                    foreach (uint dpi in new uint[] { 96, 144, 192 })
                    {
                        Arrange(card, new Rectangle(-1280, -160, 1280, 720), dpi);
                        CompactLayout(card, dpi);
                        NativeFrame(card);
                        Render(card, Path.Combine(output, kind + "-" + dpi + ".png"));
                    }
                    Arrange(card, new Rectangle(100, 50, 320, 160), 192);
                }
            using (var preview = Card("attention", "분기 실적 보고서 정리"))
            {
                Arrange(preview, new Rectangle(0, 0, 1280, 720), 144);
                CompactLayout(preview, 144);
                Render(preview, Path.Combine(output, "attention-144-short.png"));
            }
            foreach (bool clickOpen in new[] { true, false })
            {
                opened = dismissed = 0;
                using (var card = Card("attention"))
                {
                    Arrange(card, new Rectangle(0, 0, 1280, 720), 96);
                    AccessibleObject selectedAction = null;
                    foreach (Control control in card.Controls)
                    {
                        if (!(control is Button)) continue;
                        var style = typeof(Control).GetMethod("GetStyle", Private);
                        Check(!(bool)style.Invoke(control, new object[] { ControlStyles.Selectable }), "Button can take focus");
                        Check((bool)style.Invoke(control, new object[] { ControlStyles.UserMouse }), "Button enters native focus path");
                        Check(control.AccessibilityObject.Role == AccessibleRole.PushButton, "Missing accessible button role");
                        bool isClose = control.AccessibleName == "알림 닫기";
                        if (isClose != clickOpen) selectedAction = control.AccessibilityObject;
                    }
                    Check(selectedAction != null, "Accessible action missing");
                    selectedAction.DoDefaultAction();
                    cardType.GetMethod("Finish", Private).Invoke(card, new object[] { !clickOpen });
                }
                Check(opened == (clickOpen ? 1 : 0) && dismissed == (clickOpen ? 0 : 1), "Callback was missing, duplicated or retargeted");
            }
            Collect();
            var ownProcess = Process.GetCurrentProcess();
            IntPtr process = ownProcess.Handle;
            int gdiBefore = GetGuiResources(process, 0), userBefore = GetGuiResources(process, 1);
            for (int index = 0; index < 64; index++)
                using (var card = Card("completed"))
                {
                    Arrange(card, new Rectangle(0, 0, 1280, 720), 144);
                    card.CreateControl();
                    Arrange(card, new Rectangle(0, 0, 800, 600), 96);
                }
            Collect();
            int gdiAfter = GetGuiResources(process, 0), userAfter = GetGuiResources(process, 1);
            GC.KeepAlive(ownProcess); ownProcess.Dispose();
            Check(gdiBefore > 0 && userBefore > 0 && gdiAfter > 0 && userAfter > 0, "Native handle measurements unavailable");
            Check(gdiAfter <= gdiBefore + 8 && userAfter <= userBefore + 3, "Native handles grew across dispose cycles");
            Console.WriteLine("PASS: input validation; full Hangul private fonts (actual 400/600 weights and different rendered weight); 3 compact card kinds x 3 DPIs; negative/short work areas; uniform 10-DIP region without legacy shadow or native non-client frame; non-activation styles; accessible open/close exactly once; 64 dispose cycles.");
            Console.WriteLine("GDI {0}->{1}; USER {2}->{3}. Rendered cards: {4}", gdiBefore, gdiAfter, userBefore, userAfter, output);
            Console.WriteLine("Available DWM assertions (of 9 HWND/DPI samples): NC={0}; corners={1}; border={2}.", nativeNcChecks, nativeCornerChecks, nativeBorderChecks);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
