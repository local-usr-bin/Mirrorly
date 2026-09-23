using System.Text.Json;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class DesktopFlowTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        async Task<BackupSetupViewModel> Ready(FakeDesktopSession session)
        {
            var model = new BackupSetupViewModel(new FakeFolders(), session);
            await model.Source.SelectAsync(@"C:\Source"); await model.Destination.SelectAsync(@"E:\Backups");
            await model.ContinueAsync(); return model;
        }
        await test("Review uses authoritative path; input changes invalidate preflight", async () =>
        {
            var fake = new FakeDesktopSession(); var model = await Ready(fake);
            check(model.RepositoryPreview == @"X:\authoritative" && model.CanCreate && fake.Checks == 1);
            model.BackupName = "edited"; check(!model.CanCreate);
            await model.CheckAsync(); check(model.CanCreate && fake.LastIntent!.task_name == "edited");
            fake.Preflight = FakeDesktopSession.Check(problem: true); await model.CheckAsync();
            check(model.State == SetupState.Blocked && !model.CanCreate);
        });
        await test("Explicit copy approval and decline; stale preflight never auto-approves", async () =>
        {
            var fake = new FakeDesktopSession { Preflight = FakeDesktopSession.Check(approval: true) };
            var model = await Ready(fake);
            await model.CreateAsync(() => Task.FromResult(false)); check(fake.Creates == 0 && model.CanCreate);
            await model.CreateAsync(() => Task.FromResult(true)); check(fake.Creates == 1 && fake.Approvals.Single());
            fake = new FakeDesktopSession(); model = await Ready(fake);
            fake.Replies.Enqueue(FakeDesktopSession.Decision()); fake.Replies.Enqueue(FakeDesktopSession.Success());
            await model.CreateAsync(() => Task.FromResult(true));
            check(fake.Approvals.SequenceEqual(new[] { false, true }) && model.State == SetupState.Succeeded);
            fake = new FakeDesktopSession(); model = await Ready(fake); fake.Replies.Enqueue(FakeDesktopSession.Decision());
            await model.CreateAsync(() => Task.FromResult(false)); check(fake.Creates == 1 && model.State == SetupState.ApprovalRequired);
        });
        await test("Setup partial facts and admission refusals remain distinct", async () =>
        {
            foreach (var (reply, expected, retry) in new[] {
                (FakeDesktopSession.Failure(false, false), SetupState.Blocked, true),
                (FakeDesktopSession.Failure(true, false), SetupState.Partial, false),
                (FakeDesktopSession.Failure(true, null), SetupState.Partial, false),
                (FakeDesktopSession.Failure(null, false), SetupState.Unknown, false),
                (FakeDesktopSession.Reject("busy"), SetupState.Rejected, true),
                (FakeDesktopSession.Reject("mutation_gate_unavailable"), SetupState.Rejected, true) })
            {
                var fake = new FakeDesktopSession(); var model = await Ready(fake); fake.Replies.Enqueue(reply);
                await model.CreateAsync(() => Task.FromResult(true));
                check(model.State == expected && model.CanCheck == retry && !model.CanCreate && model.TechnicalDetails.Length > 0);
            }
        });
        await test("Double submission is blocked; success refresh finishes under supervision", async () =>
        {
            var fake = new FakeDesktopSession { Barrier = new(TaskCreationOptions.RunContinuationsAsynchronously) };
            var model = await Ready(fake); var refreshed = false;
            model.Created = async api => { check(fake.Busy); await api.CatalogAsync(); refreshed = true; };
            var first = model.CreateAsync(() => Task.FromResult(true));
            await model.CreateAsync(() => Task.FromResult(true)); check(fake.Creates == 1 && !model.CanEdit);
            fake.Barrier.SetResult(FakeDesktopSession.Success()); await first;
            check(refreshed && model.State == SetupState.Succeeded && !model.IsReview);
            check(model.Source.SelectedPath is null && model.Destination.SelectedPath is null && model.BackupName == "" && !model.CanContinue);
        });
        await test("Transport loss never becomes no-write failure or retryable setup", async () =>
        {
            var fake = new FakeDesktopSession { CreateError = new WorkerTransportUncertainException("lost") };
            var model = await Ready(fake); await model.CreateAsync(() => Task.FromResult(true));
            check(model.State == SetupState.Unknown && !model.CanCreate && !model.CanCheck && !model.CanEdit && fake.Creates == 1);
            model.Back(); await model.CreateAsync(() => Task.FromResult(true)); check(fake.Creates == 1);
        });
        await test("Production Home starts without fixtures; catalog identity and problems are factual", async () =>
        {
            check(DesktopSession.CompareCatalogCursor("😀.toml", "\uE000.toml") > 0);
            check(DesktopSession.CompareCatalogCursor("Z.toml", "a.toml") < 0);
            var home = new HomeViewModel(); check(home.Backups.Count == 0 && home.Activity.Count == 0 && !home.Loaded);
            var fake = new FakeDesktopSession(); await home.RefreshAsync(fake); check(home.ShowEmpty && !home.DesignPreview);
            home.ApplyCatalog(new([new("selector", "config", "Different name", "source", "configured repo")], ["broken TOML"]));
            check(home.Backups.Single().Id == "selector" && home.Backups.Single().Name == "Different name");
            check(home.Status.Title == "Backup set up" && home.Activity.Count == 0 && home.Problem.Contains("couldn't be read"));
        });
        await test("Desktop session real create, catalog and restart rediscovery use Python truth", async () =>
        {
            var root = Workspace();
            try
            {
                var paths = new GuiDataPaths(root); var session = new DesktopSession(launch, paths);
                BackupCatalog? first = null;
                await session.RunAsync(async api => {
                    check((await api.CatalogAsync()).Tasks.Count == 0);
                    var intent = Intent(root);
                    var pre = await api.PreflightAsync(intent); check(pre.GetProperty("error").ValueKind == JsonValueKind.Null);
                    var result = await api.CreateAsync(new(intent.task_name, intent.source, intent.target, intent.filesystem_policy, false));
                    check(result.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                    first = await api.CatalogAsync(); check(first.Tasks.Single().Name == "documents");
                });
                check(await session.ExitAsync(() => throw new Exception("Idle exit asked for confirmation")));
                var next = new DesktopSession(launch, paths);
                await next.RunAsync(async api => check((await api.CatalogAsync()).Tasks.SequenceEqual(first!.Tasks)));
                await next.ExitAsync(() => Task.FromResult(true));
                check(!Directory.EnumerateFileSystemEntries(Path.Combine(root, "target", "MirrorlyRepo", "snapshots")).Any());
            }
            finally { Directory.Delete(root, true); }
        });
        await test("Confirmed active Exit waits through late result; Stay leaves operation unchanged", async () =>
        {
            var root = Workspace();
            var paths = new GuiDataPaths(root);
            var session = new DesktopSession(Fixture(launch, "create_blocked_config", root), paths) { ResponseWait = TimeSpan.FromMilliseconds(50) };
            var consumed = false;
            try
            {
                var intent = Intent(root);
                var running = session.RunAsync(async api => { var reply = await api.CreateAsync(new(intent.task_name, intent.source, intent.target, intent.filesystem_policy, false)); consumed = reply.GetProperty("result").GetProperty("outcome").GetString() == "succeeded"; });
                await WaitForFile(Path.Combine(root, "entered"));
                check(!await session.ExitAsync(() => Task.FromResult(false)) && session.Busy && !session.ExitPending);
                var exiting = session.ExitAsync(() => Task.FromResult(true)); check(session.ExitPending && !exiting.IsCompleted);
                try { await session.RunAsync(_ => Task.CompletedTask); throw new Exception("Admitted after Exit"); } catch (InvalidOperationException) { }
                var deadline = DateTime.UtcNow.AddSeconds(5);
                while (!session.WaitingForTerminal && DateTime.UtcNow < deadline) await Task.Delay(5);
                check(session.WaitingForTerminal && session.Observation.ProcessExists);
                File.WriteAllText(Path.Combine(root, "release"), "go");
                await running; check(await exiting && consumed && !session.Observation.ProcessExists);
                check(File.ReadAllLines(Path.Combine(root, "create_calls")).Length == 1);
            }
            finally { File.WriteAllText(Path.Combine(root, "release"), "go"); Directory.Delete(root, true); }
        });
        await test("Desktop channel loss preserves uncertainty through true Exit", async () =>
        {
            var root = Workspace(); var session = new DesktopSession(Fixture(launch, "create_crash", root), new(root));
            try
            {
                var intent = Intent(root); var unknown = false;
                await session.RunAsync(async api => {
                    try { await api.CreateAsync(new(intent.task_name, intent.source, intent.target, intent.filesystem_policy, false)); }
                    catch (WorkerTransportUncertainException) { unknown = true; }
                });
                check(unknown && await session.ExitAsync(() => Task.FromResult(true)));
                check(File.ReadAllLines(Path.Combine(root, "create_calls")).Length == 1);
            }
            finally { Directory.Delete(root, true); }
        });
    }
    static SetupPreflightIntent Intent(string root) => new("documents", Path.Combine(root, "source"), Path.Combine(root, "target"));
    static string Workspace() { var root = Path.Combine(Path.GetTempPath(), "mirrorly-gui-flow-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(Path.Combine(root, "source")); Directory.CreateDirectory(Path.Combine(root, "target")); return root; }
    static WorkerDevelopmentLaunch Fixture(WorkerDevelopmentLaunch launch, string scenario, string root) => launch with { TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"), TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = scenario, ["MIRRORLY_TEST_GATE"] = root } };
    static async Task WaitForFile(string path) { using var token = new CancellationTokenSource(TimeSpan.FromSeconds(8)); while (!File.Exists(path)) await Task.Delay(10, token.Token); }
}
sealed class FakeDesktopSession : IDesktopSession, ISetupApi
{
    public bool Busy { get; private set; }
    public bool ExitPending { get; private set; }
    public event Action? Changed;
    public JsonElement Preflight { get; set; } = Check();
    public Queue<JsonElement> Replies { get; } = new();
    public List<bool> Approvals { get; } = new();
    public int Creates { get; private set; }
    public int Checks { get; private set; }
    public SetupPreflightIntent? LastIntent;
    public Exception? CreateError;
    public TaskCompletionSource<JsonElement>? Barrier;
    public async Task RunAsync(Func<ISetupApi, Task> action) { if (Busy || ExitPending) throw new InvalidOperationException(); Busy = true; Changed?.Invoke(); try { await action(this); } finally { Busy = false; Changed?.Invoke(); } }
    public Task<JsonElement> PreflightAsync(SetupPreflightIntent intent) { Checks++; LastIntent = intent; return Task.FromResult(Preflight); }
    public Task<JsonElement> CreateAsync(SetupCreateIntent intent) { Creates++; Approvals.Add(intent.copy_mode_approved); return CreateError is not null ? Task.FromException<JsonElement>(CreateError) : Barrier?.Task ?? Task.FromResult(Replies.Count > 0 ? Replies.Dequeue() : Success()); }
    public Task<BackupCatalog> CatalogAsync() => Task.FromResult(new BackupCatalog([], []));
    public Task<bool> ExitAsync(Func<Task<bool>> confirm) { ExitPending = true; return Task.FromResult(true); }
    public static JsonElement Check(bool approval = false, bool problem = false) => JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null, result = new { outcome = "succeeded", preflight = new { repository_path = @"X:\authoritative", copy_mode_approval_required = approval, problem = problem ? new { stage = "inputs" } : null } } });
    public static JsonElement Success() => JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null, result = new { outcome = "succeeded", setup = new { repository_initialized = true, config_written = true } } });
    public static JsonElement Failure(bool? repo, bool? config) => JsonSerializer.SerializeToElement(new { phase = "terminal", error = new { code = "setup_failure" }, result = new { outcome = "failed", setup = new { repository_initialized = repo, config_written = config } } });
    public static JsonElement Decision() => JsonSerializer.SerializeToElement(new { phase = "terminal", error = new { code = "copy_mode_approval_required" }, result = new { outcome = "decision_required" } });
    public static JsonElement Reject(string code) => JsonSerializer.SerializeToElement(new { phase = "rejected", error = new { code, application_invoked = false }, result = (object?)null });
}
