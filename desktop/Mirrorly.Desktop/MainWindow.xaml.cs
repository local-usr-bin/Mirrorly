using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
using Mirrorly.Desktop.Views;
using Windows.Graphics;
namespace Mirrorly.Desktop;

public sealed partial class MainWindow : Window
{
    private readonly ShellViewModel shell = new();
    private readonly DesktopSession session;
    private readonly HomeView home;
    private readonly BackupSetupView setup;
    private TrayService? tray;
    private bool exiting;
#if DEBUG
    private DiagnosticsView? diagnostics;
#endif

    public MainWindow(DesktopSession session)
    {
        this.session = session;
        home = new(session); setup = new(session);
        setup.Model.Created = async api => { await home.Model.RefreshCoreAsync(api); Navigate(ShellPage.Home); };
        InitializeComponent();
        setup.ConfirmCopy = () => ConfirmAsync("Use full-file copies?",
            "The selected location does not provide NTFS hardlink reuse. Mirrorly will use full-file copies, which may need more space. Continue with this location?",
            "Use full-file copies", "Go back");
        Navigation.RequestedTheme = ElementTheme.Light;
        Navigation.ExpandedModeThresholdWidth = HomePolicy.ExpandedNavigationAt;
        Navigation.CompactModeThresholdWidth = HomePolicy.CompactNavigationAt;
        home.Navigate += Navigate;
        setup.Navigate += Navigate;
        setup.StepChanged += () => DispatcherQueue.TryEnqueue(() => Scroller.ChangeView(null, 0, null, true));
        home.DecorationChanged += _ => UpdateShellLayout();
        PageHost.Content = home;
#if DEBUG
        var diagnosticsItem = new NavigationViewItem { Content = "Developer diagnostics", Tag = "Diagnostics", Icon = new FontIcon { Glyph = "\uE943" } };
        Navigation.FooterMenuItems.Add(diagnosticsItem);
        diagnostics = new DiagnosticsView();
        // Historical fixtures remain in tests; normal Home never displays sample tasks.
        diagnostics.TestRequested += async command => {
            try
            {
                if (command == "ping") diagnostics.ShowMessage(session.Observation.ToString());
                else if (command == "exit") await ExitAsync();
                else diagnostics.ShowMessage("Phase 1A test worker is isolated from the normal runtime.");
            }
            catch (Exception error) { diagnostics.ShowMessage(error.Message); }
        };
        diagnostics.ResizeRequested += ResizeForReview;
#endif
        try
        {
            tray = new TrayService(WinRT.Interop.WindowNative.GetWindowHandle(this), Reopen,
                () => DispatcherQueue.TryEnqueue(async () => await ExitAsync()));
        }
        catch (Exception error) { Debug.WriteLine($"Tray unavailable: {error.Message}. Close will use supervised Exit."); }
        AppWindow.Closing += (sender, args) => {
            if (exiting) return;
            args.Cancel = true;
            if (tray is not null) AppWindow.Hide();
            else _ = ExitAsync();
        };
        Navigation.SizeChanged += (_, _) => UpdateShellLayout();
        Navigation.PaneOpened += (_, _) => UpdateShellLayout();
        Navigation.PaneClosed += (_, _) => UpdateShellLayout();
        Navigation.Loaded += async (_, _) => {
            ResizeForReview(1120, 840);
            Navigation.XamlRoot.Changed += (_, _) => UpdateShellLayout();
            UpdateShellLayout();
            await home.Model.RefreshAsync(session);
        };
    }
    private void ResizeForReview(double width, double height)
    {
        // Window geometry, not page dimensions. Respect the monitor's current work area.
        var area = Microsoft.UI.Windowing.DisplayArea.GetFromWindowId(AppWindow.Id,
            Microsoft.UI.Windowing.DisplayAreaFallback.Primary).WorkArea;
        var scale = Navigation.XamlRoot.RasterizationScale;
        var w = Math.Min((int)(width * scale), area.Width);
        var h = Math.Min((int)(height * scale), area.Height);
        AppWindow.MoveAndResize(new RectInt32(area.X + (area.Width - w) / 2, area.Y + (area.Height - h) / 2, w, h));
    }
    private void UpdateShellLayout()
    {
        if (PageHost is null) return;
        PageHost.Margin = (Thickness)Application.Current.Resources[
            Navigation.ActualWidth < HomePolicy.CompactNavigationAt ? "MirrorlyCompactPageMargin" : "MirrorlyPageMargin"];
        Scroller.Margin = (Thickness)Application.Current.Resources[Navigation.DisplayMode == NavigationViewDisplayMode.Minimal
            ? "MirrorlyMinimalNavigationInset" : "MirrorlyNavigationInset"];
        SidebarDecoration.Visibility = Navigation.IsPaneOpen && Navigation.DisplayMode == NavigationViewDisplayMode.Expanded
            && home.Model.ShowDecoration && shell.IsHome && Navigation.ActualHeight > 600 ? Visibility.Visible : Visibility.Collapsed;
#if DEBUG
        if (Navigation.XamlRoot is not null) diagnostics?.SetDisplay(
            $"Actual DPI scale: {Navigation.XamlRoot.RasterizationScale:P0} · content {Navigation.ActualWidth:F0} × {Navigation.ActualHeight:F0} effective pixels");
#endif
    }
    private void Reopen()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter) presenter.Restore();
        Activate();
    }
    private bool exitDialog;
    private readonly SemaphoreSlim dialogs = new(1);
    private async Task<bool> ConfirmAsync(string title, string content, string primary, string close)
    {
        await dialogs.WaitAsync();
        try
        {
            return await new ContentDialog { XamlRoot = Navigation.XamlRoot, Title = title, Content = content,
                PrimaryButtonText = primary, CloseButtonText = close, DefaultButton = ContentDialogButton.Close }.ShowAsync() == ContentDialogResult.Primary;
        }
        finally { dialogs.Release(); }
    }
    private async Task ExitAsync()
    {
        if (exiting || exitDialog || session.ExitPending) return;
        exitDialog = true;
        try
        {
            var done = await session.ExitAsync(async () =>
            {
                Reopen();
                return await ConfirmAsync("Mirrorly is still working",
                    "Exit after the current operation finishes? Mirrorly will stay running until it has processed the result.",
                    "Exit after it finishes", "Stay in Mirrorly");
            });
            if (!done) return;
            exiting = true; tray?.Dispose(); Close(); Application.Current.Exit();
        }
        catch (Exception error)
        {
            Reopen();
            await new ContentDialog { XamlRoot = Navigation.XamlRoot, Title = "Mirrorly has not finished exiting",
                Content = "The service did not confirm shutdown. It has not been force-stopped. " + error.Message, CloseButtonText = "Close" }.ShowAsync();
        }
        finally { exitDialog = false; }
    }
    private void Navigate(ShellPage page)
    {
        shell.Navigate(page);
        PageHost.Content = page == ShellPage.Home ? home :
            page == ShellPage.BackupSetup ? setup :
#if DEBUG
            page == ShellPage.Diagnostics ? diagnostics :
#endif
            new PlaceholderView(shell.Title);
        if (page == ShellPage.Settings) Navigation.SelectedItem = Navigation.SettingsItem;
        else
        {
            var tag = page == ShellPage.BackupSetup ? ShellPage.Backups : page;
            var selected = Navigation.MenuItems.Concat(Navigation.FooterMenuItems)
                .OfType<NavigationViewItem>().FirstOrDefault(item => item.Tag?.ToString() == tag.ToString());
            // Setup belongs under Backups without replacing the active setup view.
            if (!ReferenceEquals(Navigation.SelectedItem, selected)) { suppressSelection = true; Navigation.SelectedItem = selected; suppressSelection = false; }
        }
        PageHost.UpdateLayout();
        Scroller.ChangeView(null, 0, null, true);
        DispatcherQueue.TryEnqueue(() => Scroller.ChangeView(null, 0, null, true));
        if (Navigation.DisplayMode != NavigationViewDisplayMode.Expanded) Navigation.IsPaneOpen = false;
        UpdateShellLayout();
    }
    private bool suppressSelection;
    private void Navigation_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (PageHost is null || suppressSelection) return;
        var page = args.IsSettingsSelected ? ShellPage.Settings :
            Enum.TryParse<ShellPage>((args.SelectedItem as NavigationViewItem)?.Tag?.ToString(), out var parsed) ? parsed : ShellPage.Home;
        if (shell.SelectedPage != page) Navigate(page);
    }
    private void Navigation_DisplayModeChanged(NavigationView sender, NavigationViewDisplayModeChangedEventArgs args) => UpdateShellLayout();
}
