namespace Mirrorly.Desktop.Services;

public enum RestoreGuiState { Idle, Starting, Running, Terminal, TransportUncertain }

// One app-scoped Restore execution, independent of the GUI Backup FIFO.
public sealed class RestoreExecutionCoordinator
{
    private readonly IDesktopSession session;
    private readonly BackupExecutionCoordinator backup;
    private readonly SynchronizationContext? ui = SynchronizationContext.Current;
    public RestoreGuiState State { get; private set; }
    public RestorePreparedPlanPreview? Plan { get; private set; }
    public string BackupName { get; private set; } = "";
    public RestoreExecutionResult? Result { get; private set; }
    public string TechnicalDetails { get; private set; } = "";
    public WorkerAdmission? Admission { get; private set; }
    public bool RequiresNewPlan { get; private set; }
    public Task Completion { get; private set; } = Task.CompletedTask;
    public bool BlocksBackup => State is RestoreGuiState.Starting or RestoreGuiState.Running or RestoreGuiState.TransportUncertain;
    public bool IsActive => State is RestoreGuiState.Starting or RestoreGuiState.Running;
    public event Action? Changed;

    public RestoreExecutionCoordinator(IDesktopSession session, BackupExecutionCoordinator backup)
    {
        this.session = session;
        this.backup = backup;
        backup.RestoreBlocksScheduling = () => BlocksBackup;
        backup.RestoreOutcomeUncertain = () => State == RestoreGuiState.TransportUncertain;
    }

    private void Notify()
    {
        backup.RefreshAdmission();
        if (ui is not null && SynchronizationContext.Current != ui) ui.Post(_ => Changed?.Invoke(), null);
        else Changed?.Invoke();
    }

    public bool Start(RestorePreparedPlanPreview plan, string backupName)
    {
        if (State != RestoreGuiState.Idle || session.Busy || session.ExitPending ||
            backup.HasActiveBackup || backup.QueuedSelectors.Count > 0 || string.IsNullOrEmpty(plan.PlanId))
            return false;
        Plan = plan;
        BackupName = backupName;
        Result = null;
        Admission = null;
        TechnicalDetails = "";
        RequiresNewPlan = false;
        State = RestoreGuiState.Starting; // Blocks Backup before the first await, not yet a Running claim.
        Notify();
        Completion = ExecuteAsync(plan);
        return true;
    }

    private async Task ExecuteAsync(RestorePreparedPlanPreview plan)
    {
        try
        {
            await session.RunAsync(async api =>
            {
                var terminal = await api.ExecuteRestoreAsync(plan.PlanId,
                    plan.Policy == RestoreConflictPolicy.ReplaceExisting,
                    admission =>
                    {
                        Admission = admission;
                        State = RestoreGuiState.Running;
                        Notify();
                    });
                if ((terminal.Outcome is RestoreExecutionOutcome.Completed or RestoreExecutionOutcome.CompletedWithIssues) &&
                    (terminal.Facts?.SnapshotId != plan.SnapshotId ||
                     !string.Equals(terminal.Facts.Destination, plan.Destination, StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidDataException("Restore result identity differs from the prepared plan.");
                Result = terminal;
                TechnicalDetails = terminal.TechnicalDetails;
                State = terminal.Outcome == RestoreExecutionOutcome.Unreported
                    ? RestoreGuiState.TransportUncertain : RestoreGuiState.Terminal;
                Notify();
            });
        }
        catch (RestoreExecuteRejectedException error)
        {
            // No admitted mutation occurred. Keep the Review, but never retry automatically.
            State = RestoreGuiState.Idle;
            RequiresNewPlan = error.Code == "restore_plan_unavailable";
            TechnicalDetails = error.ToString();
            Notify();
        }
        catch (Exception error)
        {
            // Send/terminal uncertainty cannot be called an application failure.
            State = RestoreGuiState.TransportUncertain;
            TechnicalDetails = error.ToString();
            Notify();
        }
    }

    public void ClearTerminal()
    {
        if (State != RestoreGuiState.Terminal) return;
        State = RestoreGuiState.Idle;
        Result = null;
        Plan = null;
        Admission = null;
        TechnicalDetails = "";
        Notify();
    }
}
