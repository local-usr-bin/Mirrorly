using System.Diagnostics;
using System.Text.Json;
using Mirrorly.Desktop.Services;

static class ProductionBackupTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Production C# client creates two real snapshots via shared Backup service", async () =>
        {
            var root = Workspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(launch);
                await Create(client, root, check);
                File.WriteAllText(Path.Combine(root, "source", "alpha.txt"), "alpha");
                File.WriteAllText(Path.Combine(root, "source", "same.txt"), "same");
                var first = await client.BackupAsync(Input(root));
                var firstResult = (await first.WaitAsync(TimeSpan.FromSeconds(15))).Payload;
                var firstFacts = firstResult.GetProperty("result").GetProperty("facts");
                check(firstResult.GetProperty("error").ValueKind == JsonValueKind.Null &&
                    firstFacts.GetProperty("commit_state").GetString() == "published" &&
                    firstFacts.GetProperty("lifecycle_seq").GetInt64() == 0);
                File.WriteAllText(Path.Combine(root, "source", "alpha.txt"), "changed");
                File.WriteAllText(Path.Combine(root, "source", "new.txt"), "new");
                var second = await client.BackupAsync(Input(root));
                var nextFacts = (await second.WaitAsync(TimeSpan.FromSeconds(15))).Payload.GetProperty("result").GetProperty("facts");
                check(nextFacts.GetProperty("commit_state").GetString() == "published" &&
                    nextFacts.GetProperty("lifecycle_seq").GetInt64() == 1 &&
                    nextFacts.GetProperty("changes").GetProperty("added").GetInt32() == 1 &&
                    nextFacts.GetProperty("materialization").GetProperty("linked").GetInt32() == 1);
                check(firstFacts.GetProperty("snapshot_id").GetString() != nextFacts.GetProperty("snapshot_id").GetString());
                await PythonVerify(launch.Interpreter, root, 2, check);
                await client.ShutdownIdleAsync(); await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { Directory.Delete(root, true); }
        });

        foreach (var answer in new[] { ResumeAnswer.Resume, ResumeAnswer.DeclineResume, ResumeAnswer.Unavailable })
            await test($"C# Resume interaction round-trip: {answer}", async () =>
            {
                var root = Workspace();
                try
                {
                    await using var client = new ProductionWorkerClient();
                    await client.StartAsync(Fixture(launch, "backup_resume_roundtrip", root));
                    await Create(client, root, check);
                    File.WriteAllText(Path.Combine(root, "source", "data.txt"), "resume");
                    var incomplete = await client.BackupAsync(Input(root));
                    var initial = (await incomplete.Terminal).Payload.GetProperty("result").GetProperty("facts");
                    check(initial.GetProperty("commit_state").GetString() == "not_published");
                    var asked = false;
                    client.ResumeResponder = async interaction =>
                    {
                        asked = true;
                        check(interaction.SnapshotId == initial.GetProperty("snapshot_id").GetString() &&
                            interaction.DeadlineSeconds == 600);
                        // The protocol reader must remain live while the answer is pending.
                        var ping = await client.RequestAsync("ping");
                        check((await ping.Terminal).Payload.GetProperty("result").GetProperty("reply").GetString() == "pong");
                        return answer;
                    };
                    var resumed = await client.BackupAsync(Input(root));
                    var reply = (await resumed.WaitAsync(TimeSpan.FromSeconds(15))).Payload;
                    check(asked && resumed.ResponseAvailable);
                    var facts = reply.GetProperty("result").GetProperty("facts");
                    if (answer == ResumeAnswer.Unavailable)
                        check(reply.GetProperty("error").GetProperty("stage").GetString() == "baseline" &&
                            facts.GetProperty("commit_state").GetString() == "not_published");
                    else
                        check(facts.GetProperty("commit_state").GetString() == "published" &&
                            (facts.GetProperty("resumed_from").GetString() is not null) == (answer == ResumeAnswer.Resume));
                    await client.ShutdownIdleAsync(); await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                }
                finally { Directory.Delete(root, true); }
            });

        foreach (var (scenario, stage, state) in new[] {
            ("backup_publication_unknown", "complete_publication", "unknown"),
            ("backup_retention_failure", "retention_plan", "published"),
            ("backup_report_failure", "report", "published"),
            ("backup_materialization_failure", "materialization", "not_published") })
            await test($"C# Backup wire retains {stage}/{state}", async () =>
            {
                var root = Workspace();
                try
                {
                    await using var client = new ProductionWorkerClient();
                    await client.StartAsync(Fixture(launch, scenario, root));
                    await Create(client, root, check);
                    File.WriteAllText(Path.Combine(root, "source", "data.txt"), "fault");
                    var request = await client.BackupAsync(Input(root));
                    var terminal = (await request.Terminal).Payload;
                    check(terminal.GetProperty("error").GetProperty("stage").GetString() == stage &&
                        terminal.GetProperty("result").GetProperty("facts").GetProperty("commit_state").GetString() == state);
                    await client.ShutdownIdleAsync(); await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                }
                finally { Directory.Delete(root, true); }
            });

        await test("C# resumed cleanup failure retains published snapshot facts", async () =>
        {
            var root = Workspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "backup_resume_cleanup_failure", root));
                await Create(client, root, check);
                File.WriteAllText(Path.Combine(root, "source", "data.txt"), "cleanup failure");
                var incomplete = await client.BackupAsync(Input(root));
                var first = (await incomplete.Terminal).Payload.GetProperty("result").GetProperty("facts");
                check(first.GetProperty("commit_state").GetString() == "not_published");
                client.ResumeResponder = _ => Task.FromResult(ResumeAnswer.Resume);
                var request = await client.BackupAsync(Input(root));
                var terminal = (await request.Terminal).Payload;
                var facts = terminal.GetProperty("result").GetProperty("facts");
                check(terminal.GetProperty("error").GetProperty("stage").GetString() == "resume_cleanup" &&
                    facts.GetProperty("commit_state").GetString() == "published" &&
                    facts.GetProperty("resumed_from").GetString() == first.GetProperty("snapshot_id").GetString() &&
                    facts.GetProperty("report_path").ValueKind == JsonValueKind.Null);
                await PythonVerifyIncompleteAndComplete(launch.Interpreter, root, check);
                await client.ShutdownIdleAsync(); await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { Directory.Delete(root, true); }
        });

        await test("C# Backup caller timeout retains a late terminal without replay", async () =>
        {
            var root = Workspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "backup_block_scan", root));
                await Create(client, root, check);
                File.WriteAllText(Path.Combine(root, "source", "data.txt"), "late terminal");
                var request = await client.BackupAsync(Input(root));
                await WaitForFile(Path.Combine(root, "entered"));
                try { await request.WaitAsync(TimeSpan.FromMilliseconds(25)); throw new Exception("Expected wait timeout"); }
                catch (TimeoutException) { }
                check(!request.ResponseAvailable);
                File.WriteAllText(Path.Combine(root, "release"), "go");
                var terminal = await request.WaitAsync(TimeSpan.FromSeconds(15));
                check(request.ResponseAvailable && terminal.Payload.GetProperty("result").GetProperty("facts")
                    .GetProperty("commit_state").GetString() == "published" &&
                    File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                await PythonVerify(launch.Interpreter, root, 1, check);
                await client.ShutdownIdleAsync(); await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });

        await test("C# Backup worker crash is transport uncertainty without restart replay", async () =>
        {
            var root = Workspace();
            try
            {
                await using (var client = new ProductionWorkerClient())
                {
                    await client.StartAsync(Fixture(launch, "backup_crash", root));
                    await Create(client, root, check);
                    File.WriteAllText(Path.Combine(root, "source", "data.txt"), "crash fixture");
                    var request = await client.BackupAsync(Input(root));
                    try { await request.Terminal; throw new Exception("Crash produced a false terminal"); }
                    catch (WorkerTransportUncertainException) { }
                    check(!request.ResponseAvailable && File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                    await client.Completion.WaitAsync(TimeSpan.FromSeconds(10));
                }
                await using var fresh = new ProductionWorkerClient();
                await fresh.StartAsync(launch);
                var catalog = await fresh.RequestAsync("tasks.list", new { config_root = Path.Combine(root, "config"), after = (string?)null });
                check((await catalog.Terminal).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded" &&
                    File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                await fresh.ShutdownIdleAsync(); await fresh.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { Directory.Delete(root, true); }
        });

        await test("C# Backup timeout and client loss never replay or claim application failure", async () =>
        {
            var root = Workspace(); var client = new ProductionWorkerClient();
            try
            {
                await client.StartAsync(Fixture(launch, "backup_block_scan", root));
                await Create(client, root, check);
                File.WriteAllText(Path.Combine(root, "source", "data.txt"), "survives client loss");
                var request = await client.BackupAsync(Input(root));
                await WaitForFile(Path.Combine(root, "entered"));
                try { await request.WaitAsync(TimeSpan.FromMilliseconds(25)); throw new Exception("Expected wait timeout"); }
                catch (TimeoutException) { }
                check(!request.ResponseAvailable);
                await using var contender = new ProductionWorkerClient();
                await contender.StartAsync(launch);
                var rejected = await contender.BackupAsync(Input(root));
                check((await rejected.Terminal).Payload.GetProperty("error").GetProperty("code").GetString() == "mutation_gate_unavailable");
                var disposal = client.DisposeAsync().AsTask();
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await disposal; await client.Completion.WaitAsync(TimeSpan.FromSeconds(10));
                try { await request.Terminal; throw new Exception("Invented application result"); }
                catch (WorkerTransportUncertainException) { }
                check(!request.ResponseAvailable && File.ReadAllLines(Path.Combine(root, "backup_calls")).Length == 1);
                await PythonVerify(launch.Interpreter, root, 1, check);
                await contender.ShutdownIdleAsync(); await contender.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });
    }

    static BackupRunInput Input(string root) => new(Path.Combine(root, "config"), "documents", false, false, []);
    static async Task Create(ProductionWorkerClient client, string root, Action<bool> check)
    {
        var request = await client.CreateAsync(new("documents", Path.Combine(root, "source"),
            Path.Combine(root, "target"), Path.Combine(root, "config"), "strict", false));
        check((await request.Terminal).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
    }
    static string Workspace()
    {
        var root = Path.Combine(Path.GetTempPath(), "mirrorly-backup-ipc-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "source"));
        Directory.CreateDirectory(Path.Combine(root, "target"));
        return root;
    }
    static WorkerDevelopmentLaunch Fixture(WorkerDevelopmentLaunch launch, string scenario, string root) => launch with
    {
        TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"),
        TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = scenario, ["MIRRORLY_TEST_GATE"] = root },
    };
    static async Task WaitForFile(string path)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        while (!File.Exists(path)) await Task.Delay(10, timeout.Token);
    }
    static async Task PythonVerify(string interpreter, string root, int expected, Action<bool> check)
    {
        const string script = """
from pathlib import Path
import sys
from mirrorly.config import load_task_config
from mirrorly.repo import load_repo
from mirrorly.manifest import list_manifests, load_manifest
root=Path(sys.argv[1]); task=load_task_config(root/'config'/'config.d'/'documents.toml')
repo=load_repo(root/'target'); items=list_manifests(repo)
assert task.repo_id==repo.repo_id and len(items)==int(sys.argv[2])
assert all(load_manifest(repo, item.snapshot_id, require_complete=True) for item in items)
assert all((repo.path/'snapshots'/item.snapshot_id/'same.txt').is_file() for item in items) if len(items)>1 else True
assert len(list((repo.path/'logs').glob('backup-*.json')))==int(sys.argv[2])
""";
        var start = new ProcessStartInfo(interpreter) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-I", "-c", script, root, expected.ToString() }) start.ArgumentList.Add(arg);
        using var child = Process.Start(start)!;
        var stdout = child.StandardOutput.ReadToEndAsync(); var stderr = child.StandardError.ReadToEndAsync();
        await child.WaitForExitAsync();
        check(child.ExitCode == 0 && (await stderr).Length == 0 && (await stdout).Length == 0);
    }

    static async Task PythonVerifyIncompleteAndComplete(string interpreter, string root, Action<bool> check)
    {
        const string script = """
from pathlib import Path
import sys
from mirrorly.repo import load_repo
from mirrorly.manifest import list_manifests, load_manifest
root=Path(sys.argv[1]); repo=load_repo(root/'target'); items=list_manifests(repo)
assert len(items)==2 and {item.status for item in items}=={'incomplete','complete'}
assert len([item for item in items if item.status=='complete' and load_manifest(repo, item.snapshot_id, require_complete=True)])==1
""";
        var start = new ProcessStartInfo(interpreter) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-I", "-c", script, root }) start.ArgumentList.Add(arg);
        using var child = Process.Start(start)!;
        var stdout = child.StandardOutput.ReadToEndAsync(); var stderr = child.StandardError.ReadToEndAsync();
        await child.WaitForExitAsync();
        check(child.ExitCode == 0 && (await stderr).Length == 0 && (await stdout).Length == 0);
    }
}
