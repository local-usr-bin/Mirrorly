using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

static class SetupTests
{
    public static async Task Run(Func<string, Func<Task>, Task> test, Action<bool> check)
    {
        await test("Setup initially lists drives without selecting a source or target", async () =>
        {
            var setup = new BackupSetupViewModel(new FakeFolders());
            await setup.InitializeAsync();
            check(setup.Source.Address == "This PC" && setup.Source.Folders.Count == 2);
            check(!setup.CanContinue && !setup.Source.CanBack && !setup.Source.CanUp);
        });
        await test("Browser enters, goes up to drive/This PC, and returns through history", async () =>
        {
            var pane = new FolderBrowserViewModel("Source", new FakeFolders());
            await pane.InitializeAsync();
            await pane.NavigateAsync(@"C:\Source\Child");
            check(pane.SelectedPath == @"C:\Source\Child");
            await pane.UpAsync(); check(pane.CurrentPath == @"C:\Source");
            await pane.UpAsync(); check(pane.CurrentPath == @"C:\");
            await pane.UpAsync(); check(pane.CurrentPath is null && !pane.HasValidSelection);
            await pane.BackAsync(); check(pane.CurrentPath == @"C:\");
            await pane.BackAsync(); check(pane.CurrentPath == @"C:\Source");
        });
        await test("Manual path errors stay inline and do not contaminate Back history", async () =>
        {
            var pane = new FolderBrowserViewModel("Source", new FakeFolders());
            await pane.InitializeAsync();
            await pane.NavigateAsync(@"C:\Source");
            pane.BeginEdit(); check(!pane.HasValidSelection);
            await pane.NavigateAsync(@"C:\missing");
            check(pane.Error!.Contains("can't be found") && pane.IsEditing && !pane.HasValidSelection);
            check(pane.CurrentPath == @"C:\Source");
            await pane.BackAsync(); check(pane.CurrentPath is null && !pane.CanBack && !pane.IsEditing);
            await pane.NavigateAsync(@"C:\denied");
            check(pane.Error!.Contains("permission") && !pane.HasValidSelection);
        });
        await test("Source and destination selections enable review with name and repository preview", async () =>
        {
            var setup = new BackupSetupViewModel(new FakeFolders());
            await setup.Source.SelectAsync(@"C:\Source"); check(!setup.CanContinue);
            await setup.Destination.SelectAsync(@"E:\Backups");
            check(setup.CanContinue && setup.BackupName == "Source");
            check(setup.RepositoryPreview == @"E:\Backups\MirrorlyRepo");
            await setup.ContinueAsync(); check(setup.IsReview && setup.CanCreate);
            setup.BackupName = ""; check(!setup.CanCreate);
            setup.BackupName = "My papers";
            setup.CreatePrototype(); check(setup.ShowPrototypeNotice && !setup.CanCreate);
            setup.Back(); check(!setup.IsReview && !setup.ShowPrototypeNotice);
            await setup.Source.SelectAsync(@"C:\Source\Child"); check(setup.BackupName == "My papers");
        });
        await test("Continue rechecks paths that disappear or become inaccessible", async () =>
        {
            var service = new FakeFolders();
            var setup = new BackupSetupViewModel(service);
            await setup.Source.SelectAsync(@"C:\Source");
            await setup.Destination.SelectAsync(@"E:\Backups");
            service.Unavailable.Add(@"E:\Backups");
            await setup.ContinueAsync();
            check(!setup.IsReview && !setup.CanContinue && setup.Destination.Error is not null);
            setup.CreatePrototype(); check(!setup.ShowPrototypeNotice);
        });
        await test("Late selection validation cannot override a later navigation", async () =>
        {
            var service = new FakeFolders { Delayed = new TaskCompletionSource<FolderValidation>() };
            var pane = new FolderBrowserViewModel("Source", service);
            var selecting = pane.SelectAsync(@"C:\slow");
            check(pane.IsValidating && !pane.HasValidSelection);
            await pane.NavigateAsync(@"C:\Source");
            service.Delayed.SetResult(new(@"C:\slow", null));
            await selecting;
            check(pane.SelectedPath == @"C:\Source" && pane.HasValidSelection);
        });
        await test("Long and UNC paths are preserved; layout stacks at the specified boundary", async () =>
        {
            var service = new FakeFolders();
            var longPath = @"C:\Source\" + string.Join('\\', Enumerable.Repeat("ResearchDocuments", 25));
            service.Paths.Add(longPath);
            service.Paths.Add(@"\\server\share\Backups");
            var setup = new BackupSetupViewModel(service);
            await setup.Source.SelectAsync(longPath);
            await setup.Destination.SelectAsync(@"\\server\share\Backups");
            check(setup.Source.SelectedPath == longPath && setup.CanContinue);
            check(setup.RepositoryPreview == @"\\server\share\Backups\MirrorlyRepo");
            foreach (var width in new[] { 320d, 720, 880, 920, 959.9 })
                check(SetupPreview.StackPanes(width));
            foreach (var width in new[] { 960d, 1008, 1200 })
                check(!SetupPreview.StackPanes(width));
        });
        await test("Display name follows new source until edited; same-path warning is limited", async () =>
        {
            var setup = new BackupSetupViewModel(new FakeFolders());
            await setup.Source.SelectAsync(@"C:\Source");
            await setup.Source.SelectAsync(@"C:\Source\Child"); check(setup.BackupName == "Child");
            await setup.Destination.SelectAsync(@"C:\Source\Child");
            check(setup.SamePathWarning); // Not a replacement for core safety validation.
        });
        await test("Read-only service browses isolated test folders, rejects files/relative/missing paths", async () =>
        {
            var root = Path.Combine(Path.GetTempPath(), "Mirrorly-SetupTests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(root, "Child"));
            var file = Path.Combine(root, "ordinary.txt");
            await File.WriteAllTextAsync(file, "unchanged");
            try
            {
                var service = new FolderBrowserService();
                var listing = await service.BrowseAsync(root);
                check(listing.Error is null && listing.Folders.Single().Name == "Child");
                check((await service.ValidateAsync('"' + root + '\\' + '"')).Path == root);
                foreach (var path in new[] { file, "relative", Path.Combine(root, "missing") })
                    check(!(await service.ValidateAsync(path)).IsValid);
                check((await service.BrowseAsync(null)).Folders.Count > 0);
                check(await File.ReadAllTextAsync(file) == "unchanged" && !Directory.Exists(Path.Combine(root, "MirrorlyRepo")));
                check(Directory.GetFileSystemEntries(root).Length == 2);
            }
            finally
            {
                // This exact, newly created test-owned directory is the only cleanup target.
                Directory.Delete(root, true);
            }
        });
    }
}

sealed class FakeFolders : IFolderBrowserService
{
    public HashSet<string> Paths { get; } = new(StringComparer.OrdinalIgnoreCase) { @"C:\", @"E:\", @"C:\Source", @"C:\Source\Child", @"E:\Backups" };
    public HashSet<string> Unavailable { get; } = new(StringComparer.OrdinalIgnoreCase);
    public TaskCompletionSource<FolderValidation>? Delayed { get; init; }
    private string? Error(string path) => path == @"C:\denied" ? "You may not have permission to access this folder."
        : !Paths.Contains(path) || Unavailable.Contains(path) ? "This folder can't be found. Check the path and try again." : null;
    public Task<FolderValidation> ValidateAsync(string path) => path == @"C:\slow" && Delayed is not null ? Delayed.Task
        : Task.FromResult(Error(path) is { } error ? new FolderValidation(null, error) : new(path, null));
    public Task<FolderListing> BrowseAsync(string? path)
    {
        if (path is null) return Task.FromResult(new FolderListing(null, null, [new("C:\\", @"C:\"), new("E:\\", @"E:\")]));
        if (Error(path) is { } error) return Task.FromResult(new FolderListing(null, null, [], error));
        return Task.FromResult(new FolderListing(path, Directory.GetParent(path)?.FullName, Paths
            .Where(p => Directory.GetParent(p)?.FullName == path).Select(p => new FolderEntry(Path.GetFileName(p), p)).ToArray()));
    }
}
