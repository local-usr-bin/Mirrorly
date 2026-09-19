using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
using Windows.Graphics;

namespace Mirrorly.Desktop;

public sealed partial class MainWindow : Window
{
    private readonly HomeViewModel model = new();
    private readonly FakeWorkerClient worker = new();
    private readonly NotificationService notifications = new();
    private TrayService? tray;
    private bool exiting;

    public MainWindow()
    {
        InitializeComponent();
        Navigation.DataContext = model;
        Navigation.RequestedTheme = ElementTheme.Light;
        AppWindow.Resize(new SizeInt32(960, 720));
        worker.StatusChanged += status => DispatcherQueue.TryEnqueue(() => model.UpdateStatus(status));
        worker.EventReceived += message => DispatcherQueue.TryEnqueue(() => model.ShowEvent(message.Payload.ToString()));
        worker.TechnicalLog += text => Debug.WriteLine(text);
        try
        {
            tray = new TrayService(WinRT.Interop.WindowNative.GetWindowHandle(this), Reopen,
                () => DispatcherQueue.TryEnqueue(async () => await ExitAsync()));
        }
        catch (Exception error) { model.ShowEvent($"Tray unavailable: {error.Message}. Close will exit."); }
        AppWindow.Closing += (sender, args) =>
        {
            if (exiting) return;
            args.Cancel = true;
            if (tray is not null) AppWindow.Hide();
            else _ = ExitAsync();
        };
        Navigation.Loaded += async (_, _) =>
        {
            Navigation.XamlRoot.Changed += (_, _) => UpdateDisplayStatus();
            UpdateDisplayStatus();
            await worker.StartAsync(PrototypeConfiguration.PythonInterpreter, PrototypeConfiguration.WorkerScript);
        };
    }

    private void UpdateDisplayStatus() => DisplayStatus.Text =
        $"Display scale: {Navigation.XamlRoot.RasterizationScale:P0} · content: {Navigation.ActualWidth:F0} × {Navigation.ActualHeight:F0} effective pixels";

    private void Reopen()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter) presenter.Restore();
        Activate();
    }

    private async Task ExitAsync()
    {
        if (exiting) return;
        exiting = true;
        try { await worker.DisposeAsync(); }
        catch (Exception error) { Debug.WriteLine(error); }
        finally
        {
            tray?.Dispose();
            Close();
            Application.Current.Exit();
        }
    }

    private async Task RunTestAsync(Func<Task> action)
    {
        try { await action(); }
        catch (Exception error) { model.ShowEvent($"Test request ended: {error.Message}"); }
    }

    private async void Ping_Click(object sender, RoutedEventArgs e) => await RunTestAsync(worker.PingAsync);
    private async void TestEvent_Click(object sender, RoutedEventArgs e) => await RunTestAsync(async () => await worker.RequestAsync("test_event"));
    private async void Crash_Click(object sender, RoutedEventArgs e) => await RunTestAsync(async () => await worker.RequestAsync("test_crash"));
    private async void Exit_Click(object sender, RoutedEventArgs e) => await ExitAsync();
    private void Notification_Click(object sender, RoutedEventArgs e)
    {
        try { NotificationStatus.Text = notifications.ShowTest(); }
        catch (Exception error) { NotificationStatus.Text = $"Test notification failed: {error.Message}"; }
    }

    private void Navigation_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        // Selection can fire while InitializeComponent is still constructing content.
        if (PageTitle is null) return;
        var page = args.IsSettingsSelected ? "Settings" : (args.SelectedItem as NavigationViewItem)?.Tag?.ToString() ?? "Home";
        PageTitle.Text = page;
        HomePanel.Visibility = page == "Home" ? Visibility.Visible : Visibility.Collapsed;
        Placeholder.Visibility = page == "Home" ? Visibility.Collapsed : Visibility.Visible;
    }
}
