using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Mirrorly.Desktop.Services;

// Small Win32 boundary; no WinForms dependency or background message-loop framework.
public sealed class TrayService : IDisposable
{
    private const uint CallbackMessage = 0x8001;
    private readonly nint window;
    private readonly SubclassProc callback;
    private readonly Action open;
    private readonly Action exit;
    private readonly uint taskbarCreated;
    private NotifyIconData icon;
    private bool disposed;

    public TrayService(nint window, Action open, Action exit)
    {
        this.window = window;
        this.open = open;
        this.exit = exit;
        callback = WindowProc; // Root the delegate for the full native registration lifetime.
        taskbarCreated = RegisterWindowMessage("TaskbarCreated");
        icon = new NotifyIconData
        {
            Size = (uint)Marshal.SizeOf<NotifyIconData>(), Window = window, Id = 1,
            Flags = 1 | 2 | 4, CallbackMessage = CallbackMessage,
            Icon = LoadIcon(0, (nint)32512), Tip = "Mirrorly Technical Prototype",
            Info = "", InfoTitle = ""
        };
        if (!SetWindowSubclass(window, callback, 1, 0)) throw new Win32Exception();
        if (!Shell_NotifyIcon(0, ref icon))
        {
            RemoveWindowSubclass(window, callback, 1);
            throw new Win32Exception("Could not add Mirrorly to the notification area.");
        }
        SetIconVersion();
    }

    private void SetIconVersion()
    {
        icon.Version = 4;
        Shell_NotifyIcon(4, ref icon);
    }

    private nint WindowProc(nint hwnd, uint message, nuint wParam, nint lParam, nuint id, nuint data)
    {
        if (message == taskbarCreated)
        {
            if (Shell_NotifyIcon(0, ref icon)) SetIconVersion();
        }
        if (message == CallbackMessage)
        {
            var notification = (uint)((long)lParam & 0xffff);
            if (notification is 0x400 or 0x401 or 0x203) open(); // Select, keyboard select, double click.
            if (notification == 0x7b) ShowMenu(); // WM_CONTEXTMENU; works with keyboard as well.
            return 0;
        }
        return DefSubclassProc(hwnd, message, wParam, lParam);
    }

    private void ShowMenu()
    {
        var menu = CreatePopupMenu();
        if (menu == 0) return;
        try
        {
            AppendMenu(menu, 0, 1, "Open Mirrorly");
            AppendMenu(menu, 0x800, 0, "");
            AppendMenu(menu, 0, 2, "Exit Mirrorly");
            GetCursorPos(out var position);
            SetForegroundWindow(window);
            var command = TrackPopupMenuEx(menu, 0x100 | 0x2, position.X, position.Y, window, 0);
            PostMessage(window, 0, 0, 0);
            if (command == 1) open();
            if (command == 2) exit();
        }
        finally { DestroyMenu(menu); }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        Shell_NotifyIcon(2, ref icon);
        RemoveWindowSubclass(window, callback, 1);
        // LoadIcon returns a shared system icon; do not DestroyIcon it.
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NotifyIconData
    {
        public uint Size;
        public nint Window;
        public uint Id, Flags, CallbackMessage;
        public nint Icon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string Tip;
        public uint State, StateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string Info;
        public uint Version;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string InfoTitle;
        public uint InfoFlags;
        public Guid Guid;
        public nint BalloonIcon;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    private delegate nint SubclassProc(nint hwnd, uint message, nuint wParam, nint lParam, nuint id, nuint data);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] private static extern bool Shell_NotifyIcon(uint command, ref NotifyIconData data);
    [DllImport("comctl32.dll")] private static extern bool SetWindowSubclass(nint hwnd, SubclassProc callback, nuint id, nuint data);
    [DllImport("comctl32.dll")] private static extern bool RemoveWindowSubclass(nint hwnd, SubclassProc callback, nuint id);
    [DllImport("comctl32.dll")] private static extern nint DefSubclassProc(nint hwnd, uint message, nuint wParam, nint lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern uint RegisterWindowMessage(string text);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern nint LoadIcon(nint instance, nint name);
    [DllImport("user32.dll")] private static extern nint CreatePopupMenu();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool AppendMenu(nint menu, uint flags, nuint id, string text);
    [DllImport("user32.dll")] private static extern bool DestroyMenu(nint menu);
    [DllImport("user32.dll")] private static extern uint TrackPopupMenuEx(nint menu, uint flags, int x, int y, nint hwnd, nint parameters);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(nint hwnd);
    [DllImport("user32.dll")] private static extern bool PostMessage(nint hwnd, uint message, nuint wParam, nint lParam);
}
