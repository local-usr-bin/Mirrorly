using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;

namespace Mirrorly.Desktop.Services;

public enum WorkerState { Starting, Connected, Disconnected, Crashed }
public sealed record WorkerStatus(WorkerState State, string Detail);

// Phase 1A test peer only. Shutdown/cleanup is NOT a core cancellation contract.
public sealed class FakeWorkerClient : IAsyncDisposable
{
    private readonly ConcurrentDictionary<string, TaskCompletionSource<ProtocolMessage>> pending = new();
    private readonly SemaphoreSlim writes = new(1);
    private readonly object stateGate = new();
    private Process? process;
    private Task? lifetime;
    private volatile bool stopping;
    private volatile bool transportClosed;
    public WorkerStatus Status { get; private set; } = new(WorkerState.Disconnected, "Not started.");
    public event Action<WorkerStatus>? StatusChanged;
    public event Action<ProtocolMessage>? EventReceived;
    public event Action<string>? TechnicalLog;

    public async Task StartAsync(string interpreter, string script)
    {
        if (process is not null) throw new InvalidOperationException("No automatic restart in Phase 1A.");
        SetState(WorkerState.Starting, "Waiting for the first successful ping.");
        try
        {
            if (!Path.IsPathFullyQualified(interpreter) || !Path.IsPathFullyQualified(script))
                throw new ArgumentException("Both interpreter and worker script must be absolute paths.");
            if (!File.Exists(interpreter) || !File.Exists(script))
                throw new FileNotFoundException("Configured Python interpreter or prototype worker script is missing.");
            var info = new ProcessStartInfo(interpreter)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardInputEncoding = new UTF8Encoding(false), StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Path.GetDirectoryName(script)!
            };
            info.ArgumentList.Add("-I"); // Ignore user site packages and Python environment overrides.
            info.ArgumentList.Add("-u");
            info.ArgumentList.Add(script);
            process = Process.Start(info) ?? throw new IOException("Python did not start.");
            lifetime = ObserveLifetimeAsync(process, ReadOutputAsync(process), DrainErrorsAsync(process));
            await PingAsync().ConfigureAwait(false);
        }
        catch (Exception error)
        {
            SetState(WorkerState.Disconnected, error.Message);
        }
    }

    public async Task PingAsync()
    {
        try
        {
            var reply = await RequestAsync("ping").ConfigureAwait(false);
            if (!reply.Payload.TryGetProperty("reply", out var pong) || pong.GetString() != "pong")
                throw new InvalidDataException("Worker did not return pong.");
            SetState(WorkerState.Connected, "Ping succeeded. This is not a continuous health guarantee.");
        }
        catch (Exception error)
        {
            SetState(WorkerState.Disconnected, error.Message);
            throw;
        }
    }

    public async Task<ProtocolMessage> RequestAsync(string command)
    {
        var child = process ?? throw new IOException("Worker has not started.");
        if (child.HasExited || transportClosed) throw new IOException("Worker has exited or IPC is closed.");
        var id = Guid.NewGuid().ToString("N");
        var completion = new TaskCompletionSource<ProtocolMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = completion;
        try
        {
            await writes.WaitAsync().ConfigureAwait(false);
            try
            {
                await child.StandardInput.WriteLineAsync(Protocol.Request(id, command)).ConfigureAwait(false);
                await child.StandardInput.FlushAsync().ConfigureAwait(false);
            }
            finally { writes.Release(); }
            return await completion.Task.WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }
        finally { pending.TryRemove(id, out _); }
    }

    private async Task ReadOutputAsync(Process child)
    {
        var reason = "Worker stdout closed.";
        try
        {
            await foreach (var line in Protocol.ReadFramesAsync(child.StandardOutput.BaseStream))
            {
                var message = Protocol.Parse(line);
                if (message.MessageType == "event") EventReceived?.Invoke(message);
                else if (message.RequestId is not null && pending.TryGetValue(message.RequestId, out var completion))
                {
                    if (message.MessageType == "response") completion.TrySetResult(message);
                    else if (message.MessageType == "error")
                        completion.TrySetException(new InvalidDataException(message.Payload.ToString()));
                    else throw new InvalidDataException("Worker sent an unexpected request.");
                }
                else throw new InvalidDataException("Worker sent an uncorrelated response.");
            }
        }
        catch (Exception error)
        {
            reason = $"IPC disconnected: {error.Message}";
        }
        finally
        {
            transportClosed = true;
            if (!stopping) SetState(WorkerState.Disconnected, reason);
            FailPending("Worker IPC disconnected.");
        }
    }

    private async Task DrainErrorsAsync(Process child)
    {
        // Drain chunks rather than unbounded log lines; no persistent Activity/log store.
        var buffer = new char[2048];
        int count;
        while ((count = await child.StandardError.ReadAsync(buffer).ConfigureAwait(false)) != 0)
            TechnicalLog?.Invoke(new string(buffer, 0, count));
    }

    private async Task ObserveLifetimeAsync(Process child, Task stdout, Task stderr)
    {
        await child.WaitForExitAsync().ConfigureAwait(false);
        await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
        FailPending("Worker process exited.");
        SetState(stopping && child.ExitCode == 0 ? WorkerState.Disconnected : WorkerState.Crashed,
            $"Worker exited with code {child.ExitCode}.");
    }

    private void FailPending(string reason)
    {
        foreach (var completion in pending.Values) completion.TrySetException(new IOException(reason));
    }

    private void SetState(WorkerState state, string detail)
    {
        lock (stateGate)
        {
            if (Status.State == WorkerState.Crashed && state != WorkerState.Crashed) return;
            if (state == WorkerState.Connected && (stopping || transportClosed || process is null || process.HasExited)) return;
            Status = new(state, detail);
            StatusChanged?.Invoke(Status);
        }
    }

    public async ValueTask DisposeAsync()
    {
        stopping = true;
        if (process is null) return;
        if (!process.HasExited)
        {
            try { await RequestAsync("shutdown").ConfigureAwait(false); }
            catch (Exception error) { TechnicalLog?.Invoke($"Fake-worker shutdown: {error.Message}"); }
            process.StandardInput.Close(); // EOF is also a clean exit for this fake peer.
            try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false); }
            catch (TimeoutException)
            {
                // Only this side-effect-free child. Never reuse for the production worker.
                process.Kill();
                await process.WaitForExitAsync().ConfigureAwait(false);
            }
        }
        if (lifetime is not null) await lifetime.ConfigureAwait(false);
        process.Dispose();
        writes.Dispose();
    }
}
