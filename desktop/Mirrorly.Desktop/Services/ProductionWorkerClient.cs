using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace Mirrorly.Desktop.Services;

// Development launch inputs are supplied by the caller, never a portable business config.
public sealed record WorkerDevelopmentLaunch(string Interpreter, string Checkout)
{
    internal string? TestHostPath { get; init; }
    internal Dictionary<string, string>? TestEnvironment { get; init; }
    public string HostPath => TestHostPath ?? Path.Combine(Checkout, "src", "mirrorly", "worker", "launch.py");
}
public sealed record SetupPreflightInput(string task_name, string source, string target, string config_root, string filesystem_policy = "strict");
public sealed record SetupPreflightIntent(string task_name, string source, string target, string filesystem_policy = "strict");
public sealed record SetupCreateInput(string task_name, string source, string target, string config_root, string filesystem_policy, bool copy_mode_approved);
public sealed record SetupCreateIntent(string task_name, string source, string target, string filesystem_policy, bool copy_mode_approved);
public sealed record BackupRunInput(string config_root, string? task, bool dry_run, bool full_hash, string[] exclude);
public sealed record BackupRunIntent(string? task, bool dry_run = false, bool full_hash = false, string[]? exclude = null);
public sealed record SnapshotListInput(string config_root, string task, string? after, int limit);
public sealed record ResumeInteraction(string RequestId, string OperationId, string InteractionId, string SnapshotId, string CreatedAt, int DeadlineSeconds);
public sealed record WorkerAdmission(string RequestId, string OperationId);
public enum ResumeAnswer { Resume, DeclineResume, Unavailable }
public sealed record ProductionWorkerObservation(bool ProcessExists, bool TransportHealthy, bool Initialized, string? ActiveOperationId, string Detail);
public sealed class WorkerTransportUncertainException(string message) : IOException(message);

public sealed class WorkerRequest
{
    internal WorkerRequest(string id, bool application) { RequestId = id; IsApplication = application; }
    public string RequestId { get; }
    internal bool IsApplication { get; }
    public string? OperationId { get; internal set; }
    internal readonly TaskCompletionSource<ProductionMessage> Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
    internal readonly TaskCompletionSource<WorkerAdmission?> Admission = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Task<ProductionMessage> Terminal => Completion.Task;
    // False after transport loss or a wait timeout without a received response.
    // True means a response is available, not that the application succeeded.
    public bool ResponseAvailable => Completion.Task.IsCompletedSuccessfully;
    // A wait timeout does not remove correlation, cancel the operation or resend it.
    public Task<ProductionMessage> WaitAsync(TimeSpan timeout) => Terminal.WaitAsync(timeout);
}

public sealed class ProductionWorkerClient : IAsyncDisposable
{
    private readonly ConcurrentDictionary<string, WorkerRequest> pending = new();
    private readonly SemaphoreSlim writes = new(1);
    private readonly ConcurrentQueue<ProductionMessage> notices = new();
    // Caller dispatch is always off the stdout reader. Normal GUI does not bind this yet.
    public Func<ResumeInteraction, Task<ResumeAnswer>>? ResumeResponder { get; set; }
    public ProductionMessage[] DrainNotices()
    {
        var values = new List<ProductionMessage>();
        while (notices.TryDequeue(out var value)) values.Add(value);
        return values.ToArray();
    }
    private readonly TaskCompletionSource<ProductionMessage> hello = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private Process? process;
    private Task? lifetime;
    private string? session;
    private ulong nextId;
    private int disposeStarted;
    private volatile bool initialized, disconnected, processExited;
    private string? activeOperation;
    private string detail = "Not started.";
    public ProductionWorkerObservation Observation => new(process is not null && !processExited, !disconnected && session is not null, initialized, activeOperation, detail);
    public Task Completion => lifetime ?? Task.CompletedTask;

    public async Task StartAsync(WorkerDevelopmentLaunch launch)
    {
        if (process is not null) throw new InvalidOperationException("Use a new client for a new session; no automatic restart.");
        if (!Path.IsPathFullyQualified(launch.Interpreter) || !Path.IsPathFullyQualified(launch.Checkout) ||
            !File.Exists(launch.Interpreter) || !File.Exists(launch.HostPath))
            throw new ArgumentException("Explicit interpreter, checkout and worker host must exist.");
        var start = new ProcessStartInfo(launch.Interpreter)
        {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = launch.Checkout,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardErrorEncoding = new UTF8Encoding(false, false)
        };
        foreach (var argument in new[] { "-I", "-u", launch.HostPath, "--expected-interpreter", launch.Interpreter, "--expected-checkout", launch.Checkout })
            start.ArgumentList.Add(argument);
        // Internal process seam for tests, never protocol methods or GUI business input.
        if (launch.TestEnvironment is not null)
            foreach (var pair in launch.TestEnvironment) start.Environment[pair.Key] = pair.Value;
        process = Process.Start(start) ?? throw new IOException("Worker did not start.");
        var stdout = ReadOutputAsync(process);
        var stderr = DrainErrorsAsync(process);
        lifetime = ObserveLifetimeAsync(process, stdout, stderr);
        try
        {
            var greeting = await hello.Task.WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
            ValidateQualification(greeting.Payload.GetProperty("qualification"), launch);
            var supported = greeting.Payload.GetProperty("supported_versions").EnumerateArray()
                .Any(v => v.GetProperty("major").GetInt32() == 1 && v.GetProperty("minor").GetInt32() == 0);
            if (!supported) throw new InvalidDataException("No supported production version.");
            var initialize = await SendAsync("initialize", new { required_capabilities = Array.Empty<string>() }).ConfigureAwait(false);
            var reply = await initialize.WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
            if (reply.Payload.GetProperty("phase").GetString() != "terminal" || reply.Payload.GetProperty("error").ValueKind != JsonValueKind.Null)
                throw new InvalidDataException("Worker initialization rejected.");
            var result = reply.Payload.GetProperty("result");
            if (result.GetProperty("version").Deserialize<ProductionVersion>() != ProductionProtocol.Version ||
                result.GetProperty("limits").GetProperty("frame_bytes").GetInt32() != ProductionProtocol.FrameBytes ||
                !result.GetProperty("methods").EnumerateArray().Any(m => m.GetString() == "setup.preflight"))
                throw new InvalidDataException("Unexpected negotiated contract.");
            if (!result.GetProperty("capabilities").GetProperty("resume_interaction").GetBoolean() ||
                !result.GetProperty("methods").EnumerateArray().Any(m => m.GetString() == "backup.run"))
                throw new InvalidDataException("Backup/Resume contract was not negotiated.");
            foreach (var capability in new[] { "phase_progress", "item_progress", "byte_progress", "current_item", "cooperative_cancel" })
                if (result.GetProperty("capabilities").GetProperty(capability).GetBoolean())
                    throw new InvalidDataException("Unsupported capability advertisement.");
            initialized = true;
            detail = "Initialized; host responsiveness is not operation progress.";
        }
        catch
        {
            Disconnect("Handshake failed.");
            throw;
        }
    }

    private static void ValidateQualification(JsonElement facts, WorkerDevelopmentLaunch launch)
    {
        static bool Same(string actual, string expected) => string.Equals(
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(actual)),
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(expected)), StringComparison.OrdinalIgnoreCase);
        if (!Same(facts.GetProperty("executable").GetString()!, launch.Interpreter) ||
            !Same(facts.GetProperty("package_path").GetString()!, Path.Combine(launch.Checkout, "src", "mirrorly", "__init__.py")) ||
            !Same(facts.GetProperty("setup_path").GetString()!, Path.Combine(launch.Checkout, "src", "mirrorly", "application", "setup.py")) ||
            facts.GetProperty("cli_imported").GetBoolean())
            throw new InvalidDataException("Worker checkout qualification mismatch.");
        var origin = facts.GetProperty("editable_origin");
        if (!origin.GetProperty("dir_info").GetProperty("editable").GetBoolean() ||
            !Same(new Uri(origin.GetProperty("url").GetString()!).LocalPath, launch.Checkout))
            throw new InvalidDataException("Worker editable origin mismatch.");
    }

    public Task<WorkerRequest> PreflightAsync(SetupPreflightInput request) => RequestAsync("setup.preflight", request);
    public Task<WorkerRequest> PreflightAsync(GuiDataPaths paths, SetupPreflightIntent intent) => PreflightAsync(
        new SetupPreflightInput(intent.task_name, intent.source, intent.target, paths.TaskConfigRoot, intent.filesystem_policy));
    public Task<WorkerRequest> CreateAsync(SetupCreateInput request) => RequestAsync("setup.create", request);
    public Task<WorkerRequest> CreateAsync(GuiDataPaths paths, SetupCreateIntent intent) => CreateAsync(
        new SetupCreateInput(intent.task_name, intent.source, intent.target, paths.TaskConfigRoot, intent.filesystem_policy, intent.copy_mode_approved));
    public Task<WorkerRequest> ListTasksAsync(GuiDataPaths paths, string? after = null) => RequestAsync("tasks.list", new { config_root = paths.TaskConfigRoot, after });
    public Task<WorkerRequest> BackupAsync(BackupRunInput request) => RequestAsync("backup.run", request);
    public Task<WorkerRequest> BackupAsync(GuiDataPaths paths, BackupRunIntent intent) => BackupAsync(
        new BackupRunInput(paths.TaskConfigRoot, intent.task, intent.dry_run, intent.full_hash, intent.exclude ?? []));
    public Task<WorkerRequest> BackupSummaryAsync(GuiDataPaths paths, string selector) =>
        RequestAsync("backup.summary", new { config_root = paths.TaskConfigRoot, task = selector });
    public Task<WorkerRequest> ListSnapshotsAsync(GuiDataPaths paths, string selector, string? after = null, int limit = 16) =>
        RequestAsync("snapshots.list", new SnapshotListInput(paths.TaskConfigRoot, selector, after, limit));

    internal static string AllocateRequestId(ref ulong highest)
    {
        if (highest == ulong.MaxValue) throw new InvalidOperationException("Session request ID space exhausted; no wrapping or automatic restart.");
        return (++highest).ToString(System.Globalization.CultureInfo.InvariantCulture);
    }
    public Task<WorkerRequest> RequestAsync(string method, object? parameters = null)
    {
        if (!initialized) throw new InvalidOperationException("Worker is not initialized.");
        return SendAsync("request", new { method, @params = parameters ?? new { } }, method is "setup.preflight" or "setup.create" or "tasks.list" or "backup.run" or "backup.summary" or "snapshots.list");
    }

    private async Task<WorkerRequest> SendAsync(string kind, object payload, bool application = false)
    {
        await writes.WaitAsync().ConfigureAwait(false);
        try
        {
            if (process is null || process.HasExited || disconnected || session is null)
                throw new WorkerTransportUncertainException("Worker channel is unavailable.");
            if (pending.Count >= 64) throw new InvalidOperationException("Client pending-request limit reached.");
            var id = AllocateRequestId(ref nextId);
            var frame = new ProductionMessage(ProductionProtocol.Identity, ProductionProtocol.Version, kind, session, id, null, null, JsonSerializer.SerializeToElement(payload));
            // A local encoding rejection happens before any bytes/pending application request.
            var bytes = ProductionProtocol.Encode(frame, initialized ? ProductionProtocol.FrameBytes : ProductionProtocol.HandshakeBytes);
            var request = new WorkerRequest(id, application);
            pending[id] = request;
            try
            {
                await process.StandardInput.BaseStream.WriteAsync(bytes).AsTask().WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
                await process.StandardInput.BaseStream.FlushAsync().WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                // Some/all bytes may have been sent. Never retry automatically.
                Disconnect($"Send uncertainty: {error.Message}");
                throw new WorkerTransportUncertainException(detail);
            }
            return request;
        }
        finally { writes.Release(); }
    }

    private async Task ReadOutputAsync(Process child)
    {
        try
        {
            await foreach (var message in ProductionProtocol.ReadFramesAsync(child.StandardOutput.BaseStream,
                () => initialized ? ProductionProtocol.FrameBytes : ProductionProtocol.HandshakeBytes))
            {
                if (session is null)
                {
                    if (message.MessageType != "hello" || message.Version is not null || string.IsNullOrEmpty(message.SessionId) || message.RequestId is not null || message.OperationId is not null || message.InteractionId is not null)
                        throw new InvalidDataException("Expected production hello.");
                    session = message.SessionId;
                    hello.TrySetResult(message);
                    continue;
                }
                if (message.SessionId != session || message.Version != ProductionProtocol.Version)
                    throw new InvalidDataException("Session/version mismatch.");
                if (message.MessageType == "protocol_error") throw new InvalidDataException(message.Payload.ToString());
                if (message.MessageType == "event")
                {
                    if (message.InteractionId is not null || message.RequestId is null || message.OperationId is null)
                        throw new InvalidDataException("Invalid Backup notice correlation.");
                    notices.Enqueue(message);
                    while (notices.Count > 16) notices.TryDequeue(out _);
                    continue;
                }
                if (message.MessageType == "interaction_request")
                {
                    if (message.RequestId is null || message.OperationId is null || message.InteractionId is null ||
                        !pending.TryGetValue(message.RequestId, out var owner) || owner.OperationId != message.OperationId ||
                        message.Payload.GetProperty("kind").GetString() != "backup.resume")
                        throw new InvalidDataException("Invalid Resume interaction correlation.");
                    var interaction = new ResumeInteraction(message.RequestId, message.OperationId, message.InteractionId,
                        message.Payload.GetProperty("snapshot_id").GetString()!,
                        message.Payload.GetProperty("created_at").GetString()!,
                        message.Payload.GetProperty("deadline_seconds").GetInt32());
                    _ = Task.Run(() => AnswerResumeAsync(interaction)); // Keep stdout draining.
                    continue;
                }
                if (message.InteractionId is not null) throw new InvalidDataException("Unexpected interaction ID.");
                if (message.MessageType != "response" || message.RequestId is null || !pending.TryGetValue(message.RequestId, out var request))
                    throw new InvalidDataException("Unexpected correlation/message.");
                var phase = message.Payload.GetProperty("phase").GetString();
                if (phase == "accepted")
                {
                    if (!request.IsApplication || request.OperationId is not null || message.OperationId is null ||
                        message.Payload.GetProperty("result").ValueKind != JsonValueKind.Null || message.Payload.GetProperty("error").ValueKind != JsonValueKind.Null)
                        throw new InvalidDataException("Invalid admission.");
                    request.OperationId = message.OperationId;
                    activeOperation = message.OperationId;
                    request.Admission.TrySetResult(new(message.RequestId, message.OperationId));
                }
                else if (phase is "terminal" or "rejected")
                {
                    if (request.OperationId != message.OperationId) throw new InvalidDataException("Operation mismatch.");
                    if (phase == "terminal" && request.IsApplication && request.OperationId is null)
                        throw new InvalidDataException("Application terminal preceded admission.");
                    if (phase == "rejected" && (request.OperationId is not null || message.Payload.GetProperty("error").GetProperty("application_invoked").GetBoolean()))
                        throw new InvalidDataException("Invalid rejection facts.");
                    if (phase == "rejected") request.Admission.TrySetResult(null);
                    if (activeOperation == request.OperationId) activeOperation = null;
                    request.Completion.TrySetResult(message);
                    pending.TryRemove(message.RequestId, out _);
                }
                else throw new InvalidDataException("Invalid response phase.");
            }
            Disconnect("Worker stdout reached EOF; unreported outcomes are unknown.");
        }
        catch (Exception error)
        {
            Disconnect($"Protocol disconnected: {error.Message}");
            // The session is no longer trusted, but keep draining until the child finishes.
            var discard = new byte[4096];
            try { while (await child.StandardOutput.BaseStream.ReadAsync(discard).ConfigureAwait(false) != 0) { } }
            catch (IOException) { }
        }
    }

    private async Task AnswerResumeAsync(ResumeInteraction interaction)
    {
        var answer = ResumeAnswer.Unavailable;
        try
        {
            if (ResumeResponder is { } responder) answer = await responder(interaction).ConfigureAwait(false);
        }
        catch (Exception) { /* A UI/test handler failure cannot invent a business answer. */ }
        var choice = answer switch
        {
            ResumeAnswer.Resume => "resume",
            ResumeAnswer.DeclineResume => "decline_resume",
            _ => "unavailable",
        };
        await writes.WaitAsync().ConfigureAwait(false);
        try
        {
            if (process is null || process.HasExited || disconnected || session is null) return;
            var frame = new ProductionMessage(ProductionProtocol.Identity, ProductionProtocol.Version,
                "interaction_response", session, interaction.RequestId, interaction.OperationId,
                interaction.InteractionId, JsonSerializer.SerializeToElement(new { kind = "backup.resume", answer = choice }));
            var bytes = ProductionProtocol.Encode(frame);
            await process.StandardInput.BaseStream.WriteAsync(bytes).AsTask().WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
            await process.StandardInput.BaseStream.FlushAsync().WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
        }
        catch (Exception error) { Disconnect($"Resume interaction send uncertainty: {error.Message}"); }
        finally { writes.Release(); }
    }

    private async Task DrainErrorsAsync(Process child)
    {
        var buffer = new char[2048];
        try
        {
            int count;
            while ((count = await child.StandardError.ReadAsync(buffer).ConfigureAwait(false)) != 0)
            {
                // Never let a UI/log subscriber block the diagnostic drain.
                diagnostics.Enqueue(new string(buffer, 0, count));
                while (diagnostics.Count > 32) diagnostics.TryDequeue(out _);
            }
        }
        catch (Exception) { /* Diagnostic loss is not a business/transport result. */ }
    }
    private readonly ConcurrentQueue<string> diagnostics = new();
    public string[] DrainDiagnostics()
    {
        var result = new List<string>();
        while (diagnostics.TryDequeue(out var text)) result.Add(text);
        return result.ToArray();
    }

    private async Task ObserveLifetimeAsync(Process child, Task stdout, Task stderr)
    {
        await child.WaitForExitAsync().ConfigureAwait(false);
        processExited = true;
        await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
        detail = $"Worker exited ({child.ExitCode}); exit code is not an application result.";
    }

    private void Disconnect(string reason)
    {
        disconnected = true;
        initialized = false;
        detail = reason;
        hello.TrySetException(new WorkerTransportUncertainException(reason));
        foreach (var request in pending.Values)
        {
            request.Admission.TrySetResult(null);
            request.Completion.TrySetException(new WorkerTransportUncertainException(reason));
        }
        try { process?.StandardInput.Close(); }
        catch (Exception error) when (error is IOException or InvalidOperationException) { }
    }

    public async Task<ProductionMessage> ShutdownIdleAsync()
    {
        var request = await RequestAsync("worker.shutdown").ConfigureAwait(false);
        return await request.WaitAsync(TimeSpan.FromSeconds(10)).ConfigureAwait(false);
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref disposeStarted, 1) != 0) return;
        if (process is null) return;
        // Disposal is channel loss, never cancellation or a force-kill fallback.
        Disconnect("Client disposed; active application calls may still be finishing.");
        if (lifetime is not null)
        {
            try { await lifetime.WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false); }
            catch (TimeoutException)
            {
                _ = ReleaseAfterExitAsync(); // Keep drains alive; release handles after the child finishes.
                return;
            }
        }
        process.Dispose();
        // No WaitHandle is allocated for writes. Leave its managed lifetime to GC:
        // an in-flight send may still need to release it after channel closure.
    }

    private async Task ReleaseAfterExitAsync()
    {
        try { if (lifetime is not null) await lifetime.ConfigureAwait(false); }
        finally { process?.Dispose(); }
    }
}
