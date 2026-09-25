using System.Text.Json;
using Mirrorly.Desktop.Services;

static class RestorePrepareTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Typed Restore preview retains selector, frozen destination and explicit Skip/Replace policy", () =>
        {
            var request = JsonSerializer.SerializeToElement(new RestorePrepareInput(
                @"C:\gui\Tasks", "selector-two", null, @"D:\restore here\中文", "skip_existing"));
            check(request.GetProperty("task").GetString() == "selector-two" &&
                request.GetProperty("config_root").GetString() == @"C:\gui\Tasks" &&
                request.GetProperty("snapshot_id").ValueKind == JsonValueKind.Null &&
                request.GetProperty("destination").GetString() == @"D:\restore here\中文" &&
                request.GetProperty("policy").GetString() == "skip_existing");
            var id = Guid.NewGuid().ToString("N");
            JsonElement Reply(string selector, string policy) => JsonSerializer.SerializeToElement(new {
                phase = "terminal", error = (object?)null,
                result = new { outcome = "succeeded", preview = new {
                    plan_id = id, selector, snapshot_id = "snapshot", destination = @"D:\restore here",
                    policy, file_create_count = 2L, file_overwrite_count = 1L,
                    file_skip_count = 3L, file_conflict_count = 4L, directory_entry_count = 5L
                } }
            });
            var preview = DesktopSession.ParseRestorePrepare(Reply("selector-two", "replace_existing"),
                "selector-two", RestoreConflictPolicy.ReplaceExisting);
            check(preview.PlanId == id && preview.Selector == "selector-two" &&
                preview.SnapshotId == "snapshot" && preview.Destination == @"D:\restore here" &&
                preview.Policy == RestoreConflictPolicy.ReplaceExisting &&
                preview.FileCreateCount == 2 && preview.FileOverwriteCount == 1 &&
                preview.FileSkipCount == 3 && preview.FileConflictCount == 4 &&
                preview.DirectoryEntryCount == 5);
            var skip = DesktopSession.ParseRestorePrepare(Reply("selector-two", "skip_existing"),
                "selector-two", RestoreConflictPolicy.SkipExisting);
            check(skip.Policy == RestoreConflictPolicy.SkipExisting);
            try { DesktopSession.ParseRestorePrepare(Reply("selector-two", "skip_existing"),
                "selector-two", RestoreConflictPolicy.SkipExisting, "another-snapshot");
                throw new Exception("Explicit snapshot identity changed in the response."); }
            catch (InvalidDataException) { }
            foreach (var wrong in new[] {
                Reply("same-name-other-selector", "replace_existing"),
                Reply("selector-two", "skip_existing")
            })
            {
                try { DesktopSession.ParseRestorePrepare(wrong, "selector-two", RestoreConflictPolicy.ReplaceExisting);
                    throw new Exception("Mismatched Restore intent was accepted."); }
                catch (InvalidDataException) { }
            }
            foreach (var malformed in new[] {
                JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null,
                    result = new { outcome = "succeeded", preview = new { selector = "selector-two" } } }),
                JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null,
                    result = new { outcome = "succeeded", preview = new {
                        plan_id = id, selector = "selector-two", snapshot_id = "snapshot", destination = @"D:\restore",
                        policy = "skip_existing", file_create_count = -1L, file_overwrite_count = 0L,
                        file_skip_count = 0L, file_conflict_count = 0L, directory_entry_count = 0L } } })
            })
            {
                try { DesktopSession.ParseRestorePrepare(malformed, "selector-two", RestoreConflictPolicy.SkipExisting);
                    throw new Exception("Malformed Restore preview was accepted."); }
                catch (InvalidDataException) { }
            }
            var unavailable = JsonSerializer.SerializeToElement(new { phase = "terminal", result = new {
                outcome = "failed", preview = (object?)null },
                error = new { kind = "application", code = "restore_prepare_unavailable" } });
            try { DesktopSession.ParseRestorePrepare(unavailable, "selector-two", RestoreConflictPolicy.SkipExisting);
                throw new Exception("Application failure became a preview."); }
            catch (RestorePrepareRejectedException error) {
                check(error.Kind == "application" && error.Code == "restore_prepare_unavailable"); }
            var busy = JsonSerializer.SerializeToElement(new { phase = "rejected", result = (object?)null,
                error = new { kind = "admission", code = "busy" } });
            try { DesktopSession.ParseRestorePrepare(busy, "selector-two", RestoreConflictPolicy.SkipExisting);
                throw new Exception("Busy admission became a preview."); }
            catch (RestorePrepareRejectedException error) { check(error.Kind == "admission" && error.Code == "busy"); }
            return Task.CompletedTask;
        });

        await test("Real C# session prepares Restore through the production worker without writing destination", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-restore-prepare-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "source"));
            Directory.CreateDirectory(Path.Combine(root, "target"));
            Directory.CreateDirectory(Path.Combine(root, "destination"));
            File.WriteAllText(Path.Combine(root, "source", "sample.txt"), "source data");
            File.WriteAllText(Path.Combine(root, "destination", "sample.txt"), "user data");
            try
            {
                var session = new DesktopSession(launch, new(root));
                await session.RunAsync(async api => {
                    var created = await api.CreateAsync(new("one", Path.Combine(root, "source"),
                        Path.Combine(root, "target"), "strict", false));
                    check(created.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                string? snapshot = null;
                await session.RunAsync(async api => {
                    var backup = await api.BackupAsync("one");
                    check(backup.Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                    snapshot = backup.Payload.GetProperty("result").GetProperty("facts").GetProperty("snapshot_id").GetString();
                });
                RestorePreparedPlanPreview? skip = null, replace = null;
                await session.RunAsync(async api => skip = await api.PrepareRestoreAsync(new(
                    "one", null, Path.Combine(root, "destination"))));
                await session.RunAsync(async api => replace = await api.PrepareRestoreAsync(new(
                    "one", snapshot, Path.Combine(root, "destination"), RestoreConflictPolicy.ReplaceExisting)));
                check(skip!.Selector == "one" && skip.SnapshotId == snapshot &&
                    skip.Policy == RestoreConflictPolicy.SkipExisting && skip.FileSkipCount == 1 &&
                    skip.FileOverwriteCount == 0 && replace!.FileOverwriteCount == 1 &&
                    replace.Policy == RestoreConflictPolicy.ReplaceExisting && replace.PlanId != skip.PlanId);
                check(File.ReadAllText(Path.Combine(root, "destination", "sample.txt")) == "user data");
                foreach (var unsafeDestination in new[] {
                    Path.Combine(root, "source"), Path.Combine(root, "target", "MirrorlyRepo")
                })
                {
                    try {
                        await session.RunAsync(async api => _ = await api.PrepareRestoreAsync(new(
                            "one", snapshot, unsafeDestination)));
                        throw new Exception("Unsafe Restore destination yielded a plan.");
                    }
                    catch (RestorePrepareRejectedException error) {
                        check(error.Kind == "application" && error.Code == "unsafe_destination");
                    }
                }
                check(File.ReadAllText(Path.Combine(root, "destination", "sample.txt")) == "user data");
                try {
                    await session.RunAsync(async api => _ = await api.PrepareRestoreAsync(new(
                        "unknown", snapshot, Path.Combine(root, "destination"))));
                    throw new Exception("Unknown task yielded a Restore plan.");
                }
                catch (RestorePrepareRejectedException error) { check(error.Kind == "application" && error.Code == "unknown_task"); }
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { Directory.Delete(root, true); }
        });
    }
}
