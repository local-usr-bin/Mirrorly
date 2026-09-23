namespace Mirrorly.Desktop.Services;

// O-07: GUI-owned tasks are Python TOML truth; this provider selects only their root.
// No directory creation, task discovery/import, persistence or deployment decision.
public sealed class GuiDataPaths
{
    public string DataRoot { get; }
    public string TaskConfigRoot { get; }

    public static GuiDataPaths ForCurrentUser() => new(Environment.GetFolderPath(
        Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolderOption.DoNotVerify));

    // Isolated tests supply an absolute, test-owned LocalAppData equivalent.
    internal GuiDataPaths(string localApplicationData)
    {
        if (!Path.IsPathFullyQualified(localApplicationData))
            throw new ArgumentException("Local application data must be an explicit absolute path.", nameof(localApplicationData));
        DataRoot = Path.Combine(Path.GetFullPath(localApplicationData), "Mirrorly", "Gui");
        TaskConfigRoot = Path.Combine(DataRoot, "Tasks");
    }
}
