using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

namespace Mirrorly.Desktop.Views;

// Shares Home's real catalog projection and the app-scoped Backup coordinator.
// The shell ScrollViewer owns scrolling for the complete collection.
public sealed partial class BackupsView : UserControl
{
    private readonly HomeViewModel model;
    private readonly IDesktopSession session;
    public event Action<ShellPage>? Navigate;

    public BackupsView(HomeViewModel model, IDesktopSession session)
    {
        this.model = model;
        this.session = session;
        InitializeComponent();
        DataContext = model;
        model.PropertyChanged += (_, _) => RenderCatalog();
        RenderCatalog();
    }

    private void RenderCatalog()
    {
        var focused = Backups.Children.OfType<BackupSummary>()
            .Select(card => (card.Selector, Action: card.FocusedAction))
            .FirstOrDefault(item => item.Action is not null);
        var backups = model.AllBackups;
        CatalogState.Text = !model.Loaded ? "Reading your configured backups…" : backups.Count == 0
            ? "No backups are configured yet. Set up your first backup to get started."
            : backups.Count == 1 ? "1 configured backup" : $"{backups.Count} configured backups";
        ProblemText.Visibility = model.Problem.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        TechnicalPanel.Visibility = model.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        SetupAction.IsEnabled = !session.ExitPending;
        RefreshAction.IsEnabled = !session.Busy && !session.ExitPending;
        Backups.Children.Clear();
        foreach (var backup in backups)
        {
            var summary = new BackupSummary(backup, true, model.DesignPreview,
                model.CanBackUpTask(backup.Id), model.RunState(backup.Id));
            summary.BackupRequested += selector => _ = model.BackUpNowAsync(selector);
            summary.RemoveQueuedRequested += selector => model.RemoveFromQueue(selector);
            summary.OpenRequested += OpenSnapshot;
            summary.PreviewAction += model.ShowPrototypeAction;
            Backups.Children.Add(summary);
        }
        if (focused.Action is { } action)
            Backups.Children.OfType<BackupSummary>().FirstOrDefault(card => card.Selector == focused.Selector)?.RestoreFocus(action);
    }

    private void OpenSnapshot(string path)
    {
        if (!Directory.Exists(path))
        {
            OpenNotice.Message = "The saved Backup folder is not available right now. No files were changed.";
            OpenNotice.Visibility = Visibility.Visible;
            OpenNotice.IsOpen = true;
            return;
        }
        try
        {
            var start = new ProcessStartInfo("explorer.exe") { UseShellExecute = false };
            start.ArgumentList.Add(path);
            Process.Start(start);
        }
        catch (Exception error)
        {
            OpenNotice.Message = "File Explorer couldn't open the saved Backup. No Backup files were changed.";
            OpenNotice.Visibility = Visibility.Visible;
            OpenNotice.IsOpen = true;
            Debug.WriteLine(error);
        }
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await model.RefreshAsync(session);
    private void Setup_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.BackupSetup);
}
