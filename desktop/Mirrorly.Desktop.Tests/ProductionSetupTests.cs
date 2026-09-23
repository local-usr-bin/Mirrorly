using System.Diagnostics;
using System.Text.Json;
using Mirrorly.Desktop.Services;

static class ProductionSetupTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Production create uses GUI root provider and Python core readers verify real artifacts", async () =>
        {
            var directory = Workspace();
            try
            {
                var paths = new GuiDataPaths(directory);
                var intent = Intent(directory);
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(launch);
                var request = await client.CreateAsync(paths, intent);
                var terminal = await request.WaitAsync(TimeSpan.FromSeconds(10));
                check(request.ResponseAvailable && request.OperationId == terminal.OperationId && terminal.OperationId is not null);
                var result = terminal.Payload.GetProperty("result");
                check(result.GetProperty("outcome").GetString() == "succeeded");
                var facts = result.GetProperty("setup");
                var inspected = await Inspect(launch, paths, intent);
                check(facts.GetProperty("repository_initialized").GetBoolean() && facts.GetProperty("config_written").GetBoolean());
                check(facts.GetProperty("repo").GetProperty("repo_id").GetString() == inspected.GetProperty("repo_id").GetString());
                check(facts.GetProperty("config_path").GetString() == inspected.GetProperty("config_path").GetString());
                var collision = await client.CreateAsync(paths, intent);
                var failed = (await collision.Terminal).Payload;
                check(failed.GetProperty("error").GetProperty("stage").GetString() == "inputs");
                check(!failed.GetProperty("result").GetProperty("setup").GetProperty("repository_initialized").GetBoolean());
                check((await Inspect(launch, paths, intent)).GetProperty("repo_id").GetString() == inspected.GetProperty("repo_id").GetString());
                await Stop(client);
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production create preserves acknowledged repository and unknown config/init effects", async () =>
        {
            foreach (var scenario in new[] { "create_config_partial", "create_config_complete", "create_repo_failure" })
            {
                var directory = Workspace();
                try
                {
                    await using var client = new ProductionWorkerClient();
                    await client.StartAsync(Fixture(launch, scenario, directory));
                    var request = await client.CreateAsync(new GuiDataPaths(directory), Intent(directory));
                    var response = (await request.WaitAsync(TimeSpan.FromSeconds(10))).Payload;
                    check(request.ResponseAvailable && response.GetProperty("result").GetProperty("outcome").GetString() == "failed");
                    var facts = response.GetProperty("result").GetProperty("setup");
                    check(response.GetProperty("error").GetProperty("kind").GetString() == "application");
                    if (scenario == "create_repo_failure")
                    {
                        check(facts.GetProperty("repository_initialized").ValueKind == JsonValueKind.Null);
                        check(!facts.GetProperty("config_written").GetBoolean());
                        check(facts.GetProperty("repo").ValueKind == JsonValueKind.Null);
                    }
                    else
                    {
                        check(facts.GetProperty("repository_initialized").GetBoolean());
                        check(facts.GetProperty("config_written").ValueKind == JsonValueKind.Null);
                        check(facts.GetProperty("repo").GetProperty("repo_id").GetString() is not null);
                    }
                    check(File.Exists(Path.Combine(directory, "target", "MirrorlyRepo", "lifecycle.json")));
                    check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 1);
                    await Stop(client);
                }
                finally { Directory.Delete(directory, true); }
            }
        });

        await test("Production create requires explicit fresh copy-mode approval without automatic retry", async () =>
        {
            var directory = Workspace();
            try
            {
                var paths = new GuiDataPaths(directory);
                var intent = Intent(directory) with { filesystem_policy = "warn" };
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "create_approval", directory));
                var request = await client.CreateAsync(paths, intent);
                var response = (await request.Terminal).Payload;
                check(response.GetProperty("result").GetProperty("outcome").GetString() == "decision_required");
                check(response.GetProperty("error").GetProperty("code").GetString() == "copy_mode_approval_required");
                check(!Directory.Exists(paths.DataRoot) && !Directory.Exists(Path.Combine(directory, "target", "MirrorlyRepo")));
                check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 1);
                var approved = await client.CreateAsync(paths, intent with { copy_mode_approved = true });
                check((await approved.Terminal).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                await Inspect(launch, paths, intent);
                check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 2);
                await Stop(client);
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production mutation timeout retains late terminal; busy rejects both application methods", async () =>
        {
            var directory = Workspace();
            try
            {
                var paths = new GuiDataPaths(directory);
                var intent = Intent(directory);
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "create_blocked", directory));
                var first = await client.CreateAsync(paths, intent);
                await Entered(directory);
                try { await first.WaitAsync(TimeSpan.FromMilliseconds(20)); throw new Exception("Expected caller timeout."); }
                catch (TimeoutException) { }
                check(!first.ResponseAvailable);
                var second = await client.CreateAsync(paths, intent);
                check((await second.Terminal).Payload.GetProperty("error").GetProperty("code").GetString() == "busy");
                var preview = await client.PreflightAsync(paths, new SetupPreflightIntent(intent.task_name, intent.source, intent.target));
                check((await preview.Terminal).Payload.GetProperty("error").GetProperty("code").GetString() == "busy");
                check((await client.ShutdownIdleAsync()).Payload.GetProperty("phase").GetString() == "rejected");
                var ping = await client.RequestAsync("ping");
                check((await ping.Terminal).Payload.GetProperty("result").GetProperty("reply").GetString() == "pong");
                File.WriteAllText(Path.Combine(directory, "release"), "go");
                check((await first.WaitAsync(TimeSpan.FromSeconds(10))).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                check(first.ResponseAvailable && client.Observation.TransportHealthy);
                check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 1);
                await Inspect(launch, paths, intent);
                await Stop(client);
            }
            finally { Release(directory); Directory.Delete(directory, true); }
        });

        await test("Production client loss preserves mutation and gate; unreported outcome remains unknown", async () =>
        {
            var directory = Workspace();
            var first = new ProductionWorkerClient();
            try
            {
                var paths = new GuiDataPaths(directory);
                var intent = Intent(directory);
                await first.StartAsync(Fixture(launch, "create_blocked_config", directory));
                var request = await first.CreateAsync(paths, intent);
                await Entered(directory);
                var disposal = first.DisposeAsync().AsTask();
                check(File.Exists(Path.Combine(directory, "target", "MirrorlyRepo", "repo.json")));
                await using var second = new ProductionWorkerClient();
                await second.StartAsync(launch);
                var rejected = await second.CreateAsync(paths, intent);
                var error = (await rejected.Terminal).Payload.GetProperty("error");
                check(error.GetProperty("code").GetString() == "mutation_gate_unavailable" && !error.GetProperty("application_invoked").GetBoolean());
                try { await request.Terminal; throw new Exception("Expected transport uncertainty."); }
                catch (WorkerTransportUncertainException) { }
                check(!request.ResponseAvailable && !File.Exists(Path.Combine(directory, "create_finished")));
                Release(directory);
                await first.Completion.WaitAsync(TimeSpan.FromSeconds(10));
                await disposal;
                await Inspect(launch, paths, intent); // External evidence; never synthesized as a client result.
                check(!request.ResponseAvailable);
                check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 1);
                var nextIntent = intent with { task_name = "second", target = Path.Combine(directory, "second-target") };
                var next = await second.CreateAsync(paths, nextIntent); // Explicit independent operation.
                check((await next.Terminal).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                await Inspect(launch, paths, nextIntent);
                await Stop(second);
            }
            finally { Release(directory); await first.DisposeAsync(); await first.Completion.WaitAsync(TimeSpan.FromSeconds(10)); Directory.Delete(directory, true); }
        });

        await test("Production mutation crash reports transport uncertainty and never replays", async () =>
        {
            var directory = Workspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "create_crash", directory));
                var request = await client.CreateAsync(new GuiDataPaths(directory), Intent(directory));
                try { await request.WaitAsync(TimeSpan.FromSeconds(5)); throw new Exception("Expected unknown outcome."); }
                catch (WorkerTransportUncertainException) { }
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                check(!request.ResponseAvailable && !client.Observation.ProcessExists);
                check(File.ReadAllLines(Path.Combine(directory, "create_calls")).Length == 1);
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production second client gets factual gate rejection without creating artifacts", async () =>
        {
            var directory = Workspace();
            try
            {
                await using var owner = new ProductionWorkerClient();
                await using var contender = new ProductionWorkerClient();
                await owner.StartAsync(launch);
                await contender.StartAsync(launch);
                var paths = new GuiDataPaths(directory);
                var rejected = await contender.CreateAsync(paths, Intent(directory));
                var reply = await rejected.Terminal;
                check(rejected.ResponseAvailable && rejected.OperationId is null);
                check(reply.Payload.GetProperty("phase").GetString() == "rejected");
                var error = reply.Payload.GetProperty("error");
                check(error.GetProperty("code").GetString() == "mutation_gate_unavailable");
                check(!error.GetProperty("application_invoked").GetBoolean() && !error.GetProperty("lifecycle_gate").GetProperty("owned").GetBoolean());
                check(!Directory.Exists(paths.DataRoot) && !Directory.Exists(Path.Combine(directory, "target", "MirrorlyRepo")));
                await Stop(contender);
                await Stop(owner);
            }
            finally { Directory.Delete(directory, true); }
        });
    }

    static SetupCreateIntent Intent(string root) => new("documents", Path.Combine(root, "source"), Path.Combine(root, "target"), "strict", false);
    static string Workspace()
    {
        var root = Path.Combine(Path.GetTempPath(), "mirrorly-create-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "source"));
        Directory.CreateDirectory(Path.Combine(root, "target"));
        return root;
    }
    static WorkerDevelopmentLaunch Fixture(WorkerDevelopmentLaunch launch, string scenario, string root) => launch with
    {
        TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"),
        TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = scenario, ["MIRRORLY_TEST_GATE"] = root }
    };
    static void Release(string root) => File.WriteAllText(Path.Combine(root, "release"), "go");
    static async Task Entered(string root)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!File.Exists(Path.Combine(root, "entered"))) await Task.Delay(10, timeout.Token);
    }
    static async Task Stop(ProductionWorkerClient client)
    {
        await client.ShutdownIdleAsync();
        await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
    }
    static async Task<JsonElement> Inspect(WorkerDevelopmentLaunch launch, GuiDataPaths paths, SetupCreateIntent intent)
    {
        var start = new ProcessStartInfo(launch.Interpreter) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var value in new[] { "-I", "-u", Path.Combine(launch.Checkout, "tests", "inspect_created_setup.py"),
            "--expected-interpreter", launch.Interpreter, "--expected-checkout", launch.Checkout,
            "--source", intent.source, "--target", intent.target, "--task-name", intent.task_name, "--config-root", paths.TaskConfigRoot }) start.ArgumentList.Add(value);
        using var process = Process.Start(start) ?? throw new IOException("Reader did not start.");
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(10));
        if (process.ExitCode != 0) throw new Exception("Python core reader: " + await errors);
        return JsonDocument.Parse(await output).RootElement.Clone();
    }
}
