using Mirrorly.Desktop.Presentation;
namespace Mirrorly.Desktop.ViewModels;
public sealed class ShellViewModel
{
    public ShellPage SelectedPage { get; private set; } = ShellPage.Home;
    public string Title => SelectedPage == ShellPage.BackupSetup ? "Set up backup" : SelectedPage.ToString();
    public bool IsHome => SelectedPage == ShellPage.Home;
    public void Navigate(ShellPage page) => SelectedPage = page;
}
