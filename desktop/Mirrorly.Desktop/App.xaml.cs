using Microsoft.UI.Xaml;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop;

public partial class App : Application
{
    private MainWindow? window;
#if PACKAGING_POC
    // P1 deliberately has no worker, catalog fixtures, or development-path fallback.
    private readonly DesktopSession session = new(null, GuiDataPaths.ForCurrentUser());
#elif PACKAGING_WORKER_POC
    private readonly DesktopSession session = new(new WorkerPayloadLaunch(ProductionPayloadPaths.Current()), GuiDataPaths.ForCurrentUser());
#else
    private readonly DesktopSession session = new(DesktopDevelopment.Launch, DesktopDevelopment.Paths);
#endif
    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new MainWindow(session);
#if PACKAGING_POC
        window.Title = "Mirrorly — GUI deployment PoC (worker disabled)";
#endif
        window.Activate();
    }
}
