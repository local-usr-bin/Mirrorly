using System.Collections.ObjectModel;
using System.ComponentModel;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public enum RestoreSelectionState { LoadingCatalog, Selecting, Preparing, Review, Unavailable }

public sealed class RestoreVersionChoice(SnapshotCollectionItem item, bool latest)
{
    public string SnapshotId { get; } = item.SnapshotId;
    public string Time { get; } = HomePolicy.FormatSavedVersionTime(item.CreatedAt);
    public string Status { get; } = latest ? "Latest saved backup" : "Complete saved backup";
}

// A selection draft and read-only Review. The worker alone owns the prepared Python plan.
public sealed class RestoreSelectionViewModel : INotifyPropertyChanged
{
    private readonly IDesktopSession session;
    private readonly BackupExecutionCoordinator backup;
    private int generation;
    private string? selectedSelector;
    private string? explicitSnapshotId;
    private RestoreConflictPolicy policy = RestoreConflictPolicy.SkipExisting;
    private bool chooseAnother;
    private bool latestAvailable;
    private bool latestChecked;
    private bool revalidating;

    public RestoreSelectionViewModel(IDesktopSession session, BackupExecutionCoordinator backup,
        IFolderBrowserService folders)
    {
        this.session = session;
        this.backup = backup;
        Destination = new("Restore destination", folders);
        Versions = new(session);
        Destination.PropertyChanged += (_, _) => { if (!revalidating) InvalidateReview(); Changed(); };
        Versions.Changed += () => { SyncVersions(); Changed(); };
        backup.Changed += Changed;
        session.Changed += Changed;
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new(null));
    public FolderBrowserViewModel Destination { get; }
    public SnapshotCollectionViewModel Versions { get; }
    public ObservableCollection<RestoreVersionChoice> CompleteVersions { get; } = [];
    public IReadOnlyList<ConfiguredBackup> Backups { get; private set; } = [];
    public RestoreSelectionState State { get; private set; } = RestoreSelectionState.Selecting;
    public string? SelectedSelector => selectedSelector;
    public ConfiguredBackup? SelectedBackup => Backups.FirstOrDefault(b => b.Selector == selectedSelector);
    public bool ChooseAnother => chooseAnother;
    public string? ExplicitSnapshotId => explicitSnapshotId;
    public RestoreConflictPolicy Policy => policy;
    public RestorePreparedPlanPreview? Preview { get; private set; }
    public string Message { get; private set; } = "Choose a Backup to restore.";
    public string TechnicalDetails { get; private set; } = "";
    public bool BackupWorkPending => backup.HasActiveBackup || backup.QueuedSelectors.Count > 0;
    public bool CanReview => State == RestoreSelectionState.Selecting && SelectedBackup is not null &&
        Destination.HasValidSelection && latestChecked && (chooseAnother ? explicitSnapshotId is not null : latestAvailable) &&
        !BackupWorkPending && !session.Busy && !session.ExitPending;
    public bool IsReview => State == RestoreSelectionState.Review && Preview is not null;
    public bool IsCurrentReview(RestorePreparedPlanPreview preview) => IsReview && ReferenceEquals(Preview, preview) &&
        SelectedSelector == preview.Selector && Policy == preview.Policy &&
        string.Equals(Destination.SelectedPath, preview.Destination, StringComparison.OrdinalIgnoreCase) &&
        (chooseAnother ? explicitSnapshotId == preview.SnapshotId : explicitSnapshotId is null);
    public bool HasNoBackups => State == RestoreSelectionState.Selecting && Backups.Count == 0;
    public bool NoSavedVersion => latestChecked && !latestAvailable;
    public bool CanChooseVersion => State == RestoreSelectionState.Selecting && SelectedBackup is not null && latestChecked;

    public async Task EnterAsync()
    {
        generation++;
        selectedSelector = explicitSnapshotId = null;
        Backups = [];
        Preview = null;
        chooseAnother = latestAvailable = latestChecked = false;
        policy = RestoreConflictPolicy.SkipExisting;
        Versions.Select("");
        CompleteVersions.Clear();
        Destination.ClearSelection();
        Message = "Reading configured Backups…";
        TechnicalDetails = "";
        State = RestoreSelectionState.LoadingCatalog;
        Changed();
        var current = generation;
        try
        {
            revalidating = true;
            try { await Destination.InitializeAsync(); }
            finally { revalidating = false; }
            if (current != generation) return;
            BackupCatalog? catalog = null;
            await session.RunAsync(async api => catalog = await api.CatalogAsync());
            if (current != generation) return;
            Backups = catalog!.Tasks;
            State = RestoreSelectionState.Selecting;
            Message = Backups.Count == 0 ? "No configured Backups yet." : "Choose a Backup and a destination.";
        }
        catch (Exception error)
        {
            if (current != generation) return;
            State = RestoreSelectionState.Unavailable;
            Message = "Configured Backups are unavailable. Try again when Mirrorly is idle.";
            TechnicalDetails = error.ToString();
        }
        Changed();
    }

    public void Leave()
    {
        generation++;
        Preview = null;
        Changed();
    }

    public async Task SelectBackupAsync(string? selector)
    {
        if (selector is not null && !Backups.Any(b => b.Selector == selector)) return;
        InvalidateReview();
        selectedSelector = selector;
        explicitSnapshotId = null;
        chooseAnother = false;
        latestAvailable = latestChecked = false;
        CompleteVersions.Clear();
        Versions.Select(selector ?? "");
        TechnicalDetails = "";
        Message = selector is null ? "Choose a Backup to restore." : "Checking saved versions…";
        Changed();
        if (selector is null) return;
        var current = generation;
        try
        {
            SavedBackupSummary? summary = null;
            await session.RunAsync(async api => summary = await api.BackupSummaryAsync(selector));
            if (current != generation) return;
            latestAvailable = summary?.SnapshotId is not null;
            latestChecked = true;
            Message = latestAvailable ? "Choose a complete saved version and destination." :
                "No saved version is available to restore.";
        }
        catch (Exception error)
        {
            if (current != generation) return;
            latestChecked = false;
            Message = "Saved versions are unavailable. Try selecting this Backup again.";
            TechnicalDetails = error.ToString();
        }
        Changed();
    }

    public async Task ChooseAnotherAsync()
    {
        if (!CanChooseVersion || session.Busy) return;
        InvalidateReview();
        chooseAnother = true;
        explicitSnapshotId = null;
        Changed();
        await Versions.LoadFirstAsync();
    }

    public void ChooseLatest()
    {
        InvalidateReview();
        chooseAnother = false;
        explicitSnapshotId = null;
        Changed();
    }

    public bool SelectVersion(string snapshotId)
    {
        if (!CanChooseVersion || !chooseAnother ||
            !Versions.Items.Any(i => i.SnapshotId == snapshotId && i.Status == "complete")) return false;
        InvalidateReview();
        explicitSnapshotId = snapshotId;
        Changed();
        return true;
    }

    public void SetPolicy(RestoreConflictPolicy value)
    {
        if (policy == value) return;
        InvalidateReview();
        policy = value;
        Changed();
    }

    public void Edit()
    {
        InvalidateReview();
        State = RestoreSelectionState.Selecting;
        Message = "Review your choices before preparing again.";
        Changed();
    }

    public async Task ReviewAsync()
    {
        if (!CanReview) return;
        var intendedSelector = selectedSelector;
        var intendedSnapshot = chooseAnother ? explicitSnapshotId : null;
        var intendedDestination = Destination.SelectedPath;
        var intendedPolicy = policy;
        var current = generation;
        State = RestoreSelectionState.Preparing;
        Preview = null;
        Message = "Preparing Restore review…";
        TechnicalDetails = "";
        Changed();
        revalidating = true;
        try { await Destination.RevalidateAsync(); }
        catch (Exception error)
        {
            State = RestoreSelectionState.Selecting;
            Message = "The destination could not be checked. Choose an available folder and try again.";
            TechnicalDetails = error.ToString();
            Changed();
            return;
        }
        finally { revalidating = false; }
        if (current != generation) return;
        if (selectedSelector != intendedSelector ||
            (chooseAnother ? explicitSnapshotId : null) != intendedSnapshot ||
            intendedDestination != Destination.SelectedPath || policy != intendedPolicy)
        {
            State = RestoreSelectionState.Selecting;
            Message = "Choices changed. Review again to prepare the current selection.";
            Changed();
            return;
        }
        var selector = selectedSelector;
        var destination = Destination.SelectedPath;
        var snapshot = chooseAnother ? explicitSnapshotId : null;
        var selectedPolicy = policy;
        if (selector is null || destination is null || BackupWorkPending || session.ExitPending)
        {
            State = RestoreSelectionState.Selecting;
            Message = BackupWorkPending ? "Finish current Backup work before preparing a Restore." :
                "Choose an available destination before reviewing.";
            Changed();
            return;
        }
        try
        {
            RestorePreparedPlanPreview? preview = null;
            await session.RunAsync(async api => preview = await api.PrepareRestoreAsync(new(selector, snapshot, destination, selectedPolicy)));
            if (current != generation) return;
            if (preview is null || preview.Selector != selector ||
                !string.Equals(preview.Destination, destination, StringComparison.OrdinalIgnoreCase) ||
                preview.Policy != selectedPolicy || snapshot is not null && preview.SnapshotId != snapshot)
                throw new InvalidDataException("Restore preview does not match the selected intent.");
            Preview = preview;
            State = RestoreSelectionState.Review;
            Message = "Restore plan prepared. This review does not start Restore or change the destination.";
        }
        catch (Exception error)
        {
            if (current != generation) return;
            Preview = null;
            State = RestoreSelectionState.Selecting;
            Message = FriendlyPrepareError(error);
            TechnicalDetails = error.ToString();
        }
        Changed();
    }

    private void SyncVersions()
    {
        if (Versions.State == SnapshotCollectionState.Loaded && explicitSnapshotId is not null &&
            !Versions.Items.Any(item => item.SnapshotId == explicitSnapshotId))
        {
            InvalidateReview();
            explicitSnapshotId = null;
        }
        var latest = Versions.LatestCompleteSnapshotId;
        var complete = Versions.Items.Where(i => i.Status == "complete").ToArray();
        if (CompleteVersions.Count != complete.Length || CompleteVersions.Where((row, index) =>
                row.SnapshotId != complete[index].SnapshotId || row.Status !=
                (complete[index].SnapshotId == latest ? "Latest saved backup" : "Complete saved backup")).Any())
        {
            CompleteVersions.Clear();
            foreach (var item in complete) CompleteVersions.Add(new(item, item.SnapshotId == latest));
        }
    }

    private void InvalidateReview()
    {
        generation++;
        Preview = null;
        if (State is RestoreSelectionState.Review or RestoreSelectionState.Preparing)
            State = RestoreSelectionState.Selecting;
    }

    private static string FriendlyPrepareError(Exception error) => error switch
    {
        RestorePrepareRejectedException { Code: "no_complete_snapshot" } => "No saved version is available to restore.",
        RestorePrepareRejectedException { Code: "incomplete_snapshot" } => "This version is unfinished and cannot be restored.",
        RestorePrepareRejectedException { Code: "unknown_snapshot" } => "This saved version is no longer available. Choose another one.",
        RestorePrepareRejectedException { Code: "unknown_task" or "task_unreadable" } => "This Backup is no longer available. Refresh the Restore page.",
        RestorePrepareRejectedException { Code: "unsafe_destination" } => "This destination is not safe for Restore. Choose a different folder.",
        RestorePrepareRejectedException { Code: "repository_unavailable" or "repository_invalid" or "manifest_unavailable" } => "The saved version or repository cannot be read. Check its location and try again.",
        RestorePrepareRejectedException { Code: "busy" } => "Mirrorly is handling another operation. Try again when it finishes.",
        WorkerTransportUncertainException => "Mirrorly could not confirm the Restore preparation result. No Restore was started.",
        _ => "Restore review is unavailable. Check technical details and try again."
    };
}
