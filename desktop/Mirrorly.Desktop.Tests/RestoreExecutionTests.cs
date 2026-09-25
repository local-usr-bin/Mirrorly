using System.Text.Json;
using Mirrorly.Desktop.Services;

static class RestoreExecutionTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Restore execution parses factual results, failures and admission distinctly", () =>
        {
            var input = JsonSerializer.SerializeToElement(new RestoreExecuteInput("a" + new string('b', 31), true));
            check(input.GetProperty("plan_id").GetString()!.Length == 32 && input.GetProperty("overwrite_approved").GetBoolean());
            WorkerReply Reply(object result, object? error = null, string phase = "terminal", string? operation = "operation") =>
                new("1", operation, JsonSerializer.SerializeToElement(new { phase, result, error }));
            var complete = DesktopSession.ParseRestoreExecute(Reply(new { outcome = "completed", facts = Facts(1, 1) }));
            check(complete.Outcome == RestoreExecutionOutcome.Completed && complete.Facts is { FilesRestored: 1, ItemsSkipped: 1 });
            var issues = DesktopSession.ParseRestoreExecute(Reply(new { outcome = "completed_with_issues", facts = Facts(1, 0, 2) }));
            check(issues.Outcome == RestoreExecutionOutcome.CompletedWithIssues && issues.Facts?.Conflicts == 2);
            var failed = DesktopSession.ParseRestoreExecute(Reply(new { outcome = "failed", facts = (object?)null,
                destination_may_have_changed = true }, new { kind = "application", code = "restore_execution_failed" }));
            check(failed.Outcome == RestoreExecutionOutcome.ApplicationFailed && failed.Facts is null);
            var unknown = DesktopSession.ParseRestoreExecute(Reply(new { outcome = "unreported", facts = (object?)null,
                destination_may_have_changed = true }, new { kind = "worker", code = "result_projection_failed" }));
            check(unknown.Outcome == RestoreExecutionOutcome.Unreported);
            try { DesktopSession.ParseRestoreExecute(Reply(null!, new { kind = "admission", code = "busy" }, "rejected", null));
                throw new Exception("Admission rejection became Restore result."); }
            catch (RestoreExecuteRejectedException error) { check(error.Code == "busy"); }
            try { DesktopSession.ParseRestoreExecute(Reply(new { outcome = "completed", facts = Facts(1, 0, 1) }));
                throw new Exception("Clean completion included conflicts."); }
            catch (InvalidDataException) { }
            return Task.CompletedTask;
        });

        await test("One app-scoped Restore blocks Backup without joining FIFO and releases after terminal", async () =>
        {
            var session = new RestoreExecutionFixture();
            var backup = new BackupExecutionCoordinator(session);
            var restore = new RestoreExecutionCoordinator(session, backup);
            var preview = Plan();
            check(backup.CanSchedule("backup"));
            check(restore.Start(preview, "Documents"));
            check(restore.State == RestoreGuiState.Starting && !backup.CanSchedule("backup") &&
                !await backup.StartAsync("backup") && backup.QueuedSelectors.Count == 0);
            session.Admit();
            check(restore.State == RestoreGuiState.Running && restore.Admission?.OperationId == "op");
            session.Finish(new(RestoreExecutionOutcome.Completed, new(preview.SnapshotId, preview.Destination,
                1, 0, 1, 0, 0, 0, 6), "", "1", "op"));
            await restore.Completion;
            check(restore.State == RestoreGuiState.Terminal && backup.CanSchedule("backup") && !restore.BlocksBackup);
            check(!restore.Start(preview, "Documents") && session.ExecuteCalls == 1);
            restore.ClearTerminal();
            check(restore.State == RestoreGuiState.Idle);
        });

        await test("Queued Backup blocks Restore and transport loss keeps Backup admission closed", async () =>
        {
            var session = new RestoreExecutionFixture();
            var backup = new BackupExecutionCoordinator(session);
            var restore = new RestoreExecutionCoordinator(session, backup);
            var first = backup.StartAsync("A");
            check(backup.HasActiveBackup && !restore.Start(Plan(), "Documents"));
            check(await backup.StartAsync("B") && backup.QueuedSelectors.SequenceEqual(new[] { "B" }));
            check(!restore.Start(Plan(), "Documents"));
            session.BackupReply.SetException(new WorkerTransportUncertainException("lost"));
            await first;
            check(backup.QueuedSelectors.Count == 1 && !restore.Start(Plan(), "Documents"));
            backup.RemoveQueued("B");

            var otherSession = new RestoreExecutionFixture();
            var otherBackup = new BackupExecutionCoordinator(otherSession);
            var otherRestore = new RestoreExecutionCoordinator(otherSession, otherBackup);
            check(otherRestore.Start(Plan(), "Documents"));
            otherSession.ExecuteReply.SetException(new WorkerTransportUncertainException("lost terminal"));
            await otherRestore.Completion;
            check(otherRestore.State == RestoreGuiState.TransportUncertain && !otherBackup.CanSchedule("A") &&
                !otherRestore.Start(Plan(), "Documents") && otherSession.ExecuteCalls == 1);
        });

        await test("Rejected or failed Restore never replays a prepared plan", async () =>
        {
            var session = new RestoreExecutionFixture();
            var backup = new BackupExecutionCoordinator(session);
            var restore = new RestoreExecutionCoordinator(session, backup);
            check(restore.Start(Plan(), "Documents"));
            session.ExecuteReply.SetException(new RestoreExecuteRejectedException("admission", "restore_plan_unavailable"));
            await restore.Completion;
            check(restore.State == RestoreGuiState.Idle && restore.RequiresNewPlan && session.ExecuteCalls == 1);
            var secondSession = new RestoreExecutionFixture();
            var second = new RestoreExecutionCoordinator(secondSession, new BackupExecutionCoordinator(secondSession));
            check(second.Start(Plan(), "Documents"));
            secondSession.Admit();
            secondSession.Finish(new(RestoreExecutionOutcome.ApplicationFailed, null, "partial", "1", "op"));
            await second.Completion;
            check(second.State == RestoreGuiState.Terminal && second.Result?.Facts is null && secondSession.ExecuteCalls == 1);
        });

        await test("Real C# client executes Skip and Replace through the Python worker", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-restore-execute-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "source"));
            Directory.CreateDirectory(Path.Combine(root, "target"));
            File.WriteAllText(Path.Combine(root, "source", "same.txt"), "snapshot");
            File.WriteAllText(Path.Combine(root, "source", "new.txt"), "new");
            try
            {
                var session = new DesktopSession(launch, new(root));
                await session.RunAsync(async api => _ = await api.CreateAsync(new("one", Path.Combine(root, "source"),
                    Path.Combine(root, "target"), "strict", false)));
                await session.RunAsync(async api => _ = await api.BackupAsync("one"));
                var repository = Directory.GetFiles(Path.Combine(root, "target"), "*", SearchOption.AllDirectories)
                    .ToDictionary(path => Path.GetRelativePath(Path.Combine(root, "target"), path), File.ReadAllBytes);
                foreach (var policy in new[] { RestoreConflictPolicy.SkipExisting, RestoreConflictPolicy.ReplaceExisting })
                {
                    var destination = Path.Combine(root, "destination-" + policy);
                    Directory.CreateDirectory(destination);
                    File.WriteAllText(Path.Combine(destination, "same.txt"), "user");
                    File.WriteAllText(Path.Combine(destination, "extra.txt"), "extra");
                    RestorePreparedPlanPreview? plan = null;
                    await session.RunAsync(async api => plan = await api.PrepareRestoreAsync(new("one", null, destination, policy)));
                    RestoreExecutionResult? result = null;
                    var admissions = 0;
                    await session.RunAsync(async api => result = await api.ExecuteRestoreAsync(plan!.PlanId,
                        policy == RestoreConflictPolicy.ReplaceExisting, _ => admissions++));
                    check(admissions == 1 && result?.Outcome == RestoreExecutionOutcome.Completed &&
                        File.ReadAllText(Path.Combine(destination, "same.txt")) == (policy == RestoreConflictPolicy.SkipExisting ? "user" : "snapshot") &&
                        File.ReadAllText(Path.Combine(destination, "new.txt")) == "new" &&
                        File.ReadAllText(Path.Combine(destination, "extra.txt")) == "extra");
                    check(Directory.GetFiles(Path.Combine(root, "target"), "*", SearchOption.AllDirectories)
                        .All(path => repository[Path.GetRelativePath(Path.Combine(root, "target"), path)]
                            .SequenceEqual(File.ReadAllBytes(path))));
                    try { await session.RunAsync(async api => _ = await api.ExecuteRestoreAsync(plan!.PlanId,
                        policy == RestoreConflictPolicy.ReplaceExisting));
                        throw new Exception("Consumed Restore plan executed twice."); }
                    catch (RestoreExecuteRejectedException error) { check(error.Code == "restore_plan_unavailable"); }
                }
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { Directory.Delete(root, true); }
        });
    }

    private static object Facts(long restored, long skipped, long conflicts = 0) => new {
        snapshot_id = "snapshot", destination = @"C:\destination", files_restored = restored,
        directories_created = 0L, items_skipped = skipped, conflicts,
        errors = 0L, leftover_temporary_files = 0L, bytes_written = 6L
    };
    private static RestorePreparedPlanPreview Plan() => new("a" + new string('b', 31), "one", "snapshot",
        @"C:\destination", RestoreConflictPolicy.SkipExisting, 1, 0, 0, 0, 0);
}

sealed class RestoreExecutionFixture : IDesktopSession, ISetupApi
{
    public bool Busy { get; private set; }
    public bool ExitPending { get; private set; }
    public event Action? Changed;
    public TaskCompletionSource<RestoreExecutionResult> ExecuteReply { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource<WorkerReply> BackupReply { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Action<WorkerAdmission>? admission;
    public int ExecuteCalls { get; private set; }
    public void Admit() => admission?.Invoke(new("1", "op"));
    public void Finish(RestoreExecutionResult result) => ExecuteReply.SetResult(result);
    public async Task RunAsync(Func<ISetupApi, Task> workflow)
    {
        if (Busy || ExitPending) throw new InvalidOperationException("busy");
        Busy = true; Changed?.Invoke();
        try { await workflow(this); }
        finally { Busy = false; Changed?.Invoke(); }
    }
    public Task<bool> ExitAsync(Func<Task<bool>> confirm) => Task.FromResult(false);
    public Task<RestoreExecutionResult> ExecuteRestoreAsync(string planId, bool overwriteApproved,
        Action<WorkerAdmission>? onAdmitted = null)
    { ExecuteCalls++; admission = onAdmitted; return ExecuteReply.Task; }
    public Task<WorkerReply> BackupAsync(string selector, Action<WorkerAdmission>? onAdmitted = null) => BackupReply.Task;
    public Task<JsonElement> PreflightAsync(SetupPreflightIntent intent) => throw new NotSupportedException();
    public Task<JsonElement> CreateAsync(SetupCreateIntent intent) => throw new NotSupportedException();
    public Task<BackupCatalog> CatalogAsync() => throw new NotSupportedException();
    public Task<SavedBackupSummary> BackupSummaryAsync(string selector) => throw new NotSupportedException();
    public Task<SnapshotCollectionPage> SnapshotPageAsync(string selector, string? after = null, int limit = 16) => throw new NotSupportedException();
    public Task<RestorePreparedPlanPreview> PrepareRestoreAsync(RestorePrepareIntent intent) => throw new NotSupportedException();
}
