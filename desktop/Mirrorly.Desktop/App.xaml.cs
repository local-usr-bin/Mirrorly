using Microsoft.UI.Xaml;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop;

public partial class App : Application
{
    private MainWindow? window;
    private readonly DesktopSession session = new(DesktopDevelopment.Launch, DesktopDevelopment.Paths);
    public App() => InitializeComponent();

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new MainWindow(session);
        window.Activate();
    }
}
