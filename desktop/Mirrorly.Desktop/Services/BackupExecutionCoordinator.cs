using System.Text.Json;

namespace Mirrorly.Desktop.Services;

public enum BackupGuiState { Idle, Running, AwaitingResumeDecision, Terminal, TransportUncertain }
public enum BackupTaskRunState { Idle, Running, Queued }
public enum BackupQueueAttention { None, TransportUncertain, AdmissionRejected, UnreportedTerminal }

public sealed record BackupOperationResult(string TaskSelector, string? RequestId, string? OperationId,
    string Outcome, string? CommitState, string? SnapshotId, string? Stage, string TechnicalDetails,
    string? AdmissionCode);

// The only GUI FIFO owner. Entries are durable selectors, never copied Python task truth.
public sealed class BackupExecutionCoordinator
{
    private readonly IDesktopSession session;
    private readonly SynchronizationContext? ui;
    private readonly object sync = new();
    private readonly List<string> queued = [];
    private readonly Dictionary<string, BackupOperationResult> results = new(StringComparer.Ordinal);
    private bool starting;

    public BackupExecutionCoordinator(IDesktopSession session)
    {
        this.session = session;
        ui = SynchronizationContext.Current;
        session.Changed += OnSessionChanged;
    }

    public BackupGuiState State { get; private set; } = BackupGuiState.Idle;
    public BackupQueueAttention QueueAttention { get; private set; }
    public string? TaskSelector { get; private set; }
    public BackupOperationResult? Result { get; private set; }
    public IReadOnlyList<string> QueuedSelectors { get { lock (sync) return queued.ToArray(); } }
    public bool HasActiveBackup => State is BackupGuiState.Running or BackupGuiState.AwaitingResumeDecision;
    public Func<bool>? RestoreBlocksScheduling { get; set; }
    public Func<bool>? RestoreOutcomeUncertain { get; set; }
    public bool RestoreInProgress => RestoreBlocksScheduling?.Invoke() == true;
    public bool RestoreUncertain => RestoreOutcomeUncertain?.Invoke() == true;
    public void RefreshAdmission() => Notify();
    public bool CanStart => !starting && !HasActiveBackup && QueueAttention == BackupQueueAttention.None &&
        !RestoreInProgress && !session.Busy && !session.ExitPending && SessionHealthy;
    private bool SessionHealthy => session is not DesktopSession desktop ||
        desktop.Observation is { Initialized: true, TransportHealthy: true };
    public Func<ResumeInteraction, Task<ResumeAnswer>>? ResumePrompt { get; set; }
    public event Action? Changed;
    public event Action<WorkerAdmission>? BackupAdmitted;

    public BackupTaskRunState TaskState(string selector)
    {
        lock (sync)
        {
            if (HasActiveBackup && TaskSelector == selector) return BackupTaskRunState.Running;
            return queued.Contains(selector, StringComparer.Ordinal) ? BackupTaskRunState.Queued : BackupTaskRunState.Idle;
        }
    }

    public BackupOperationResult? ResultFor(string selector)
    {
        lock (sync) return results.GetValueOrDefault(selector);
    }

    public bool CanSchedule(string selector)
    {
        lock (sync)
        {
            if (string.IsNullOrWhiteSpace(selector) || session.ExitPending || RestoreInProgress || QueueAttention != BackupQueueAttention.None ||
                !SessionHealthy || queued.Contains(selector, StringComparer.Ordinal) ||
                HasActiveBackup && TaskSelector == selector) return false;
            if (results.TryGetValue(selector, out var previous) &&
                (previous.CommitState == "unknown" || previous.Outcome is "unreported" or "transport_uncertain")) return false;
            // A terminal-to-next-dispatch gap still belongs to the existing FIFO.
            return HasActiveBackup || queued.Count > 0 || !starting && !session.Busy;
        }
    }

    public bool RemoveQueued(string selector)
    {
        bool removed;
        lock (sync) removed = queued.Remove(selector);
        if (removed) Notify();
        return removed;
    }

    public void ClearQueuedForExit()
    {
        bool changed;
        lock (sync) { changed = queued.Count != 0; queued.Clear(); }
        if (changed) Notify();
    }

    private void OnSessionChanged()
    {
        if (session.ExitPending) ClearQueuedForExit();
        Notify();
    }

    private void Notify()
    {
        if (ui is not null && SynchronizationContext.Current != ui) ui.Post(_ => Changed?.Invoke(), null);
        else Changed?.Invoke();
    }

    public async Task<ResumeAnswer> ResolveResumeAsync(ResumeInteraction interaction)
    {
        if (State != BackupGuiState.Running || TaskSelector is null) return ResumeAnswer.Unavailable;
        State = BackupGuiState.AwaitingResumeDecision; Notify();
        try { return ResumePrompt is null ? ResumeAnswer.Unavailable : await ResumePrompt(interaction); }
        catch (Exception) { return ResumeAnswer.Unavailable; }
        finally
        {
            if (State == BackupGuiState.AwaitingResumeDecision) { State = BackupGuiState.Running; Notify(); }
        }
    }

    // An idle request executes now. A different task requested during Running is admitted to the GUI FIFO only.
    public Task<bool> StartAsync(string selector, Func<ISetupApi, Task>? refresh = null)
    {
        lock (sync)
        {
            if (!CanSchedule(selector)) return Task.FromResult(false);
            if (HasActiveBackup || queued.Count > 0)
            {
                queued.Add(selector);
                Notify();
                return Task.FromResult(true);
            }
            starting = true;
        }
        var first = ExecuteOneAsync(selector, refresh);
        _ = DrainAfterAsync(first, refresh);
        return first;
    }

    private async Task DrainAfterAsync(Task<bool> first, Func<ISetupApi, Task>? refresh)
    {
        if (!await first) return;
        while (true)
        {
            string next;
            lock (sync)
            {
                if (session.ExitPending || QueueAttention != BackupQueueAttention.None || queued.Count == 0) return;
                next = queued[0];
                queued.RemoveAt(0);
                starting = true;
                TaskSelector = next;
                State = BackupGuiState.Running;
            }
            Notify();
            if (await ExecuteOneAsync(next, refresh)) continue;
            // An unconfirmed admission leaves the not-yet-started item at the FIFO head.
            lock (sync) queued.Insert(0, next);
            Notify();
            return;
        }
    }

    private async Task<bool> ExecuteOneAsync(string selector, Func<ISetupApi, Task>? refresh)
    {
        TaskSelector = selector; Result = null;
        State = BackupGuiState.Running; Notify();
        var admittedLocally = false;
        var applicationTerminal = false;
        try
        {
            await session.RunAsync(async api =>
            {
                admittedLocally = true;
                WorkerReply terminal;
                try { terminal = await api.BackupAsync(selector, admission => {
                    // Only the worker's accepted response confirms admission. Local Running is optimistic.
                    try { BackupAdmitted?.Invoke(admission); }
                    catch (Exception) { /* Decoration cannot change Backup outcome. */ }
                }); }
                catch (Exception error)
                {
                    Record(new(selector, null, null, "transport_uncertain", null, null, null, error.ToString(), null));
                    QueueAttention = BackupQueueAttention.TransportUncertain;
                    State = BackupGuiState.TransportUncertain; Notify();
                    return;
                }
                try
                {
                    Result = Interpret(selector, terminal);
                    applicationTerminal = Result.Outcome is not ("rejected" or "unreported");
                    if (!applicationTerminal)
                        QueueAttention = Result.Outcome == "rejected" ? BackupQueueAttention.AdmissionRejected : BackupQueueAttention.UnreportedTerminal;
                }
                catch (Exception error)
                {
                    Result = new(selector, terminal.RequestId, terminal.OperationId, "unreported", null, null, null,
                        "Unusable terminal response: " + error, null);
                    QueueAttention = BackupQueueAttention.UnreportedTerminal;
                }
                Record(Result);
                State = BackupGuiState.Terminal; Notify();
                if (applicationTerminal && refresh is not null)
                {
                    try { await refresh(api); }
                    catch (Exception error)
                    {
                        Record(Result with { TechnicalDetails = Result.TechnicalDetails + "\nSummary refresh: " + error });
                        Notify();
                    }
                }
            });
        }
        catch (InvalidOperationException) when (!admittedLocally)
        {
            QueueAttention = BackupQueueAttention.AdmissionRejected;
            State = BackupGuiState.Idle; Result = null; Notify();
        }
        catch (Exception error)
        {
            Record(new(selector, null, null, "transport_uncertain", null, null, null, error.ToString(), null));
            QueueAttention = BackupQueueAttention.TransportUncertain;
            State = BackupGuiState.TransportUncertain; Notify();
        }
        finally { starting = false; Notify(); }
        return applicationTerminal;
    }

    private void Record(BackupOperationResult value)
    {
        Result = value;
        lock (sync) results[value.TaskSelector] = value;
    }

    private static BackupOperationResult Interpret(string selector, WorkerReply reply)
    {
        var payload = reply.Payload;
        var phase = payload.GetProperty("phase").GetString();
        var error = payload.GetProperty("error");
        var result = payload.GetProperty("result");
        if (phase == "rejected")
            return new(selector, reply.RequestId, null, "rejected", null, null, null,
                error.ToString(), error.GetProperty("code").GetString());
        if (phase != "terminal") throw new InvalidDataException("Backup response is not terminal.");
        var facts = result.ValueKind == JsonValueKind.Object && result.TryGetProperty("facts", out var value) ? value : default;
        static string? Field(JsonElement item, string key) => item.ValueKind == JsonValueKind.Object &&
            item.TryGetProperty(key, out var field) && field.ValueKind == JsonValueKind.String ? field.GetString() : null;
        var outcome = Field(result, "outcome") ?? "unreported";
        return new(selector, reply.RequestId, reply.OperationId, outcome, Field(facts, "commit_state"),
            Field(facts, "snapshot_id"), Field(error, "stage"), payload.ToString(), Field(error, "code"));
    }
}
