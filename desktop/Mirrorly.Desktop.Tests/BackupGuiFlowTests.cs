using System.Diagnostics;
using System.Text.Json;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class BackupGuiFlowTests
{
    static BackupCatalog Catalog => new([
        new("documents", "config/documents.toml", "Documents", "source", "configured repo"),
        new("photos", "config/photos.toml", "Photos", "source2", "configured repo2")], []);

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
        await test("Home action becomes available when supervised catalog refresh returns idle", async () =>
        {
            var fake = new FakeDesktopSession { Catalog = Catalog };
            var home = new HomeViewModel(new BackupExecutionCoordinator(fake));
            var notifications = 0;
            home.PropertyChanged += (_, _) => notifications++;
            await fake.RunAsync(async api => {
                await home.RefreshCoreAsync(api);
                check(!home.CanBackUp);
            });
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
                home.ApplyCatalog(Catalog);
                await home.BackUpNowAsync("documents");
                check(home.Status.Title == expected && home.TechnicalDetails.Length > 0 && home.Activity.Count == 0);
                if (coordinator.Result?.CommitState == "unknown") check(!home.CanBackUp);
            }
            var lost = new FakeDesktopSession { Catalog = Catalog,
                BackupError = new WorkerTransportUncertainException("stdout lost") };
            var uncertain = new BackupExecutionCoordinator(lost);
            var uncertainHome = new HomeViewModel(uncertain); uncertainHome.ApplyCatalog(Catalog);
            await uncertainHome.BackUpNowAsync("documents");
            check(uncertain.State == BackupGuiState.TransportUncertain && uncertain.Result?.CommitState is null &&
                uncertainHome.Status.Title == "Backup result unconfirmed" && !uncertainHome.CanBackUp && lost.BackupCalls == 1);
            var malformed = new FakeDesktopSession { Catalog = Catalog };
            malformed.BackupReplies.Enqueue(new("7", "operation", JsonSerializer.SerializeToElement(new { phase = "terminal" })));
            var unreported = new HomeViewModel(new BackupExecutionCoordinator(malformed));
            unreported.ApplyCatalog(Catalog);
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
