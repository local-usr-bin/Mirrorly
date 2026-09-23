using System.ComponentModel;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;

namespace Mirrorly.Desktop.ViewModels;

public sealed class HomeViewModel : INotifyPropertyChanged
{
    public HomeScenario Scenario { get; private set; } = HomeScenario.Empty;
    public bool DesignPreview { get; private set; }
    public HomeFixture Fixture { get; private set; } = new(new("Loading backups…", "Reading your configured backups.", "", "Back up now", StatusTone.Neutral, "○"), [], []);
    public StatusPresentation Status => Fixture.Status;
    public IReadOnlyList<BackupPresentation> Backups => HomePolicy.Preview(Fixture.Backups);
    public IReadOnlyList<ActivityPresentation> Activity => Fixture.Activity.Take(HomePolicy.RecentActivityLimit).ToArray();
    public bool IsEmpty => Fixture.Backups.Count == 0;
    public bool Loaded { get; private set; }
    public bool ShowEmpty => Loaded && IsEmpty && Problem.Length == 0;
    public bool CompactBackups => Fixture.Backups.Count > 1;
    public bool ShowAllBackups => Fixture.Backups.Count > 3;
    public bool ShowDecoration => HomePolicy.DecorationsEnabled && (IsEmpty || Status.Tone == StatusTone.Success || !DesignPreview && Status.Tone == StatusTone.Neutral);
    public string BackupHeading => ShowAllBackups ? "Backups at a glance" : CompactBackups ? "Your backups" : "Your backup";
    public string Problem { get; private set; } = "";
    public string TechnicalDetails { get; private set; } = "";
    public string PrototypeMessage { get; private set; } = "";
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Changed() => PropertyChanged?.Invoke(this, new(null));
    public async Task RefreshAsync(IDesktopSession session)
    {
        if (session.Busy || session.ExitPending) return;
        try { await session.RunAsync(RefreshCoreAsync); }
        catch (Exception error) { Unavailable(error); }
    }
    public async Task RefreshCoreAsync(ISetupApi api)
    {
        try { ApplyCatalog(await api.CatalogAsync()); }
        catch (Exception error) { Unavailable(error); }
    }
    private void Unavailable(Exception error)
    {
        Loaded = false;
        Problem = "Mirrorly couldn't refresh your configured backups. View technical details, then use Refresh to try reading again.";
        TechnicalDetails = error.ToString(); Changed();
    }
    public void ApplyCatalog(BackupCatalog catalog)
    {
        DesignPreview = false; Loaded = true;
        Problem = catalog.Problems.Count == 0 ? "" : "Some Backup configurations couldn't be read. They have not been repaired or added as valid backups. View technical details.";
        TechnicalDetails = string.Join("\n", catalog.Problems);
        Fixture = new(new("Backup set up", "Configuration saved. Backup execution is not available in this version.",
            "Setup creates no snapshot. Existing backup history and destination availability have not been checked.", "Back up now", StatusTone.Neutral, "○"),
            catalog.Tasks.Select((t, index) => new BackupPresentation(t.Selector, t.Name, t.Source, t.RepositoryPath,
                "Not checked", "Backup set up", StatusTone.Neutral, -index)).ToArray(), []);
        Changed();
    }
    // Tests/design-time only. Normal desktop never selects a fixture.
    public void SelectFixture(HomeScenario scenario)
    {
        DesignPreview = true; Loaded = true; Scenario = scenario;
        Fixture = HomeFixtures.Create(scenario); PrototypeMessage = ""; Changed();
    }
    public void ShowPrototypeAction(string action)
    {
        PrototypeMessage = $"{action} is not available in this version. No files were read or changed.";
        PropertyChanged?.Invoke(this, new(nameof(PrototypeMessage)));
    }
}
