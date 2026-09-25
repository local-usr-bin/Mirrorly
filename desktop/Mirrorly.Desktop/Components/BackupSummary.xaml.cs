using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
namespace Mirrorly.Desktop.Components;
public sealed partial class BackupSummary : UserControl
{
    public event Action<string>? PreviewAction;
    public event Action<string>? BackupRequested;
    public event Action<string>? RemoveQueuedRequested;
    public event Action<string>? OpenRequested;
    private readonly BackupPresentation model;
    public BackupSummary(BackupPresentation model, bool compact, bool designPreview = false,
        bool canBackUp = false, BackupTaskRunState runState = BackupTaskRunState.Idle)
    {
        this.model = model;
        InitializeComponent();
        DataContext = model;
        BackupNow.IsEnabled = CompactBackupNow.IsEnabled = canBackUp && !designPreview;
        var action = runState switch { BackupTaskRunState.Running => "Backing up…", BackupTaskRunState.Queued => "Queued", _ => "Back up now" };
        BackupNowLabel.Text = action;
        CompactBackupNow.Content = action;
        RemoveQueued.Visibility = CompactRemoveQueued.Visibility = runState == BackupTaskRunState.Queued && !designPreview
            ? Visibility.Visible : Visibility.Collapsed;
        Explorer.IsEnabled = CompactExplorer.IsEnabled = model.SavedSnapshotPath is not null && !designPreview;
        foreach (var button in Actions.Children.OfType<Button>())
            if (button != BackupNow && button != Explorer) button.IsEnabled = designPreview;
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
    private void Explorer_Click(object sender, RoutedEventArgs e)
    {
        if (model.SavedSnapshotPath is not null) OpenRequested?.Invoke(model.SavedSnapshotPath);
    }
    private void Backup_Click(object sender, RoutedEventArgs e) => BackupRequested?.Invoke(model.Id);
    private void RemoveQueued_Click(object sender, RoutedEventArgs e) => RemoveQueuedRequested?.Invoke(model.Id);
    private void View_Click(object sender, RoutedEventArgs e) => PreviewAction?.Invoke("View backup");
}
