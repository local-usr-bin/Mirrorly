using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
namespace Mirrorly.Desktop.Components;
public enum BackupCardFocus { Card, Backup, RemoveQueued, Explorer, View }
public sealed partial class BackupSummary : UserControl
{
    public event Action<string>? PreviewAction;
    public event Action<string>? BackupRequested;
    public event Action<string>? RemoveQueuedRequested;
    public event Action<string>? OpenRequested;
    public event Action<string>? ViewRequested;
    private readonly BackupPresentation model;
    private readonly bool designPreview;
    public string Selector => model.Id;
    public BackupCardFocus? FocusedAction =>
        FocusState != FocusState.Unfocused ? BackupCardFocus.Card :
        BackupNow.FocusState != FocusState.Unfocused || CompactBackupNow.FocusState != FocusState.Unfocused ? BackupCardFocus.Backup :
        RemoveQueued.FocusState != FocusState.Unfocused || CompactRemoveQueued.FocusState != FocusState.Unfocused ? BackupCardFocus.RemoveQueued :
        Explorer.FocusState != FocusState.Unfocused || CompactExplorer.FocusState != FocusState.Unfocused ? BackupCardFocus.Explorer :
        ViewBackup.FocusState != FocusState.Unfocused || CompactView.FocusState != FocusState.Unfocused ? BackupCardFocus.View : null;
    public void RestoreFocus(BackupCardFocus action)
    {
        var backup = CompactLayout.Visibility == Visibility.Visible ? CompactBackupNow : BackupNow;
        var remove = CompactLayout.Visibility == Visibility.Visible ? CompactRemoveQueued : RemoveQueued;
        var explorer = CompactLayout.Visibility == Visibility.Visible ? CompactExplorer : Explorer;
        ButtonBase view = CompactLayout.Visibility == Visibility.Visible ? CompactView : ViewBackup;
        var preferred = action switch
        {
            BackupCardFocus.Backup => backup,
            BackupCardFocus.RemoveQueued => remove,
            BackupCardFocus.Explorer => explorer,
            BackupCardFocus.View => view,
            _ => null
        };
        if (preferred is { IsEnabled: true, Visibility: Visibility.Visible } && preferred.Focus(FocusState.Programmatic)) return;
        if (remove is { IsEnabled: true, Visibility: Visibility.Visible } && remove.Focus(FocusState.Programmatic)) return;
        // The initiating action may now be disabled (Running). Keep focus on its task,
        // rather than allowing card reconstruction to move it to a distant control.
        IsTabStop = true;
        AutomationProperties.SetName(this, $"{model.Name}: {model.Status}");
        Focus(FocusState.Programmatic);
    }
    public BackupSummary(BackupPresentation model, bool compact, bool designPreview = false,
        bool canBackUp = false, BackupTaskRunState runState = BackupTaskRunState.Idle)
    {
        this.model = model;
        this.designPreview = designPreview;
        InitializeComponent();
        DataContext = model;
        BackupNow.IsEnabled = CompactBackupNow.IsEnabled = canBackUp && !designPreview;
        var action = runState switch { BackupTaskRunState.Running => "Backing up…", BackupTaskRunState.Queued => "Queued", _ => "Back up now" };
        BackupNowLabel.Text = action;
        CompactBackupNow.Content = action;
        RemoveQueued.Visibility = CompactRemoveQueued.Visibility = runState == BackupTaskRunState.Queued && !designPreview
            ? Visibility.Visible : Visibility.Collapsed;
        Explorer.IsEnabled = CompactExplorer.IsEnabled = model.SavedSnapshotPath is not null && !designPreview;
        ViewBackup.IsEnabled = CompactView.IsEnabled = true;
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
    private void View_Click(object sender, RoutedEventArgs e)
    {
        if (designPreview) PreviewAction?.Invoke("View backup");
        else ViewRequested?.Invoke(model.Id);
    }
}
