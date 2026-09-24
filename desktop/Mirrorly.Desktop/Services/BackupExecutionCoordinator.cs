using System.Text.Json;

namespace Mirrorly.Desktop.Services;

public enum BackupGuiState { Idle, Running, AwaitingResumeDecision, Terminal, TransportUncertain }

public sealed record BackupOperationResult(string TaskSelector, string? RequestId, string? OperationId,
    string Outcome, string? CommitState, string? SnapshotId, string? Stage, string TechnicalDetails,
    string? AdmissionCode);

// One GUI-started Backup at a time. This is supervision, not Python Backup policy or the future FIFO.
public sealed class BackupExecutionCoordinator
{
    private readonly IDesktopSession session;
    private readonly SynchronizationContext? ui;
    public BackupExecutionCoordinator(IDesktopSession session)
    {
        this.session = session;
        ui = SynchronizationContext.Current;
        session.Changed += Notify;
    }
    private bool starting;
    public BackupGuiState State { get; private set; } = BackupGuiState.Idle;
    public string? TaskSelector { get; private set; }
    public BackupOperationResult? Result { get; private set; }
    public bool CanStart => !starting && State is not (BackupGuiState.Running or BackupGuiState.AwaitingResumeDecision or BackupGuiState.TransportUncertain)
        && Result?.CommitState != "unknown" && Result?.Outcome != "unreported"
        && !session.Busy && !session.ExitPending
        && (session is not DesktopSession desktop || desktop.Observation is { Initialized: true, TransportHealthy: true });
    public Func<ResumeInteraction, Task<ResumeAnswer>>? ResumePrompt { get; set; }
    public event Action? Changed;

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

    public async Task<bool> StartAsync(string selector, Func<ISetupApi, Task>? refresh = null)
    {
        if (string.IsNullOrWhiteSpace(selector) || !CanStart) return false;
        starting = true; TaskSelector = selector; Result = null;
        State = BackupGuiState.Running; Notify();
        var admittedLocally = false;
        try
        {
            await session.RunAsync(async api =>
            {
                admittedLocally = true;
                WorkerReply terminal;
                try { terminal = await api.BackupAsync(selector); }
                catch (Exception error)
                {
                    // Without a received terminal there is no application commit-state evidence.
                    Result = new(selector, null, null, "transport_uncertain", null, null, null, error.ToString(), null);
                    State = BackupGuiState.TransportUncertain; Notify();
                    return;
                }
                try { Result = Interpret(selector, terminal); }
                catch (Exception error)
                {
                    // A terminal arrived, but its facts could not be read. This is not EOF
                    // and it must not become a safe-to-retry application failure.
                    Result = new(selector, terminal.RequestId, terminal.OperationId, "unreported",
                        null, null, null, "Unusable terminal response: " + error, null);
                }
                State = BackupGuiState.Terminal; Notify();
                if (refresh is not null)
                {
                    try { await refresh(api); }
                    catch (Exception error)
                    {
                        // Reading the durable summary failed; the Backup terminal is still factual.
                        Result = Result with { TechnicalDetails = Result.TechnicalDetails + "\nSummary refresh: " + error };
                        Notify();
                    }
                }
            });
            return true;
        }
        catch (InvalidOperationException) when (!admittedLocally)
        {
            // A local admission race starts no second GUI request.
            State = BackupGuiState.Idle; Result = null; Notify();
            return false;
        }
        catch (Exception error)
        {
            Result = new(selector, null, null, "transport_uncertain", null, null, null, error.ToString(), null);
            State = BackupGuiState.TransportUncertain; Notify();
            return true;
        }
        finally { starting = false; Notify(); }
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
        var facts = result.ValueKind == JsonValueKind.Object && result.TryGetProperty("facts", out var value) ? value : default;
        static string? Field(JsonElement item, string key) => item.ValueKind == JsonValueKind.Object &&
            item.TryGetProperty(key, out var field) && field.ValueKind == JsonValueKind.String ? field.GetString() : null;
        return new(selector, reply.RequestId, reply.OperationId,
            Field(result, "outcome") ?? "unreported", Field(facts, "commit_state"),
            Field(facts, "snapshot_id"), Field(error, "stage"), payload.ToString(), Field(error, "code"));
    }
}
