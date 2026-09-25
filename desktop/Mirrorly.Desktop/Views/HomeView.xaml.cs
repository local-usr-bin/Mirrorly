using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using System.Diagnostics;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.ViewModels;
using Mirrorly.Desktop.Services;
namespace Mirrorly.Desktop.Views;
public sealed partial class HomeView : UserControl
{
    public HomeViewModel Model { get; }
    public event Action<ShellPage>? Navigate;
    public event Action<bool>? DecorationChanged;
    private readonly IDesktopSession session;
    public HomeView(IDesktopSession session, BackupExecutionCoordinator execution)
    {
        this.session = session;
        Model = new(execution);
        InitializeComponent();
        DataContext = Model;
        Model.PropertyChanged += (_, args) => {
            if (args.PropertyName == nameof(Model.PrototypeMessage)) { PreviewNotice.Visibility = Visibility.Visible; PreviewNotice.IsOpen = true; }
            else RenderFixture();
        };
        SizeChanged += (_, _) => UpdateLayoutMode();
        RenderFixture();
    }
    private void RenderFixture()
    {
        Populated.Visibility = Model.ShowEmpty ? Visibility.Collapsed : Visibility.Visible;
        EmptyCard.Visibility = Model.ShowEmpty ? Visibility.Visible : Visibility.Collapsed;
        AdditionalSetup.Visibility = Model.ShowEmpty ? Visibility.Collapsed : Visibility.Visible;
        ProblemText.Visibility = Model.Problem.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        TechnicalPanel.Visibility = Model.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        Backups.Children.Clear();
        foreach (var backup in Model.Backups)
        {
            var summary = new BackupSummary(backup, Model.CompactBackups, Model.DesignPreview,
                Model.CanBackUpTask(backup.Id), Model.RunState(backup.Id));
            summary.PreviewAction += Model.ShowPrototypeAction;
            summary.BackupRequested += selector => _ = Model.BackUpNowAsync(selector);
            summary.RemoveQueuedRequested += selector => Model.RemoveFromQueue(selector);
            summary.OpenRequested += OpenSnapshot;
            Backups.Children.Add(summary);
        }
        StatusBadge.Style = (Style)Application.Current.Resources[Model.Status.Tone == StatusTone.Success ? "MirrorlySuccessBadge" : "MirrorlyStatusBadge"];
        StatusSymbol.Style = (Style)Application.Current.Resources[Model.Status.Tone == StatusTone.Success ? "MirrorlySuccessBadgeText" : $"Mirrorly{Model.Status.Tone}Text"];
        PrimaryAction.IsEnabled = Model.DesignPreview ? !Model.Status.Busy : Model.CanBackUp;
        ToolTipService.SetToolTip(PrimaryAction, Model.BackupActionHelp);
        AutomationProperties.SetHelpText(PrimaryAction, Model.BackupActionHelp);
        DetailsAction.Visibility = Model.TechnicalDetails.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = Model.IsBackingUp || Model.DesignPreview && Model.Scenario == HomeScenario.Running ? Visibility.Visible : Visibility.Collapsed;
        ManyHint.Visibility = AllBackups.Visibility = Model.ShowAllBackups ? Visibility.Visible : Visibility.Collapsed;
        PreviewNotice.IsOpen = false;
        PreviewNotice.Visibility = Visibility.Collapsed;
        DecorationChanged?.Invoke(Model.ShowDecoration);
        UpdateLayoutMode();
    }
    private void UpdateLayoutMode()
    {
        var stacked = HomePolicy.StackStatus(ActualWidth);
        Grid.SetColumn(StatusActions, stacked ? 0 : 1);
        Grid.SetRow(StatusActions, stacked ? 1 : 0);
        ActionColumn.Width = stacked ? new GridLength(0) : GridLength.Auto;
        StatusActions.HorizontalAlignment = stacked ? HorizontalAlignment.Left : HorizontalAlignment.Stretch;
        HeaderDecoration.Visibility = Model.ShowDecoration && ActualWidth >= HomePolicy.CompactActivityBelow ? Visibility.Visible : Visibility.Collapsed;
        var compact = ActualWidth < HomePolicy.CompactActivityBelow;
        ActivityCard.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        CompactActivity.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
    }
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await Model.RefreshAsync(session);
    private async void Primary_Click(object sender, RoutedEventArgs e)
    {
        if (Model.DesignPreview) Model.ShowPrototypeAction(Model.Status.Action);
        else await Model.BackUpNowAsync();
    }
    private void OpenSnapshot(string path)
    {
        if (!Directory.Exists(path))
        {
            Model.ShowPresentationNotice("The saved Backup location is not available right now. No files were changed.");
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
            Model.ShowPresentationNotice("File Explorer couldn't open the saved Backup. No Backup files were changed. View technical details for the cause.", error.ToString());
        }
    }
    private void ActivitySymbol_DataContextChanged(FrameworkElement sender, DataContextChangedEventArgs args)
    {
        if (sender is TextBlock text && args.NewValue is ActivityPresentation activity)
            text.Style = (Style)Application.Current.Resources[$"Mirrorly{activity.Tone}Text"];
    }
    private void PreviewNotice_Closed(InfoBar sender, InfoBarClosedEventArgs args) => PreviewNotice.Visibility = Visibility.Collapsed;
    private void Details_Click(object sender, RoutedEventArgs e) => TechnicalPanel.IsExpanded = true;
    private void Setup_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.BackupSetup);
    private void AllBackups_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Backups);
    private void Activity_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Activity);
}
