using System.Text.Json;
using System.Diagnostics;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.ViewModels;

static class BackupQueueTests
{
    private static BackupCatalog Catalog(params string[] selectors) => new(
        selectors.Select(s => new ConfiguredBackup(s, s + ".toml", s, "source", "repository")).ToArray(), []);
    private static WorkerReply Reply(string outcome = "succeeded", string? commit = "published") => new("1", "op",
        JsonSerializer.SerializeToElement(new { phase = "terminal", result = new { outcome,
            facts = new { commit_state = commit, snapshot_id = "snapshot" } }, error = (object?)null }));
    private static WorkerReply Rejected(string code) => new("1", null,
        JsonSerializer.SerializeToElement(new { phase = "rejected", result = (object?)null,
            error = new { code, application_invoked = false } }));
    private static TaskCompletionSource<WorkerReply> Barrier() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    private static TaskCompletionSource Started(FakeDesktopSession fake, string selector)
    {
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        fake.BackupStarted += value => { if (value == selector) started.TrySetResult(); };
        return started;
    }

    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Fifth and later catalog tasks use the same GUI FIFO and removal path", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog("A", "B", "C", "D", "E", "F") };
            var a = Barrier(); var f = Barrier();
            fake.BackupBarriers.Enqueue(a); fake.BackupBarriers.Enqueue(f);
            var fStarted = Started(fake, "F");
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
            check(home.Backups.Count == HomePolicy.ManyBackupPreviewLimit && !home.Backups.Any(b => b.Id == "F") &&
                home.AllBackups.Count == 6 && home.AllBackups.Any(b => b.Id == "F"));
            var running = home.BackUpNowAsync("A");
            await home.BackUpNowAsync("F"); await home.BackUpNowAsync("F");
            check(fake.BackupSelectors.SequenceEqual(["A"]) && coordinator.QueuedSelectors.SequenceEqual(["F"]) &&
                home.AllBackups.Single(b => b.Id == "F").Status == "Queued" && !home.CanBackUpTask("F"));
            check(home.RemoveFromQueue("F") && !home.RemoveFromQueue("A") &&
                coordinator.QueuedSelectors.Count == 0);
            await home.BackUpNowAsync("F"); await home.BackUpNowAsync("F");
            check(coordinator.QueuedSelectors.SequenceEqual(["F"]));
            a.SetResult(Reply()); await running;
            await fStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            check(fake.BackupSelectors.SequenceEqual(["A", "F"]) &&
                home.AllBackups.Single(b => b.Id == "F").Status == "Backing up…");
            f.SetResult(Reply());
        });

        await test("FIFO insertion, duplicate suppression, middle removal and re-enqueue", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog("A", "B", "C", "D") };
            var a = Barrier(); var c = Barrier(); var b = Barrier();
            fake.BackupBarriers.Enqueue(a); fake.BackupBarriers.Enqueue(c); fake.BackupBarriers.Enqueue(b);
            var cStarted = Started(fake, "C"); var bStarted = Started(fake, "B");
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
            var running = home.BackUpNowAsync("A");
            await home.BackUpNowAsync("A"); await home.BackUpNowAsync("B");
            await home.BackUpNowAsync("C"); await home.BackUpNowAsync("B");
            check(fake.BackupSelectors.SequenceEqual(["A"]) && coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) &&
                home.Backups.Single(x => x.Id == "B").Status == "Queued" &&
                home.Backups.Single(x => x.Id == "A").Status == "Backing up…");
            check(home.RemoveFromQueue("B") && !home.RemoveFromQueue("A") &&
                coordinator.QueuedSelectors.SequenceEqual(["C"]));
            await home.BackUpNowAsync("B");
            check(coordinator.QueuedSelectors.SequenceEqual(["C", "B"]));
            home.ApplyCatalog(Catalog("D", "B", "A", "C"));
            check(coordinator.QueuedSelectors.SequenceEqual(["C", "B"]));
            a.SetResult(Reply()); await running;
            await cStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            check(fake.BackupSelectors.SequenceEqual(["A", "C"]) && coordinator.QueuedSelectors.SequenceEqual(["B"]));
            c.SetResult(Reply()); await bStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            check(fake.BackupSelectors.SequenceEqual(["A", "C", "B"]) && coordinator.QueuedSelectors.Count == 0);
            b.SetResult(Reply());
        });

        await test("Each factual application terminal advances FIFO, including application unknown", async () =>
        {
            foreach (var (outcome, commit) in new[] {
                ("succeeded", "published"), ("completed_with_issues", "published"),
                ("failed", "not_published"), ("failed", "unknown"), ("failed", "published") })
            {
                var fake = new FakeDesktopSession { Catalog = Catalog("A", "B") };
                var a = Barrier(); var b = Barrier();
                fake.BackupBarriers.Enqueue(a); fake.BackupBarriers.Enqueue(b);
                var bStarted = Started(fake, "B");
                var coordinator = new BackupExecutionCoordinator(fake);
                var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
                var running = home.BackUpNowAsync("A"); await home.BackUpNowAsync("B");
                a.SetResult(Reply(outcome, commit)); await running;
                await bStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
                check(coordinator.ResultFor("A")?.CommitState == commit &&
                    coordinator.QueueAttention == BackupQueueAttention.None && fake.BackupSelectors.SequenceEqual(["A", "B"]));
                if (commit == "unknown") check(!coordinator.CanSchedule("A") && coordinator.CanSchedule("B") == false);
                b.SetResult(Reply());
            }
        });

        await test("A new click during the terminal-to-dispatch gap joins the FIFO tail", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog("A", "B", "D") };
            var a = Barrier(); var b = Barrier(); var d = Barrier();
            fake.BackupBarriers.Enqueue(a); fake.BackupBarriers.Enqueue(b); fake.BackupBarriers.Enqueue(d);
            var dStarted = Started(fake, "D");
            var coordinator = new BackupExecutionCoordinator(fake);
            var added = false;
            fake.Changed += () => {
                if (!fake.Busy && fake.BackupCalls == 1 && !added)
                {
                    added = true;
                    _ = coordinator.StartAsync("D");
                }
            };
            var first = coordinator.StartAsync("A");
            check(await coordinator.StartAsync("B") && coordinator.QueuedSelectors.SequenceEqual(["B"]));
            a.SetResult(Reply()); await first;
            check(added && fake.BackupSelectors.SequenceEqual(["A", "B"]) &&
                coordinator.QueuedSelectors.SequenceEqual(["D"]));
            b.SetResult(Reply()); await dStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            check(fake.BackupSelectors.SequenceEqual(["A", "B", "D"]));
            d.SetResult(Reply());
        });

        await test("Transport uncertainty and worker admission rejection stop FIFO without discarding it", async () =>
        {
            foreach (var (failure, attention) in new[] {
                ("transport", BackupQueueAttention.TransportUncertain),
                ("busy", BackupQueueAttention.AdmissionRejected),
                ("mutation_gate_unavailable", BackupQueueAttention.AdmissionRejected) })
            {
                var fake = new FakeDesktopSession { Catalog = Catalog("A", "B") };
                var a = Barrier(); fake.BackupBarriers.Enqueue(a);
                var coordinator = new BackupExecutionCoordinator(fake);
                var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
                var running = home.BackUpNowAsync("A"); await home.BackUpNowAsync("B");
                if (failure == "transport") a.SetException(new WorkerTransportUncertainException("lost terminal"));
                else a.SetResult(Rejected(failure));
                await running;
                check(fake.BackupSelectors.SequenceEqual(["A"]) && coordinator.QueuedSelectors.SequenceEqual(["B"]) &&
                    coordinator.QueueAttention == attention && home.Problem.Contains("Queue paused") &&
                    !home.CanBackUpTask("B"));
            }
        });

        await test("Paused queue attention and affected-operation details outrank selected queued Backup", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog("A", "B", "C") };
            fake.BackupReplies.Enqueue(Reply());
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
            await home.BackUpNowAsync("C");
            var previousC = coordinator.ResultFor("C")?.TechnicalDetails;
            var a = Barrier(); fake.BackupBarriers.Enqueue(a);
            var running = home.BackUpNowAsync("A");
            await home.BackUpNowAsync("B");
            await home.BackUpNowAsync("C");
            check(home.SelectedSelector == "C" && coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) &&
                home.Status.Title == "C is queued" && fake.BackupSelectors.SequenceEqual(["C", "A"]));
            a.SetException(new WorkerTransportUncertainException("lost terminal for A"));
            await running;
            check(coordinator.QueueAttention == BackupQueueAttention.TransportUncertain &&
                coordinator.ResultFor("A")?.CommitState is null &&
                coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) &&
                home.RunState("C") == BackupTaskRunState.Queued &&
                home.AllBackups.Single(b => b.Id == "C").Status == "Queued" &&
                home.Status.Title == "Backup result unconfirmed" &&
                home.Status.Detail.Contains("lost the connection") &&
                home.Status.NextStep.Contains("will not start automatically") &&
                !home.BackupActionHelp.Contains("starts after") &&
                home.Problem.Contains("Queue paused") &&
                home.TechnicalDetails.Contains("lost terminal for A") &&
                home.TechnicalDetails == coordinator.ResultFor("A")?.TechnicalDetails &&
                home.TechnicalDetails != previousC);
            check(home.RemoveFromQueue("C") && coordinator.QueuedSelectors.SequenceEqual(["B"]) &&
                home.RunState("C") == BackupTaskRunState.Idle && fake.BackupSelectors.SequenceEqual(["C", "A"]));
        });

        await test("Resume holds the slot; true Exit clears queued work while Stay preserves it", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog("A", "B", "C") };
            var a = Barrier(); fake.BackupBarriers.Enqueue(a);
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator); home.ApplyCatalog(fake.Catalog);
            var decision = new TaskCompletionSource<ResumeAnswer>(TaskCreationOptions.RunContinuationsAsynchronously);
            coordinator.ResumePrompt = _ => decision.Task;
            var running = home.BackUpNowAsync("A");
            await home.BackUpNowAsync("B"); await home.BackUpNowAsync("C");
            var resume = coordinator.ResolveResumeAsync(new("1", "op", "interaction", "incomplete", "today", 600));
            check(coordinator.State == BackupGuiState.AwaitingResumeDecision &&
                coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) && fake.BackupCalls == 1);
            // The Stay answer leaves both scheduling and the required decision unchanged.
            check(!fake.ExitPending && coordinator.QueuedSelectors.SequenceEqual(["B", "C"]));
            fake.BeginExitForTest();
            check(fake.ExitPending && coordinator.QueuedSelectors.Count == 0 && !coordinator.CanSchedule("B"));
            decision.SetResult(ResumeAnswer.DeclineResume);
            check(await resume == ResumeAnswer.DeclineResume);
            a.SetResult(Reply()); await running;
            check(fake.BackupCalls == 1);
            check(new BackupExecutionCoordinator(new FakeDesktopSession()).QueuedSelectors.Count == 0);
        });

        await test("Three real repositories execute through one worker in GUI FIFO order", async () =>
        {
            var root = Workspace("fifo");
            try
            {
                var session = new DesktopSession(Fixture(launch, "backup_block_scan", root), new(root));
                await CreateTasks(session, root, "A", "B", "C");
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator); await home.RefreshAsync(session);
                var order = new List<string>();
                coordinator.Changed += () => {
                    if (coordinator.State == BackupGuiState.Running && coordinator.TaskSelector is { } selector &&
                        (order.Count == 0 || order[^1] != selector)) order.Add(selector);
                };
                var cDone = TerminalFor(coordinator, session, "C");
                var first = home.BackUpNowAsync("A");
                await WaitForFile(Path.Combine(root, "entered"));
                await home.BackUpNowAsync("B"); await home.BackUpNowAsync("C");
                check(coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) &&
                    File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await first; await cDone.Task.WaitAsync(TimeSpan.FromSeconds(20));
                check(order.SequenceEqual(["A", "B", "C"]) && coordinator.QueuedSelectors.Count == 0 &&
                    File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 3 &&
                    new[] { "A", "B", "C" }.All(s => coordinator.ResultFor(s)?.Outcome == "succeeded"));
                await VerifyRepositories(launch.Interpreter, root, ["A", "B", "C"], check);
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });

        await test("Removing queued B keeps it out of real worker; C runs after A", async () =>
        {
            var root = Workspace("remove");
            try
            {
                var session = new DesktopSession(Fixture(launch, "backup_block_scan", root), new(root));
                await CreateTasks(session, root, "A", "B", "C");
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator); await home.RefreshAsync(session);
                var cDone = TerminalFor(coordinator, session, "C");
                var first = home.BackUpNowAsync("A"); await WaitForFile(Path.Combine(root, "entered"));
                await home.BackUpNowAsync("B"); await home.BackUpNowAsync("C");
                check(home.RemoveFromQueue("B") && coordinator.QueuedSelectors.SequenceEqual(["C"]));
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await first; await cDone.Task.WaitAsync(TimeSpan.FromSeconds(20));
                check(File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 2);
                await VerifyRepositories(launch.Interpreter, root, ["A", "C"], check);
                var summary = await ReadSummary(session, "B");
                check(summary.SnapshotId is null);
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });

        await test("Real supervised Exit keeps FIFO on Stay, then clears pending and finishes only A", async () =>
        {
            var root = Workspace("exit");
            try
            {
                var session = new DesktopSession(Fixture(launch, "backup_block_scan", root), new(root));
                await CreateTasks(session, root, "A", "B", "C");
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator); await home.RefreshAsync(session);
                var first = home.BackUpNowAsync("A"); await WaitForFile(Path.Combine(root, "entered"));
                await home.BackUpNowAsync("B"); await home.BackUpNowAsync("C");
                check(!await session.ExitAsync(() => Task.FromResult(false)) &&
                    coordinator.QueuedSelectors.SequenceEqual(["B", "C"]) && session.Observation.ProcessExists);
                var exiting = session.ExitAsync(() => Task.FromResult(true));
                check(session.ExitPending && coordinator.QueuedSelectors.Count == 0 &&
                    !exiting.IsCompleted && !coordinator.CanSchedule("B"));
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await first;
                check(await exiting && File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1 &&
                    !session.Observation.ProcessExists);
                await VerifyRepositories(launch.Interpreter, root, ["A"], check);
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });

        await test("Real worker application failures advance FIFO; transport loss does not", async () =>
        {
            foreach (var (scenario, expected) in new[] {
                ("backup_materialization_failure", "not_published"),
                ("backup_publication_unknown", "unknown"),
                ("backup_report_failure", "published"),
                ("backup_crash", "transport") })
            {
                var root = Workspace(scenario);
                try
                {
                    var session = new DesktopSession(Fixture(launch, scenario, root), new(root));
                    await CreateTasks(session, root, "A", "B");
                    var delayed = new DeferredBackupSession(session);
                    var coordinator = new BackupExecutionCoordinator(delayed);
                    var home = new HomeViewModel(coordinator); await home.RefreshAsync(delayed);
                    var bDone = TerminalFor(coordinator, delayed, "B");
                    var first = home.BackUpNowAsync("A");
                    await delayed.FirstCalled.Task.WaitAsync(TimeSpan.FromSeconds(5));
                    await home.BackUpNowAsync("B");
                    check(coordinator.QueuedSelectors.SequenceEqual(["B"]));
                    delayed.ReleaseFirst(); await first;
                    if (expected == "transport")
                    {
                        check(coordinator.QueueAttention == BackupQueueAttention.TransportUncertain &&
                            coordinator.QueuedSelectors.SequenceEqual(["B"]) && !bDone.Task.IsCompleted &&
                            File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                    }
                    else
                    {
                        await bDone.Task.WaitAsync(TimeSpan.FromSeconds(20));
                        check(coordinator.ResultFor("A")?.CommitState == expected &&
                            File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 2);
                    }
                    check(await session.ExitAsync(() => Task.FromResult(true)));
                }
                finally { Directory.Delete(root, true); }
            }
        });
    }

    private static string Workspace(string name)
    {
        var root = Path.Combine(Path.GetTempPath(), "mirrorly-gui-queue-" + name + "-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }
    private static WorkerDevelopmentLaunch Fixture(WorkerDevelopmentLaunch launch, string scenario, string root) => launch with {
        TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"),
        TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = scenario, ["MIRRORLY_TEST_GATE"] = root }
    };
    private static async Task CreateTasks(DesktopSession session, string root, params string[] selectors)
    {
        foreach (var selector in selectors)
        {
            Directory.CreateDirectory(Path.Combine(root, selector, "source"));
            Directory.CreateDirectory(Path.Combine(root, selector, "target"));
            File.WriteAllText(Path.Combine(root, selector, "source", selector + ".txt"), selector);
            await session.RunAsync(async api => {
                var reply = await api.CreateAsync(new(selector, Path.Combine(root, selector, "source"),
                    Path.Combine(root, selector, "target"), "strict", false));
                if (reply.GetProperty("result").ValueKind != JsonValueKind.Object ||
                    reply.GetProperty("result").GetProperty("outcome").GetString() != "succeeded")
                    throw new Exception("Temporary task creation failed: " + reply);
            });
        }
    }
    private static TaskCompletionSource TerminalFor(BackupExecutionCoordinator coordinator, IDesktopSession session, string selector)
    {
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        coordinator.Changed += () => {
            if (!session.Busy && coordinator.TaskSelector == selector && coordinator.State == BackupGuiState.Terminal &&
                coordinator.ResultFor(selector) is not null) done.TrySetResult();
        };
        return done;
    }
    private static async Task WaitForFile(string path)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        while (!File.Exists(path)) await Task.Delay(10, deadline.Token);
    }
    private static async Task<SavedBackupSummary> ReadSummary(DesktopSession session, string selector)
    {
        SavedBackupSummary? summary = null;
        await session.RunAsync(async api => summary = await api.BackupSummaryAsync(selector));
        return summary!;
    }
    private static async Task VerifyRepositories(string interpreter, string root, string[] selectors, Action<bool> check)
    {
        const string script = """
from pathlib import Path
import sys
from mirrorly.application.queries import saved_backup_summary
from mirrorly.config import load_task_config
from mirrorly.manifest import list_manifests
from mirrorly.repo import load_repo
root=Path(sys.argv[1]); config=root/'Mirrorly'/'Gui'/'Tasks'; selectors=sys.argv[2:]
for selector in selectors:
    cfg=load_task_config(config/'config.d'/f'{selector}.toml')
    repo=load_repo(root/selector/'target')
    saved=saved_backup_summary(config, selector)
    complete=[m for m in list_manifests(repo) if m.status=='complete']
    assert cfg.repo_id==repo.repo_id and len(complete)==1
    assert saved.latest.snapshot_id==complete[0].snapshot_id
    assert (saved.snapshot_path/f'{selector}.txt').read_text()==selector
    assert (root/selector/'source'/f'{selector}.txt').read_text()==selector
    assert len(list((repo.path/'logs').glob('backup-*.json')))==1
""";
        var start = new ProcessStartInfo(interpreter) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-I", "-c", script, root }.Concat(selectors)) start.ArgumentList.Add(arg);
        using var child = Process.Start(start)!;
        var output = child.StandardOutput.ReadToEndAsync(); var errors = child.StandardError.ReadToEndAsync();
        await child.WaitForExitAsync();
        check(child.ExitCode == 0 && (await output).Length == 0 && (await errors).Length == 0);
    }
}

// Test-only delay after the real worker has been selected, before the first request is sent.
// It lets the GUI enqueue B while A owns the GUI slot, then exercises the real worker result.
sealed class DeferredBackupSession(DesktopSession inner) : IDesktopSession, ISetupApi
{
    private readonly TaskCompletionSource release = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int calls;
    public TaskCompletionSource FirstCalled { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public bool Busy => inner.Busy;
    public bool ExitPending => inner.ExitPending;
    public event Action? Changed { add => inner.Changed += value; remove => inner.Changed -= value; }
    public void ReleaseFirst() => release.TrySetResult();
    public Task RunAsync(Func<ISetupApi, Task> workflow) => inner.RunAsync(_ => workflow(this));
    public Task<bool> ExitAsync(Func<Task<bool>> confirm) => inner.ExitAsync(confirm);
    public Task<JsonElement> PreflightAsync(SetupPreflightIntent intent) => inner.PreflightAsync(intent);
    public Task<JsonElement> CreateAsync(SetupCreateIntent intent) => inner.CreateAsync(intent);
    public Task<BackupCatalog> CatalogAsync() => inner.CatalogAsync();
    public Task<SavedBackupSummary> BackupSummaryAsync(string selector) => inner.BackupSummaryAsync(selector);
    public async Task<WorkerReply> BackupAsync(string selector)
    {
        if (Interlocked.Increment(ref calls) == 1) { FirstCalled.TrySetResult(); await release.Task; }
        return await inner.BackupAsync(selector);
    }
}
