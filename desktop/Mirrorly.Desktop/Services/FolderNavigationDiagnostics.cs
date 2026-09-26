using System.Runtime.CompilerServices;

namespace Mirrorly.Desktop.Services;

// Evidence only: no token returned here may be used as a business-state guard.
internal sealed class FolderNavigationDiagnostics(string title, Func<string> modelSnapshot, BoundedSetupLog? testLog = null)
{
    private readonly string pane = title switch { "Source" => "Source", "Backup location" => "Backup", _ => "Other" };
    private readonly AsyncLocal<long> request = new();
    private long navigation, selection, focus, latestNavigation;
    internal Func<string, string>? UiSnapshot { get; set; }
    internal bool Enabled => testLog?.Enabled ?? SetupDiagnostics.Enabled;
    internal long LatestNavigation => latestNavigation;
    internal static string ObjectId(object? value) => value is null ? "none" : RuntimeHelpers.GetHashCode(value).ToString("X8");
    internal long NextSelection() => Enabled ? Interlocked.Increment(ref selection) : 0;
    internal long NextFocus() => Enabled ? Interlocked.Increment(ref focus) : 0;
    internal long NavigationStarted()
    {
        if (!Enabled) return 0;
        var id = request.Value != 0 ? request.Value : Interlocked.Increment(ref navigation);
        latestNavigation = id;
        return id;
    }
    internal RequestScope BeginRequest(string kind, bool? rowResolved = null)
    {
        if (!Enabled) return new(this, 0, 0);
        var prior = request.Value;
        var id = Interlocked.Increment(ref navigation);
        request.Value = id;
        Record("N0 request", () => $"nav={id} kind={kind} row_resolved={rowResolved?.ToString() ?? "na"}");
        return new(this, prior, id);
    }
    internal sealed class RequestScope(FolderNavigationDiagnostics owner, long prior, long sequence) : IDisposable
    {
        internal long Sequence => sequence;
        public void Dispose() { if (sequence != 0) owner.request.Value = prior; }
    }
    internal void Record(string point, Func<string> facts)
    {
        if (!Enabled) return;
        string Snapshot()
        {
            string ui;
            try { ui = UiSnapshot?.Invoke(point) ?? "HasThreadAccess=unavailable ui=unavailable"; }
            catch (Exception error) { ui = $"ui=unavailable exception={error.GetType().Name} hresult=0x{error.HResult:X8}"; }
            return $"pane={pane} instance={ObjectId(this)} {facts()} {modelSnapshot()} | {ui}";
        }
        if (testLog is not null) testLog.Record(point, Snapshot);
        else SetupDiagnostics.RecordNavigation(point, Snapshot);
    }
}
