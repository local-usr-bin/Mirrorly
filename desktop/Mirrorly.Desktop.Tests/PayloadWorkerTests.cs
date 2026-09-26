using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using Mirrorly.Desktop.Services;

static class PayloadWorkerTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Payload paths follow app/gui, never cwd or previous installation", () =>
        {
            var old = Environment.CurrentDirectory;
            try
            {
                Environment.CurrentDirectory = Path.GetTempPath();
                foreach (var root in new[] { @"C:\Mirrorly", @"D:\新的 位置\Mirrorly 搬家测试" })
                {
                    var paths = new ProductionPayloadPaths(Path.Combine(root, "app", "gui"));
                    check(paths.Interpreter == Path.Combine(root, "app", "python", "python.exe"));
                    check(paths.HostPath == Path.Combine(root, "app", "worker", "mirrorly", "worker", "payload_launch.py"));
                }
                foreach (var bad in new[] { "relative", @"C:\arbitrary" })
                {
                    try { _ = new ProductionPayloadPaths(bad); throw new Exception("Accepted invalid layout."); }
                    catch (ArgumentException) { }
                }
            }
            finally { Environment.CurrentDirectory = old; }
            return Task.CompletedTask;
        });
        await test("Missing payload never launches a fallback interpreter", async () =>
        {
            var paths = new ProductionPayloadPaths(Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"), "app", "gui"));
            await using var client = new ProductionWorkerClient();
            try { await client.StartAsync(new WorkerPayloadLaunch(paths)); throw new Exception("Missing payload started."); }
            catch (IOException error) { check(error.Message.Contains("payload invalid")); }
            check(!client.Observation.ProcessExists);
        });

        var supplied = Environment.GetEnvironmentVariable("MIRRORLY_TEST_PAYLOAD");
        if (string.IsNullOrEmpty(supplied))
        {
            Console.WriteLine("NOT RUN: P2 artifact integration; set MIRRORLY_TEST_PAYLOAD after Build-WorkerPoC.ps1.");
            return;
        }
        var payload = new ProductionPayloadPaths(Path.Combine(supplied, "app", "gui"));
        await test("Payload launch sanitizes Python/Conda variables and preserves Windows environment", () =>
        {
            var names = new[] { "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "CONDA_PREFIX", "VIRTUAL_ENV" };
            var saved = names.ToDictionary(n => n, Environment.GetEnvironmentVariable);
            try
            {
                foreach (var name in names) Environment.SetEnvironmentVariable(name, "hostile");
                var start = new WorkerPayloadLaunch(payload).CreateStartInfo();
                check(names.All(n => !start.Environment.ContainsKey(n)));
                check(start.Environment.ContainsKey("SystemRoot") && start.Environment.ContainsKey("PATH"));
                check(start.FileName == payload.Interpreter && start.WorkingDirectory == payload.AppRoot);
                check(start.ArgumentList.Contains("-B") && start.ArgumentList.Contains("-I"));
                check(!start.ArgumentList.Contains("--expected-checkout"));
            }
            finally { foreach (var pair in saved) Environment.SetEnvironmentVariable(pair.Key, pair.Value); }
            return Task.CompletedTask;
        });
        await test("Payload client rejects wrong qualification facts from a genuine probe", async () =>
        {
            var launch = new WorkerPayloadLaunch(payload);
            var start = launch.CreateStartInfo(); start.ArgumentList.Add("--probe");
            using var probe = Process.Start(start)!;
            var text = await probe.StandardOutput.ReadToEndAsync();
            var errors = await probe.StandardError.ReadToEndAsync();
            await probe.WaitForExitAsync();
            check(probe.ExitCode == 0 && errors.Length == 0);
            var good = JsonNode.Parse(text)!;
            check(launch.Validate(JsonSerializer.SerializeToElement(good), probe.Id).PythonVersion == "3.13.15");
            foreach (var key in new[] { "executable", "python_version", "architecture", "blake3_path", "blake3_version", "blake3_digest", "mode" })
            {
                var bad = good.DeepClone(); bad[key] = "wrong";
                try { launch.Validate(JsonSerializer.SerializeToElement(bad), probe.Id); throw new Exception("Accepted " + key); }
                catch (InvalidDataException) { }
            }
            foreach (var field in new[] { "module_origins", "protocol_version", "search_path" })
            {
                var bad = good.DeepClone();
                if (field == "module_origins") bad[field]!["mirrorly"] = @"C:\checkout\mirrorly\__init__.py";
                else if (field == "protocol_version") bad[field]!["major"] = 999;
                else bad[field]![0] = @"C:\outside";
                try { launch.Validate(JsonSerializer.SerializeToElement(bad), probe.Id); throw new Exception("Accepted " + field); }
                catch (InvalidDataException) { }
            }
        });
        await test("Payload real protocol plus typed Setup/Backup/Restore survive post-use relocation", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "Mirrorly P2 " + Guid.NewGuid().ToString("N"));
            var first = Path.Combine(root, "中文 软件", "app");
            Directory.CreateDirectory(first);
            foreach (var part in new[] { "python", "worker" }) Copy(Path.Combine(payload.AppRoot, part), Path.Combine(first, part));
            var data = new GuiDataPaths(Path.Combine(root, "user state"));
            var source = Path.Combine(root, "source"); var target = Path.Combine(root, "target");
            Directory.CreateDirectory(source); Directory.CreateDirectory(target);
            File.WriteAllText(Path.Combine(source, "original.txt"), "payload backup");
            var originalCwd = Environment.CurrentDirectory;
            string? selector = null, repository = null;
            try
            {
                Environment.CurrentDirectory = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
                for (var round = 0; round < 2; round++)
                {
                    var app = first;
                    if (round == 1)
                    {
                        app = Path.Combine(root, "新的 位置", "app");
                        Directory.CreateDirectory(Path.GetDirectoryName(app)!);
                        Directory.Move(first, app);
                    }
                    var paths = new ProductionPayloadPaths(Path.Combine(app, "gui"));
                    await using (var client = new ProductionWorkerClient())
                    {
                        await client.StartAsync(new WorkerPayloadLaunch(paths));
                        var facts = client.PayloadQualification!;
                        check(facts.Interpreter == paths.Interpreter && facts.ProcessId > 0);
                        check(facts.ModuleOrigins.Values.All(p => p.StartsWith(paths.WorkerDirectory, StringComparison.OrdinalIgnoreCase)));
                        check((await (await client.RequestAsync("ping")).Terminal).Payload.GetProperty("result").GetProperty("reply").GetString() == "pong");
                        check((await (await client.RequestAsync("status")).Terminal).Payload.GetProperty("error").ValueKind == JsonValueKind.Null);
                        await client.ShutdownIdleAsync(); await client.Completion;
                        check(client.DrainDiagnostics().Length == 0);
                    }
                    var session = new DesktopSession(new WorkerPayloadLaunch(paths), data);
                    try
                    {
                        await session.RunAsync(async api =>
                        {
                            if (round == 0)
                            {
                                check((await api.CatalogAsync()).Tasks.Count == 0);
                                await api.PreflightAsync(new("P2 backup", source, target));
                                var created = await api.CreateAsync(new("P2 backup", source, target, "strict", false));
                                check(created.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                            }
                            var task = (await api.CatalogAsync()).Tasks.Single();
                            selector ??= task.Selector; repository ??= task.RepositoryPath;
                            check(task.Selector == selector && task.RepositoryPath == repository && task.Source == source);
                            if (round == 1) check((await api.SnapshotPageAsync(selector)).Items.Count == 1);
                            var backup = await api.BackupAsync(selector);
                            check(backup.Payload.GetProperty("result").GetProperty("facts").GetProperty("commit_state").GetString() == "published");
                            var summary = await api.BackupSummaryAsync(selector);
                            var snapshots = await api.SnapshotPageAsync(selector);
                            check(snapshots.Items.Count == round + 1 && summary.SnapshotId == snapshots.LatestCompleteSnapshotId);
                            check(snapshots.Items.All(item => item.Status == "complete"));
                            var dest = Path.Combine(root, "restore " + round); Directory.CreateDirectory(dest);
                            File.WriteAllText(Path.Combine(dest, "extra.txt"), "extra");
                            var before = Inventory(repository);
                            var plan = await api.PrepareRestoreAsync(new(selector, null, dest));
                            var result = await api.ExecuteRestoreAsync(plan.PlanId, false);
                            check(result.Outcome == RestoreExecutionOutcome.Completed && result.Facts!.FilesRestored == 1);
                            check(File.ReadAllText(Path.Combine(dest, "original.txt")) == "payload backup");
                            check(File.ReadAllText(Path.Combine(dest, "extra.txt")) == "extra" && before == Inventory(repository));
                        });
                    }
                    finally { check(await session.ExitAsync(() => Task.FromResult(true))); }
                    check(!Directory.EnumerateDirectories(app, "__pycache__", SearchOption.AllDirectories).Any());
                }
                check(File.ReadAllText(Path.Combine(source, "original.txt")) == "payload backup");
            }
            finally { Environment.CurrentDirectory = originalCwd; Directory.Delete(root, true); }
        });
    }
    static void Copy(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.GetFiles(source)) File.Copy(file, Path.Combine(destination, Path.GetFileName(file)));
        foreach (var directory in Directory.GetDirectories(source)) Copy(directory, Path.Combine(destination, Path.GetFileName(directory)));
    }
    static string Inventory(string root) => string.Join("\n", Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
        .Order().Select(p => Path.GetRelativePath(root, p) + ":" + Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p)))));
}
