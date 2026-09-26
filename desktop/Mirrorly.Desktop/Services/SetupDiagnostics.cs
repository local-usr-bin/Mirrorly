using System.Diagnostics;
using System.Text;

namespace Mirrorly.Desktop.Services;

// Explicit experiment only. Never log intent, paths, DTOs, text input, or exception text.
internal static class SetupDiagnostics
{
    internal const string Variable = "MIRRORLY_SETUP_DIAGNOSTICS";
    internal static bool OptedIn(string? value) => value == "1";
    private static readonly BoundedSetupLog log = Create();
    internal static bool Enabled => log.Enabled;
    internal static Func<string>? UiSnapshot { get; set; }
    private static BoundedSetupLog Create()
    {
        try
        {
            if (!OptedIn(Environment.GetEnvironmentVariable(Variable))) return new(false, _ => { });
            string? path = null;
            return new(true, line =>
            {
                if (path is null)
                {
                    var directory = Path.Combine(Path.GetTempPath(), "Mirrorly-SetupDiagnostics");
                    Directory.CreateDirectory(directory);
                    var candidate = Path.Combine(directory, $"setup-{Environment.ProcessId}-{Guid.NewGuid():N}.log");
                    using var file = new FileStream(candidate, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                    path = candidate;
                }
                File.AppendAllText(path, line, new UTF8Encoding(false));
            });
        }
        catch { return new(false, _ => { }); }
    }
    internal static void Record(string point, Func<string>? facts = null, Exception? error = null) =>
        log.Record(point, () => $"{facts?.Invoke()} | ui={ReadUi()}", error);
    // Navigation supplies its own bounded pane snapshot; do not also walk the Setup view.
    internal static void RecordNavigation(string point, Func<string> facts) => log.Record(point, facts);
    private static string ReadUi()
    {
        try { return UiSnapshot?.Invoke() ?? "unavailable"; }
        catch (Exception error) { return $"snapshot_unavailable type={error.GetType().FullName} hresult=0x{error.HResult:X8}"; }
    }
}

// Testable sink: one locked writer, no background task, no timers, no retry on failure.
internal sealed class BoundedSetupLog(bool enabled, Action<string> sink)
{
    internal const int MaxRecords = 512, MaxBytes = 256 * 1024, MaxLineCharacters = 4096;
    private readonly object gate = new();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private int records, bytes;
    private bool failed;
    internal bool Enabled => enabled && !failed;
    internal void Record(string point, Func<string> facts, Exception? error = null)
    {
        if (!Enabled) return;
        lock (gate)
        {
            if (failed || records >= MaxRecords || bytes >= MaxBytes) return;
            try
            {
                var text = $"{DateTimeOffset.UtcNow:O} elapsed_ms={clock.ElapsedMilliseconds} pid={Environment.ProcessId} " +
                    $"thread={Environment.CurrentManagedThreadId} sync={SynchronizationContext.Current?.GetType().FullName ?? "null"} " +
                    $"point={point} | {facts()}";
                if (error is not null)
                    text += $" | exception={error.GetType().FullName} hresult=0x{error.HResult:X8} " +
                        $"message=[redacted] message_length={error.Message.Length}";
                text = text.Replace('\r', ' ').Replace('\n', ' ');
                if (text.Length > MaxLineCharacters) text = text[..MaxLineCharacters];
                text += "\n";
                var size = Encoding.UTF8.GetByteCount(text);
                if (bytes + size > MaxBytes) { bytes = MaxBytes; return; }
                sink(text); records++; bytes += size;
            }
            catch { failed = true; } // Evidence failure must never become application failure.
        }
    }
}
