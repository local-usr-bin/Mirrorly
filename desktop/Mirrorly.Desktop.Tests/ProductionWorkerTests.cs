using System.Reflection;
using System.Text;
using System.Text.Json;
using Mirrorly.Desktop.Services;

static class ProductionWorkerTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, string[] args)
    {
        var root = typeof(ProductionWorkerTests).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .Single(a => a.Key == "CheckoutRoot").Value!;
        var interpreter = args.SingleOrDefault() ?? throw new ArgumentException("Pass the explicit mirrorly-gui-dev python.exe as the test harness argument.");
        var launch = new WorkerDevelopmentLaunch(interpreter, root);
        ProductionMessage Message(object payload) => new(ProductionProtocol.Identity, ProductionProtocol.Version, "request", "session", "1", null, null, JsonSerializer.SerializeToElement(payload));

        await test("Production parser separates fake identity and rejects duplicate/depth/collection/type faults", () =>
        {
            var valid = Encoding.UTF8.GetString(ProductionProtocol.Encode(Message(new { text = "中文🌸" }))).TrimEnd();
            check(ProductionProtocol.Parse(valid).Payload.GetProperty("text").GetString() == "中文🌸");
            var nested = "{}";
            for (var index = 0; index < 33; index++) nested = "{\"x\":" + nested + "}";
            foreach (var bad in new[] {
                "{", "[]", valid.Replace("mirrorly.worker", "fake"),
                valid.Replace("\"major\":1", "\"major\":true"),
                valid.Replace("\"major\":1", "\"major\":1,\"major\":1"),
                valid.Replace("\"interaction_id\":null", "\"interaction_id\":false"),
                JsonSerializer.Serialize(Message(new { items = new int[4097] })),
                JsonSerializer.Serialize(Message(JsonDocument.Parse(nested).RootElement)),
            })
            {
                try { ProductionProtocol.Parse(bad); throw new Exception("Accepted invalid production frame."); }
                catch (Exception error) when (error is InvalidDataException or JsonException or InvalidOperationException) { }
            }
            return Task.CompletedTask;
        });

        await test("Production frames split multibyte/coalesce and enforce both byte limits", async () =>
        {
            // Default System.Text.Json escapes non-ASCII; use actual multibyte bytes
            // here so the one-byte stream genuinely splits UTF-8 code points.
            var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(Message(new { text = "中文🌸" }),
                new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }) + "\n");
            check(bytes.Any(value => value >= 128));
            using var stream = new BytewiseStream(bytes.Concat(bytes).ToArray());
            var count = 0;
            await foreach (var frame in ProductionProtocol.ReadFramesAsync(stream, () => ProductionProtocol.FrameBytes)) count++;
            check(count == 2);
            foreach (var limit in new[] { ProductionProtocol.HandshakeBytes, ProductionProtocol.FrameBytes })
            {
                var exact = bytes[..^1].Concat(Encoding.UTF8.GetBytes(new string(' ', limit - bytes.Length))).Append((byte)10).ToArray();
                using var atLimit = new MemoryStream(exact);
                var received = 0;
                await foreach (var frame in ProductionProtocol.ReadFramesAsync(atLimit, () => limit)) received++;
                check(exact.Length == limit && received == 1);
                foreach (var data in new[] { Encoding.UTF8.GetBytes(new string('x', limit)), new byte[] { 0xff, 10 }, bytes[..^1], new byte[] { 10 } })
                {
                    using var invalid = new MemoryStream(data);
                    try { await foreach (var frame in ProductionProtocol.ReadFramesAsync(invalid, () => limit)) { } throw new Exception("Accepted bad framing."); }
                    catch (Exception error) when (error is InvalidDataException or DecoderFallbackException or JsonException) { }
                }
            }
        });

        await test("Production real preflight, qualification, readonly artifacts and idle shutdown", async () =>
        {
            var directory = MakeWorkspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(launch);
                check(client.Observation.Initialized && client.Observation.TransportHealthy && client.Observation.ProcessExists);
                var request = await client.PreflightAsync(Input(directory));
                var response = await request.WaitAsync(TimeSpan.FromSeconds(10));
                check(request.OperationId is not null && response.OperationId == request.OperationId);
                var facts = response.Payload.GetProperty("result").GetProperty("preflight");
                check(facts.GetProperty("inputs_valid").GetBoolean() && facts.GetProperty("problem").ValueKind == JsonValueKind.Null);
                check(!Directory.Exists(Path.Combine(directory, "target", "MirrorlyRepo")) && !Directory.Exists(Path.Combine(directory, "config")));
                var bad = await client.PreflightAsync(Input(directory) with { source = Path.Combine(directory, "missing") });
                check((await bad.Terminal).Payload.GetProperty("result").GetProperty("preflight").GetProperty("problem").GetProperty("stage").GetString() == "inputs");
                var status = await client.RequestAsync("status", new { request_id = request.RequestId });
                check((await status.Terminal).Payload.GetProperty("result").GetProperty("request").GetProperty("terminal_available").GetBoolean());
                var rejected = await client.RequestAsync("setup.create", Input(directory));
                check((await rejected.Terminal).Payload.GetProperty("error").GetProperty("application_invoked").GetBoolean() == false);
                check((await client.ShutdownIdleAsync()).Payload.GetProperty("result").GetProperty("shutdown").GetString() == "idle");
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production late terminal remains correlated; busy never queues or cancels", async () =>
        {
            var directory = MakeWorkspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "blocked", directory));
                var first = await client.PreflightAsync(Input(directory));
                await WaitForFile(Path.Combine(directory, "entered"));
                try { await first.WaitAsync(TimeSpan.FromMilliseconds(20)); throw new Exception("Expected caller timeout."); }
                catch (TimeoutException) { }
                var second = await client.PreflightAsync(Input(directory));
                check((await second.Terminal).Payload.GetProperty("error").GetProperty("code").GetString() == "busy");
                check((await client.ShutdownIdleAsync()).Payload.GetProperty("phase").GetString() == "rejected");
                var ping = await client.RequestAsync("ping");
                check((await ping.Terminal).Payload.GetProperty("result").GetProperty("reply").GetString() == "pong");
                File.WriteAllText(Path.Combine(directory, "release"), "go");
                check((await first.WaitAsync(TimeSpan.FromSeconds(5))).Payload.GetProperty("phase").GetString() == "terminal");
                check(client.Observation.TransportHealthy);
                await client.ShutdownIdleAsync();
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { File.WriteAllText(Path.Combine(directory, "release"), "go"); Directory.Delete(directory, true); }
        });

        await test("Production crash is transport uncertainty, not application failure/replay", async () =>
        {
            var directory = MakeWorkspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "crash", directory));
                var request = await client.PreflightAsync(Input(directory));
                try { await request.WaitAsync(TimeSpan.FromSeconds(5)); throw new Exception("Expected disconnect."); }
                catch (WorkerTransportUncertainException) { }
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                check(!client.Observation.TransportHealthy && !client.Observation.ProcessExists);
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production continuously drains large stderr independently of protocol", async () =>
        {
            var directory = MakeWorkspace();
            try
            {
                await using var client = new ProductionWorkerClient();
                await client.StartAsync(Fixture(launch, "stderr", directory));
                var request = await client.PreflightAsync(Input(directory));
                check((await request.WaitAsync(TimeSpan.FromSeconds(5))).Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                check(client.DrainDiagnostics().Length is > 0 and <= 32);
                await client.ShutdownIdleAsync();
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            finally { Directory.Delete(directory, true); }
        });

        await test("Production client EOF during a call finishes child without cancellation", async () =>
        {
            var directory = MakeWorkspace();
            var client = new ProductionWorkerClient();
            try
            {
                await client.StartAsync(Fixture(launch, "blocked", directory));
                var request = await client.PreflightAsync(Input(directory));
                await WaitForFile(Path.Combine(directory, "entered"));
                var disposal = client.DisposeAsync().AsTask();
                File.WriteAllText(Path.Combine(directory, "release"), "go");
                await disposal.WaitAsync(TimeSpan.FromSeconds(5));
                await client.Completion.WaitAsync(TimeSpan.FromSeconds(5));
                try { await request.Terminal; throw new Exception("Transport loss was not preserved."); }
                catch (WorkerTransportUncertainException) { }
                check(!client.Observation.ProcessExists && !client.Observation.TransportHealthy);
            }
            finally { File.WriteAllText(Path.Combine(directory, "release"), "go"); Directory.Delete(directory, true); }
        });
    }

    static SetupPreflightInput Input(string directory) => new("documents", Path.Combine(directory, "source"), Path.Combine(directory, "target"), Path.Combine(directory, "config"));
    static string MakeWorkspace()
    {
        var path = Path.Combine(Path.GetTempPath(), "mirrorly-worker-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(path, "source"));
        Directory.CreateDirectory(Path.Combine(path, "target"));
        return path;
    }
    static WorkerDevelopmentLaunch Fixture(WorkerDevelopmentLaunch launch, string scenario, string gate) => launch with
    {
        TestHostPath = Path.Combine(launch.Checkout, "tests", "worker_fixture_host.py"),
        TestEnvironment = new() { ["MIRRORLY_TEST_SCENARIO"] = scenario, ["MIRRORLY_TEST_GATE"] = gate }
    };
    static async Task WaitForFile(string path)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!File.Exists(path)) await Task.Delay(10, timeout.Token);
    }
    sealed class BytewiseStream(byte[] data) : MemoryStream(data)
    {
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default) => base.ReadAsync(buffer[..Math.Min(1, buffer.Length)], token);
    }
}
