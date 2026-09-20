using System.ComponentModel;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public static class SetupPreview
{
    // Available page width in effective pixels; preserve comfortable folder/address space.
    public const double SideBySideAt = 960;
    public static bool StackPanes(double width) => width < SideBySideAt;
    // Phase 1C preview ONLY. Replace with a shared application-service result on real integration.
    public static string RepositoryPath(string target) => Path.Combine(target, "MirrorlyRepo");
    public static string DefaultName(string source)
    {
        var name = Path.GetFileName(Path.TrimEndingDirectorySeparator(source));
        return string.IsNullOrWhiteSpace(name) ? source.TrimEnd('\\', ':') + " drive" : name;
    }
}

public sealed class BackupSetupViewModel : INotifyPropertyChanged
{
    private string backupName = "";
    private string lastDefaultName = "";
    public BackupSetupViewModel(IFolderBrowserService service)
    {
        Source = new("Source", service);
        Destination = new("Backup location", service);
        Source.PropertyChanged += (_, _) => { UpdateDefaultName(); Changed(); };
        Destination.PropertyChanged += (_, _) => Changed();
    }
    public FolderBrowserViewModel Source { get; }
    public FolderBrowserViewModel Destination { get; }
    public bool IsReview { get; private set; }
    public bool IsChecking { get; private set; }
    public bool ShowPrototypeNotice { get; private set; }
    public string BackupName { get => backupName; set { backupName = value; Changed(); } }
    public bool CanContinue => Source.HasValidSelection && Destination.HasValidSelection && !IsChecking;
    public bool CanCreate => IsReview && !string.IsNullOrWhiteSpace(BackupName) && !ShowPrototypeNotice;
    public string RepositoryPreview => Destination.SelectedPath is { } path ? SetupPreview.RepositoryPath(path) : "";
    public bool SamePathWarning => Source.SelectedPath is { } source && Destination.SelectedPath is { } target
        && string.Equals(source, target, StringComparison.OrdinalIgnoreCase);
    public const string PrototypeMessage = "Backup setup is not connected to the Mirrorly backup engine yet. No folders or backup data were changed.";
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(null));
    private void UpdateDefaultName()
    {
        if (Source.SelectedPath is not { } path) return;
        var name = SetupPreview.DefaultName(path);
        if (backupName.Length == 0 || backupName == lastDefaultName) backupName = name;
        lastDefaultName = name;
    }
    public Task InitializeAsync() => Task.WhenAll(Source.InitializeAsync(), Destination.InitializeAsync());
    public async Task ContinueAsync()
    {
        if (!CanContinue) return;
        IsChecking = true;
        Changed();
        await Task.WhenAll(Source.RevalidateAsync(), Destination.RevalidateAsync());
        IsChecking = false;
        IsReview = CanContinue;
        Changed();
    }
    public void Back() { IsReview = false; ShowPrototypeNotice = false; Changed(); }
    public void CreatePrototype() { if (CanCreate) { ShowPrototypeNotice = true; Changed(); } }
}
