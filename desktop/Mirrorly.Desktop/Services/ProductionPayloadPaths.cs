using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace Mirrorly.Desktop.Services;

public abstract record WorkerLaunch;

// Internal program paths only. No task paths, persistence, PATH lookup, or cwd lookup.
public sealed class ProductionPayloadPaths
{
    public string AppRoot { get; }
    public string GuiDirectory { get; }
    public string Interpreter => Path.Combine(AppRoot, "python", "python.exe");
    public string WorkerDirectory => Path.Combine(AppRoot, "worker");
    public string HostPath => Path.Combine(WorkerDirectory, "mirrorly", "worker", "payload_launch.py");
    public static ProductionPayloadPaths Current() => new(AppContext.BaseDirectory);
    public ProductionPayloadPaths(string guiDirectory)
    {
        if (!Path.IsPathFullyQualified(guiDirectory)) throw new ArgumentException("Payload GUI directory must be absolute.");
        GuiDirectory = Path.TrimEndingDirectorySeparator(Path.GetFullPath(guiDirectory));
        var gui = new DirectoryInfo(GuiDirectory);
        if (!gui.Name.Equals("gui", StringComparison.OrdinalIgnoreCase) ||
            gui.Parent is not { } app || !app.Name.Equals("app", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Invalid production payload layout: expected app/gui.");
        AppRoot = app.FullName;
    }
}

public sealed record WorkerPayloadQualification(int ProcessId, string Interpreter, string PythonVersion,
    string Blake3Path, string Blake3Version, IReadOnlyDictionary<string, string> ModuleOrigins);

public sealed record WorkerPayloadLaunch(ProductionPayloadPaths Paths) : WorkerLaunch
{
    public const string PythonVersion = "3.13.15";
    public const string Blake3Version = "1.0.9";
    internal ProcessStartInfo CreateStartInfo()
    {
        foreach (var file in new[] { Paths.Interpreter, Paths.HostPath,
            Path.Combine(Paths.WorkerDirectory, "mirrorly", "__init__.py"),
            Path.Combine(Paths.AppRoot, "python", "python313._pth") })
            if (!File.Exists(file)) throw new IOException("Production payload invalid: required worker/runtime file is missing: " + file);
        var start = new ProcessStartInfo(Paths.Interpreter)
        {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Paths.AppRoot,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardErrorEncoding = new UTF8Encoding(false, false)
        };
        foreach (var argument in new[] { "-I", "-B", "-u", Paths.HostPath,
            "--expected-interpreter", Paths.Interpreter, "--payload-root", Paths.AppRoot })
            start.ArgumentList.Add(argument);
        // Keep normal Windows process variables. _pth/-I enforce import isolation too.
        foreach (var key in start.Environment.Keys.ToArray())
            if (key.StartsWith("PYTHON", StringComparison.OrdinalIgnoreCase) ||
                key.StartsWith("CONDA", StringComparison.OrdinalIgnoreCase) ||
                key.Equals("VIRTUAL_ENV", StringComparison.OrdinalIgnoreCase) ||
                key.Equals("__PYVENV_LAUNCHER__", StringComparison.OrdinalIgnoreCase))
                start.Environment.Remove(key);
        return start;
    }
    internal WorkerPayloadQualification Validate(JsonElement facts, int processId)
    {
        static bool Same(string? actual, string expected) => actual is not null && Path.IsPathFullyQualified(actual) &&
            string.Equals(Path.GetFullPath(actual), Path.GetFullPath(expected), StringComparison.OrdinalIgnoreCase);
        if (facts.GetProperty("mode").GetString() != "payload" ||
            facts.GetProperty("pid").GetInt32() != processId ||
            !Same(facts.GetProperty("executable").GetString(), Paths.Interpreter) ||
            !Same(facts.GetProperty("payload_root").GetString(), Paths.AppRoot) ||
            facts.GetProperty("python_version").GetString() != PythonVersion ||
            facts.GetProperty("architecture").GetString() != "x64" ||
            facts.GetProperty("site_enabled").GetBoolean() || facts.GetProperty("bytecode_writes").GetBoolean() ||
            facts.GetProperty("cli_imported").GetBoolean() ||
            facts.GetProperty("blake3_version").GetString() != Blake3Version ||
            facts.GetProperty("blake3_digest").GetString() != "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85" ||
            facts.GetProperty("protocol_version").Deserialize<ProductionVersion>() != ProductionProtocol.Version ||
            !Same(facts.GetProperty("blake3_path").GetString(), Path.Combine(Paths.AppRoot, "python", "packages", "blake3", "blake3.cp313-win_amd64.pyd")))
            throw new InvalidDataException("Production payload qualification mismatch.");
        var origins = facts.GetProperty("module_origins").Deserialize<Dictionary<string, string>>()!;
        foreach (var name in new[] { "mirrorly", "mirrorly.application.setup", "mirrorly.application.backup",
            "mirrorly.application.restoration", "mirrorly.application.queries", "mirrorly.snapshot",
            "mirrorly.restore", "mirrorly.hashing", "mirrorly.worker.host" })
        {
            var relative = name.Replace('.', Path.DirectorySeparatorChar) + (name == "mirrorly" ? Path.DirectorySeparatorChar + "__init__.py" : ".py");
            if (!origins.TryGetValue(name, out var value) || !Same(value, Path.Combine(Paths.WorkerDirectory, relative)))
                throw new InvalidDataException("Production worker module origin mismatch.");
        }
        var search = facts.GetProperty("search_path").EnumerateArray().Select(x => x.GetString()).ToArray();
        var expected = new[] { Path.Combine(Paths.AppRoot, "python", "python313.zip"),
            Path.Combine(Paths.AppRoot, "python"), Path.Combine(Paths.AppRoot, "python", "packages"), Paths.WorkerDirectory };
        if (search.Length != expected.Length || search.Where((s, i) => !Same(s, expected[i])).Any())
            throw new InvalidDataException("Production worker import search path mismatch.");
        return new(processId, Paths.Interpreter, PythonVersion, facts.GetProperty("blake3_path").GetString()!, Blake3Version, origins);
    }
}
