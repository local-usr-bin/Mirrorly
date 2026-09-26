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
    internal Func<string>? ShellDiagnosticSnapshot { get; set; }
    public BackupSetupView(IDesktopSession session)
    {
        Model = new(new FolderBrowserService(), session);
        InitializeComponent(); DataContext = Model;
        if (SetupDiagnostics.Enabled) SetupDiagnostics.UiSnapshot = DiagnosticSnapshot;
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
        SetupDiagnostics.Record("T0 Continue_Click entry", Model.DiagnosticState);
        try
        {
            await Model.ContinueAsync();
            SetupDiagnostics.Record("T6 ContinueAsync returned", Model.DiagnosticState);
            if (Model.IsReview)
            {
                StepChanged?.Invoke();
                var focused = NameInput.Focus(FocusState.Keyboard);
                SetupDiagnostics.Record("T7 after Focus", () => $"{Model.DiagnosticState()} focus_result={focused}");
            }
        }
        catch (Exception error) { SetupDiagnostics.Record("Continue_Click catch/rethrow", Model.DiagnosticState, error); throw; }
        finally
        {
            SetupDiagnostics.Record("Continue_Click exit", Model.DiagnosticState);
            if (SetupDiagnostics.Enabled)
            {
                try
                {
                    var queued = DispatcherQueue.TryEnqueue(() => SetupDiagnostics.Record("T8 dispatcher callback entered", Model.DiagnosticState));
                    SetupDiagnostics.Record("T8 enqueue result", () => $"queued={queued}");
                }
                catch (Exception error) { SetupDiagnostics.Record("diagnostic enqueue catch", error: error); }
            }
        }
    }
    private string DiagnosticSnapshot()
    {
        if (!DispatcherQueue.HasThreadAccess) return "HasThreadAccess=false; UI properties not read";
        static string Box(FrameworkElement element) => $"Visibility={element.Visibility},Opacity={element.Opacity},Width={element.ActualWidth},Height={element.ActualHeight}";
        var focus = XamlRoot is null ? null : Microsoft.UI.Xaml.Input.FocusManager.GetFocusedElement(XamlRoot);
        // Names are XAML identifiers, never Text/Content or AutomationProperties.Name.
        var name = (focus as FrameworkElement)?.Name;
        return $"HasThreadAccess=true {Model.DiagnosticState()} ChooseStep=[{Box(ChooseStep)}] ReviewStep=[{Box(ReviewStep)}] " +
            $"InfoBar=[Open={SetupNotice.IsOpen},Severity={SetupNotice.Severity},TitleState={(string.IsNullOrEmpty(SetupNotice.Title) ? "empty" : "present")},MessageState={(string.IsNullOrEmpty(SetupNotice.Message) ? "empty" : "present")}] " +
            $"Focus=[Type={focus?.GetType().FullName ?? "null"},Name={name}] Shell=[{ShellDiagnosticSnapshot?.Invoke() ?? "unavailable"}]";
    }
    private void Back_Click(object sender, RoutedEventArgs e) { Model.Back(); StepChanged?.Invoke(); }
    private async void Check_Click(object sender, RoutedEventArgs e) => await Model.CheckAsync();
    private async void Create_Click(object sender, RoutedEventArgs e) => await Model.CreateAsync(() => ConfirmCopy?.Invoke() ?? Task.FromResult(false));
    private void Return_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Home);
}
