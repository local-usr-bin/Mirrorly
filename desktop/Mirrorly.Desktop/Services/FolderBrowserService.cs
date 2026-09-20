namespace Mirrorly.Desktop.Services;

public sealed record FolderEntry(string Name, string Path);
public sealed record FolderValidation(string? Path, string? Error)
{
    public bool IsValid => Path is not null && Error is null;
}
public sealed record FolderListing(string? Path, string? Parent, IReadOnlyList<FolderEntry> Folders, string? Error = null);

// Read-only UI browsing. This does not perform Mirrorly setup or safety preflight.
public interface IFolderBrowserService
{
    Task<FolderListing> BrowseAsync(string? path);
    Task<FolderValidation> ValidateAsync(string path);
}

public sealed class FolderBrowserService : IFolderBrowserService
{
    public Task<FolderListing> BrowseAsync(string? path) => Task.Run(() =>
    {
        try
        {
            if (path is null)
                return new FolderListing(null, null, DriveInfo.GetDrives()
                    .Select(d => new FolderEntry(d.Name, d.Name)).ToArray());
            var normalized = Normalize(path);
            RequireDirectory(normalized);
            var folders = Directory.EnumerateDirectories(normalized)
                .Select(p => new FolderEntry(System.IO.Path.GetFileName(p), p))
                .OrderBy(f => f.Name, StringComparer.OrdinalIgnoreCase).ToArray();
            return new FolderListing(normalized, Directory.GetParent(normalized)?.FullName, folders);
        }
        catch (Exception error) when (IsExpected(error))
        {
            return new FolderListing(null, null, [], FriendlyError(error));
        }
    });

    public Task<FolderValidation> ValidateAsync(string path) => Task.Run(() =>
    {
        try
        {
            var normalized = Normalize(path);
            RequireDirectory(normalized);
            return new FolderValidation(normalized, null);
        }
        catch (Exception error) when (IsExpected(error))
        {
            return new FolderValidation(null, FriendlyError(error));
        }
    });

    private static string Normalize(string path)
    {
        var value = path.Trim().Trim('"');
        if (!System.IO.Path.IsPathFullyQualified(value)) throw new ArgumentException("An absolute path is required.");
        return System.IO.Path.TrimEndingDirectorySeparator(System.IO.Path.GetFullPath(value));
    }
    private static void RequireDirectory(string path)
    {
        if (!File.GetAttributes(path).HasFlag(FileAttributes.Directory)) throw new DirectoryNotFoundException();
        // MoveNext probes read access even for an empty folder; it never reads file contents.
        using var entries = Directory.EnumerateDirectories(path).GetEnumerator();
        entries.MoveNext();
    }
    private static bool IsExpected(Exception error) => error is IOException or UnauthorizedAccessException
        or ArgumentException or NotSupportedException or System.Security.SecurityException;
    private static string FriendlyError(Exception error) => error switch
    {
        UnauthorizedAccessException or System.Security.SecurityException => "This folder can't be opened. You may not have permission to access it. Choose another folder or check its permissions.",
        DriveNotFoundException => "This drive isn't available. Connect the drive, then try again.",
        DirectoryNotFoundException or FileNotFoundException or ArgumentException or NotSupportedException => "This folder can't be found. Check the path and try again.",
        PathTooLongException => "This path can't be opened here. Try a shorter folder path.",
        _ => "This location can't be opened right now. Check that the drive or network is available, then try again."
    };
}
