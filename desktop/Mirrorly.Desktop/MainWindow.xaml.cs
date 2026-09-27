using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
using Mirrorly.Desktop.Views;
using Windows.Graphics;
using Windows.UI.ViewManagement;
namespace Mirrorly.Desktop;

public sealed partial class MainWindow : Window
{
    private readonly ShellViewModel shell = new();
    private readonly DesktopSession session;
    private readonly BackupExecutionCoordinator backup;
    private readonly RestoreExecutionCoordinator restoreExecution;
    private readonly PetalMotionPolicy petalMotion = new();
    private bool flourishHostActive;
    private int flourishGeneration;
    private readonly HomeView home;
    private readonly BackupsView backups;
    private readonly BackupDetailView detail;
    private readonly BackupSetupView setup;
    private readonly RestoreView restore;
    private TrayService? tray;
    private bool exiting;
#if DEBUG
    private DiagnosticsView? diagnostics;
#endif

    public MainWindow(DesktopSession session)
    {
        this.session = session;
        backup = new(session);
        restoreExecution = new(session, backup);
        session.ResumeResponder = backup.ResolveResumeAsync;
        backup.ResumePrompt = ShowResumeAsync;
        home = new(session, backup); backups = new(home.Model, session);
        detail = new(home.Model, session); setup = new(session); restore = new(session, backup, restoreExecution);
        restore.ConfirmStart = ConfirmRestoreStartAsync;
        setup.Model.Created = async api => {
            await home.Model.RefreshCoreAsync(api);
            Navigate(ShellPage.Home);
            if (setup.Model.CreatedConfigPath is { } configPath)
                _ = home.Model.CheckCreatedSourceAsync(configPath);
        };
        InitializeComponent();
        var iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "Mirrorly.ico");
        if (File.Exists(iconPath))
        {
            try { AppWindow.SetIcon(iconPath); }
            catch (Exception error) { Debug.WriteLine($"Window icon unavailable: {error.Message}"); }
        }
        if (SetupDiagnostics.Enabled) setup.ShellDiagnosticSnapshot = () =>
        {
            if (!DispatcherQueue.HasThreadAccess) return "HasThreadAccess=false";
            var selected = Navigation.SelectedItem;
            var tag = (selected as NavigationViewItem)?.Tag as string;
            // Only known code-owned navigation tags; no Content/user text.
            var safeTag = Enum.TryParse<ShellPage>(tag, out var page) ? page.ToString() : "other/null";
            return $"PageHost={PageHost.Content?.GetType().FullName ?? "null"} SelectedType={selected?.GetType().FullName ?? "null"} Tag={safeTag} " +
                $"PaneOpen={Navigation.IsPaneOpen} Enabled={Navigation.IsEnabled} Opacity={Navigation.Opacity} " +
                $"Width={Navigation.ActualWidth} Height={Navigation.ActualHeight} Visibility={Navigation.Visibility} DisplayMode={Navigation.DisplayMode}";
        };
        setup.ConfirmCopy = () => ConfirmAsync("Use full-file copies?",
            "The selected location does not provide NTFS hardlink reuse. Mirrorly will use full-file copies, which may need more space. Continue with this location?",
            "Use full-file copies", "Go back");
        Navigation.RequestedTheme = ElementTheme.Light;
        Navigation.ExpandedModeThresholdWidth = HomePolicy.ExpandedNavigationAt;
        Navigation.CompactModeThresholdWidth = HomePolicy.CompactNavigationAt;
        home.Navigate += Navigate;
        home.ViewBackupRequested += NavigateBackupDetail;
        backups.Navigate += Navigate;
        backups.ViewBackupRequested += NavigateBackupDetail;
        detail.ReturnRequested += selector => {
            Navigate(ShellPage.Backups);
            DispatcherQueue.TryEnqueue(() => backups.FocusViewAction(selector));
        };
        setup.Navigate += Navigate;
        setup.StepChanged += () => DispatcherQueue.TryEnqueue(() => Scroller.ChangeView(null, 0, null, true));
        home.DecorationChanged += _ => UpdateShellLayout();
        backup.BackupAdmitted += admission => {
            if (DispatcherQueue.HasThreadAccess) OnBackupAdmitted(admission);
            else DispatcherQueue.TryEnqueue(() => OnBackupAdmitted(admission));
        };
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
            tray = new TrayService(WinRT.Interop.WindowNative.GetWindowHandle(this), iconPath, Reopen,
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
        var plant = HomePolicy.PlantFor((home.Model.ShowDecoration || flourishHostActive) && shell.IsHome, Navigation.IsPaneOpen,
            Navigation.DisplayMode == NavigationViewDisplayMode.Expanded,
            Navigation.DisplayMode == NavigationViewDisplayMode.Compact,
            Navigation.ActualWidth, Navigation.ActualHeight);
        SidebarDecoration.Visibility = plant == PlantPlacement.Expanded ? Visibility.Visible : Visibility.Collapsed;
        CompactDecoration.Visibility = plant == PlantPlacement.Compact ? Visibility.Visible : Visibility.Collapsed;
#if DEBUG
        if (Navigation.XamlRoot is not null) diagnostics?.SetDisplay(
            $"Actual DPI scale: {Navigation.XamlRoot.RasterizationScale:P0} · content {Navigation.ActualWidth:F0} × {Navigation.ActualHeight:F0} effective pixels");
#endif
    }
    private void OnBackupAdmitted(WorkerAdmission admission)
    {
        // Admission is received before terminal handling. The optimistic local Running flag
        // is deliberately never an animation trigger.
        if (!backup.HasActiveBackup)
        {
            petalMotion.ShouldPlay(admission, false, false, false);
            return; // A delayed UI dispatch must not celebrate an already-terminal call.
        }
        var placement = HomePolicy.PlantFor(shell.IsHome, Navigation.IsPaneOpen,
            Navigation.DisplayMode == NavigationViewDisplayMode.Expanded,
            Navigation.DisplayMode == NavigationViewDisplayMode.Compact,
            Navigation.ActualWidth, Navigation.ActualHeight);
        var visible = AppWindow.IsVisible && placement != PlantPlacement.Hidden;
        if (!petalMotion.ShouldPlay(admission, visible, new UISettings().AnimationsEnabled,
            new AccessibilitySettings().HighContrast)) return;
        // The stationary Running layout keeps the plant hidden. Expose only its
        // responsive decorative host for this short flourish, then restore that rule.
        flourishHostActive = true;
        var generation = ++flourishGeneration;
        UpdateShellLayout();
        var sprig = placement == PlantPlacement.Expanded ? SidebarSprig : CompactSprig;
        try
        {
            sprig.PlayPetalFlourish(() => {
                if (generation != flourishGeneration) return;
                flourishHostActive = false;
                UpdateShellLayout();
            });
        }
        catch (Exception error)
        {
            flourishHostActive = false;
            UpdateShellLayout();
            Debug.WriteLine($"Decorative flourish unavailable: {error}");
        }
    }
    private void Reopen()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter) presenter.Restore();
        Activate();
    }
    private bool exitDialog;
    private readonly SemaphoreSlim dialogs = new(1);
    private ContentDialog? activeExitDialog;
    private bool resumePending, exitInterrupted;
    private TaskCompletionSource resumeDone = CompletedDialog();
    private static TaskCompletionSource CompletedDialog()
    {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        done.SetResult();
        return done;
    }
    private Task<ResumeAnswer> ShowResumeAsync(ResumeInteraction interaction)
    {
        var result = new TaskCompletionSource<ResumeAnswer>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DispatcherQueue.TryEnqueue(() => _ = ShowResumeOnUiAsync(interaction, result)))
            result.SetResult(ResumeAnswer.Unavailable);
        return result.Task;
    }
    private async Task ShowResumeOnUiAsync(ResumeInteraction interaction, TaskCompletionSource<ResumeAnswer> result)
    {
        resumePending = true;
        resumeDone = new(TaskCreationOptions.RunContinuationsAsynchronously);
        if (activeExitDialog is not null) { exitInterrupted = true; activeExitDialog.Hide(); }
        try
        {
            await dialogs.WaitAsync();
            try
            {
                Reopen(); // A required decision must not remain hidden in the tray/minimized window.
                var dialog = new ContentDialog {
                    XamlRoot = Navigation.XamlRoot,
                    Title = "Continue interrupted backup?",
                    Content = "Mirrorly found unfinished Backup work. Resume it, or continue without using that unfinished work. The Backup will not continue until you choose.",
                    PrimaryButtonText = "Resume", SecondaryButtonText = "Don't resume", CloseButtonText = "Not now",
                    DefaultButton = ContentDialogButton.Close
                };
                var answer = await dialog.ShowAsync();
                result.TrySetResult(answer switch {
                    ContentDialogResult.Primary => ResumeAnswer.Resume,
                    ContentDialogResult.Secondary => ResumeAnswer.DeclineResume,
                    _ => ResumeAnswer.Unavailable
                });
            }
            finally { dialogs.Release(); }
        }
        catch (Exception) { result.TrySetResult(ResumeAnswer.Unavailable); }
        finally { resumePending = false; resumeDone.TrySetResult(); }
    }
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
    private Task<bool> ConfirmRestoreStartAsync(RestorePreparedPlanPreview plan)
    {
        var message = "Restore the entire saved version to the selected destination. This version cannot be cancelled once it starts. Keep the Backup repository and destination devices connected, and avoid shutting down or restarting. You may close Mirrorly to the notification area; Restore will continue. Extra destination files are not deleted.";
        if (plan.Policy == RestoreConflictPolicy.ReplaceExisting)
            message += $"\n\n{plan.FileOverwriteCount:N0} existing files are planned for replacement. Files already replaced will not be rolled back if Restore later fails or is interrupted.";
        return ConfirmAsync("Start restore?", message, "Start restore", "Cancel");
    }
    private async Task<bool> ConfirmExitAfterResumeAsync()
    {
        while (true)
        {
            await resumeDone.Task;
            await dialogs.WaitAsync();
            try
            {
                if (resumePending) continue;
                Reopen();
                exitInterrupted = false;
                activeExitDialog = new ContentDialog { XamlRoot = Navigation.XamlRoot,
                    Title = "Mirrorly is still working",
                    Content = restoreExecution.IsActive
                        ? "Exit after the current Restore finishes? Mirrorly will keep supervising Restore and will not cancel it."
                        : "Exit after the current operation finishes? Queued Backups that have not started will be removed. Mirrorly will stay running until it has processed the current result.",
                    PrimaryButtonText = "Exit after it finishes", CloseButtonText = "Stay in Mirrorly",
                    DefaultButton = ContentDialogButton.Close };
                var answer = await activeExitDialog.ShowAsync();
                if (!exitInterrupted) return answer == ContentDialogResult.Primary;
            }
            finally { activeExitDialog = null; dialogs.Release(); }
        }
    }
    private async Task ExitAsync()
    {
        if (exiting || exitDialog || session.ExitPending) return;
        exitDialog = true;
        try
        {
            var done = await session.ExitAsync(ConfirmExitAfterResumeAsync);
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
        if (shell.SelectedPage == ShellPage.Restore && page != ShellPage.Restore) restore.Leave();
        shell.Navigate(page);
        PageHost.Content = page == ShellPage.Home ? home :
            page == ShellPage.Backups ? backups :
            page == ShellPage.BackupDetail ? detail :
            page == ShellPage.BackupSetup ? setup :
            page == ShellPage.Restore ? restore :
#if DEBUG
            page == ShellPage.Diagnostics ? diagnostics :
#endif
            new PlaceholderView(shell.Title);
        if (page == ShellPage.Settings) Navigation.SelectedItem = Navigation.SettingsItem;
        else
        {
            var tag = shell.TopLevelPage;
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
        if (page == ShellPage.Restore) _ = restore.EnterAsync();
    }
    private void NavigateBackupDetail(string selector)
    {
        detail.Select(selector);
        Navigate(ShellPage.BackupDetail);
        DispatcherQueue.TryEnqueue(detail.FocusReturnAction);
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
