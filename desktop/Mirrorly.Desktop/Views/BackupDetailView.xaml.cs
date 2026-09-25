using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

namespace Mirrorly.Desktop.Views;

// A selector route over the existing catalog and GUI coordinator, not a second state owner.
public sealed partial class BackupDetailView : UserControl
{
    private readonly HomeViewModel model;
    private readonly IDesktopSession session;
    private readonly SnapshotCollectionViewModel snapshots;
    private string? selector;
    private bool snapshotsSelected;
    public ObservableCollection<SnapshotRowPresentation> SnapshotRows { get; } = [];
    public event Action<string>? ReturnRequested;

    public BackupDetailView(HomeViewModel model, IDesktopSession session)
    {
        this.model = model;
        this.session = session;
        snapshots = new(session);
        InitializeComponent();
        model.PropertyChanged += (_, _) => Render();
        snapshots.Changed += RenderSnapshots;
    }

    public void Select(string taskSelector)
    {
        selector = taskSelector;
        snapshotsSelected = false;
        snapshots.Select(taskSelector);
        model.SelectBackup(taskSelector);
        Render();
    }

    public void FocusReturnAction() => BackAction.Focus(FocusState.Programmatic);

    private void Render()
    {
        if (selector is null) return;
        var overview = model.Overview(selector);
        BackupTitle.Text = overview.Name;
        var unavailable = overview.Availability is BackupOverviewAvailability.Missing or
            BackupOverviewAvailability.CatalogUnavailable or BackupOverviewAvailability.Loading;
        UnavailableText.Visibility = unavailable ? Visibility.Visible : Visibility.Collapsed;
        UnavailableText.Text = overview.Attention;
        OverviewContent.Visibility = unavailable || snapshotsSelected ? Visibility.Collapsed : Visibility.Visible;
        SnapshotsContent.Visibility = unavailable || !snapshotsSelected ? Visibility.Collapsed : Visibility.Visible;
        OverviewSection.BorderThickness = snapshotsSelected ? new(0) : new(0, 0, 0, 2);
        SnapshotsSection.BorderThickness = snapshotsSelected ? new(0, 0, 0, 2) : new(0);
        OverviewSection.FontWeight = snapshotsSelected ? Microsoft.UI.Text.FontWeights.Normal : Microsoft.UI.Text.FontWeights.SemiBold;
        SnapshotsSection.FontWeight = snapshotsSelected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal;
        AutomationProperties.SetItemStatus(OverviewSection, snapshotsSelected ? "" : "Selected");
        AutomationProperties.SetItemStatus(SnapshotsSection, snapshotsSelected ? "Selected" : "");
        SourcePath.Text = overview.Source;
        BackupLocationPath.Text = overview.BackupLocation;
        RepositoryPath.Text = overview.RepositoryPath ?? "Current repository location unavailable";
        SavedVersion.Text = overview.SavedVersion;
        CurrentState.Text = overview.Status;
        CurrentState.Style = (Style)Application.Current.Resources[$"Mirrorly{overview.Tone}Text"];
        AttentionText.Text = overview.Attention;
        AttentionText.Visibility = overview.Attention.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        BackupAction.Content = overview.RunState switch
        {
            BackupTaskRunState.Running => "Backing up…",
            BackupTaskRunState.Queued => "Queued",
            _ => "Back up now"
        };
        BackupAction.IsEnabled = overview.CanBackUp;
        RemoveAction.Visibility = overview.RunState == BackupTaskRunState.Queued && !unavailable
            ? Visibility.Visible : Visibility.Collapsed;
        RefreshAction.IsEnabled = !session.Busy && !session.ExitPending;
        TechnicalText.Text = overview.TechnicalDetails;
        TechnicalPanel.Visibility = !snapshotsSelected && overview.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        RenderSnapshots();
    }

    private void RenderSnapshots()
    {
        var rows = snapshots.Rows;
        if (SnapshotRows.Count != rows.Count || SnapshotRows.Where((row, index) =>
                row.SnapshotId != rows[index].SnapshotId || row.Time != rows[index].Time ||
                row.Status != rows[index].Status || row.Facts != rows[index].Facts).Any())
        {
            SnapshotRows.Clear();
            foreach (var row in rows) SnapshotRows.Add(row);
        }
        SnapshotStateText.Text = snapshots.State switch
        {
            SnapshotCollectionState.LoadingFirst => "Loading snapshots…",
            SnapshotCollectionState.Refreshing => "Refreshing snapshots…",
            SnapshotCollectionState.Unavailable => "Snapshots unavailable. Refresh to try reading them again.",
            SnapshotCollectionState.Loaded when snapshots.IsEmpty => "No saved versions yet",
            SnapshotCollectionState.Loaded when snapshots.OnlyIncomplete => "No complete saved versions yet. Unfinished work is listed below.",
            _ => ""
        };
        SnapshotStateText.Visibility = SnapshotStateText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        SnapshotAttentionText.Text = snapshots.State == SnapshotCollectionState.ContinuationError
            ? "Couldn't load more snapshots. The snapshots already shown remain available. Retry or Refresh to read from the beginning."
            : "";
        SnapshotAttentionText.Visibility = SnapshotAttentionText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        SnapshotRefreshAction.IsEnabled = !snapshots.IsLoading && !session.Busy && !session.ExitPending;
        LoadMoreAction.Visibility = snapshots.HasMore && snapshots.State is not SnapshotCollectionState.Unavailable
            ? Visibility.Visible : Visibility.Collapsed;
        LoadMoreAction.IsEnabled = !snapshots.IsLoading && !session.Busy && !session.ExitPending;
        LoadMoreAction.Content = snapshots.State == SnapshotCollectionState.ContinuationError ? "Retry load more" : "Load more";
        SnapshotTechnicalText.Text = snapshots.TechnicalDetails;
        SnapshotTechnicalPanel.Visibility = snapshots.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OverviewSection_Click(object sender, RoutedEventArgs e)
    {
        snapshotsSelected = false;
        Render();
    }
    private async void SnapshotsSection_Click(object sender, RoutedEventArgs e)
    {
        snapshotsSelected = true;
        Render();
        if (selector is not null && model.Overview(selector).Availability is not
            (BackupOverviewAvailability.Missing or BackupOverviewAvailability.CatalogUnavailable or BackupOverviewAvailability.Loading))
            await snapshots.LoadFirstAsync();
    }
    private async void SnapshotRefresh_Click(object sender, RoutedEventArgs e) => await snapshots.RefreshAsync();
    private async void LoadMore_Click(object sender, RoutedEventArgs e)
    {
        await snapshots.LoadMoreAsync();
        if (!snapshots.HasMore && snapshots.State == SnapshotCollectionState.Loaded)
            SnapshotRefreshAction.Focus(FocusState.Programmatic);
    }

    private void Back_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) ReturnRequested?.Invoke(selector);
    }
    private async void Backup_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) await model.BackUpNowAsync(selector);
    }
    private void Remove_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) model.RemoveFromQueue(selector);
    }
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await model.RefreshAsync(session);
}
