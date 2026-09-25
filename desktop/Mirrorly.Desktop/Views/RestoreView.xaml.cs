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
    private readonly RestoreExecutionCoordinator execution;
    private readonly IDesktopSession session;
    public Func<RestorePreparedPlanPreview, Task<bool>>? ConfirmStart { get; set; }
    private bool rendering;

    public RestoreView(IDesktopSession session, BackupExecutionCoordinator backup, RestoreExecutionCoordinator execution)
    {
        this.session = session;
        this.execution = execution;
        Model = new(session, backup, new FolderBrowserService());
        InitializeComponent();
        DestinationHost.Children.Add(new FolderBrowserPane(Model.Destination));
        VersionList.ItemsSource = Model.CompleteVersions;
        Model.PropertyChanged += (_, _) => Render();
        execution.Changed += Render;
        Render();
    }

    public async Task EnterAsync()
    {
        if (execution.State != RestoreGuiState.Idle) { Render(); return; }
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
            var executionVisible = execution.State is RestoreGuiState.Starting or RestoreGuiState.Running;
            var resultVisible = execution.State is RestoreGuiState.Terminal or RestoreGuiState.TransportUncertain;
            StateText.Text = execution.State switch
            {
                RestoreGuiState.Starting => "Starting Restore; waiting for worker admission…",
                RestoreGuiState.Running => "Restore is running. No progress estimate is available.",
                RestoreGuiState.TransportUncertain => "Restore status is uncertain. Do not immediately start the same Restore again.",
                RestoreGuiState.Terminal => "The Restore operation reached a factual application result.",
                _ when execution.TechnicalDetails.Length > 0 => "Restore could not start. Review the plan and try again when the service is available.",
                _ => Model.Message
            };
            SelectionContent.Visibility = executionVisible || resultVisible || Model.IsReview ? Visibility.Collapsed : Visibility.Visible;
            ReviewContent.Visibility = !executionVisible && !resultVisible && Model.IsReview ? Visibility.Visible : Visibility.Collapsed;
            ExecutionContent.Visibility = executionVisible ? Visibility.Visible : Visibility.Collapsed;
            ResultContent.Visibility = resultVisible ? Visibility.Visible : Visibility.Collapsed;
            ExecutionHeading.Text = execution.State == RestoreGuiState.Running ? "Restoring…" : "Starting Restore…";
            RestoreProgress.IsActive = executionVisible;
            ExecutionFacts.Text = execution.Plan is { } runningPlan
                ? $"{execution.BackupName}\nSaved version: {runningPlan.SnapshotId}\nDestination: {runningPlan.Destination}\nExisting files: {(runningPlan.Policy == RestoreConflictPolicy.ReplaceExisting ? "Replace existing" : "Skip existing")}"
                : "";
            RenderResult();
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
            ReviewAdmissionText.Text = Model.BackupWorkPending
                ? "Finish the current Backup work before restoring. Restore will not join the Backup queue."
                : execution.TechnicalDetails.Length > 0 ? "Restore could not start. View technical details before trying again." : "";
            ReviewAdmissionText.Visibility = ReviewAdmissionText.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            StartAction.IsEnabled = Model.Preview is { } current && Model.IsCurrentReview(current) && !execution.RequiresNewPlan &&
                execution.State == RestoreGuiState.Idle && !Model.BackupWorkPending && !session.Busy && !session.ExitPending;
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
            TechnicalText.Text = execution.TechnicalDetails.Length > 0 ? execution.TechnicalDetails :
                Model.TechnicalDetails.Length > 0 ? Model.TechnicalDetails : Model.Versions.TechnicalDetails;
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

    private void RenderResult()
    {
        if (execution.State == RestoreGuiState.TransportUncertain)
        {
            ResultHeading.Text = "Restore status uncertain";
            ResultSummary.Text = "Mirrorly did not receive a usable application result. The destination may have changed; no automatic retry was started.";
            ResultNextStep.Text = "Do not immediately repeat this Restore. Check the destination and saved version before deciding what to do next.";
        }
        else if (execution.Result is { } result)
        {
            ResultHeading.Text = result.Outcome switch
            {
                RestoreExecutionOutcome.Completed => "Restore complete",
                RestoreExecutionOutcome.CompletedWithIssues => "Restore completed with issues",
                _ => "Restore stopped"
            };
            ResultSummary.Text = result.Facts is { } facts
                ? string.Format(CultureInfo.CurrentCulture,
                    "{0:N0} files restored · {1:N0} directories created · {2:N0} items skipped\n{3:N0} conflicts · {4:N0} errors · {5:N0} leftover temporary files · {6:N0} logical bytes written",
                    facts.FilesRestored, facts.DirectoriesCreated, facts.ItemsSkipped, facts.Conflicts,
                    facts.Errors, facts.LeftoverTemporaryFiles, facts.BytesWritten)
                : "Restore stopped before it finished. The destination may have been partially modified.";
            ResultNextStep.Text = result.Outcome switch
            {
                RestoreExecutionOutcome.ApplicationFailed when execution.Plan?.Policy == RestoreConflictPolicy.ReplaceExisting =>
                    "Files already replaced are not automatically returned to their previous versions. Review the destination before trying again.",
                RestoreExecutionOutcome.ApplicationFailed => "Review the destination before trying again. Prepare a new plan for another attempt.",
                RestoreExecutionOutcome.CompletedWithIssues => "Review conflicts, errors and leftover temporary files before relying on the destination.",
                RestoreExecutionOutcome.Completed when result.Facts?.ItemsSkipped > 0 =>
                    "Existing items skipped under your selected policy were not changed. Prepare a new plan to restore again.",
                _ => "Prepare a new plan to restore again."
            };
        }
        NewRestoreAction.Visibility = execution.State == RestoreGuiState.Terminal ? Visibility.Visible : Visibility.Collapsed;
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
    private async void Start_Click(object sender, RoutedEventArgs e)
    {
        if (Model.Preview is not { } preview || !Model.IsCurrentReview(preview) || ConfirmStart is null) return;
        StartAction.IsEnabled = false;
        var confirmed = await ConfirmStart(preview);
        if (!confirmed || !Model.IsCurrentReview(preview)) { Render(); return; }
        if (!execution.Start(preview, Model.SelectedBackup?.Name ?? "Backup")) Render();
    }
    private async void NewRestore_Click(object sender, RoutedEventArgs e)
    {
        execution.ClearTerminal();
        await EnterAsync();
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
