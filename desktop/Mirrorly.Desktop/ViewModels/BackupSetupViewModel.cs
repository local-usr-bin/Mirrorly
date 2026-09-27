using System.ComponentModel;
using System.Text.Json;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public static class SetupPreview
{
    public const double SideBySideAt = 960;
    public static bool StackPanes(double width) => width < SideBySideAt;
    public static string DefaultName(string source)
    {
        var name = Path.GetFileName(Path.TrimEndingDirectorySeparator(source));
        return string.IsNullOrWhiteSpace(name) ? source.TrimEnd('\\', ':') + " drive" : name;
    }
}
public enum SetupState { Choose, Unchecked, Checking, Ready, ApprovalRequired, Blocked, Unavailable, Creating, Rejected, Partial, Unknown, Succeeded }
public sealed class BackupSetupViewModel : INotifyPropertyChanged
{
    private readonly IDesktopSession session;
    private string backupName = "", lastDefaultName = "";
    private bool working, unsafeToRetry;
    public BackupSetupViewModel(IFolderBrowserService folders, IDesktopSession session)
    {
        this.session = session;
        Source = new("Source", folders); Destination = new("Backup location", folders);
        Source.PropertyChanged += (_, _) => { UpdateDefaultName(); Changed(); };
        Destination.PropertyChanged += (_, _) => Changed();
        session.Changed += () => {
            if (State == SetupState.Creating && session is DesktopSession { WaitingForTerminal: true })
                Message = "Still waiting for a confirmed setup result. The request has not been repeated.";
            Changed();
        };
    }
    public FolderBrowserViewModel Source { get; }
    public FolderBrowserViewModel Destination { get; }
    public bool IsReview { get; private set; }
    public SetupState State { get; private set; } = SetupState.Choose;
    public string Message { get; private set; } = "Choose folders to begin.";
    public string TechnicalDetails { get; private set; } = "";
    internal string? CreatedConfigPath { get; private set; }
    public string RepositoryPreview { get; private set; } = "Checked when you review this backup.";
    public bool IsChecking => working;
    public bool CanEdit => !working && !unsafeToRetry && !session.ExitPending;
    public bool CanCheck => IsReview && CanEdit && !session.Busy;
    public bool CanContinue => Source.HasValidSelection && Destination.HasValidSelection && CanEdit && !session.Busy;
    public bool CanCreate => !working && !session.Busy && !session.ExitPending && !unsafeToRetry && State is SetupState.Ready or SetupState.ApprovalRequired;
    public string BackupName
    {
        get => backupName;
        set { if (backupName == value || !CanEdit) return; backupName = value; if (IsReview) Set(SetupState.Unchecked, "Name changed. Check setup again before creating."); Changed(); }
    }
    public bool SamePathWarning => Source.SelectedPath is { } source && Destination.SelectedPath is { } target && string.Equals(source, target, StringComparison.OrdinalIgnoreCase);
    public event PropertyChangedEventHandler? PropertyChanged;
    public Func<ISetupApi, Task>? Created { get; set; }
    private void Changed() => PropertyChanged?.Invoke(this, new(null));
    private void Set(SetupState state, string message) { State = state; Message = message; Changed(); }
    private void UpdateDefaultName()
    {
        if (Source.SelectedPath is not { } path) return;
        var name = SetupPreview.DefaultName(path);
        if (backupName.Length == 0 || backupName == lastDefaultName) backupName = name;
        lastDefaultName = name;
    }
    // GUI permits the existing warn policy; non-NTFS copying still requires explicit approval.
    private SetupPreflightIntent Intent() => new(BackupName, Source.SelectedPath!, Destination.SelectedPath!, "warn");
    public Task InitializeAsync() => Task.WhenAll(Source.InitializeAsync(), Destination.InitializeAsync());
    public async Task ContinueAsync()
    {
        if (!CanContinue) return;
        working = true; Changed();
        await Task.WhenAll(Source.RevalidateAsync(), Destination.RevalidateAsync());
        working = false;
        SetupDiagnostics.Record("T1 revalidation complete", DiagnosticState);
        if (!CanContinue) { Changed(); return; }
        IsReview = true;
        await CheckAsync();
    }
    public async Task CheckAsync()
    {
        if (!CanCheck) return;
        working = true; TechnicalDetails = "";
        Set(SetupState.Checking, "Checking setup…");
        SetupDiagnostics.Record("T2 review checking", DiagnosticState);
        var intent = Intent();
        try
        {
            await session.RunAsync(async api =>
            {
                var reply = await api.PreflightAsync(intent);
                SetupDiagnostics.Record("T4 preflight returned to ViewModel", DiagnosticState);
                TechnicalDetails = reply.ToString();
                if (Rejected(reply)) return;
                if (reply.GetProperty("error").ValueKind != JsonValueKind.Null) { Set(SetupState.Blocked, "Mirrorly couldn't check this setup. View technical details, then check again."); return; }
                var facts = reply.GetProperty("result").GetProperty("preflight");
                RepositoryPreview = facts.GetProperty("repository_path").GetString()!;
                if (facts.GetProperty("problem").ValueKind != JsonValueKind.Null)
                    Set(SetupState.Blocked, "Setup cannot continue with these details. Review the name and folders. View technical details for the reported problem.");
                else if (facts.GetProperty("copy_mode_approval_required").GetBoolean())
                    Set(SetupState.ApprovalRequired, "This location requires your approval to use full-file copies.");
                else Set(SetupState.Ready, "Ready to set up. This creates the backup repository and configuration; it does not back up files yet.");
            });
        }
        catch (Exception error) { SetupDiagnostics.Record("CheckAsync catch", DiagnosticState, error); TechnicalDetails = error.ToString(); Set(SetupState.Unavailable, "Mirrorly's service is unavailable. View technical details. No create request was made by this check."); }
        finally { working = false; Changed(); SetupDiagnostics.Record("T5 final ViewModel state", DiagnosticState); }
    }
    internal string DiagnosticState() => $"IsReview={IsReview} State={State} working={working}";
    private bool Rejected(JsonElement reply)
    {
        if (reply.GetProperty("phase").GetString() != "rejected") return false;
        var code = reply.GetProperty("error").GetProperty("code").GetString();
        Set(SetupState.Rejected, code switch
        {
            "busy" => "Mirrorly is currently handling another operation. Check again when it finishes.",
            "mutation_gate_unavailable" => "Another Mirrorly session is currently responsible for changes. Close that session normally, then check again.",
            _ => "Mirrorly did not accept this request. View technical details before checking again."
        });
        return true;
    }
    public async Task CreateAsync(Func<Task<bool>> approve)
    {
        if (!CanCreate) return;
        var requiresApproval = State == SetupState.ApprovalRequired;
        var intent = Intent();
        CreatedConfigPath = null;
        working = true; Changed();
        try
        {
            await session.RunAsync(async api =>
            {
                var approved = false;
                if (requiresApproval)
                {
                    approved = await approve();
                    if (!approved || session.ExitPending) return;
                }
                Set(SetupState.Creating, "Setting up backup… Please wait for a confirmed result.");
                var reply = await api.CreateAsync(new(intent.task_name, intent.source, intent.target, intent.filesystem_policy, approved));
                TechnicalDetails = reply.ToString();
                // Only this definite no-write decision permits another explicitly approved request.
                if (DecisionRequired(reply))
                {
                    Set(SetupState.ApprovalRequired, "The location now requires approval for full-file copies. Nothing was created by this request.");
                    if (session.ExitPending || !await approve() || session.ExitPending) return;
                    Set(SetupState.Creating, "Setting up backup…");
                    reply = await api.CreateAsync(new(intent.task_name, intent.source, intent.target, intent.filesystem_policy, true));
                    TechnicalDetails = reply.ToString();
                    if (DecisionRequired(reply)) { Set(SetupState.ApprovalRequired, "Approval is still required. Check setup again."); return; }
                }
                if (Rejected(reply)) return;
                var result = reply.GetProperty("result");
                if (reply.GetProperty("error").ValueKind == JsonValueKind.Null && result.GetProperty("outcome").GetString() == "succeeded")
                {
                    var createdSetup = result.GetProperty("setup");
                    CreatedConfigPath = createdSetup.TryGetProperty("config_path", out var configPath) &&
                        configPath.ValueKind == JsonValueKind.String ? configPath.GetString() : null;
                    Set(SetupState.Succeeded, "Backup set up. No files have been backed up by setup.");
                    IsReview = false;
                    Source.ClearSelection(); Destination.ClearSelection();
                    backupName = ""; lastDefaultName = "";
                    RepositoryPreview = "Checked when you review this backup.";
                    if (Created is not null) await Created(api); // Catalog refresh before supervision is released.
                    return;
                }
                var facts = result.TryGetProperty("setup", out var setup) ? setup : default;
                var repo = Known(facts, "repository_initialized");
                var config = Known(facts, "config_written");
                unsafeToRetry = repo != false || config != false;
                if (repo == true)
                    Set(SetupState.Partial, "The backup repository was created, but Mirrorly could not finish saving the Backup configuration. It is not rolled back automatically. The configuration may be incomplete; do not create it again blindly.");
                else if (repo is null || config is null)
                    Set(SetupState.Unknown, "Setup did not finish. Mirrorly cannot confirm the complete filesystem outcome. Do not create this backup again before checking the outcome.");
                else Set(SetupState.Blocked, "Setup did not complete before repository initialization. Review the details before checking setup again.");
            });
        }
        catch (Exception error)
        {
            if (State == SetupState.Succeeded)
            {
                TechnicalDetails = error.ToString();
                Message = "Backup set up, but Mirrorly couldn't refresh the display. Return Home and refresh the configured backups.";
                return;
            }
            TechnicalDetails = error.ToString(); unsafeToRetry = true;
            Set(SetupState.Unknown, "Mirrorly lost the connection before it could confirm whether setup finished. Creating the same backup again is not known to be safe. View technical details.");
        }
        finally { working = false; Changed(); }
    }
    private static bool? Known(JsonElement facts, string name) => facts.ValueKind == JsonValueKind.Object && facts.TryGetProperty(name, out var v) && v.ValueKind is JsonValueKind.True or JsonValueKind.False ? v.GetBoolean() : null;
    private static bool DecisionRequired(JsonElement reply) => reply.GetProperty("error").ValueKind == JsonValueKind.Object && reply.GetProperty("error").GetProperty("code").GetString() == "copy_mode_approval_required";
    public void Back() { if (!CanEdit) return; IsReview = false; Set(SetupState.Choose, "Choose folders to begin."); }
}
