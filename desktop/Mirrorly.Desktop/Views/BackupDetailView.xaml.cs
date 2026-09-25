using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;

namespace Mirrorly.Desktop.Views;

// A selector route over the existing catalog and GUI coordinator, not a second state owner.
public sealed partial class BackupDetailView : UserControl
{
    private readonly HomeViewModel model;
    private readonly IDesktopSession session;
    private string? selector;
    public event Action<string>? ReturnRequested;

    public BackupDetailView(HomeViewModel model, IDesktopSession session)
    {
        this.model = model;
        this.session = session;
        InitializeComponent();
        model.PropertyChanged += (_, _) => Render();
    }

    public void Select(string taskSelector)
    {
        selector = taskSelector;
        model.SelectBackup(taskSelector);
        Render();
    }

    public void FocusReturnAction() => BackAction.Focus(FocusState.Programmatic);

    private void Render()
    {
        if (selector is null) return;
        var overview = model.Overview(selector);
        BackupTitle.Text = overview.Name;
        var unavailable = overview.Availability is BackupOverviewAvailability.Missing or
            BackupOverviewAvailability.CatalogUnavailable or BackupOverviewAvailability.Loading;
        UnavailableText.Visibility = unavailable ? Visibility.Visible : Visibility.Collapsed;
        UnavailableText.Text = overview.Attention;
        OverviewContent.Visibility = unavailable ? Visibility.Collapsed : Visibility.Visible;
        SourcePath.Text = overview.Source;
        BackupLocationPath.Text = overview.BackupLocation;
        RepositoryPath.Text = overview.RepositoryPath ?? "Current repository location unavailable";
        SavedVersion.Text = overview.SavedVersion;
        CurrentState.Text = overview.Status;
        CurrentState.Style = (Style)Application.Current.Resources[$"Mirrorly{overview.Tone}Text"];
        AttentionText.Text = overview.Attention;
        AttentionText.Visibility = overview.Attention.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        BackupAction.Content = overview.RunState switch
        {
            BackupTaskRunState.Running => "Backing up…",
            BackupTaskRunState.Queued => "Queued",
            _ => "Back up now"
        };
        BackupAction.IsEnabled = overview.CanBackUp;
        RemoveAction.Visibility = overview.RunState == BackupTaskRunState.Queued && !unavailable
            ? Visibility.Visible : Visibility.Collapsed;
        RefreshAction.IsEnabled = !session.Busy && !session.ExitPending;
        TechnicalText.Text = overview.TechnicalDetails;
        TechnicalPanel.Visibility = overview.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private void Back_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) ReturnRequested?.Invoke(selector);
    }
    private async void Backup_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) await model.BackUpNowAsync(selector);
    }
    private void Remove_Click(object sender, RoutedEventArgs e)
    {
        if (selector is not null) model.RemoveFromQueue(selector);
    }
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await model.RefreshAsync(session);
}
