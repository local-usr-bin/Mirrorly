using System.Reflection;
using System.Text;
using System.Text.Json;

namespace Mirrorly.Desktop.Services;

public sealed record ConfiguredBackup(string Selector, string ConfigPath, string Name, string Source, string RepositoryPath);
public sealed record BackupCatalog(IReadOnlyList<ConfiguredBackup> Tasks, IReadOnlyList<string> Problems);
public sealed record SavedBackupSummary(string Selector, string RepositoryPath, string RepositoryId,
    string? SnapshotId, string? CreatedAt, long? LifecycleSequence, string? SnapshotPath);
public sealed record WorkerReply(string RequestId, string? OperationId, JsonElement Payload);
public interface ISetupApi
{
    Task<JsonElement> PreflightAsync(SetupPreflightIntent intent);
    Task<JsonElement> CreateAsync(SetupCreateIntent intent);
    Task<BackupCatalog> CatalogAsync();
    Task<SavedBackupSummary> BackupSummaryAsync(string selector);
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
public sealed class DesktopSession(WorkerDevelopmentLaunch launch, GuiDataPaths paths) : IDesktopSession, ISetupApi
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
    private async Task<WorkerReply> ReceiveReplyAsync(Func<Task<WorkerRequest>> send, Action<WorkerAdmission>? onAdmitted = null)
    {
        if (!Busy) throw new InvalidOperationException("Application requests require a supervised workflow.");
        await (startup ??= client.StartAsync(launch));
        var request = await send();
        RequestId = request.RequestId;
        Changed?.Invoke();
        var admissionObserver = onAdmitted is null ? Task.CompletedTask : ObserveAdmissionAsync(request, onAdmitted);
        try
        {
            try
            {
                var terminal = await request.WaitAsync(ResponseWait);
                await admissionObserver;
                return new(request.RequestId, request.OperationId, terminal.Payload);
            }
            catch (TimeoutException)
            {
                // Only the wait timed out: keep the request and supervision, never resend.
                WaitingForTerminal = true;
                Changed?.Invoke();
                var terminal = await request.Terminal;
                await admissionObserver;
                return new(request.RequestId, request.OperationId, terminal.Payload);
            }
        }
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
    public Task<JsonElement> PreflightAsync(SetupPreflightIntent intent) => ReceiveAsync(() => client.PreflightAsync(paths, intent));
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
                    task.GetProperty("name").GetString()!, task.GetProperty("source").GetString()!, task.GetProperty("configured_repository_path").GetString()!));
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
