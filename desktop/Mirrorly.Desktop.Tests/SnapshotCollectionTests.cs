using System.Globalization;
using System.Reflection;
using System.Xml.Linq;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class SnapshotCollectionTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Snapshot section remains subordinate to Backups and first entry is lazy", () =>
        {
            var root = Assembly.GetExecutingAssembly().GetCustomAttributes<AssemblyMetadataAttribute>()
                .Single(a => a.Key == "CheckoutRoot").Value!;
            var xaml = XDocument.Load(Path.Combine(root, "desktop", "Mirrorly.Desktop", "Views", "BackupDetailView.xaml"));
            XNamespace x = "http://schemas.microsoft.com/winfx/2006/xaml";
            check(xaml.Descendants().Any(e => (string?)e.Attribute(x + "Name") == "OverviewSection") &&
                xaml.Descendants().Any(e => (string?)e.Attribute(x + "Name") == "SnapshotsSection") &&
                xaml.Descendants().Any(e => (string?)e.Attribute(x + "Name") == "LoadMoreAction") &&
                xaml.Descendants().Any(e => (string?)e.Attribute(x + "Name") == "SnapshotRefreshAction"));
            var view = File.ReadAllText(Path.Combine(root, "desktop", "Mirrorly.Desktop", "Views", "BackupDetailView.xaml.cs"));
            check(view.Contains("snapshotsSelected = false") && view.Contains("await snapshots.LoadFirstAsync()") &&
                view.Contains("SnapshotRefreshAction.Focus(FocusState.Programmatic)"));
            var shell = File.ReadAllText(Path.Combine(root, "desktop", "Mirrorly.Desktop", "MainWindow.xaml.cs"));
            check(shell.Contains("page == ShellPage.BackupDetail ? detail") && shell.Contains("shell.TopLevelPage"));
            var fake = new FakeSnapshotSession();
            var model = new SnapshotCollectionViewModel(fake);
            model.Select("one");
            check(model.State == SnapshotCollectionState.NotLoaded && fake.Calls.Count == 0);
            return Task.CompletedTask;
        });
        await test("First snapshot page, status, time, counts and authoritative latest", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("one");
            fake.Enqueue(Page([Item("a", "complete", null), Item("b", "incomplete", "malformed"),
                Item("c", "complete", "2025-12-01T10:00:00+00:00")], null, "c"));
            await model.LoadFirstAsync();
            check(fake.Calls.Single() == ("one", null, 16) && model.State == SnapshotCollectionState.Loaded &&
                model.Rows.Count == 3 && model.Rows[0].Status == "Complete" &&
                model.Rows[0].Time == "Time unavailable" && model.Rows[1].Status.Contains("Incomplete") &&
                model.Rows[1].Time == "Time unavailable" && model.Rows[2].Status == "Latest saved backup" &&
                model.Rows[2].Facts.Contains("logical bytes") && model.Items[2].FileCount == 2 &&
                model.Rows[2].Time != "Time unavailable" && model.LatestCompleteSnapshotId == "c");
        });
        await test("True empty and only incomplete remain distinct", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("one"); fake.Enqueue(Page([], null, null)); await model.LoadFirstAsync();
            check(model.IsEmpty && !model.OnlyIncomplete && !model.HasMore);
            model.Select("one"); fake.Enqueue(Page([Item("work", "incomplete", null)], null, null));
            await model.LoadFirstAsync();
            check(!model.IsEmpty && model.OnlyIncomplete && model.Rows.Single().Status.Contains("Incomplete"));
        });
        await test("Explicit Load more appends in contract order without guessing latest", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("one"); fake.Enqueue(Page([Item("z", "complete", null)], "z", "a"));
            await model.LoadFirstAsync();
            check(model.HasMore && model.Rows.Single().Status == "Complete");
            var barrier = new TaskCompletionSource<SnapshotCollectionPage>(TaskCreationOptions.RunContinuationsAsynchronously);
            fake.Enqueue(barrier.Task);
            var more = model.LoadMoreAsync();
            await model.LoadMoreAsync();
            check(fake.Calls.Count == 2 && model.State == SnapshotCollectionState.LoadingMore && model.Items.Count == 1);
            barrier.SetResult(Page([Item("a", "complete", null)], null, "a"));
            await more;
            check(model.State == SnapshotCollectionState.Loaded && !model.HasMore &&
                model.Rows.Select(r => r.SnapshotId).SequenceEqual(["z", "a"]) &&
                model.Rows[0].Status == "Complete" && model.Rows[1].Status == "Latest saved backup" &&
                fake.Calls[1] == ("one", "z", 16));
        });
        await test("First-page failure is unavailable; Retry and Refresh start at first page", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("one"); fake.Enqueue(new IOException("malformed manifest technical fact"));
            await model.LoadFirstAsync();
            check(model.State == SnapshotCollectionState.Unavailable && !model.IsEmpty &&
                model.TechnicalDetails.Contains("malformed manifest"));
            fake.Enqueue(Page([Item("a", "complete", null)], "a", "a")); await model.LoadFirstAsync();
            check(model.State == SnapshotCollectionState.Loaded && model.HasMore);
            fake.Enqueue(Page([], null, null)); await model.RefreshAsync();
            check(model.IsEmpty && fake.Calls.Last().After is null && model.LatestCompleteSnapshotId is null);
        });
        await test("Continuation error and duplicate ID preserve loaded facts until Refresh", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("one"); fake.Enqueue(Page([Item("a", "complete", null)], "a", "a"));
            await model.LoadFirstAsync();
            fake.Enqueue(new IOException("stale cursor")); await model.LoadMoreAsync();
            check(model.State == SnapshotCollectionState.ContinuationError && model.HasMore &&
                model.Items.Single().SnapshotId == "a");
            fake.Enqueue(Page([Item("a", "complete", null)], "a", "a")); await model.LoadMoreAsync();
            check(model.State == SnapshotCollectionState.ContinuationError && model.Items.Count == 1 &&
                model.TechnicalDetails.Contains("twice"));
            fake.Enqueue(Page([Item("b", "complete", null)], null, "b")); await model.RefreshAsync();
            check(model.State == SnapshotCollectionState.Loaded && model.Items.Single().SnapshotId == "b" &&
                fake.Calls.Last().After is null);
        });
        await test("Stale task completion cannot replace another selector's snapshots", async () =>
        {
            var fake = new FakeSnapshotSession(); var model = new SnapshotCollectionViewModel(fake);
            model.Select("first");
            var barrier = new TaskCompletionSource<SnapshotCollectionPage>(TaskCreationOptions.RunContinuationsAsynchronously);
            fake.Enqueue(barrier.Task);
            var pending = model.LoadFirstAsync();
            model.Select("second");
            barrier.SetResult(new("first", [Item("old", "complete", null)], null, "old"));
            await pending;
            check(model.Selector == "second" && model.Items.Count == 0 && model.State == SnapshotCollectionState.NotLoaded);
        });
    }

    private static SnapshotCollectionItem Item(string id, string status, string? time) =>
        new(id, status, time, null, 2, 1, 1200, null, 1);
    private static SnapshotCollectionPage Page(IReadOnlyList<SnapshotCollectionItem> items, string? after, string? latest) =>
        new("one", items, after, latest);

    private sealed class FakeSnapshotSession : IDesktopSession, ISetupApi
    {
        private readonly Queue<Task<SnapshotCollectionPage>> replies = new();
        public readonly List<(string Selector, string? After, int Limit)> Calls = [];
        public bool Busy { get; private set; }
        public bool ExitPending => false;
        public event Action? Changed;
        public void Enqueue(SnapshotCollectionPage page) => replies.Enqueue(Task.FromResult(page));
        public void Enqueue(Task<SnapshotCollectionPage> page) => replies.Enqueue(page);
        public void Enqueue(Exception error) => replies.Enqueue(Task.FromException<SnapshotCollectionPage>(error));
        public async Task RunAsync(Func<ISetupApi, Task> workflow)
        {
            if (Busy) throw new InvalidOperationException("busy");
            Busy = true; Changed?.Invoke();
            try { await workflow(this); }
            finally { Busy = false; Changed?.Invoke(); }
        }
        public Task<SnapshotCollectionPage> SnapshotPageAsync(string selector, string? after = null, int limit = 16)
        {
            Calls.Add((selector, after, limit));
            return replies.Dequeue();
        }
        public Task<bool> ExitAsync(Func<Task<bool>> confirm) => throw new NotSupportedException();
        public Task<System.Text.Json.JsonElement> PreflightAsync(SetupPreflightIntent intent) => throw new NotSupportedException();
        public Task<System.Text.Json.JsonElement> CreateAsync(SetupCreateIntent intent) => throw new NotSupportedException();
        public Task<BackupCatalog> CatalogAsync() => throw new NotSupportedException();
        public Task<SavedBackupSummary> BackupSummaryAsync(string selector) => throw new NotSupportedException();
        public Task<WorkerReply> BackupAsync(string selector, Action<WorkerAdmission>? onAdmitted = null) => throw new NotSupportedException();
    }
}
