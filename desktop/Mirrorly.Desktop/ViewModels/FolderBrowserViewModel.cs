using System.ComponentModel;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public sealed class FolderBrowserViewModel(string title, IFolderBrowserService service) : INotifyPropertyChanged
{
    private readonly Stack<string?> history = new();
    private int selectionRevision;
    private bool initialized;
    private FolderNavigationDiagnostics? diagnostics;
    internal FolderNavigationDiagnostics Diagnostics => diagnostics ??= new(Title, DiagnosticState);
    private string DiagnosticState() => $"revision={selectionRevision} browsing={IsBrowsing} validating={IsValidating} editing={IsEditing} " +
        $"list={FolderNavigationDiagnostics.ObjectId(Folders)} count={Folders.Count} selected_path_present={SelectedPath is not null} " +
        $"current_kind={(CurrentPath is null ? "ThisPC" : string.Equals(Path.GetPathRoot(CurrentPath), CurrentPath, StringComparison.OrdinalIgnoreCase) ? "root" : "directory")}";
    public string Title { get; } = title;
    public string? CurrentPath { get; private set; }
    public string? ParentPath { get; private set; }
    public string Address => CurrentPath ?? "This PC";
    public IReadOnlyList<FolderEntry> Folders { get; private set; } = [];
    public string? SelectedPath { get; private set; }
    public string SelectionText => SelectedPath ?? "No folder selected";
    public string? Error { get; private set; }
    public bool IsBrowsing { get; private set; }
    public bool IsValidating { get; private set; }
    public bool IsEditing { get; private set; }
    public bool HasValidSelection => SelectedPath is not null && Error is null && !IsBrowsing && !IsValidating && !IsEditing;
    public bool CanBack => history.Count > 0 && !IsBrowsing;
    public bool CanUp => CurrentPath is not null && !IsBrowsing;
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(null));

    public async Task InitializeAsync()
    {
        if (initialized) return;
        initialized = true;
        await NavigateAsync(null, false);
    }
    public void BeginEdit() { IsEditing = true; Changed(); }
    public void CancelEdit() { IsEditing = false; Changed(); }
    public void ClearSelection()
    {
        selectionRevision++;
        SelectedPath = null;
        Error = null;
        IsEditing = false;
        IsValidating = false;
        Changed();
    }
    public Task NavigateAsync(string? path) => NavigateAsync(path, true);
    private async Task NavigateAsync(string? path, bool remember)
    {
        if (IsBrowsing) return;
        var diagnosticNavigation = Diagnostics.NavigationStarted();
        selectionRevision++;
        IsValidating = false;
        IsBrowsing = true;
        Error = null;
        Changed();
        Diagnostics.Record("N1 navigate start", () => $"nav={diagnosticNavigation}");
        var listing = await service.BrowseAsync(path);
        Diagnostics.Record("N2 browse returned", () => $"nav={diagnosticNavigation} result={(listing.Error is null ? "success" : "failure")} returned_count={listing.Folders.Count}");
        IsBrowsing = false;
        if (listing.Error is not null)
        {
            SelectedPath = null;
            Error = listing.Error;
        }
        else
        {
            if (remember && !string.Equals(CurrentPath, listing.Path, StringComparison.OrdinalIgnoreCase)) history.Push(CurrentPath);
            CurrentPath = listing.Path;
            ParentPath = listing.Parent;
            Folders = listing.Folders;
            SelectedPath = CurrentPath;
            IsEditing = false;
        }
        Changed();
        Diagnostics.Record("N3 published", () => $"nav={diagnosticNavigation} published={listing.Error is null}");
    }
    public async Task BackAsync()
    {
        if (!CanBack) return;
        var target = history.Peek();
        await NavigateAsync(target, false);
        if (Error is null) history.Pop();
        Changed();
    }
    public Task UpAsync() => CanUp ? NavigateAsync(ParentPath) : Task.CompletedTask;
    public async Task SelectAsync(string path)
    {
        var diagnosticSelection = Diagnostics.NextSelection();
        var capturedRevision = selectionRevision;
        Diagnostics.Record("S0 select entry", () => $"selection={diagnosticSelection} captured_revision={capturedRevision} admitted={!IsBrowsing}");
        if (IsBrowsing) return;
        var revision = ++selectionRevision;
        SelectedPath = null;
        Error = null;
        IsValidating = true;
        Changed();
        Diagnostics.Record("S0 validation pending", () => $"selection={diagnosticSelection} captured_revision={revision}");
        var result = await service.ValidateAsync(path);
        if (revision != selectionRevision)
        {
            Diagnostics.Record("S2 stale discarded", () => $"selection={diagnosticSelection} captured_revision={revision} discarded=true applied=false");
            return; // A later selection/navigation owns the pane.
        }
        SelectedPath = result.IsValid ? result.Path : null;
        Error = result.Error;
        IsValidating = false;
        Changed();
        Diagnostics.Record("S1 selection applied", () => $"selection={diagnosticSelection} captured_revision={revision} result={(result.IsValid ? "valid" : "invalid")} applied=true");
    }
    public Task RevalidateAsync() => SelectedPath is { } path ? SelectAsync(path) : Task.CompletedTask;
}
