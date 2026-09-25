using System.Text.Json;
using Mirrorly.Desktop.Services;

static class SnapshotQueryTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check, WorkerDevelopmentLaunch launch)
    {
        await test("Typed snapshot pages retain complete/incomplete, nullable facts and latest identity", () =>
        {
            var payload = JsonSerializer.SerializeToElement(new {
                phase = "terminal", error = (object?)null,
                result = new { outcome = "succeeded", page = new {
                    selector = "one", latest_complete_snapshot_id = "complete-id", next_after = "incomplete-id",
                    items = new object[] {
                        new { snapshot_id = "complete-id", status = "complete", created_at = "2026-01-01T00:00:00Z",
                            lifecycle_seq = (ulong?)ulong.MaxValue, file_count = 2L, directory_count = 1L,
                            logical_bytes = 42L, resumed_from_snapshot_id = (string?)null, format_version = 2 },
                        new { snapshot_id = "incomplete-id", status = "incomplete", created_at = (string?)null,
                            lifecycle_seq = (ulong?)null, file_count = 0L, directory_count = 0L,
                            logical_bytes = 0L, resumed_from_snapshot_id = "older", format_version = 1 }
                    }
                } }
            });
            var page = DesktopSession.ParseSnapshotPage(payload, "one");
            check(page.Selector == "one" && page.LatestCompleteSnapshotId == "complete-id" &&
                page.NextAfter == "incomplete-id" && page.Items.Count == 2 &&
                page.Items[0].LifecycleSequence == ulong.MaxValue && page.Items[0].FileCount == 2 &&
                page.Items[0].DirectoryCount == 1 && page.Items[0].LogicalBytes == 42 &&
                page.Items[1].Status == "incomplete" && page.Items[1].CreatedAt is null &&
                page.Items[1].LifecycleSequence is null && page.Items[1].ResumedFromSnapshotId == "older");
            try { DesktopSession.ParseSnapshotPage(payload, "other"); throw new Exception("Selector mismatch accepted."); }
            catch (InvalidDataException) { }
            try { DesktopSession.ParseSnapshotPage(payload, "one", 1); throw new Exception("Oversized page accepted."); }
            catch (InvalidDataException) { }
            var empty = JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null,
                result = new { outcome = "succeeded", page = new {
                    selector = "one", items = Array.Empty<object>(), next_after = (string?)null,
                    latest_complete_snapshot_id = (string?)null } } });
            check(DesktopSession.ParseSnapshotPage(empty, "one").Items.Count == 0 &&
                DesktopSession.ParseSnapshotPage(empty, "one").LatestCompleteSnapshotId is null);
            foreach (var invalid in new[] {
                JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null,
                    result = new { outcome = "succeeded", page = new { selector = "one", items = new[] {
                        new { snapshot_id = "bad", status = "unknown", created_at = "x", lifecycle_seq = (ulong?)1,
                            file_count = 1L, directory_count = 0L, logical_bytes = 1L,
                            resumed_from_snapshot_id = (string?)null, format_version = 2 } },
                        next_after = (string?)null, latest_complete_snapshot_id = (string?)null } } }),
                JsonSerializer.SerializeToElement(new { phase = "terminal", error = (object?)null,
                    result = new { outcome = "succeeded", page = new { selector = "one", items = Array.Empty<object>(),
                        next_after = "missing", latest_complete_snapshot_id = (string?)null } } })
            })
            {
                try { DesktopSession.ParseSnapshotPage(invalid, "one"); throw new Exception("Invalid page accepted."); }
                catch (InvalidDataException) { }
            }
            return Task.CompletedTask;
        });

        await test("Real production snapshot query pages saved versions by selector", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "mirrorly-snapshot-query-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "source"));
            Directory.CreateDirectory(Path.Combine(root, "target"));
            File.WriteAllText(Path.Combine(root, "source", "sample.txt"), "first");
            try
            {
                var session = new DesktopSession(launch, new(root));
                await session.RunAsync(async api => {
                    var created = await api.CreateAsync(new("one", Path.Combine(root, "source"),
                        Path.Combine(root, "target"), "strict", false));
                    check(created.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                SnapshotCollectionPage? empty = null;
                await session.RunAsync(async api => empty = await api.SnapshotPageAsync("one"));
                check(empty!.Items.Count == 0 && empty.NextAfter is null && empty.LatestCompleteSnapshotId is null);
                await session.RunAsync(async api => {
                    var first = await api.BackupAsync("one");
                    check(first.Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                File.WriteAllText(Path.Combine(root, "source", "sample.txt"), "second");
                await session.RunAsync(async api => {
                    var second = await api.BackupAsync("one");
                    check(second.Payload.GetProperty("result").GetProperty("outcome").GetString() == "succeeded");
                });
                SnapshotCollectionPage? firstPage = null, lastPage = null;
                await session.RunAsync(async api => firstPage = await api.SnapshotPageAsync("one", limit: 1));
                check(firstPage!.Items.Count == 1 && firstPage.NextAfter == firstPage.Items[0].SnapshotId);
                await session.RunAsync(async api => lastPage = await api.SnapshotPageAsync("one", firstPage!.NextAfter, 1));
                check(lastPage!.Items.Count == 1 && lastPage.NextAfter is null &&
                    lastPage.LatestCompleteSnapshotId == lastPage.Items[0].SnapshotId &&
                    lastPage.Items[0].LifecycleSequence == 1 && lastPage.Items[0].LogicalBytes == 6);
                try {
                    await session.RunAsync(async api => _ = await api.SnapshotPageAsync("unknown"));
                    throw new Exception("Unknown selector returned empty success.");
                }
                catch (IOException) { }
                check(await session.ExitAsync(() => Task.FromResult(true)));
            }
            finally { Directory.Delete(root, true); }
        });
    }
}
