using System.Text;
using Mirrorly.Desktop.Services;

static class NavigationDiagnosticTests
{
    internal static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Navigation diagnostics off never sample pane/model or write", () =>
        {
            var sampled = false;
            var diagnostic = new FolderNavigationDiagnostics("Source", () => { sampled = true; return ""; }, new(false, _ => throw new Exception()));
            diagnostic.UiSnapshot = _ => { sampled = true; return ""; };
            using var request = diagnostic.BeginRequest("double-tap", true);
            check(request.Sequence == 0 && diagnostic.NavigationStarted() == 0 && diagnostic.NextFocus() == 0 && diagnostic.NextSelection() == 0);
            diagnostic.Record("F2", () => { sampled = true; return ""; });
            check(!sampled);
            return Task.CompletedTask;
        });
        await test("Navigation request identity survives await; late focus remains observable without a guard", async () =>
        {
            var lines = new List<string>();
            var diagnostic = new FolderNavigationDiagnostics("Backup location", () => "revision=4", new(true, lines.Add));
            var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            async Task<long> Request()
            {
                using var request = diagnostic.BeginRequest("row-enter", true);
                await gate.Task;
                check(diagnostic.NavigationStarted() == request.Sequence);
                return request.Sequence;
            }
            var pending = Request();
            gate.SetResult();
            var old = await pending;
            using (var newer = diagnostic.BeginRequest("up"))
            {
                check(diagnostic.NavigationStarted() == newer.Sequence && newer.Sequence > old);
                var focus = diagnostic.NextFocus();
                diagnostic.Record("F1 callback entered", () => $"origin_nav={old} latest_nav={diagnostic.LatestNavigation} focus_seq={focus} same_nav={old == diagnostic.LatestNavigation}");
            }
            // Request scope restores ambient identity; direct initialization gets a new ID.
            check(diagnostic.NavigationStarted() > old + 1);
            check(lines.Any(line => line.Contains("pane=Backup") && line.Contains("origin_nav=1 latest_nav=2 focus_seq=1 same_nav=False")));
            check(diagnostic.NextSelection() == 1 && diagnostic.NextSelection() == 2);
        });
        await test("Navigation snapshots are bounded, privacy-safe identities, and sink/read failures cannot escape", () =>
        {
            var lines = new List<string>();
            var diagnostic = new FolderNavigationDiagnostics(@"C:\private\user-title", () => "count=1", new(true, lines.Add));
            diagnostic.UiSnapshot = _ => throw new IOException(@"C:\private\secret.txt");
            using var request = diagnostic.BeginRequest("double-tap", true);
            check(lines.Single().Contains("pane=Other") && lines[0].Contains("exception=IOException") && !lines[0].Contains("private"));
            var value = new object();
            check(FolderNavigationDiagnostics.ObjectId(value) == FolderNavigationDiagnostics.ObjectId(value));
            check(FolderNavigationDiagnostics.ObjectId(null) == "none");
            for (var i = 0; i < 600; i++) diagnostic.Record("F2", () => new string('x', 5000));
            check(lines.Count <= 512 && lines.Sum(Encoding.UTF8.GetByteCount) <= 256 * 1024 && lines.All(line => line.Length <= 4097));
            var broken = new FolderNavigationDiagnostics("Source", () => "", new(true, _ => throw new IOException()));
            using var failedRequest = broken.BeginRequest("back");
            broken.Record("F1", () => throw new Exception());
            check(!broken.Enabled);
            return Task.CompletedTask;
        });
    }
}
