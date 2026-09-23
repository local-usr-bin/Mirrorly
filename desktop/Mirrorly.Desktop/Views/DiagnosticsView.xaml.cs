using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
namespace Mirrorly.Desktop.Views;
public sealed partial class DiagnosticsView : UserControl
{
    public event Action<string>? TestRequested;
    public event Action<double, double>? ResizeRequested;
    public DiagnosticsView() { InitializeComponent(); }
    public void SetDisplay(string text) => Display.Text = text;
    public void ShowMessage(string text) => Message.Text = text;
    private void Status_Click(object sender, RoutedEventArgs e) => TestRequested?.Invoke("ping");
    private void Exit_Click(object sender, RoutedEventArgs e) => TestRequested?.Invoke("exit");
    private void Small_Click(object sender, RoutedEventArgs e) => ResizeRequested?.Invoke(560, 480);
    private void Large_Click(object sender, RoutedEventArgs e) => ResizeRequested?.Invoke(1120, 860);
}
