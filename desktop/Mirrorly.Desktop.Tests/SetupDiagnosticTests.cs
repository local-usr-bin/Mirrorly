using System.Diagnostics;
using System.Text;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class SetupDiagnosticTests
{
    internal static async Task Probe()
    {
        var folders = new FakeFolders { Delayed = new TaskCompletionSource<FolderValidation>() };
        var source = new FolderBrowserViewModel("Source", folders);
        var selecting = source.SelectAsync(@"C:\slow");
        using (var request = source.Diagnostics.BeginRequest("double-tap", true))
        {
            await source.NavigateAsync(@"C:\Source");
            if (SetupDiagnostics.Enabled && source.Diagnostics.LatestNavigation != request.Sequence)
                throw new Exception("Navigation diagnostic correlation lost.");
        }
        folders.Delayed.SetResult(new(@"C:\slow", null));
        await selecting;
        if (source.SelectedPath != @"C:\Source" || source.IsValidating || source.IsBrowsing)
            throw new Exception("Diagnostic changed stale-selection behavior.");
        var model = new BackupSetupViewModel(new FakeFolders(), new FakeDesktopSession());
        await model.Source.SelectAsync(@"C:\Source");
        await model.Destination.SelectAsync(@"E:\Backups");
        await model.ContinueAsync();
        if (model.State != SetupState.Ready || !model.IsReview || !model.CanCreate)
            throw new Exception("Diagnostic changed Setup result.");
        Console.WriteLine("Ready; IsReview=True; CanCreate=True");
    }
    internal static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Setup diagnostics require exact opt-in and disabled logging does not sample", () =>
        {
            check(SetupDiagnostics.OptedIn("1"));
            foreach (var value in new string?[] { null, "", "0", "true", " 1" }) check(!SetupDiagnostics.OptedIn(value));
            var log = new BoundedSetupLog(false, _ => throw new Exception("sink called"));
            var sampled = false;
            log.Record("disabled", () => { sampled = true; return ""; });
            check(!sampled);
            return Task.CompletedTask;
        });
        await test("Setup diagnostic sink is bounded, single-line, redacts exception messages and swallows failure", () =>
        {
            var lines = new List<string>();
            var log = new BoundedSetupLog(true, lines.Add);
            log.Record("catch", () => "safe\r\nstate", new IOException(@"private C:\Users\person\Source\secret.txt"));
            check(lines[0].Contains("System.IO.IOException") && lines[0].Contains("message=[redacted]"));
            check(!lines[0].Contains("person") && !lines[0].Contains("secret") && lines[0].Count(c => c == '\n') == 1);
            for (var i = 0; i < 1000; i++) log.Record("bounded", () => new string('x', 5000));
            check(lines.Count <= BoundedSetupLog.MaxRecords && lines.Sum(Encoding.UTF8.GetByteCount) <= BoundedSetupLog.MaxBytes);
            check(lines.All(l => l.Length <= BoundedSetupLog.MaxLineCharacters + 1));
            var failures = 0;
            var broken = new BoundedSetupLog(true, _ => { failures++; throw new IOException(); });
            broken.Record("one", () => ""); broken.Record("two", () => "");
            check(failures == 1 && !broken.Enabled);
            new BoundedSetupLog(true, _ => throw new Exception()).Record("snapshot", () => throw new Exception());
            return Task.CompletedTask;
        });
        await test("Actual Setup flow has identical Ready result with diagnostics off/on; off creates no log", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "Mirrorly-diagnostic-test-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                foreach (var enabled in new[] { false, true })
                {
                    var temp = Path.Combine(root, enabled ? "on" : "off"); Directory.CreateDirectory(temp);
                    var start = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
                    if (string.Equals(Path.GetFileNameWithoutExtension(Environment.ProcessPath), "dotnet", StringComparison.OrdinalIgnoreCase))
                        start.ArgumentList.Add(typeof(SetupDiagnosticTests).Assembly.Location);
                    start.ArgumentList.Add("--setup-diagnostic-probe");
                    start.Environment.Remove(SetupDiagnostics.Variable);
                    if (enabled) start.Environment[SetupDiagnostics.Variable] = "1";
                    start.Environment["TEMP"] = temp; start.Environment["TMP"] = temp;
                    using var process = Process.Start(start)!;
                    var output = await process.StandardOutput.ReadToEndAsync();
                    var errors = await process.StandardError.ReadToEndAsync();
                    await process.WaitForExitAsync();
                    check(process.ExitCode == 0 && errors.Length == 0 && output.Contains("Ready; IsReview=True; CanCreate=True"));
                    var logs = Directory.GetFiles(temp, "*.log", SearchOption.AllDirectories);
                    check(logs.Length == (enabled ? 1 : 0));
                    if (enabled)
                    {
                        var text = File.ReadAllText(logs.Single());
                        check(text.Contains("T1 revalidation complete") && text.Contains("T2 review checking") && text.Contains("T5 final ViewModel state"));
                        check(!text.Contains(@"C:\Source") && !text.Contains(@"E:\Backups"));
                        check(!text.Contains(@"C:\slow"));
                        foreach (var point in new[] { "S0 select entry", "S1 selection applied", "S2 stale discarded", "N0 request", "N1 navigate start", "N2 browse returned", "N3 published" })
                            check(text.Contains("point=" + point));
                        check(text.Contains("pane=Source") && text.Contains("pane=Backup") && text.Contains("discarded=true applied=false"));
                    }
                }
            }
            finally { Directory.Delete(root, true); }
        });
    }
}
