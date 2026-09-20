using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.ViewModels;
namespace Mirrorly.Desktop.Views;
public sealed partial class DiagnosticsView : UserControl
{
    public event Action<HomeScenario>? FixtureSelected;
    public event Action<string>? TestRequested;
    public event Action<double, double>? ResizeRequested;
    public DiagnosticsView(WorkerDiagnosticsViewModel model)
    {
        InitializeComponent();
        DataContext = model;
        FixtureSelector.ItemsSource = Enum.GetValues<HomeScenario>();
    }
    private void Fixture_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (FixtureSelector.SelectedItem is HomeScenario scenario) FixtureSelected?.Invoke(scenario);
    }
    private void Test_Click(object sender, RoutedEventArgs e) => TestRequested?.Invoke((string)((Button)sender).Tag);
    private void Small_Click(object sender, RoutedEventArgs e) => ResizeRequested?.Invoke(560, 480);
    private void Large_Click(object sender, RoutedEventArgs e) => ResizeRequested?.Invoke(1120, 860);
    public void ShowMessage(string message) => Message.Text = message;
    public void SetDisplay(string text) => Display.Text = text;
}
