// Offline native component checks. No app, live desktop input, Claude or account
// state is opened. Reflection constructs the shipped notification control only.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class TestNotificationCard
{
    private static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static Type cardType;
    private static Delegate completed;
    private static int opened, dismissed;
    [DllImport("user32.dll")]
    private static extern int GetGuiResources(IntPtr process, uint flags);
    private static void Check(bool condition, string reason) { if (!condition) throw new Exception(reason); }
    private static void Completed(object card, bool wasOpened) { if (wasOpened) opened++; else dismissed++; }
    private static Form Card(string kind)
    {
        return (Form)Activator.CreateInstance(cardType, Private, null, new object[] {
            new String('a', 64), kind, "9월 실적 보고서 정리 · 긴 업무 제목 확인용 알림 카드입니다",
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
            cardType = Assembly.LoadFrom(Path.GetFullPath(args[0])).GetType("WorkspaceNotificationCard", true);
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
            string output = Path.GetFullPath(args[1]); Directory.CreateDirectory(output);
            foreach (string kind in new[] { "completed", "attention", "error" })
                using (var card = Card(kind))
                {
                    Check(!card.ShowInTaskbar && card.TopMost && card.FormBorderStyle == FormBorderStyle.None, "Unexpected window presentation");
                    Check((bool)cardType.GetProperty("ShowWithoutActivation", Private).GetValue(card, null), "Window would activate on show");
                    var parameters = (CreateParams)cardType.GetProperty("CreateParams", Private).GetValue(card, null);
                    Check((parameters.ExStyle & 0x08000080) == 0x08000080, "Missing non-activation/toolwindow styles");
                    foreach (uint dpi in new uint[] { 96, 144, 192 })
                    {
                        Arrange(card, new Rectangle(-1280, -160, 1280, 720), dpi);
                        Render(card, Path.Combine(output, kind + "-" + dpi + ".png"));
                    }
                    Arrange(card, new Rectangle(100, 50, 320, 160), 192);
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
            Console.WriteLine("PASS: input validation; 3 card kinds x 3 DPIs; negative/short work areas; non-activation styles; accessible open/close exactly once; 64 dispose cycles.");
            Console.WriteLine("GDI {0}->{1}; USER {2}->{3}. Rendered cards: {4}", gdiBefore, gdiAfter, userBefore, userAfter, output);
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
