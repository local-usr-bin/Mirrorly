namespace Mirrorly.Desktop.Services;

// A one-shot UI existence check. Execution and root scanning enforce readability.
// Never probe a sleeping source on a timer or block the WinUI thread.
public static class SourceAvailability
{
    public static Task<bool> CheckAsync(string source) => Task.Run(() => Directory.Exists(source));
}
