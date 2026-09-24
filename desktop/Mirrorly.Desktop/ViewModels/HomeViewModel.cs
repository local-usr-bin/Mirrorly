using System.ComponentModel;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public sealed class HomeViewModel : INotifyPropertyChanged
{
    private readonly BackupExecutionCoordinator? execution;
    private readonly Dictionary<string, SavedBackupSummary> saved = new();
    private string catalogDetails = "";
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
    public IReadOnlyList<BackupPresentation> Backups => HomePolicy.Preview(Fixture.Backups);
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
    public bool CanBackUp => !DesignPreview && Loaded && selectedSelector is not null && execution?.CanStart == true;
    public bool IsBackingUp => execution?.State is BackupGuiState.Running or BackupGuiState.AwaitingResumeDecision;
    public string? SelectedSelector => selectedSelector;
    public string BackupActionHelp => IsBackingUp ? "Mirrorly is handling one Backup. Other Backups are not queued in this version."
        : execution?.State == BackupGuiState.TransportUncertain ? "The last result is unconfirmed. Do not retry without checking the outcome."
        : execution?.Result?.CommitState == "unknown" ? "Mirrorly cannot confirm whether the last version was saved. Check the outcome before another Backup."
        : "Start one real Backup. No progress percentage or cancellation is available.";
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
            var problems = new List<string>(catalog.Problems);
            foreach (var task in catalog.Tasks)
            {
                try { found[task.Selector] = await api.BackupSummaryAsync(task.Selector); }
                catch (Exception error) { problems.Add($"Saved Backup summary for {task.Selector}: {error}"); }
            }
            ApplyCatalog(new(catalog.Tasks, problems), found);
        }
        catch (Exception error) { Unavailable(error); }
    }
    private void Unavailable(Exception error)
    {
        Loaded = false;
        Problem = "Mirrorly couldn't refresh your configured backups. View technical details, then use Refresh to try reading again.";
        TechnicalDetails = error.ToString(); Changed();
    }
    public void ApplyCatalog(BackupCatalog catalog, IReadOnlyDictionary<string, SavedBackupSummary>? summaries = null)
    {
        DesignPreview = false; Loaded = true;
        saved.Clear();
        if (summaries is not null) foreach (var pair in summaries) saved[pair.Key] = pair.Value;
        if (selectedSelector is null || !catalog.Tasks.Any(t => t.Selector == selectedSelector))
            selectedSelector = catalog.Tasks.FirstOrDefault()?.Selector;
        Problem = catalog.Problems.Count == 0 ? "" : "Some Backup configurations or saved versions couldn't be read. They have not been repaired. View technical details.";
        catalogDetails = string.Join("\n", catalog.Problems);
        var cards = catalog.Tasks.Select((t, index) =>
        {
            saved.TryGetValue(t.Selector, out var summary);
            var latest = summary?.SnapshotId is not null;
            return new BackupPresentation(t.Selector, t.Name, t.Source, summary?.RepositoryPath ?? t.RepositoryPath,
                latest ? summary!.CreatedAt ?? "Time unavailable" : "Not checked",
                latest ? "Saved version available" : summary is null ? "Saved versions not checked" : "Backup set up",
                StatusTone.Neutral, -index, summary?.SnapshotPath,
                latest ? "Latest saved backup:" : "Last backup:");
        }).ToArray();
        Fixture = new(CurrentStatus(cards), cards, []);
        UpdateStatus();
    }
    private StatusPresentation CurrentStatus(IReadOnlyList<BackupPresentation> cards)
    {
        var name = cards.FirstOrDefault(b => b.Id == selectedSelector)?.Name ?? "Backup";
        if (execution is { } active && active.TaskSelector == selectedSelector)
        {
            if (active.State == BackupGuiState.AwaitingResumeDecision)
                return new("Continue interrupted backup?", "Mirrorly is waiting for your Resume decision.",
                    "The decision is required before this Backup can continue.", "Backing up…", StatusTone.Warning, "⚠", true);
            if (active.State == BackupGuiState.Running)
                return new($"Backing up {name}…", "Mirrorly is working. No progress estimate is available.",
                    "Close to the notification area or minimize to keep it running.", "Backing up…", StatusTone.Working, "↻", true);
            if (active.State == BackupGuiState.TransportUncertain)
                return new("Backup result unconfirmed", "Mirrorly lost the connection before it could confirm the result.",
                    "Do not create another version blindly. View technical details.", "Back up now", StatusTone.Error, "⚠");
            if (active.Result is { } result)
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
            return new("Saved backup available", "Latest saved backup: " + summary.CreatedAt,
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
        if (execution is null || !execution.CanStart) return;
        if (selector is not null) SelectBackup(selector);
        if (!CanBackUp || selectedSelector is null) return;
        await execution.StartAsync(selectedSelector, RefreshCoreAsync);
        UpdateStatus();
    }
    private void UpdateStatus()
    {
        if (!DesignPreview) Fixture = Fixture with { Status = CurrentStatus(Fixture.Backups) };
        var operation = execution is { } active && active.TaskSelector == selectedSelector ? active.Result?.TechnicalDetails : null;
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
