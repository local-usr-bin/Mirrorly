using System.Text.Json;
using System.Reflection;
using System.Xml.Linq;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class RestoreSelectionTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Restore shell route and Review surface use real controls without execute action", () =>
        {
            var root = Assembly.GetExecutingAssembly().GetCustomAttributes<AssemblyMetadataAttribute>()
                .Single(a => a.Key == "CheckoutRoot").Value!;
            var shell = File.ReadAllText(Path.Combine(root, "desktop", "Mirrorly.Desktop", "MainWindow.xaml.cs"));
            check(shell.Contains("page == ShellPage.Restore ? restore", StringComparison.Ordinal) &&
                shell.Contains("restore = new(session, backup)", StringComparison.Ordinal));
            var view = XDocument.Load(Path.Combine(root, "desktop", "Mirrorly.Desktop", "Views", "RestoreView.xaml"));
            XNamespace x = "http://schemas.microsoft.com/winfx/2006/xaml";
            var names = view.Descendants().Select(e => (string?)e.Attribute(x + "Name")).ToHashSet();
            check(new[] { "BackupChoice", "LatestChoice", "AnotherChoice", "DestinationHost", "SkipChoice",
                "ReplaceChoice", "ReviewAction", "EditAction", "LoadMoreAction", "RefreshVersionsAction" }
                .All(names.Contains));
            check(!view.Descendants().Any(e => (string?)e.Attribute("Content") == "Restore" &&
                e.Name.LocalName == "Button"));
            return Task.CompletedTask;
        });
        await test("Restore selection uses durable selector and read-only latest prepare", async () =>
        {
            var session = Fixture();
            var model = New(session);
            await model.EnterAsync();
            check(model.Backups.Count == 2 && model.SelectedSelector is null && !model.CanReview);
            await model.SelectBackupAsync("second");
            await model.Destination.SelectAsync(@"C:\restore destination");
            check(model.SelectedBackup?.Name == "Same name" && model.CanReview);
            await model.ReviewAsync();
            check(model.IsReview && model.Preview?.Selector == "second" && model.Preview.SnapshotId == "actual-latest" &&
                session.Prepares.Count == 1 && session.Prepares[0].SnapshotId is null &&
                session.Prepares[0].TaskSelector == "second" && session.Prepares[0].Policy == RestoreConflictPolicy.SkipExisting &&
                model.Preview.FileCreateCount == 2 && model.Preview.FileOverwriteCount == 1 &&
                model.Preview.FileSkipCount == 3 && model.Preview.FileConflictCount == 4 &&
                model.Preview.DirectoryEntryCount == 5);
            model.Edit();
            check(!model.IsReview && model.Preview is null);
            model.SetPolicy(RestoreConflictPolicy.ReplaceExisting);
            await model.ReviewAsync();
            check(session.Prepares.Count == 2 && session.Prepares[1].Policy == RestoreConflictPolicy.ReplaceExisting &&
                model.Preview?.PlanId != "" && model.IsReview);
            model.Leave();
            check(model.Preview is null && !model.IsReview && session.ExecuteCalls == 0 && session.DestinationWrites == 0);
        });

        await test("Version choice waits for the selected Backup's factual summary", async () =>
        {
            var session = Fixture();
            var model = New(session);
            await model.EnterAsync();
            session.SummaryGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
            var selecting = model.SelectBackupAsync("second");
            check(!model.CanChooseVersion && !model.CanReview);
            await model.ChooseAnotherAsync();
            check(session.PageCalls.Count == 0);
            session.SummaryGate.SetResult(session.Summaries["second"]);
            await selecting;
            check(model.CanChooseVersion);
        });

        await test("Restore versions page complete items without guessing latest from row order", async () =>
        {
            var session = Fixture();
            session.Pages.Enqueue(new("second", [
                Item("older", "complete"), Item("unfinished", "incomplete")], "unfinished", "actual-latest"));
            session.Pages.Enqueue(new("second", [Item("actual-latest", "complete")], null, "actual-latest"));
            var model = New(session);
            await model.EnterAsync();
            await model.SelectBackupAsync("second");
            await model.Destination.SelectAsync(@"C:\restore destination");
            await model.ChooseAnotherAsync();
            check(model.CompleteVersions.Count == 1 && model.CompleteVersions[0].SnapshotId == "older" &&
                model.CompleteVersions[0].Status != "Latest saved backup" &&
                !model.SelectVersion("unfinished") && model.Versions.NextAfter == "unfinished");
            await model.Versions.LoadMoreAsync();
            check(model.CompleteVersions.Count == 2 && model.CompleteVersions[1].Status == "Latest saved backup" &&
                session.PageCalls.SequenceEqual(new string?[] { null, "unfinished" }));
            check(model.SelectVersion("older") && model.CanReview);
            await model.ReviewAsync();
            check(session.Prepares[^1].SnapshotId == "older" && model.Preview?.SnapshotId == "older");
            model.Edit();
            session.Pages.Enqueue(new("second", [Item("actual-latest", "complete")], null, "actual-latest"));
            await model.Versions.RefreshAsync();
            check(model.ExplicitSnapshotId is null && !model.CanReview);
            model.ChooseLatest();
            check(model.ExplicitSnapshotId is null && model.CanReview);
        });

        await test("Restore absence, changed catalog and unavailable pages do not become approvable", async () =>
        {
            var session = Fixture();
            session.Catalog = new([], []);
            var model = New(session);
            await model.EnterAsync();
            check(model.HasNoBackups && !model.CanReview);
            session.Catalog = new([new("first", "a.toml", "Same name", "source", "repo", "target")], []);
            await model.EnterAsync();
            await model.SelectBackupAsync("first");
            check(model.NoSavedVersion && !model.CanReview);
            session.Catalog = new([new("second", "b.toml", "Same name", "source", "repo", "target")], []);
            await model.EnterAsync();
            check(model.SelectedSelector is null && !model.CanReview);
            await model.SelectBackupAsync("second");
            session.PageError = new IOException("stale cursor");
            await model.ChooseAnotherAsync();
            check(model.Versions.State == SnapshotCollectionState.Unavailable && !model.CanReview);
            check(session.Prepares.Count == 0);
        });

        await test("Backup Running and Queued work block Restore prepare without entering FIFO", async () =>
        {
            var session = Fixture();
            var coordinator = new BackupExecutionCoordinator(session);
            var model = new RestoreSelectionViewModel(session, coordinator, new RestoreFolderFixture());
            await model.EnterAsync();
            await model.SelectBackupAsync("second");
            await model.Destination.SelectAsync(@"C:\restore destination");
            check(model.CanReview);
            var first = coordinator.StartAsync("first");
            check(coordinator.HasActiveBackup && model.BackupWorkPending && !model.CanReview);
            check(await coordinator.StartAsync("second") && coordinator.QueuedSelectors.SequenceEqual(new[] { "second" }));
            check(!model.CanReview && session.Prepares.Count == 0);
            session.BackupGate.SetException(new IOException("transport uncertain"));
            await first;
            check(coordinator.QueuedSelectors.SequenceEqual(new[] { "second" }) && model.BackupWorkPending && !model.CanReview);
            check(coordinator.RemoveQueued("second") && !model.BackupWorkPending && model.CanReview);
            check(session.Prepares.Count == 0);
        });

        await test("Stale prepare and worker busy cannot make an old Restore Review current", async () =>
        {
            var session = Fixture();
            var model = New(session);
            await model.EnterAsync();
            await model.SelectBackupAsync("second");
            await model.Destination.SelectAsync(@"C:\restore destination");
            session.PrepareGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
            var preparing = model.ReviewAsync();
            check(model.State == RestoreSelectionState.Preparing);
            model.SetPolicy(RestoreConflictPolicy.ReplaceExisting);
            session.PrepareGate.SetResult(session.MakePreview(session.Prepares[0]));
            await preparing;
            check(!model.IsReview && model.Preview is null && model.Policy == RestoreConflictPolicy.ReplaceExisting);
            session.PrepareGate = null;
            session.PrepareError = new RestorePrepareRejectedException("admission", "busy");
            await model.ReviewAsync();
            check(!model.IsReview && model.Preview is null && model.Message.Contains("another operation") &&
                session.ExecuteCalls == 0 && session.DestinationWrites == 0);
        });

        await test("Leaving Restore during destination revalidation cannot start a prepare", async () =>
        {
            var session = Fixture();
            var folders = new RestoreFolderFixture();
            var model = new RestoreSelectionViewModel(session, new BackupExecutionCoordinator(session), folders);
            await model.EnterAsync();
            await model.SelectBackupAsync("second");
            await model.Destination.SelectAsync(@"C:\restore destination");
            folders.ValidationGate = new(TaskCreationOptions.RunContinuationsAsynchronously);
            var reviewing = model.ReviewAsync();
            check(model.State == RestoreSelectionState.Preparing);
            model.Leave();
            folders.ValidationGate.SetResult(new(@"C:\restore destination", null));
            await reviewing;
            check(session.Prepares.Count == 0 && model.Preview is null);
        });
    }

    private static RestoreSelectionViewModel New(RestoreSessionFixture session) =>
        new(session, new BackupExecutionCoordinator(session), new RestoreFolderFixture());
    private static RestoreSessionFixture Fixture()
    {
        var session = new RestoreSessionFixture { Catalog = new([
            new("first", "a.toml", "Same name", "source-a", "repo-a", "target-a"),
            new("second", "b.toml", "Same name", "source-b", "repo-b", "target-b")], []) };
        session.Summaries["second"] = new("second", "repo-b", "repo-id", "actual-latest", "2026-09-25T01:00:00+00:00", 1, null);
        return session;
    }
    private static SnapshotCollectionItem Item(string id, string status) => new(id, status,
        "2026-09-25T01:00:00+00:00", null, 1, 0, 12, null, 2);
}

sealed class RestoreFolderFixture : IFolderBrowserService
{
    public TaskCompletionSource<FolderValidation>? ValidationGate { get; set; }
    public Task<FolderListing> BrowseAsync(string? path) => Task.FromResult(new FolderListing(path, null, []));
    public Task<FolderValidation> ValidateAsync(string path) => ValidationGate?.Task ?? Task.FromResult(new FolderValidation(path, null));
}

sealed class RestoreSessionFixture : IDesktopSession, ISetupApi
{
    public bool Busy { get; private set; }
    public bool ExitPending { get; private set; }
    public event Action? Changed;
    public BackupCatalog Catalog { get; set; } = new([], []);
    public Dictionary<string, SavedBackupSummary> Summaries { get; } = new();
    public TaskCompletionSource<SavedBackupSummary>? SummaryGate { get; set; }
    public Queue<SnapshotCollectionPage> Pages { get; } = new();
    public List<string?> PageCalls { get; } = [];
    public Exception? PageError { get; set; }
    public List<RestorePrepareIntent> Prepares { get; } = [];
    public TaskCompletionSource<RestorePreparedPlanPreview>? PrepareGate { get; set; }
    public Exception? PrepareError { get; set; }
    public TaskCompletionSource<WorkerReply> BackupGate { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public int ExecuteCalls => 0; // No execute API exists in the R2 session boundary.
    public int DestinationWrites => 0; // This fixture does not open or write a destination.
    public async Task RunAsync(Func<ISetupApi, Task> workflow)
    {
        if (Busy || ExitPending) throw new InvalidOperationException("busy");
        Busy = true; Changed?.Invoke();
        try { await workflow(this); }
        finally { Busy = false; Changed?.Invoke(); }
    }
    public Task<bool> ExitAsync(Func<Task<bool>> confirm) { ExitPending = true; return Task.FromResult(true); }
    public Task<JsonElement> PreflightAsync(SetupPreflightIntent intent) => throw new NotSupportedException();
    public Task<JsonElement> CreateAsync(SetupCreateIntent intent) => throw new NotSupportedException();
    public Task<BackupCatalog> CatalogAsync() => Task.FromResult(Catalog);
    public Task<SavedBackupSummary> BackupSummaryAsync(string selector) => SummaryGate?.Task ?? Task.FromResult(
        Summaries.TryGetValue(selector, out var summary) ? summary : new(selector, "repo", "repo-id", null, null, null, null));
    public Task<SnapshotCollectionPage> SnapshotPageAsync(string selector, string? after = null, int limit = 16)
    {
        PageCalls.Add(after);
        if (PageError is not null) return Task.FromException<SnapshotCollectionPage>(PageError);
        return Task.FromResult(Pages.Dequeue());
    }
    public Task<RestorePreparedPlanPreview> PrepareRestoreAsync(RestorePrepareIntent intent)
    {
        Prepares.Add(intent);
        if (PrepareError is not null) return Task.FromException<RestorePreparedPlanPreview>(PrepareError);
        return PrepareGate?.Task ?? Task.FromResult(MakePreview(intent));
    }
    public RestorePreparedPlanPreview MakePreview(RestorePrepareIntent intent) => new(
        Guid.NewGuid().ToString("N"), intent.TaskSelector, intent.SnapshotId ?? "actual-latest", intent.Destination,
        intent.Policy, 2, 1, 3, 4, 5);
    public Task<WorkerReply> BackupAsync(string selector, Action<WorkerAdmission>? onAdmitted = null) => BackupGate.Task;
}
