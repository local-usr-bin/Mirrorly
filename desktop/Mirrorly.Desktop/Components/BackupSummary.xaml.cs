using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
namespace Mirrorly.Desktop.Components;
public sealed partial class BackupSummary : UserControl
{
    public event Action<string>? PreviewAction;
    public BackupSummary(BackupPresentation model, bool compact, bool designPreview = false)
    {
        InitializeComponent();
        DataContext = model;
        foreach (var button in Actions.Children.OfType<Button>()) button.IsEnabled = designPreview;
        CompactView.IsEnabled = designPreview;
        ToolTipService.SetToolTip(Actions, "Browsing backup versions is not available yet.");
        Status.Style = (Style)Application.Current.Resources[$"Mirrorly{model.Tone}Text"];
        CompactStatus.Style = Status.Style;
        DetailedLayout.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        CompactLayout.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        if (compact)
        {
            Card.Padding = (Thickness)Application.Current.Resources["MirrorlyCompactCardPadding"];
            Card.CornerRadius = (CornerRadius)Application.Current.Resources["MirrorlyCornerRadiusMedium"];
        }
        SizeChanged += (_, args) => {
            var narrow = args.NewSize.Width < HomePolicy.StackedStatusBelow;
            Actions.Orientation = args.NewSize.Width < HomePolicy.CompactActivityBelow ? Orientation.Vertical : Orientation.Horizontal;
            Grid.SetColumn(DestinationPanel, narrow ? 0 : 1);
            Grid.SetRow(DestinationPanel, narrow ? 1 : 0);
            DestinationColumn.Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        };
    }
    private void Explorer_Click(object sender, RoutedEventArgs e) => PreviewAction?.Invoke("Open in File Explorer");
    private void View_Click(object sender, RoutedEventArgs e) => PreviewAction?.Invoke("View backup");
}
