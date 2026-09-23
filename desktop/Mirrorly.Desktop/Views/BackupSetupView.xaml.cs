using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
namespace Mirrorly.Desktop.Views;

public sealed partial class BackupSetupView : UserControl
{
    public BackupSetupViewModel Model { get; }
    private readonly FolderBrowserPane sourcePane;
    private readonly FolderBrowserPane destinationPane;
    public event Action<ShellPage>? Navigate;
    public event Action? StepChanged;
    public Func<Task<bool>>? ConfirmCopy { get; set; }
    private bool initialized;
    public BackupSetupView(IDesktopSession session)
    {
        Model = new(new FolderBrowserService(), session);
        InitializeComponent(); DataContext = Model;
        sourcePane = new(Model.Source); destinationPane = new(Model.Destination);
        Panes.Children.Add(sourcePane); Panes.Children.Add(destinationPane);
        Model.PropertyChanged += (_, _) => Refresh();
        Loaded += async (_, _) => { if (!initialized) { initialized = true; await Model.InitializeAsync(); } };
        SizeChanged += (_, _) => UpdateLayoutPolicy(); Refresh();
    }
    private void UpdateLayoutPolicy()
    {
        var stacked = SetupPreview.StackPanes(ActualWidth);
        Grid.SetRow(destinationPane, stacked ? 1 : 0); Grid.SetColumn(destinationPane, stacked ? 0 : 1);
        Panes.ColumnDefinitions[1].Width = stacked ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Panes.ColumnSpacing = stacked ? 0 : (double)Application.Current.Resources["MirrorlyCardSpacing"];
    }
    private void Refresh()
    {
        ChooseStep.Visibility = Model.IsReview ? Visibility.Collapsed : Visibility.Visible;
        ReviewStep.Visibility = Model.IsReview ? Visibility.Visible : Visibility.Collapsed;
        StepText.Text = Model.IsReview ? "Step 2 of 2 · Review backup" : "Step 1 of 2 · Choose folders";
        SamePathNotice.IsOpen = Model.SamePathWarning;
        NameHint.Visibility = string.IsNullOrWhiteSpace(Model.BackupName) ? Visibility.Visible : Visibility.Collapsed;
        SetupNotice.IsOpen = Model.IsReview;
        SetupNotice.Severity = Model.State is SetupState.Partial or SetupState.Unknown or SetupState.Blocked or SetupState.Rejected or SetupState.Unavailable ? InfoBarSeverity.Warning : InfoBarSeverity.Informational;
    }
    private async void Continue_Click(object sender, RoutedEventArgs e)
    {
        await Model.ContinueAsync();
        if (Model.IsReview) { StepChanged?.Invoke(); NameInput.Focus(FocusState.Keyboard); }
    }
    private void Back_Click(object sender, RoutedEventArgs e) { Model.Back(); StepChanged?.Invoke(); }
    private async void Check_Click(object sender, RoutedEventArgs e) => await Model.CheckAsync();
    private async void Create_Click(object sender, RoutedEventArgs e) => await Model.CreateAsync(() => ConfirmCopy?.Invoke() ?? Task.FromResult(false));
    private void Return_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Home);
}
