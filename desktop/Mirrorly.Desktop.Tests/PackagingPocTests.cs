using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class PackagingPocTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Packaging PoC has truthful unavailable Home, no fixture or worker startup", async () =>
        {
            var session = new DesktopSession(null, GuiDataPaths.ForCurrentUser());
            var home = new HomeViewModel(new BackupExecutionCoordinator(session));
            await home.RefreshAsync(session);
            check(home.Problem.Length > 0 && !home.ShowEmpty && home.AllBackups.Count == 0);
            check(home.Status.Title == "Backups unavailable" && home.TechnicalDetails.Contains("intentionally disabled"));
            check(!session.Observation.Initialized && !session.Busy && !home.DesignPreview);
            check(await session.ExitAsync(() => throw new Exception("Idle PoC must not prompt.")));
        });
        await test("Packaging PoC rejects every application request before send or mutation", async () =>
        {
            var session = new DesktopSession(null, GuiDataPaths.ForCurrentUser());
            Func<ISetupApi, Task>[] calls = [
                api => api.CatalogAsync(), api => api.BackupSummaryAsync("a.toml"),
                api => api.SnapshotPageAsync("a.toml"), api => api.BackupAsync("a.toml"),
                api => api.PreflightAsync(new("test", @"C:\source", @"C:\target")),
                api => api.CreateAsync(new("test", @"C:\source", @"C:\target", "strict", false)),
                api => api.PrepareRestoreAsync(new("a.toml", null, @"C:\destination", RestoreConflictPolicy.SkipExisting)),
                api => api.ExecuteRestoreAsync("unusable-plan", false)
            ];
            foreach (var call in calls)
            {
                try { await session.RunAsync(call); throw new Exception("Unavailable request succeeded."); }
                catch (IOException error) { check(error.Message.Contains("intentionally disabled")); }
                check(!session.Busy && session.RequestId is null && !session.Observation.Initialized);
            }
            check(await session.ExitAsync(() => Task.FromResult(false)));
        });
    }
}
