using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

namespace Mirrorly.Desktop.Views;

public sealed partial class RestoreView : UserControl
{
    public RestoreSelectionViewModel Model { get; }
    private bool rendering;

    public RestoreView(IDesktopSession session, BackupExecutionCoordinator backup)
    {
        Model = new(session, backup, new FolderBrowserService());
        InitializeComponent();
        DestinationHost.Children.Add(new FolderBrowserPane(Model.Destination));
        VersionList.ItemsSource = Model.CompleteVersions;
        Model.PropertyChanged += (_, _) => Render();
        Render();
    }

    public async Task EnterAsync()
    {
        await Model.EnterAsync();
        if (Model.Backups.Count > 0) BackupChoice.Focus(FocusState.Programmatic);
    }

    public void Leave() => Model.Leave();

    private void Render()
    {
        if (StateText is null) return;
        rendering = true;
        try
        {
            if (!ReferenceEquals(BackupChoice.ItemsSource, Model.Backups)) BackupChoice.ItemsSource = Model.Backups;
            BackupChoice.SelectedItem = Model.SelectedBackup;
            StateText.Text = Model.Message;
            SelectionContent.Visibility = Model.IsReview ? Visibility.Collapsed : Visibility.Visible;
            ReviewContent.Visibility = Model.IsReview ? Visibility.Visible : Visibility.Collapsed;
            var selecting = Model.State == RestoreSelectionState.Selecting;
            BackupChoice.IsEnabled = selecting;
            LatestChoice.IsEnabled = AnotherChoice.IsEnabled = Model.CanChooseVersion;
            RetrySavedVersionAction.Visibility = selecting && Model.SelectedBackup is not null &&
                !Model.NoSavedVersion && Model.Message.StartsWith("Saved versions are unavailable", StringComparison.Ordinal)
                ? Visibility.Visible : Visibility.Collapsed;
            LatestChoice.IsChecked = !Model.ChooseAnother;
            AnotherChoice.IsChecked = Model.ChooseAnother;
            VersionPicker.Visibility = Model.ChooseAnother ? Visibility.Visible : Visibility.Collapsed;
            EmptyBackups.Visibility = Model.HasNoBackups ? Visibility.Visible : Visibility.Collapsed;
            RetryCatalogAction.Visibility = Model.State == RestoreSelectionState.Unavailable ? Visibility.Visible : Visibility.Collapsed;
            BackupWorkText.Visibility = Model.BackupWorkPending ? Visibility.Visible : Visibility.Collapsed;
            ReviewAction.IsEnabled = Model.CanReview;
            SkipChoice.IsChecked = Model.Policy == RestoreConflictPolicy.SkipExisting;
            ReplaceChoice.IsChecked = Model.Policy == RestoreConflictPolicy.ReplaceExisting;
            SkipChoice.IsEnabled = ReplaceChoice.IsEnabled = selecting;
            VersionState.Text = Model.Versions.State switch
            {
                SnapshotCollectionState.LoadingFirst => "Reading saved versions…",
                SnapshotCollectionState.LoadingMore => "Loading more saved versions…",
                SnapshotCollectionState.Unavailable => "Saved versions are unavailable. Refresh to try again.",
                SnapshotCollectionState.ContinuationError => "Couldn't load more. Existing choices remain; retry or refresh.",
                SnapshotCollectionState.Loaded when Model.CompleteVersions.Count == 0 => "No complete saved versions on loaded pages.",
                _ => ""
            };
            VersionList.IsEnabled = selecting && !Model.Versions.IsLoading;
            var selected = Model.CompleteVersions.FirstOrDefault(v => v.SnapshotId == Model.ExplicitSnapshotId);
            if (!ReferenceEquals(VersionList.SelectedItem, selected)) VersionList.SelectedItem = selected;
            LoadMoreAction.Visibility = Model.Versions.HasMore ? Visibility.Visible : Visibility.Collapsed;
            LoadMoreAction.IsEnabled = selecting && !Model.Versions.IsLoading;
            RefreshVersionsAction.IsEnabled = selecting && !Model.Versions.IsLoading;
            TechnicalText.Text = Model.TechnicalDetails.Length > 0 ? Model.TechnicalDetails : Model.Versions.TechnicalDetails;
            TechnicalPanel.Visibility = TechnicalText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            if (Model.Preview is { } preview)
            {
                ReviewBackup.Text = Model.SelectedBackup?.Name ?? "Backup unavailable";
                ReviewSnapshot.Text = preview.SnapshotId;
                ReviewDestination.Text = preview.Destination;
                ReviewPolicy.Text = preview.Policy == RestoreConflictPolicy.SkipExisting ? "Existing files: Skip existing" : "Existing files: Replace existing";
                ReviewCounts.Text = string.Format(CultureInfo.CurrentCulture,
                    "Planned files: {0:N0} create · {1:N0} overwrite · {2:N0} skip · {3:N0} conflict\nPlanned directory entries: {4:N0}",
                    preview.FileCreateCount, preview.FileOverwriteCount, preview.FileSkipCount,
                    preview.FileConflictCount, preview.DirectoryEntryCount);
                ReviewPolicyExplanation.Text = preview.Policy == RestoreConflictPolicy.SkipExisting
                    ? "Same-name existing files are kept. Skipped files are not restored."
                    : "Matching ordinary files may be overwritten. Type conflicts remain conflicts.";
                ReplaceWarning.Visibility = preview.Policy == RestoreConflictPolicy.ReplaceExisting ? Visibility.Visible : Visibility.Collapsed;
            }
        }
        finally { rendering = false; }
    }

    private async void BackupChoice_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (rendering) return;
        await Model.SelectBackupAsync((BackupChoice.SelectedItem as ConfiguredBackup)?.Selector);
    }
    private void LatestChoice_Checked(object sender, RoutedEventArgs e)
    {
        if (!rendering && Model is not null) Model.ChooseLatest();
    }
    private async void AnotherChoice_Checked(object sender, RoutedEventArgs e)
    {
        if (!rendering && Model is not null) await Model.ChooseAnotherAsync();
    }
    private void VersionList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!rendering && VersionList.SelectedItem is RestoreVersionChoice choice) Model.SelectVersion(choice.SnapshotId);
    }
    private async void LoadMore_Click(object sender, RoutedEventArgs e)
    {
        await Model.Versions.LoadMoreAsync();
        if (!Model.Versions.HasMore) RefreshVersionsAction.Focus(FocusState.Programmatic);
    }
    private async void RefreshVersions_Click(object sender, RoutedEventArgs e) => await Model.Versions.RefreshAsync();
    private async void RetryCatalog_Click(object sender, RoutedEventArgs e) => await EnterAsync();
    private async void RetrySavedVersion_Click(object sender, RoutedEventArgs e)
    {
        if (Model.SelectedSelector is { } selector) await Model.SelectBackupAsync(selector);
    }
    private async void Review_Click(object sender, RoutedEventArgs e)
    {
        await Model.ReviewAsync();
        if (Model.IsReview) EditAction.Focus(FocusState.Programmatic);
    }
    private void Edit_Click(object sender, RoutedEventArgs e)
    {
        Model.Edit();
        ReviewAction.Focus(FocusState.Programmatic);
    }
    private void SkipChoice_Checked(object sender, RoutedEventArgs e)
    {
        if (!rendering && Model is not null) Model.SetPolicy(RestoreConflictPolicy.SkipExisting);
    }
    private void ReplaceChoice_Checked(object sender, RoutedEventArgs e)
    {
        if (!rendering && Model is not null) Model.SetPolicy(RestoreConflictPolicy.ReplaceExisting);
    }
}
