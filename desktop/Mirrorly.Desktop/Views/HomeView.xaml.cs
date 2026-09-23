using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.ViewModels;
using Mirrorly.Desktop.Services;
namespace Mirrorly.Desktop.Views;
public sealed partial class HomeView : UserControl
{
    public HomeViewModel Model { get; } = new();
    public event Action<ShellPage>? Navigate;
    public event Action<bool>? DecorationChanged;
    private readonly IDesktopSession session;
    public HomeView(IDesktopSession session)
    {
        this.session = session;
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
            var summary = new BackupSummary(backup, Model.CompactBackups, Model.DesignPreview);
            summary.PreviewAction += Model.ShowPrototypeAction;
            Backups.Children.Add(summary);
        }
        StatusBadge.Style = (Style)Application.Current.Resources[Model.Status.Tone == StatusTone.Success ? "MirrorlySuccessBadge" : "MirrorlyStatusBadge"];
        StatusSymbol.Style = (Style)Application.Current.Resources[Model.Status.Tone == StatusTone.Success ? "MirrorlySuccessBadgeText" : $"Mirrorly{Model.Status.Tone}Text"];
        PrimaryAction.IsEnabled = Model.DesignPreview && !Model.Status.Busy;
        DetailsAction.Visibility = Model.DesignPreview && Model.Status.Tone is StatusTone.Warning or StatusTone.Error && Model.Status.Action != "View details" ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = Model.DesignPreview && Model.Scenario == HomeScenario.Running ? Visibility.Visible : Visibility.Collapsed;
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
    private void Primary_Click(object sender, RoutedEventArgs e) => Model.ShowPrototypeAction(Model.Status.Action);
    private void ActivitySymbol_DataContextChanged(FrameworkElement sender, DataContextChangedEventArgs args)
    {
        if (sender is TextBlock text && args.NewValue is ActivityPresentation activity)
            text.Style = (Style)Application.Current.Resources[$"Mirrorly{activity.Tone}Text"];
    }
    private void PreviewNotice_Closed(InfoBar sender, InfoBarClosedEventArgs args) => PreviewNotice.Visibility = Visibility.Collapsed;
    private void Details_Click(object sender, RoutedEventArgs e) => Model.ShowPrototypeAction("View details");
    private void Setup_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.BackupSetup);
    private void AllBackups_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Backups);
    private void Activity_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Activity);
}
