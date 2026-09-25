using System.ComponentModel;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public sealed class HomeViewModel : INotifyPropertyChanged
{
    private readonly BackupExecutionCoordinator? execution;
    private readonly Dictionary<string, ConfiguredBackup> configured = new(StringComparer.Ordinal);
    private readonly Dictionary<string, SavedBackupSummary> saved = new();
    private readonly Dictionary<string, string> summaryProblems = new(StringComparer.Ordinal);
    private string catalogDetails = "";
    private string catalogProblem = "";
    private string presentationDetails = "";
    private string? selectedSelector;
    public HomeViewModel(BackupExecutionCoordinator? execution = null)
    {
        this.execution = execution;
        if (execution is not null) execution.Changed += UpdateStatus;
    }
    public HomeScenario Scenario { get; private set; } = HomeScenario.Empty;
    public bool DesignPreview { get; private set; }
    public HomeFixture Fixture { get; private set; } = new(new("Loading backups…", "Reading your configured backups.", "", "Back up now", StatusTone.Neutral, "○"), [], []);
    public StatusPresentation Status => Fixture.Status;
    public IReadOnlyList<BackupPresentation> AllBackups => Fixture.Backups.Select(WithExecutionState).ToArray();
    public IReadOnlyList<BackupPresentation> Backups => HomePolicy.Preview(Fixture.Backups.Select(WithExecutionState).ToArray());
    public IReadOnlyList<ActivityPresentation> Activity => Fixture.Activity.Take(HomePolicy.RecentActivityLimit).ToArray();
    public bool IsEmpty => Fixture.Backups.Count == 0;
    public bool Loaded { get; private set; }
    public bool ShowEmpty => Loaded && IsEmpty && Problem.Length == 0;
    public bool CompactBackups => Fixture.Backups.Count > 1;
    public bool ShowAllBackups => Fixture.Backups.Count > 3;
    public bool ShowDecoration => HomePolicy.DecorationsEnabled && (IsEmpty || Status.Tone == StatusTone.Success || !DesignPreview && Status.Tone == StatusTone.Neutral);
    public string BackupHeading => ShowAllBackups ? "Backups at a glance" : CompactBackups ? "Your backups" : "Your backup";
    public string Problem { get; private set; } = "";
    public string TechnicalDetails { get; private set; } = "";
    public string PrototypeMessage { get; private set; } = "";
    public bool CanBackUp => selectedSelector is not null && CanBackUpTask(selectedSelector);
    public bool CanBackUpTask(string selector) => !DesignPreview && Loaded &&
        Fixture.Backups.Any(b => b.Id == selector) && execution?.CanSchedule(selector) == true;
    public BackupTaskRunState RunState(string selector) => execution?.TaskState(selector) ?? BackupTaskRunState.Idle;
    public bool IsBackingUp => selectedSelector is not null && RunState(selectedSelector) == BackupTaskRunState.Running;
    public string? SelectedSelector => selectedSelector;
    public string BackupActionHelp => execution is { QueueAttention: not BackupQueueAttention.None }
        ? "The automatic queue is paused. View technical details before starting more work."
        : selectedSelector is not null && RunState(selectedSelector) == BackupTaskRunState.Queued
        ? "This Backup is waiting. Use Remove from queue on its card if it should not run."
        : IsBackingUp ? "This Backup is running. No cancellation or progress percentage is available."
        : execution?.HasActiveBackup == true ? "Add this Backup to the in-memory FIFO queue. It starts after the current Backup finishes."
        : execution?.ResultFor(selectedSelector ?? "")?.CommitState == "unknown" ? "Mirrorly cannot confirm whether this Backup's last version was saved."
        : "Start a real Backup. No progress percentage or cancellation is available.";
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new(null));
    public async Task RefreshAsync(IDesktopSession session)
    {
        if (session.Busy || session.ExitPending) return;
        try { await session.RunAsync(RefreshCoreAsync); }
        catch (Exception error) { Unavailable(error); }
    }
    public async Task RefreshCoreAsync(ISetupApi api)
    {
        try
        {
            var catalog = await api.CatalogAsync();
            var found = new Dictionary<string, SavedBackupSummary>();
            var summaryErrors = new Dictionary<string, string>(StringComparer.Ordinal);
            var problems = new List<string>(catalog.Problems);
            foreach (var task in catalog.Tasks)
            {
                try { found[task.Selector] = await api.BackupSummaryAsync(task.Selector); }
                catch (Exception error)
                {
                    summaryErrors[task.Selector] = error.ToString();
                    problems.Add($"Saved Backup summary for {task.Selector}: {error}");
                }
            }
            ApplyCatalog(new(catalog.Tasks, problems), found, summaryErrors);
        }
        catch (Exception error) { Unavailable(error); }
    }
    private void Unavailable(Exception error)
    {
        Loaded = false;
        catalogProblem = "Mirrorly couldn't refresh your configured backups. View technical details, then use Refresh to try reading again.";
        TechnicalDetails = error.ToString(); UpdateStatus();
    }
    public void ApplyCatalog(BackupCatalog catalog, IReadOnlyDictionary<string, SavedBackupSummary>? summaries = null,
        IReadOnlyDictionary<string, string>? errors = null)
    {
        DesignPreview = false; Loaded = true;
        configured.Clear();
        foreach (var task in catalog.Tasks) configured[task.Selector] = task;
        saved.Clear();
        if (summaries is not null) foreach (var pair in summaries) saved[pair.Key] = pair.Value;
        summaryProblems.Clear();
        if (errors is not null) foreach (var pair in errors) summaryProblems[pair.Key] = pair.Value;
        if (selectedSelector is null || !catalog.Tasks.Any(t => t.Selector == selectedSelector))
            selectedSelector = catalog.Tasks.FirstOrDefault()?.Selector;
        catalogProblem = catalog.Problems.Count == 0 ? "" : "Some Backup configurations or saved versions couldn't be read. They have not been repaired. View technical details.";
        catalogDetails = string.Join("\n", catalog.Problems);
        var cards = catalog.Tasks.Select((t, index) =>
        {
            saved.TryGetValue(t.Selector, out var summary);
            var latest = summary?.SnapshotId is not null;
            return new BackupPresentation(t.Selector, t.Name, t.Source, summary?.RepositoryPath ?? t.RepositoryPath,
                latest ? HomePolicy.FormatSavedVersionTime(summary!.CreatedAt) : "Not checked",
                latest ? "Saved version available" : summary is null ? "Saved versions not checked" : "Backup set up",
                StatusTone.Neutral, -index, summary?.SnapshotPath,
                latest ? "Latest saved backup:" : "Last backup:",
                latest ? HomePolicy.SavedVersionTime(summary!.CreatedAt) : null);
        }).ToArray();
        Fixture = new(CurrentStatus(cards), cards, []);
        UpdateStatus();
    }
    public BackupOverviewPresentation Overview(string selector)
    {
        if (!Loaded)
            return new(catalogProblem.Length > 0 ? BackupOverviewAvailability.CatalogUnavailable : BackupOverviewAvailability.Loading,
                "Backup unavailable", "", "", null,
                "Saved versions unavailable", "Configured backups unavailable", StatusTone.Warning,
                BackupTaskRunState.Idle, false, "Mirrorly couldn't refresh your configured backups.", TechnicalDetails);
        if (!configured.TryGetValue(selector, out var task))
            return new(BackupOverviewAvailability.Missing, "Backup not found", "", "", null,
                "Saved versions unavailable", "Backup no longer configured", StatusTone.Warning,
                BackupTaskRunState.Idle, false, "This Backup is no longer in the configured task catalog.", catalogDetails);
        saved.TryGetValue(selector, out var summary);
        var card = AllBackups.First(b => b.Id == selector);
        var state = RunState(selector);
        var summaryUnavailable = summary is null;
        var queuePaused = execution is { QueueAttention: not BackupQueueAttention.None };
        var attention = queuePaused ? Problem : summaryUnavailable
            ? "Mirrorly couldn't read the repository or saved-version summary. No saved-version count is known."
            : "";
        var operation = queuePaused ? execution?.Result?.TechnicalDetails : execution?.ResultFor(selector)?.TechnicalDetails;
        summaryProblems.TryGetValue(selector, out var summaryError);
        return new(summaryUnavailable ? BackupOverviewAvailability.SummaryUnavailable : BackupOverviewAvailability.Available,
            task.Name, task.Source, task.BackupLocation ?? "Backup location unavailable",
            summary?.RepositoryPath,
            summaryUnavailable ? "Saved versions unavailable" : summary!.SnapshotId is null
                ? "No saved versions yet" : HomePolicy.FormatSavedVersionTime(summary.CreatedAt),
            card.Status, card.Tone, state, CanBackUpTask(selector), attention,
            string.Join("\n", new[] { summaryError, operation }.Where(s => !string.IsNullOrWhiteSpace(s))));
    }
    private StatusPresentation CurrentStatus(IReadOnlyList<BackupPresentation> cards)
    {
        var name = cards.FirstOrDefault(b => b.Id == selectedSelector)?.Name ?? "Backup";
        if (execution?.QueueAttention == BackupQueueAttention.TransportUncertain)
            return new("Backup result unconfirmed", "Mirrorly lost the connection before it could confirm the Backup result.",
                "Queued Backups will not start automatically. View technical details before starting more work.", "Back up now", StatusTone.Error, "⚠");
        if (execution?.QueueAttention == BackupQueueAttention.AdmissionRejected)
            return new("Queue paused", "Mirrorly could not admit the Backup request.",
                "Queued Backups will not start automatically. View technical details before starting more work.", "Back up now", StatusTone.Warning, "⚠");
        if (execution?.QueueAttention == BackupQueueAttention.UnreportedTerminal)
            return new("Backup result not fully reported", "Mirrorly couldn't read the Backup result.",
                "Queued Backups will not start automatically. View technical details before starting more work.", "Back up now", StatusTone.Warning, "⚠");
        if (selectedSelector is not null && execution is { } active)
        {
            if (active.TaskState(selectedSelector) == BackupTaskRunState.Queued)
                return new($"{name} is queued", "This Backup will start after the current one finishes.",
                    "Use Remove from queue on its card to withdraw it before it starts.", "Queued", StatusTone.Neutral, "◷", true);
            if (active.TaskSelector == selectedSelector && active.State == BackupGuiState.AwaitingResumeDecision)
                return new("Continue interrupted backup?", "Mirrorly is waiting for your Resume decision.",
                    "The decision is required before this Backup can continue.", "Backing up…", StatusTone.Warning, "⚠", true);
            if (active.TaskState(selectedSelector) == BackupTaskRunState.Running)
                return new($"Backing up {name}…", "Mirrorly is working. No progress estimate is available.",
                    "Close to the notification area or minimize to keep it running.", "Backing up…", StatusTone.Working, "↻", true);
            if (active.TaskSelector == selectedSelector && active.State == BackupGuiState.TransportUncertain)
                return new("Backup result unconfirmed", "Mirrorly lost the connection before it could confirm the result.",
                    "Do not create another version blindly. View technical details.", "Back up now", StatusTone.Error, "⚠");
            if (active.ResultFor(selectedSelector) is { } result)
                return result switch
                {
                    { Outcome: "succeeded" } => new("Backup completed", "A saved version was created.", "Latest saved version is shown below. This does not check whether the source is still unchanged.", "Back up now", StatusTone.Success, "✓"),
                    { Outcome: "completed_with_issues" } => new("Backup completed with issues", "A saved version was created, but some items had issues.", "View technical details before relying on this version.", "Back up now", StatusTone.Warning, "⚠"),
                    { CommitState: "published" } => new("Backup saved; final steps need attention", "The backup was saved, but Mirrorly couldn't finish all final steps.", "The saved version remains. View technical details.", "Back up now", StatusTone.Warning, "⚠"),
                    { CommitState: "unknown" } => new("Backup outcome needs attention", "Mirrorly couldn't confirm whether the new backup version was saved.", "Do not retry before checking the outcome. View technical details.", "Back up now", StatusTone.Error, "⚠"),
                    { CommitState: "not_published" } => new("No new complete version published", "Mirrorly did not publish a new complete Backup version.", "Other repository changes may have occurred. View technical details.", "Back up now", StatusTone.Error, "⚠"),
                    { Outcome: "rejected" } => new("Backup could not start", "Mirrorly did not admit this request.", "View technical details; another operation or session may be active.", "Back up now", StatusTone.Warning, "⚠"),
                    _ => new("Backup result not fully reported", "Mirrorly returned a result without complete details.", "View technical details before starting another Backup.", "Back up now", StatusTone.Error, "⚠")
                };
        }
        if (selectedSelector is not null && saved.TryGetValue(selectedSelector, out var summary) && summary.SnapshotId is not null)
            return new("Saved backup available", "Latest saved backup: " + HomePolicy.FormatSavedVersionTime(summary.CreatedAt),
                "This stored version does not establish that the source is currently unchanged.", "Back up now", StatusTone.Neutral, "○");
        return new("Backup set up", "No saved backup version has been confirmed for this Backup.",
            "Back up now can create a saved version. Setup alone created no snapshot.", "Back up now", StatusTone.Neutral, "○");
    }
    public void SelectBackup(string selector)
    {
        if (!Fixture.Backups.Any(b => b.Id == selector)) return;
        selectedSelector = selector;
        UpdateStatus();
    }
    public async Task BackUpNowAsync(string? selector = null)
    {
        if (execution is null) return;
        if (selector is not null) SelectBackup(selector);
        if (!CanBackUp || selectedSelector is null) return;
        await execution.StartAsync(selectedSelector, RefreshCoreAsync);
        UpdateStatus();
    }
    public bool RemoveFromQueue(string selector)
    {
        var removed = execution?.RemoveQueued(selector) == true;
        if (removed) UpdateStatus();
        return removed;
    }
    private BackupPresentation WithExecutionState(BackupPresentation backup)
    {
        if (execution is null || DesignPreview) return backup;
        return execution.TaskState(backup.Id) switch
        {
            BackupTaskRunState.Running => backup with { Status = "Backing up…", Tone = StatusTone.Working },
            BackupTaskRunState.Queued => backup with { Status = "Queued", Tone = StatusTone.Neutral },
            _ => execution.ResultFor(backup.Id) switch
            {
                { Outcome: "succeeded" } => backup with { Status = "Backup completed", Tone = StatusTone.Success },
                { Outcome: "completed_with_issues" } => backup with { Status = "Backup completed with issues", Tone = StatusTone.Warning },
                { CommitState: "published" } => backup with { Status = "Saved · final steps need attention", Tone = StatusTone.Warning },
                { CommitState: "unknown" } => backup with { Status = "Publication outcome unknown", Tone = StatusTone.Error },
                { CommitState: "not_published" } => backup with { Status = "No new complete version published", Tone = StatusTone.Error },
                { Outcome: "transport_uncertain" } => backup with { Status = "Result unconfirmed", Tone = StatusTone.Error },
                _ => backup
            }
        };
    }
    private void UpdateStatus()
    {
        if (!DesignPreview) Fixture = Fixture with { Status = CurrentStatus(Fixture.Backups) };
        Problem = execution?.QueueAttention switch
        {
            BackupQueueAttention.TransportUncertain => "Queue paused: Mirrorly couldn't confirm the current Backup result. Queued Backups will not start automatically.",
            BackupQueueAttention.AdmissionRejected => "Queue paused: Mirrorly couldn't admit the next Backup. Queued Backups have been kept in order.",
            BackupQueueAttention.UnreportedTerminal => "Queue paused: Mirrorly couldn't read the Backup result. Queued Backups will not start automatically.",
            _ => catalogProblem
        };
        var operation = execution is { QueueAttention: not BackupQueueAttention.None }
            ? execution?.Result?.TechnicalDetails
            : selectedSelector is null ? null : execution?.ResultFor(selectedSelector)?.TechnicalDetails;
        TechnicalDetails = string.Join("\n", new[] { catalogDetails, operation, presentationDetails }.Where(s => !string.IsNullOrWhiteSpace(s)));
        Changed();
    }
    // Tests/design-time only. Normal desktop never selects a fixture.
    public void SelectFixture(HomeScenario scenario)
    {
        DesignPreview = true; Loaded = true; Scenario = scenario;
        Fixture = HomeFixtures.Create(scenario); PrototypeMessage = ""; Changed();
    }
    public void ShowPrototypeAction(string action)
    {
        PrototypeMessage = $"{action} is not available in this version. No files were read or changed.";
        PropertyChanged?.Invoke(this, new(nameof(PrototypeMessage)));
    }
    public void ShowPresentationNotice(string message, string? technicalDetails = null)
    {
        PrototypeMessage = message;
        if (technicalDetails is not null) presentationDetails = technicalDetails;
        UpdateStatus();
        PropertyChanged?.Invoke(this, new(nameof(PrototypeMessage)));
    }
}
