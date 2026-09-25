using System.Globalization;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public enum SnapshotCollectionState
{
    NotLoaded, LoadingFirst, Loaded, LoadingMore, Refreshing, Unavailable, ContinuationError
}

public sealed class SnapshotRowPresentation(string snapshotId, string time, string status, string facts)
{
    // WinUI compiled data templates require writable properties in their generated metadata.
    public string SnapshotId { get; set; } = snapshotId;
    public string Time { get; set; } = time;
    public string Status { get; set; } = status;
    public string Facts { get; set; } = facts;
}

// Feature-local read-only paging. Each page is a fresh repository observation, not a transaction.
public sealed class SnapshotCollectionViewModel(IDesktopSession session)
{
    private readonly List<SnapshotCollectionItem> items = [];
    private string? selector;
    private string? nextAfter;
    private string? latestCompleteId;
    private int generation;
    public event Action? Changed;
    public SnapshotCollectionState State { get; private set; } = SnapshotCollectionState.NotLoaded;
    public string? Selector => selector;
    public IReadOnlyList<SnapshotCollectionItem> Items => items;
    public IReadOnlyList<SnapshotRowPresentation> Rows => items.Select(item => new SnapshotRowPresentation(
        item.SnapshotId,
        HomePolicy.FormatSavedVersionTime(item.CreatedAt),
        item.Status == "complete" && item.SnapshotId == latestCompleteId ? "Latest saved backup" :
            item.Status == "complete" ? "Complete" : "Incomplete · unfinished work",
        string.Format(CultureInfo.CurrentCulture, "{0:N0} files · {1:N0} folders · {2:N0} logical bytes",
            item.FileCount, item.DirectoryCount, item.LogicalBytes))).ToArray();
    public string? NextAfter => nextAfter;
    public string? LatestCompleteSnapshotId => latestCompleteId;
    public string TechnicalDetails { get; private set; } = "";
    public bool IsLoading => State is SnapshotCollectionState.LoadingFirst or SnapshotCollectionState.LoadingMore or SnapshotCollectionState.Refreshing;
    public bool HasMore => nextAfter is not null;
    public bool IsEmpty => State == SnapshotCollectionState.Loaded && items.Count == 0;
    public bool OnlyIncomplete => items.Count > 0 && latestCompleteId is null;

    public void Select(string taskSelector)
    {
        generation++;
        selector = taskSelector;
        items.Clear();
        nextAfter = latestCompleteId = null;
        TechnicalDetails = "";
        State = SnapshotCollectionState.NotLoaded;
        Changed?.Invoke();
    }

    public Task LoadFirstAsync() => State is SnapshotCollectionState.NotLoaded or SnapshotCollectionState.Unavailable
        ? QueryAsync(false, false) : Task.CompletedTask;
    public Task RefreshAsync() => IsLoading ? Task.CompletedTask : QueryAsync(false, true);
    public Task LoadMoreAsync() => !IsLoading && nextAfter is not null &&
        State is SnapshotCollectionState.Loaded or SnapshotCollectionState.ContinuationError
        ? QueryAsync(true, false) : Task.CompletedTask;

    private async Task QueryAsync(bool append, bool refresh)
    {
        if (selector is null || IsLoading) return;
        var requestedSelector = selector;
        var requestedAfter = append ? nextAfter : null;
        var requestGeneration = generation;
        if (!append)
        {
            items.Clear();
            nextAfter = latestCompleteId = null;
        }
        TechnicalDetails = "";
        State = append ? SnapshotCollectionState.LoadingMore : refresh ? SnapshotCollectionState.Refreshing : SnapshotCollectionState.LoadingFirst;
        Changed?.Invoke();
        try
        {
            SnapshotCollectionPage? page = null;
            await session.RunAsync(async api => page = await api.SnapshotPageAsync(requestedSelector, requestedAfter));
            if (requestGeneration != generation) return;
            if (page is null || page.Selector != requestedSelector)
                throw new InvalidDataException("Snapshot page selector mismatch.");
            var known = new HashSet<string>(items.Select(item => item.SnapshotId), StringComparer.Ordinal);
            if (page.Items.Any(item => !known.Add(item.SnapshotId)))
                throw new InvalidDataException("A snapshot appeared twice while reading pages. Refresh to read a new observation.");
            if (page.NextAfter is not null && (page.Items.Count == 0 || page.Items[^1].SnapshotId != page.NextAfter))
                throw new InvalidDataException("Snapshot page cursor mismatch.");
            items.AddRange(page.Items);
            nextAfter = page.NextAfter;
            latestCompleteId = page.LatestCompleteSnapshotId;
            State = SnapshotCollectionState.Loaded;
        }
        catch (Exception error)
        {
            if (requestGeneration != generation) return;
            TechnicalDetails = error.ToString();
            State = append ? SnapshotCollectionState.ContinuationError : SnapshotCollectionState.Unavailable;
        }
        Changed?.Invoke();
    }
}
