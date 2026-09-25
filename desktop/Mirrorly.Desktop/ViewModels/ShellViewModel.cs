using Mirrorly.Desktop.Presentation;
namespace Mirrorly.Desktop.ViewModels;
public sealed class ShellViewModel
{
    public ShellPage SelectedPage { get; private set; } = ShellPage.Home;
    public ShellPage TopLevelPage => SelectedPage is ShellPage.BackupSetup or ShellPage.BackupDetail
        ? ShellPage.Backups : SelectedPage;
    public string Title => SelectedPage switch { ShellPage.BackupSetup => "Set up backup", ShellPage.BackupDetail => "Backup overview", _ => SelectedPage.ToString() };
    public bool IsHome => SelectedPage == ShellPage.Home;
    public void Navigate(ShellPage page) => SelectedPage = page;
}
