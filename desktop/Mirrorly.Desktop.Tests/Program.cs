using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

// Deliberately dependency-free executable test harness. Failure returns exit code 1.
var passed = 0;
var failed = 0;
await Test("Envelope round trip", () =>
{
    var result = Protocol.Parse(Protocol.Request("abc", "ping"));
    Check(result.ProtocolVersion == 1 && result.RequestId == "abc" && result.Payload.GetProperty("command").GetString() == "ping");
    return Task.CompletedTask;
});
await Test("Reject malformed, wrong version and invalid correlation", () =>
{
    foreach (var json in new[] { "{", "[]", "{}", "{\"protocol_version\":2,\"message_type\":\"response\",\"request_id\":\"a\",\"payload\":{}}", "{\"protocol_version\":1,\"message_type\":\"response\",\"request_id\":null,\"payload\":{}}" })
    {
        try { Protocol.Parse(json); throw new Exception("Accepted invalid frame."); }
        catch (Exception e) when (e is JsonException or InvalidDataException) { }
    }
    foreach (var value in new[] { "true", "null", "\"1\"", "1.5" })
    {
        var json = Protocol.Request("a", "ping").Replace("\"protocol_version\":1", $"\"protocol_version\":{value}");
        try { Protocol.Parse(json); throw new Exception("Accepted invalid version type."); }
        catch (InvalidDataException) { }
    }
    return Task.CompletedTask;
});
await Test("Fragmented and combined UTF-8 frames", async () =>
{
    var input = Protocol.Request("中文", "ping") + "\n" + Protocol.Request("two", "version") + "\n";
    using var stream = new FragmentedStream(Encoding.UTF8.GetBytes(input));
    var frames = new List<string>();
    await foreach (var frame in Protocol.ReadFramesAsync(stream)) frames.Add(frame);
    Check(frames.Count == 2 && Protocol.Parse(frames[0]).RequestId == "中文");
});
await Test("Reject truncated, oversized and invalid UTF-8 frames", async () =>
{
    foreach (var bytes in new[] { Encoding.UTF8.GetBytes("unfinished"), Encoding.UTF8.GetBytes(new string('x', Protocol.MaxFrameBytes) + "\n"), new byte[] { 0xff, 10 } })
    {
        using var stream = new MemoryStream(bytes);
        try
        {
            await foreach (var frame in Protocol.ReadFramesAsync(stream)) { }
            throw new Exception("Accepted invalid framing.");
        }
        catch (Exception e) when (e is InvalidDataException or DecoderFallbackException) { }
    }
});
await Test("ViewModel exposes disconnected/crashed states and request availability", () =>
{
    var model = new HomeViewModel();
    foreach (var state in Enum.GetValues<WorkerState>())
    {
        model.UpdateStatus(new(state, "detail"));
        Check(model.CanRequest == (state == WorkerState.Connected));
        Check(model.StatusText.Contains(state.ToString()) && model.Detail == "detail");
    }
    return Task.CompletedTask;
});
await Test("Real Python handshake, concurrent correlation, event and orderly shutdown", async () =>
{
    var states = new ConcurrentQueue<WorkerState>();
    var received = new TaskCompletionSource<ProtocolMessage>(TaskCreationOptions.RunContinuationsAsynchronously);
    var client = new FakeWorkerClient();
    client.StatusChanged += state => states.Enqueue(state.State);
    client.EventReceived += message => received.TrySetResult(message);
    await client.StartAsync(PrototypeConfiguration.PythonInterpreter, PrototypeConfiguration.WorkerScript);
    Check(client.Status.State == WorkerState.Connected);
    var replies = await Task.WhenAll(Enumerable.Range(0, 10).Select(_ => client.RequestAsync("ping")));
    Check(replies.Select(r => r.RequestId).Distinct().Count() == 10);
    var version = await client.RequestAsync("version");
    Check(version.Payload.GetProperty("worker").GetString() == "phase1a-fake");
    await client.RequestAsync("test_event");
    Check((await received.Task.WaitAsync(TimeSpan.FromSeconds(5))).Payload.GetProperty("text").GetString()!.Contains("测试"));
    await client.DisposeAsync();
    Check(states.First() == WorkerState.Starting && states.Contains(WorkerState.Connected));
    Check(client.Status.State == WorkerState.Disconnected && client.Status.Detail.Contains("code 0"));
});
await Test("Worker crash fails pending request and persists Crashed", async () =>
{
    await using var client = new FakeWorkerClient();
    var crashed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    client.StatusChanged += state => { if (state.State == WorkerState.Crashed) crashed.TrySetResult(); };
    await client.StartAsync(PrototypeConfiguration.PythonInterpreter, PrototypeConfiguration.WorkerScript);
    try { await client.RequestAsync("test_crash"); throw new Exception("Crash request returned normally."); }
    catch (IOException) { }
    await crashed.Task.WaitAsync(TimeSpan.FromSeconds(5));
    try { await client.PingAsync(); }
    catch (IOException) { }
    Check(client.Status.State == WorkerState.Crashed && client.Status.Detail.Contains("23"));
});
await Test("Missing absolute interpreter remains Disconnected", async () =>
{
    await using var client = new FakeWorkerClient();
    await client.StartAsync(Path.Combine(Path.GetTempPath(), "missing-phase1a-python.exe"), PrototypeConfiguration.WorkerScript);
    Check(client.Status.State == WorkerState.Disconnected);
});
Console.WriteLine($"{passed} passed; {failed} failed.");
return failed == 0 ? 0 : 1;

async Task Test(string name, Func<Task> body)
{
    try { await body(); passed++; Console.WriteLine($"PASS {name}"); }
    catch (Exception error) { failed++; Console.Error.WriteLine($"FAIL {name}: {error}"); }
}
static void Check(bool condition) { if (!condition) throw new Exception("Assertion failed."); }

sealed class FragmentedStream(byte[] data) : MemoryStream(data)
{
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default) =>
        base.ReadAsync(buffer[..Math.Min(buffer.Length, 1)], token);
}
