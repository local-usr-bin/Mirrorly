using System.Diagnostics;
using System.Text.Json;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class BackupGuiFlowTests
{
    static BackupCatalog Catalog => new([
        new("documents", "config/documents.toml", "Documents", Path.GetTempPath(), "configured repo"),
        new("photos", "config/photos.toml", "Photos", Path.GetTempPath(), "configured repo2")], []);
    static void ApplyKnown(HomeViewModel model, BackupCatalog catalog) =>
        model.ApplyCatalog(catalog, checkedSources: catalog.Tasks.ToDictionary(t => t.Selector, _ => true));

    static WorkerReply Reply(string outcome, string? commit = "published", string? stage = null)
    {
        var payload = JsonSerializer.SerializeToElement(new {
            phase = "terminal",
            result = new { outcome, facts = new { commit_state = commit, snapshot_id = "saved-id" } },
            error = stage is null ? null : new { code = "backup_failure", stage, technical = new { type = "OSError", message = "injected" } }
        });
        return new("1", "operation", payload);
    }

    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Source availability refresh disables all Backup actions and recovers", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-source-availability-" + Guid.NewGuid().ToString("N"));
            var source = Path.Combine(root, "source");
            var moved = Path.Combine(root, "disconnected");
            Directory.CreateDirectory(source);
            try
            {
                var fake = new FakeDesktopSession { Catalog = new([
                    new("documents", "config/documents.toml", "Documents", source, "configured repo")], []) };
                var home = new HomeViewModel(new BackupExecutionCoordinator(fake));
                await home.RefreshAsync(fake);
                check(home.CanBackUp && home.CanBackUpTask("documents") && home.Overview("documents").CanBackUp);

                fake.CatalogBarrier = new(TaskCreationOptions.RunContinuationsAsynchronously);
                var pending = home.RefreshAsync(fake);
                check(!home.CanBackUp && !home.CanBackUpTask("documents") && !pending.IsCompleted);
                fake.CatalogBarrier.SetResult(fake.Catalog);
                await pending;
                fake.CatalogBarrier = null;
                check(home.CanBackUp);

                Directory.Move(source, moved);
                await home.RefreshAsync(fake);
                check(!home.CanBackUp && !home.CanBackUpTask("documents") &&
                    !home.Overview("documents").CanBackUp &&
                    home.Status.Title == "Source unavailable" &&
                    home.AllBackups.Single().Status == "Source unavailable");
                await home.BackUpNowAsync("documents");
                check(fake.BackupCalls == 0);

                Directory.Move(moved, source);
                await home.RefreshAsync(fake);
                check(home.CanBackUp && home.Overview("documents").CanBackUp &&
                    home.AllBackups.Single().Status != "Source unavailable");
            }
            finally { Directory.Delete(root, true); }
        });
        await test("UI availability checks existence, including an empty source", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-exists-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                check(await SourceAvailability.CheckAsync(root));
                check(!await SourceAvailability.CheckAsync(Path.Combine(root, "missing")));
                var file = Path.Combine(root, "file.txt");
                File.WriteAllText(file, "not a directory");
                check(!await SourceAvailability.CheckAsync(file));
            }
            finally { Directory.Delete(root, true); }
        });
        await test("Explicit source probe releases session, rejects stale result and coalesces refreshes", async () =>
        {
            var firstProbe = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var secondProbe = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var secondStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var probes = 0;
            Task<bool> Probe(string _) => ++probes switch
            {
                1 => firstProbe.Task,
                2 => StartSecond(),
                _ => throw new Exception("Repeated Refresh accumulated source probes.")
            };
            Task<bool> StartSecond() { secondStarted.SetResult(); return secondProbe.Task; }
            var fake = new FakeDesktopSession { Catalog = new([Catalog.Tasks[0]], []) };
            var home = new HomeViewModel(new BackupExecutionCoordinator(fake), Probe);
            var first = home.RefreshAsync(fake);
            check(probes == 1 && !fake.Busy && !home.CanBackUp);
            await fake.RunAsync(_ => Task.CompletedTask); // Unrelated supervised work is admitted.
            var second = home.RefreshAsync(fake);
            var third = home.RefreshAsync(fake);
            check(probes == 1 && !home.CanBackUp);
            firstProbe.SetResult(false);
            await secondStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
            check(probes == 2 && !home.CanBackUp && home.Status.Title != "Source unavailable");
            secondProbe.SetResult(true);
            await Task.WhenAll(first, second, third);
            check(home.CanBackUp && probes == 2);
        });
        await test("Catalog and Backup terminal refresh preserve cached source state without probing", async () =>
        {
            var probes = 0;
            var fake = new FakeDesktopSession { Catalog = Catalog };
            var home = new HomeViewModel(new BackupExecutionCoordinator(fake), _ => {
                probes++; return Task.FromResult(true);
            });
            await home.RefreshAsync(fake);
            check(probes == 2 && home.CanBackUpTask("documents") && home.CanBackUpTask("photos"));
            await fake.RunAsync(home.RefreshCoreAsync);
            check(probes == 2 && home.CanBackUpTask("documents"));
            fake.BackupReplies.Enqueue(Reply("succeeded"));
            await home.BackUpNowAsync("documents");
            check(probes == 2 && home.CanBackUpTask("photos"));
            fake.Catalog = new([.. Catalog.Tasks, new("new", "config/new.toml", "New", "new source", "configured repo3")], []);
            await fake.RunAsync(home.RefreshCoreAsync);
            check(probes == 2 && home.CanBackUpTask("photos") && !home.CanBackUpTask("new") &&
                home.Overview("new").Attention.Contains("not been checked"));
            await home.RefreshAsync(fake);
            check(probes == 5 && home.CanBackUpTask("new"));
        });
        await test("Successful Setup probes only its new Source after catalog refresh", async () =>
        {
            foreach (var available in new[] { true, false })
            {
                const string oldSource = @"E:\Backups";
                const string newSource = @"C:\Source";
                const string configPath = @"C:\tasks\new.toml";
                var existing = new ConfiguredBackup("old", @"C:\tasks\old.toml", "Old", oldSource, "old repo");
                var created = new ConfiguredBackup("new", configPath, "New", newSource, "new repo");
                var fake = new FakeDesktopSession { Catalog = new([existing], []) };
                var probePaths = new List<string>();
                var newProbe = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                var home = new HomeViewModel(new BackupExecutionCoordinator(fake), source => {
                    probePaths.Add(source);
                    return source == newSource ? newProbe.Task : Task.FromResult(true);
                });
                await home.RefreshAsync(fake);
                check(probePaths.SequenceEqual([oldSource]) && home.CanBackUpTask("old"));

                var setup = new BackupSetupViewModel(new FakeFolders(), fake);
                await setup.Source.SelectAsync(newSource);
                await setup.Destination.SelectAsync(oldSource);
                await setup.ContinueAsync();
                check(setup.CanCreate);
                fake.Catalog = new([existing, created], []);
                fake.Replies.Enqueue(JsonSerializer.SerializeToElement(new {
                    phase = "terminal", error = (object?)null,
                    result = new { outcome = "succeeded", setup = new {
                        repository_initialized = true, config_written = true, config_path = configPath } }
                }));
                Task targeted = Task.CompletedTask;
                setup.Created = async api => {
                    await home.RefreshCoreAsync(api);
                    targeted = home.CheckCreatedSourceAsync(setup.CreatedConfigPath!);
                };
                await setup.CreateAsync(() => Task.FromResult(true));
                check(setup.State == SetupState.Succeeded && !fake.Busy && !targeted.IsCompleted &&
                    probePaths.SequenceEqual([oldSource, newSource]) && home.CanBackUpTask("old") &&
                    !home.CanBackUpTask("new") && home.SelectedSelector == "new");
                newProbe.SetResult(available);
                await targeted;
                check(home.CanBackUpTask("new") == available &&
                    home.Overview("new").CanBackUp == available &&
                    (home.Status.Title == "Source unavailable") == !available &&
                    probePaths.SequenceEqual([oldSource, newSource]));
            }
        });
        await test("Late Setup source check cannot override a newer explicit Refresh", async () =>
        {
            const string configPath = @"C:\tasks\new.toml";
            const string source = @"C:\Source";
            var fake = new FakeDesktopSession { Catalog = new([
                new("new", configPath, "New", source, "new repo")], []) };
            var stale = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var probes = 0;
            var home = new HomeViewModel(new BackupExecutionCoordinator(fake), _ =>
                ++probes == 1 ? stale.Task : Task.FromResult(true));
            await fake.RunAsync(home.RefreshCoreAsync);
            var targeted = home.CheckCreatedSourceAsync(configPath);
            check(!targeted.IsCompleted && !home.CanBackUpTask("new"));
            await home.RefreshAsync(fake);
            check(home.CanBackUpTask("new") && probes == 2);
            stale.SetResult(false);
            await targeted;
            check(home.CanBackUpTask("new") && probes == 2);
        });
        await test("Navigation and tray reopen contain no source refresh call", () =>
        {
            var root = (string)typeof(BackupGuiFlowTests).Assembly.GetCustomAttributes(typeof(System.Reflection.AssemblyMetadataAttribute), false)
                .Cast<System.Reflection.AssemblyMetadataAttribute>().Single(a => a.Key == "CheckoutRoot").Value!;
            var shell = File.ReadAllText(Path.Combine(root, "desktop", "Mirrorly.Desktop", "MainWindow.xaml.cs"));
            var reopen = shell.Split("private void Reopen()")[1].Split("private bool exitDialog")[0];
            var navigate = shell.Split("private void Navigate(ShellPage page)")[1].Split("private void NavigateBackupDetail")[0];
            check(!reopen.Contains("RefreshAsync") && !reopen.Contains("RefreshCoreAsync") &&
                !navigate.Contains("RefreshAsync") && !navigate.Contains("RefreshCoreAsync") &&
                shell.Contains("Navigation.Loaded += async") && shell.Contains("await home.Model.RefreshAsync(session)") &&
                shell.Contains("home.Model.CheckCreatedSourceAsync(configPath)"));
            return Task.CompletedTask;
        });
        await test("Home action becomes available after explicit source refresh returns idle", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog };
            var home = new HomeViewModel(new BackupExecutionCoordinator(fake));
            var notifications = 0;
            home.PropertyChanged += (_, _) => notifications++;
            await fake.RunAsync(async api => {
                await home.RefreshCoreAsync(api);
                check(!home.CanBackUp);
            });
            check(!home.CanBackUp);
            await home.RefreshAsync(fake);
            check(home.CanBackUp && notifications >= 2);
            home.ShowPresentationNotice("File Explorer couldn't open the saved Backup.", "Access denied by test policy");
            check(home.PrototypeMessage.Contains("couldn't open") && home.TechnicalDetails.Contains("Access denied"));
        });
        await test("GUI one-slot dispatch suppresses double click and queues another Backup; Running has no fake progress", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog,
                BackupBarrier = new(TaskCreationOptions.RunContinuationsAsynchronously) };
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator);
            await home.RefreshAsync(fake);
            check(home.CanBackUp && home.Activity.Count == 0);
            var running = home.BackUpNowAsync("documents");
            await home.BackUpNowAsync("documents");
            await home.BackUpNowAsync("photos");
            check(fake.BackupCalls == 1 && fake.LastBackupSelector == "documents" &&
                coordinator.State == BackupGuiState.Running && !coordinator.CanStart &&
                coordinator.QueuedSelectors.SequenceEqual(["photos"]) &&
                home.Status.Title == "Photos is queued" && !home.IsBackingUp &&
                !home.CanBackUp && !home.Status.Detail.Contains('%'));
            fake.BackupBarrier.SetResult(Reply("succeeded"));
            await running;
            while (fake.BackupCalls < 2) await Task.Yield();
            check(fake.BackupSelectors.SequenceEqual(["documents", "photos"]) &&
                coordinator.ResultFor("documents")?.RequestId == "1");
        });

        await test("GUI Resume responses remain explicit and coordinator exposes awaiting state", async () =>
        {
            foreach (var answer in new[] { ResumeAnswer.Resume, ResumeAnswer.DeclineResume, ResumeAnswer.Unavailable })
            {
                var fake = new FakeDesktopSession { BackupBarrier = new(TaskCreationOptions.RunContinuationsAsynchronously) };
                var coordinator = new BackupExecutionCoordinator(fake);
                var decision = new TaskCompletionSource<ResumeAnswer>(TaskCreationOptions.RunContinuationsAsynchronously);
                coordinator.ResumePrompt = _ => decision.Task;
                var running = coordinator.StartAsync("documents");
                var asked = coordinator.ResolveResumeAsync(new("1", "operation", "interaction", "incomplete", "today", 600));
                check(coordinator.State == BackupGuiState.AwaitingResumeDecision && !coordinator.CanStart);
                decision.SetResult(answer);
                check(await asked == answer && coordinator.State == BackupGuiState.Running);
                fake.BackupBarrier.SetResult(Reply("succeeded")); await running;
            }
            var absentFake = new FakeDesktopSession { BackupBarrier = new(TaskCreationOptions.RunContinuationsAsynchronously) };
            var absent = new BackupExecutionCoordinator(absentFake);
            var pending = absent.StartAsync("documents");
            check(await absent.ResolveResumeAsync(new("1", "operation", "interaction", "incomplete", "today", 600)) == ResumeAnswer.Unavailable);
            absentFake.BackupBarrier.SetResult(Reply("failed", "not_published", "baseline"));
            await pending;
        });

        await test("GUI distinguishes success, issues, three commit states and transport uncertainty", async () =>
        {
            foreach (var (reply, expected) in new[] {
                (Reply("succeeded"), "Backup completed"),
                (Reply("completed_with_issues"), "Backup completed with issues"),
                (Reply("failed", "not_published", "materialization"), "No new complete version published"),
                (Reply("failed", "unknown", "complete_publication"), "Backup outcome needs attention"),
                (Reply("failed", "published", "report"), "Backup saved; final steps need attention") })
            {
                var fake = new FakeDesktopSession { Catalog = Catalog };
                fake.BackupReplies.Enqueue(reply);
                var coordinator = new BackupExecutionCoordinator(fake);
                var home = new HomeViewModel(coordinator);
                ApplyKnown(home, Catalog);
                await home.BackUpNowAsync("documents");
                check(home.Status.Title == expected && home.TechnicalDetails.Length > 0 && home.Activity.Count == 0);
                if (coordinator.Result?.CommitState == "unknown") check(!home.CanBackUp);
            }
            var lost = new FakeDesktopSession { Catalog = Catalog,
                BackupError = new WorkerTransportUncertainException("stdout lost") };
            var uncertain = new BackupExecutionCoordinator(lost);
            var uncertainHome = new HomeViewModel(uncertain); ApplyKnown(uncertainHome, Catalog);
            await uncertainHome.BackUpNowAsync("documents");
            check(uncertain.State == BackupGuiState.TransportUncertain && uncertain.Result?.CommitState is null &&
                uncertainHome.Status.Title == "Backup result unconfirmed" && !uncertainHome.CanBackUp && lost.BackupCalls == 1);
            var malformed = new FakeDesktopSession { Catalog = Catalog };
            malformed.BackupReplies.Enqueue(new("7", "operation", JsonSerializer.SerializeToElement(new { phase = "terminal" })));
            var unreported = new HomeViewModel(new BackupExecutionCoordinator(malformed));
            ApplyKnown(unreported, Catalog);
            await unreported.BackUpNowAsync("documents");
            check(unreported.Status.Title == "Backup result not fully reported" && !unreported.CanBackUp &&
                malformed.BackupCalls == 1);
        });

        await test("GUI caller wait does not replay Backup; late terminal remains factual", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog,
                BackupBarrier = new(TaskCreationOptions.RunContinuationsAsynchronously) };
            var coordinator = new BackupExecutionCoordinator(fake);
            var home = new HomeViewModel(coordinator);
            await home.RefreshAsync(fake);
            var running = home.BackUpNowAsync("documents");
            var callerWait = await Task.WhenAny(running, Task.Delay(20));
            check(callerWait != running && coordinator.State == BackupGuiState.Running && fake.BackupCalls == 1);
            fake.BackupBarrier.SetResult(Reply("succeeded"));
            await running;
            check(coordinator.State == BackupGuiState.Terminal && home.Status.Title == "Backup completed" &&
                fake.BackupCalls == 1);
        });

        await test("Real desktop session Backup refresh and fresh session use Python saved-version truth", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-gui-backup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "source"));
            Directory.CreateDirectory(Path.Combine(root, "target"));
            var source = Path.Combine(root, "source", "data.txt");
            File.WriteAllText(source, "real GUI service Backup");
            try
            {
                var paths = new GuiDataPaths(root);
                var session = new DesktopSession(launch, paths);
                await session.RunAsync(async api => {
                    var created = await api.CreateAsync(new("documents", Path.Combine(root, "source"),
                        Path.Combine(root, "target"), "strict", false));
                    check(created.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator);
                await home.RefreshAsync(session);
                check(home.CanBackUp && home.Backups.Single().LastBackup == "Not checked" &&
                    home.Backups.Single().LastBackupLabel == "Last backup:");
                var overview = home.Overview("documents");
                check(overview.Source == Path.Combine(root, "source") &&
                    overview.BackupLocation == Path.Combine(root, "target") &&
                    overview.RepositoryPath == Path.Combine(root, "target", "MirrorlyRepo") &&
                    overview.SavedVersion == "No saved versions yet");
                await home.BackUpNowAsync("documents");
                check(coordinator.Result?.CommitState == "published" && home.Status.Title == "Backup completed" &&
                    home.Backups.Single().SavedSnapshotPath is not null && home.Activity.Count == 0 &&
                    home.Backups.Single().LastBackupLabel == "Latest saved backup:");
                check(await session.ExitAsync(() => Task.FromResult(true)) && File.ReadAllText(source) == "real GUI service Backup");
                var fresh = new DesktopSession(launch, paths);
                var rediscovered = new HomeViewModel(new BackupExecutionCoordinator(fresh));
                await rediscovered.RefreshAsync(fresh);
                check(rediscovered.Status.Title == "Saved backup available" && rediscovered.Status.Detail.StartsWith("Latest saved backup:") &&
                    rediscovered.Backups.Single().SavedSnapshotPath == home.Backups.Single().SavedSnapshotPath &&
                    rediscovered.Activity.Count == 0 && rediscovered.CanBackUp);
                await PythonVerify(launch.Interpreter, paths.TaskConfigRoot, root, check);
                await fresh.ExitAsync(() => Task.FromResult(true));
            }
            finally { Directory.Delete(root, true); }
        });

        await test("Source removed after GUI check fails the real worker without a saved version", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-gui-source-race-" + Guid.NewGuid().ToString("N"));
            var source = Path.Combine(root, "source");
            var moved = Path.Combine(root, "disconnected");
            Directory.CreateDirectory(source);
            Directory.CreateDirectory(Path.Combine(root, "target"));
            File.WriteAllText(Path.Combine(source, "data.txt"), "source race");
            try
            {
                var session = new DesktopSession(launch, new(root));
                await session.RunAsync(async api => await api.CreateAsync(new("documents", source,
                    Path.Combine(root, "target"), "strict", false)));
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator);
                await home.RefreshAsync(session);
                check(home.CanBackUp);
                Directory.Move(source, moved);
                await home.BackUpNowAsync("documents");
                check(coordinator.ResultFor("documents") is { CommitState: "not_published", Stage: "source" } &&
                    home.Status.Title == "No new complete version published");
                await home.RefreshAsync(session);
                check(home.Status.Title == "Source unavailable");
                SavedBackupSummary? summary = null;
                await session.RunAsync(async api => summary = await api.BackupSummaryAsync("documents"));
                check(summary?.SnapshotId is null);
                Directory.Move(moved, source);
                await home.RefreshAsync(session);
                check(home.CanBackUp);
                await home.BackUpNowAsync("documents");
                check(coordinator.ResultFor("documents") is { CommitState: "published" });
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { Directory.Delete(root, true); }
        });

        await test("Real active Backup supervises Stay and confirmed Exit without killing the worker", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-gui-backup-exit-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "source"));
            Directory.CreateDirectory(Path.Combine(root, "target"));
            File.WriteAllText(Path.Combine(root, "source", "data.txt"), "exit supervision");
            try
            {
                var fixture = launch with { TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"),
                    TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = "backup_block_scan", ["MIRRORLY_TEST_GATE"] = root } };
                var session = new DesktopSession(fixture, new(root));
                await session.RunAsync(async api => {
                    var created = await api.CreateAsync(new("documents", Path.Combine(root, "source"),
                        Path.Combine(root, "target"), "strict", false));
                    check(created.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                var coordinator = new BackupExecutionCoordinator(session);
                var home = new HomeViewModel(coordinator);
                await home.RefreshAsync(session);
                var running = home.BackUpNowAsync("documents");
                await WaitForFile(Path.Combine(root, "entered"));
                check(session.Busy && coordinator.State == BackupGuiState.Running && session.Observation.ProcessExists);
                check(!await session.ExitAsync(() => Task.FromResult(false)) && !session.ExitPending && !running.IsCompleted);
                var exiting = session.ExitAsync(() => Task.FromResult(true));
                check(session.ExitPending && !exiting.IsCompleted && session.Observation.ProcessExists);
                await home.BackUpNowAsync("documents");
                check(File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await running;
                check(coordinator.Result?.CommitState == "published" && home.Status.Title == "Backup completed");
                check(await exiting && !session.Observation.ProcessExists);
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });
    }

    static async Task WaitForFile(string path)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        while (!File.Exists(path)) await Task.Delay(10, deadline.Token);
    }

    static async Task PythonVerify(string interpreter, string config, string root, Action<bool> check)
    {
        const string script = """
from pathlib import Path
import sys
from mirrorly.application.queries import saved_backup_summary
from mirrorly.config import load_task_config
from mirrorly.manifest import list_manifests, load_manifest
from mirrorly.repo import load_repo
config=Path(sys.argv[1]); root=Path(sys.argv[2])
task=load_task_config(config/'config.d'/'documents.toml'); repo=load_repo(root/'target')
saved=saved_backup_summary(config,'documents'); items=list_manifests(repo)
assert task.repo_id==repo.repo_id and len(items)==1 and items[0].status=='complete'
assert saved.latest.snapshot_id==items[0].snapshot_id
assert load_manifest(repo,items[0].snapshot_id,require_complete=True)
assert (saved.snapshot_path/'data.txt').read_text()=='real GUI service Backup'
assert len(list((repo.path/'logs').glob('backup-*.json')))==1
""";
        var start = new ProcessStartInfo(interpreter) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-I", "-c", script, config, root }) start.ArgumentList.Add(arg);
        using var child = Process.Start(start)!;
        var output = child.StandardOutput.ReadToEndAsync(); var errors = child.StandardError.ReadToEndAsync();
        await child.WaitForExitAsync();
        check(child.ExitCode == 0 && (await output).Length == 0 && (await errors).Length == 0);
    }
}
