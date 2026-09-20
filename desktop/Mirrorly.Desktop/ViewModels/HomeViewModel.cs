using System.ComponentModel;
using Mirrorly.Desktop.Presentation;

namespace Mirrorly.Desktop.ViewModels;

public sealed class HomeViewModel : INotifyPropertyChanged
{
    public HomeScenario Scenario { get; private set; }
    public HomeFixture Fixture { get; private set; } = HomeFixtures.Create(HomeScenario.Healthy);
    public StatusPresentation Status => Fixture.Status;
    public IReadOnlyList<BackupPresentation> Backups => HomePolicy.Preview(Fixture.Backups);
    public IReadOnlyList<ActivityPresentation> Activity => Fixture.Activity.Take(HomePolicy.RecentActivityLimit).ToArray();
    public bool IsEmpty => Fixture.Backups.Count == 0;
    public bool CompactBackups => Fixture.Backups.Count > 1;
    public bool ShowAllBackups => Fixture.Backups.Count > 3;
    public bool ShowDecoration => HomePolicy.DecorationsEnabled && (IsEmpty || Status.Tone == StatusTone.Success);
    public string BackupHeading => ShowAllBackups ? "Backups at a glance" : CompactBackups ? "Your backups" : "Your backup";
    public string PrototypeMessage { get; private set; } = "";
    public event PropertyChangedEventHandler? PropertyChanged;
    public void SelectFixture(HomeScenario scenario)
    {
        Scenario = scenario;
        Fixture = HomeFixtures.Create(scenario);
        PrototypeMessage = "";
        PropertyChanged?.Invoke(this, new(null));
    }
    public void ShowPrototypeAction(string action)
    {
        PrototypeMessage = $"{action} is a preview in Phase 1B. No files were read or changed.";
        PropertyChanged?.Invoke(this, new(nameof(PrototypeMessage)));
    }
}
