using System.Reflection;
using System.Text;
using System.Text.Json;

namespace Mirrorly.Desktop.Services;

public sealed record ConfiguredBackup(string Selector, string ConfigPath, string Name, string Source,
    string RepositoryPath, string? BackupLocation = null);
public sealed record BackupCatalog(IReadOnlyList<ConfiguredBackup> Tasks, IReadOnlyList<string> Problems);
public sealed record SavedBackupSummary(string Selector, string RepositoryPath, string RepositoryId,
    string? SnapshotId, string? CreatedAt, long? LifecycleSequence, string? SnapshotPath);
public sealed record SnapshotCollectionItem(string SnapshotId, string Status, string? CreatedAt,
    ulong? LifecycleSequence, long FileCount, long DirectoryCount, long LogicalBytes,
    string? ResumedFromSnapshotId, int FormatVersion);
public sealed record SnapshotCollectionPage(string Selector, IReadOnlyList<SnapshotCollectionItem> Items,
    string? NextAfter, string? LatestCompleteSnapshotId);
public sealed record RestorePreparedPlanPreview(string PlanId, string Selector, string SnapshotId,
    string Destination, RestoreConflictPolicy Policy, long FileCreateCount, long FileOverwriteCount,
    long FileSkipCount, long FileConflictCount, long DirectoryEntryCount);
public sealed class RestorePrepareRejectedException(string kind, string code) : IOException($"Restore preparation was not available ({code}).")
{
    public string Kind { get; } = kind;
    public string Code { get; } = code;
}
public sealed record RestoreExecutionFacts(string SnapshotId, string Destination, long FilesRestored,
    long DirectoriesCreated, long ItemsSkipped, long Conflicts, long Errors,
    long LeftoverTemporaryFiles, long BytesWritten);
public enum RestoreExecutionOutcome { Completed, CompletedWithIssues, ApplicationFailed, Unreported }
public sealed record RestoreExecutionResult(RestoreExecutionOutcome Outcome, RestoreExecutionFacts? Facts,
    string TechnicalDetails, string? RequestId, string? OperationId);
public sealed class RestoreExecuteRejectedException(string kind, string code) : IOException($"Restore execution was not admitted ({code}).")
{
    public string Kind { get; } = kind;
    public string Code { get; } = code;
}
public sealed record WorkerReply(string RequestId, string? OperationId, JsonElement Payload);
public interface ISetupApi
{
    Task<JsonElement> PreflightAsync(SetupPreflightIntent intent);
    Task<JsonElement> CreateAsync(SetupCreateIntent intent);
    Task<BackupCatalog> CatalogAsync();
    Task<SavedBackupSummary> BackupSummaryAsync(string selector);
    Task<SnapshotCollectionPage> SnapshotPageAsync(string selector, string? after = null, int limit = 16);
    Task<RestorePreparedPlanPreview> PrepareRestoreAsync(RestorePrepareIntent intent);
    Task<RestoreExecutionResult> ExecuteRestoreAsync(string planId, bool overwriteApproved,
        Action<WorkerAdmission>? onAdmitted = null) => throw new NotSupportedException();
    Task<WorkerReply> BackupAsync(string selector, Action<WorkerAdmission>? onAdmitted = null);
}
public interface IDesktopSession
{
    bool Busy { get; }
    bool ExitPending { get; }
    event Action? Changed;
    Task RunAsync(Func<ISetupApi, Task> workflow);
    Task<bool> ExitAsync(Func<Task<bool>> confirm);
}

// One owner per desktop process. No page owns a worker and no automatic restart/replay.
public sealed class DesktopSession(WorkerLaunch? launch, GuiDataPaths paths) : IDesktopSession, ISetupApi
{
    private readonly ProductionWorkerClient client = new();
    private Task? startup;
    internal TimeSpan ResponseWait { get; init; } = TimeSpan.FromSeconds(30);
    private TaskCompletionSource idle = Completed();
    public bool Busy { get; private set; }
    public bool ExitPending { get; private set; }
    public ProductionWorkerObservation Observation => client.Observation;
    public string? RequestId { get; private set; }
    public bool WaitingForTerminal { get; private set; }
    public event Action? Changed;
    public Func<ResumeInteraction, Task<ResumeAnswer>>? ResumeResponder
    {
        get => client.ResumeResponder;
        set => client.ResumeResponder = value;
    }
    private static TaskCompletionSource Completed() { var t = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously); t.SetResult(); return t; }
    public async Task RunAsync(Func<ISetupApi, Task> workflow)
    {
        if (ExitPending) throw new InvalidOperationException("Exit is pending.");
        if (Busy) throw new InvalidOperationException("Mirrorly is handling another operation.");
        Busy = true;
        idle = new(TaskCreationOptions.RunContinuationsAsynchronously);
        Changed?.Invoke();
        try { await workflow(this); }
        finally { Busy = false; idle.TrySetResult(); Changed?.Invoke(); }
    }
    private async Task<WorkerReply> ReceiveReplyAsync(Func<Task<WorkerRequest>> send, Action<WorkerAdmission>? onAdmitted = null, bool setupDiagnostic = false)
    {
        if (!Busy) throw new InvalidOperationException("Application requests require a supervised workflow.");
        // Explicit unavailable session for the P1 GUI-only deployment experiment.
        // Fail before startup/send; never fall back to PATH, a fixture, or a checkout.
        if (launch is null) throw new IOException("GUI deployment PoC: the Python worker is intentionally disabled. No Backup or Restore can run.");
        await (startup ??= client.StartAsync(launch));
        var request = await send();
        if (setupDiagnostic) SetupDiagnostics.Record("T3 preflight sent", () => $"request={request.RequestId}");
        RequestId = request.RequestId;
        Changed?.Invoke();
        var admissionObserver = onAdmitted is null ? Task.CompletedTask : ObserveAdmissionAsync(request, onAdmitted);
        try
        {
            try
            {
                var terminal = await request.WaitAsync(ResponseWait);
                if (setupDiagnostic) SetupDiagnostics.Record("T4 preflight terminal", () => $"request={request.RequestId} operation={request.OperationId} phase={terminal.Payload.GetProperty("phase").GetString()} error_kind={terminal.Payload.GetProperty("error").ValueKind}");
                await admissionObserver;
                return new(request.RequestId, request.OperationId, terminal.Payload);
            }
            catch (TimeoutException)
            {
                // Only the wait timed out: keep the request and supervision, never resend.
                WaitingForTerminal = true;
                Changed?.Invoke();
                var terminal = await request.Terminal;
                if (setupDiagnostic) SetupDiagnostics.Record("T4 preflight terminal after wait", () => $"request={request.RequestId} operation={request.OperationId} phase={terminal.Payload.GetProperty("phase").GetString()} error_kind={terminal.Payload.GetProperty("error").ValueKind}");
                await admissionObserver;
                return new(request.RequestId, request.OperationId, terminal.Payload);
            }
        }
        catch (Exception error) { if (setupDiagnostic) SetupDiagnostics.Record("T4 preflight receive catch", () => $"request={request.RequestId}", error); throw; }
        finally { WaitingForTerminal = false; RequestId = null; Changed?.Invoke(); }
    }
    private static async Task ObserveAdmissionAsync(WorkerRequest request, Action<WorkerAdmission> onAdmitted)
    {
        try
        {
            if (await request.Admission.Task is { } admission) onAdmitted(admission);
        }
        catch (Exception) { /* Decorative presentation cannot change application facts. */ }
    }
    private async Task<JsonElement> ReceiveAsync(Func<Task<WorkerRequest>> send) =>
        (await ReceiveReplyAsync(send)).Payload;
    public async Task<JsonElement> PreflightAsync(SetupPreflightIntent intent)
    {
        SetupDiagnostics.Record("T3 preflight call");
        try { return (await ReceiveReplyAsync(() => client.PreflightAsync(paths, intent), setupDiagnostic: true)).Payload; }
        catch (Exception error) { SetupDiagnostics.Record("T4 PreflightAsync catch", error: error); throw; }
    }
    public Task<JsonElement> CreateAsync(SetupCreateIntent intent) => ReceiveAsync(() => client.CreateAsync(paths, intent));
    public Task<WorkerReply> BackupAsync(string selector, Action<WorkerAdmission>? onAdmitted = null) =>
        ReceiveReplyAsync(() => client.BackupAsync(paths, new(selector)), onAdmitted);
    public async Task<SavedBackupSummary> BackupSummaryAsync(string selector)
    {
        var payload = await ReceiveAsync(() => client.BackupSummaryAsync(paths, selector));
        if (payload.GetProperty("phase").GetString() != "terminal" ||
            payload.GetProperty("error").ValueKind != JsonValueKind.Null)
            throw new IOException(payload.ToString());
        var value = payload.GetProperty("result").GetProperty("summary");
        var latest = value.GetProperty("latest_complete");
        return new(value.GetProperty("selector").GetString()!, value.GetProperty("repository_path").GetString()!,
            value.GetProperty("repository_id").GetString()!,
            latest.ValueKind == JsonValueKind.Null ? null : latest.GetProperty("snapshot_id").GetString(),
            latest.ValueKind == JsonValueKind.Null ? null : latest.GetProperty("created_at").GetString(),
            latest.ValueKind == JsonValueKind.Null || latest.GetProperty("lifecycle_seq").ValueKind == JsonValueKind.Null
                ? null : latest.GetProperty("lifecycle_seq").GetInt64(),
            latest.ValueKind == JsonValueKind.Null ? null : latest.GetProperty("snapshot_path").GetString());
    }
    public async Task<SnapshotCollectionPage> SnapshotPageAsync(string selector, string? after = null, int limit = 16)
    {
        var payload = await ReceiveAsync(() => client.ListSnapshotsAsync(paths, selector, after, limit));
        return ParseSnapshotPage(payload, selector, limit);
    }
    public async Task<RestorePreparedPlanPreview> PrepareRestoreAsync(RestorePrepareIntent intent)
    {
        var payload = await ReceiveAsync(() => client.PrepareRestoreAsync(paths, intent));
        return ParseRestorePrepare(payload, intent.TaskSelector, intent.Policy, intent.SnapshotId);
    }
    public async Task<RestoreExecutionResult> ExecuteRestoreAsync(string planId, bool overwriteApproved,
        Action<WorkerAdmission>? onAdmitted = null)
    {
        var reply = await ReceiveReplyAsync(() => client.ExecuteRestoreAsync(planId, overwriteApproved), onAdmitted);
        return ParseRestoreExecute(reply);
    }
    internal static RestoreExecutionResult ParseRestoreExecute(WorkerReply reply)
    {
        try
        {
            var payload = reply.Payload;
            var phase = payload.GetProperty("phase").GetString();
            var error = payload.GetProperty("error");
            if (phase == "rejected")
                throw new RestoreExecuteRejectedException(error.GetProperty("kind").GetString()!,
                    error.GetProperty("code").GetString()!);
            if (phase != "terminal" || reply.OperationId is null)
                throw new InvalidDataException("Restore execution did not return an admitted terminal response.");
            var result = payload.GetProperty("result");
            var outcome = result.GetProperty("outcome").GetString() switch
            {
                "completed" => RestoreExecutionOutcome.Completed,
                "completed_with_issues" => RestoreExecutionOutcome.CompletedWithIssues,
                "failed" when error.GetProperty("code").GetString() == "restore_execution_failed" &&
                    result.GetProperty("destination_may_have_changed").GetBoolean() => RestoreExecutionOutcome.ApplicationFailed,
                "unreported" => RestoreExecutionOutcome.Unreported,
                _ => throw new InvalidDataException("Unknown Restore execution outcome.")
            };
            RestoreExecutionFacts? facts = null;
            if (outcome is RestoreExecutionOutcome.Completed or RestoreExecutionOutcome.CompletedWithIssues)
            {
                if (error.ValueKind != JsonValueKind.Null) throw new InvalidDataException("Successful Restore includes an error.");
                var value = result.GetProperty("facts");
                long Count(string key) => value.GetProperty(key).GetInt64();
                var counts = new[] { Count("files_restored"), Count("directories_created"), Count("items_skipped"),
                    Count("conflicts"), Count("errors"), Count("leftover_temporary_files"), Count("bytes_written") };
                var snapshot = value.GetProperty("snapshot_id").GetString();
                var destination = value.GetProperty("destination").GetString();
                if (string.IsNullOrEmpty(snapshot) || string.IsNullOrEmpty(destination) || counts.Any(c => c < 0) ||
                    !Path.IsPathFullyQualified(destination) ||
                    (outcome == RestoreExecutionOutcome.Completed && (counts[3] != 0 || counts[4] != 0 || counts[5] != 0)))
                    throw new InvalidDataException("Invalid Restore execution facts.");
                facts = new(snapshot, destination, counts[0], counts[1], counts[2], counts[3], counts[4], counts[5], counts[6]);
            }
            else if (result.GetProperty("facts").ValueKind != JsonValueKind.Null)
                throw new InvalidDataException("Failed Restore cannot provide invented exact counts.");
            return new(outcome, facts, error.ValueKind == JsonValueKind.Null ? "" : error.ToString(),
                reply.RequestId, reply.OperationId);
        }
        catch (Exception exception) when (exception is KeyNotFoundException or InvalidOperationException or FormatException or OverflowException or JsonException)
        {
            throw new InvalidDataException("Malformed Restore execution response.", exception);
        }
    }
    internal static RestorePreparedPlanPreview ParseRestorePrepare(JsonElement payload, string expectedSelector,
        RestoreConflictPolicy expectedPolicy, string? expectedSnapshotId = null)
    {
        try
        {
            var error = payload.GetProperty("error");
            if (error.ValueKind != JsonValueKind.Null)
            {
                var kind = error.GetProperty("kind").GetString();
                var code = error.GetProperty("code").GetString();
                if (string.IsNullOrEmpty(kind) || string.IsNullOrEmpty(code))
                    throw new InvalidDataException("Malformed Restore error response.");
                throw new RestorePrepareRejectedException(kind, code);
            }
            if (payload.GetProperty("phase").GetString() != "terminal" ||
                payload.GetProperty("result").GetProperty("outcome").GetString() != "succeeded")
                throw new InvalidDataException("Restore prepare did not return a successful terminal result.");
            var preview = payload.GetProperty("result").GetProperty("preview");
            var planId = preview.GetProperty("plan_id").GetString();
            var selector = preview.GetProperty("selector").GetString();
            var snapshot = preview.GetProperty("snapshot_id").GetString();
            var destination = preview.GetProperty("destination").GetString();
            var policy = preview.GetProperty("policy").GetString() switch {
                "skip_existing" => RestoreConflictPolicy.SkipExisting,
                "replace_existing" => RestoreConflictPolicy.ReplaceExisting,
                _ => throw new InvalidDataException("Unknown Restore policy in preview.")
            };
            long Count(string name) => preview.GetProperty(name).GetInt64();
            var counts = new[] { Count("file_create_count"), Count("file_overwrite_count"),
                Count("file_skip_count"), Count("file_conflict_count"), Count("directory_entry_count") };
            if (planId is null || !Guid.TryParseExact(planId, "N", out _) ||
                selector != expectedSelector || string.IsNullOrEmpty(snapshot) ||
                (expectedSnapshotId is not null && snapshot != expectedSnapshotId) ||
                string.IsNullOrEmpty(destination) || !Path.IsPathFullyQualified(destination) ||
                policy != expectedPolicy || counts.Any(value => value < 0))
                throw new InvalidDataException("Invalid Restore preview facts.");
            return new(planId, selector!, snapshot, destination, policy,
                counts[0], counts[1], counts[2], counts[3], counts[4]);
        }
        catch (Exception error) when (error is KeyNotFoundException or InvalidOperationException or FormatException or OverflowException or JsonException)
        {
            throw new InvalidDataException("Malformed Restore prepare response.", error);
        }
    }
    internal static SnapshotCollectionPage ParseSnapshotPage(JsonElement payload, string expectedSelector, int requestedLimit = 16)
    {
        if (payload.GetProperty("phase").GetString() != "terminal" ||
            payload.GetProperty("error").ValueKind != JsonValueKind.Null)
            throw new IOException(payload.ToString());
        var result = payload.GetProperty("result");
        if (result.GetProperty("outcome").GetString() != "succeeded")
            throw new InvalidDataException("Snapshot query did not succeed.");
        var page = result.GetProperty("page");
        var selector = page.GetProperty("selector").GetString();
        if (selector != expectedSelector) throw new InvalidDataException("Snapshot page selector mismatch.");
        var items = new List<SnapshotCollectionItem>();
        foreach (var item in page.GetProperty("items").EnumerateArray())
        {
            var id = item.GetProperty("snapshot_id").GetString();
            var status = item.GetProperty("status").GetString();
            var files = item.GetProperty("file_count").GetInt64();
            var directories = item.GetProperty("directory_count").GetInt64();
            var bytes = item.GetProperty("logical_bytes").GetInt64();
            if (string.IsNullOrEmpty(id) || status is not ("complete" or "incomplete") ||
                files < 0 || directories < 0 || bytes < 0 || items.Count >= requestedLimit)
                throw new InvalidDataException("Invalid snapshot page item.");
            var sequence = item.GetProperty("lifecycle_seq");
            var created = item.GetProperty("created_at");
            var resumed = item.GetProperty("resumed_from_snapshot_id");
            items.Add(new(id, status,
                created.ValueKind == JsonValueKind.Null ? null : created.GetString(),
                sequence.ValueKind == JsonValueKind.Null ? null : sequence.GetUInt64(),
                files, directories, bytes,
                resumed.ValueKind == JsonValueKind.Null ? null : resumed.GetString(),
                item.GetProperty("format_version").GetInt32()));
        }
        var next = page.GetProperty("next_after");
        var latest = page.GetProperty("latest_complete_snapshot_id");
        var cursor = next.ValueKind == JsonValueKind.Null ? null : next.GetString();
        if (cursor is not null && (items.Count == 0 || items[^1].SnapshotId != cursor))
            throw new InvalidDataException("Snapshot page cursor mismatch.");
        return new(selector!, items, cursor,
            latest.ValueKind == JsonValueKind.Null ? null : latest.GetString());
    }
    public async Task<BackupCatalog> CatalogAsync()
    {
        var entries = new List<ConfiguredBackup>();
        var problems = new List<string>();
        string? after = null;
        do
        {
            var payload = await ReceiveAsync(() => client.ListTasksAsync(paths, after));
            if (payload.GetProperty("error").ValueKind != JsonValueKind.Null)
                throw new IOException(payload.ToString());
            var page = payload.GetProperty("result").GetProperty("catalog");
            foreach (var entry in page.GetProperty("entries").EnumerateArray())
            {
                if (entry.GetProperty("task").ValueKind == JsonValueKind.Null) { problems.Add(entry.ToString()); continue; }
                var task = entry.GetProperty("task");
                entries.Add(new(entry.GetProperty("selector").GetString()!, entry.GetProperty("config_path").GetString()!,
                    task.GetProperty("name").GetString()!, task.GetProperty("source").GetString()!,
                    task.GetProperty("configured_repository_path").GetString()!, task.GetProperty("target").GetString()!));
            }
            var next = page.GetProperty("next_after").GetString();
            if (next is not null && after is not null && CompareCatalogCursor(next, after) <= 0)
                throw new InvalidDataException("Catalog cursor did not advance.");
            after = next;
        } while (after is not null);
        return new(entries, problems);
    }
    internal static int CompareCatalogCursor(string left, string right)
    {
        // Python compares Unicode scalar values; UTF-16 ordinal differs for supplementary characters.
        var a = left.EnumerateRunes().Select(r => r.Value).ToArray();
        var b = right.EnumerateRunes().Select(r => r.Value).ToArray();
        for (var i = 0; i < Math.Min(a.Length, b.Length); i++)
            if (a[i] != b[i]) return a[i].CompareTo(b[i]);
        return a.Length.CompareTo(b.Length);
    }
    public async Task<bool> ExitAsync(Func<Task<bool>> confirm)
    {
        if (ExitPending) return false;
        if (Busy && !await confirm()) return false;
        ExitPending = true;
        Changed?.Invoke();
        await idle.Task; // Includes presentation of the factual result, not just pipe completion.
        if (startup is not null)
        {
            if (client.Observation.Initialized && client.Observation.TransportHealthy)
            {
                try
                {
                    var request = await client.RequestAsync("worker.shutdown");
                    var response = await request.Terminal;
                    if (response.Payload.GetProperty("phase").GetString() != "terminal")
                        throw new IOException("Worker did not acknowledge idle shutdown.");
                }
                catch (WorkerTransportUncertainException) { /* Observe process completion below; never force kill. */ }
            }
            // On channel loss, keep supervising process completion; never kill/restart it.
            await client.Completion;
            await client.DisposeAsync();
        }
        return true;
    }
}

#if !PACKAGING_POC && !PACKAGING_WORKER_POC && !PORTABLE_RELEASE
public static class DesktopDevelopment
{
    public static WorkerDevelopmentLaunch Launch => new(
        @"C:\Users\sakur\anaconda3\envs\mirrorly-gui-dev\python.exe",
        typeof(DesktopDevelopment).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>().Single(a => a.Key == "CheckoutRoot").Value!);
    public static GuiDataPaths Paths
    {
        get
        {
#if DEBUG
            var args = Environment.GetCommandLineArgs();
            var index = Array.IndexOf(args, "--test-data-root");
            if (index >= 0) return new(args[index + 1]);
            // Explicit isolated GUI smoke-test data base, never a portable task setting.
            if (Environment.GetEnvironmentVariable("MIRRORLY_GUI_TEST_DATA") is { Length: > 0 } path) return new(path);
#endif
            return GuiDataPaths.ForCurrentUser();
        }
    }
}
#endif
