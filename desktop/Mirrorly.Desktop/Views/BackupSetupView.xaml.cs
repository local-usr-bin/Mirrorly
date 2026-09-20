using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Mirrorly.Desktop.Components;
using Mirrorly.Desktop.Presentation;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
namespace Mirrorly.Desktop.Views;

public sealed partial class BackupSetupView : UserControl
{
    public BackupSetupViewModel Model { get; } = new(new FolderBrowserService());
    private readonly FolderBrowserPane sourcePane;
    private readonly FolderBrowserPane destinationPane;
    public event Action<ShellPage>? Navigate;
    public event Action? StepChanged;
    public BackupSetupView()
    {
        InitializeComponent();
        DataContext = Model;
        sourcePane = new(Model.Source);
        destinationPane = new(Model.Destination);
        Panes.Children.Add(sourcePane);
        Panes.Children.Add(destinationPane);
        Model.PropertyChanged += (_, _) => Refresh();
        PrototypeNotice.Message = BackupSetupViewModel.PrototypeMessage;
        Loaded += async (_, _) => await Model.InitializeAsync();
        SizeChanged += (_, _) => UpdateLayoutPolicy();
        Refresh();
    }
    private void UpdateLayoutPolicy()
    {
        var stacked = SetupPreview.StackPanes(ActualWidth);
        Grid.SetRow(destinationPane, stacked ? 1 : 0);
        Grid.SetColumn(destinationPane, stacked ? 0 : 1);
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
        PrototypeNotice.IsOpen = Model.ShowPrototypeNotice;
        ReturnButton.Visibility = Model.ShowPrototypeNotice ? Visibility.Visible : Visibility.Collapsed;
    }
    private async void Continue_Click(object sender, RoutedEventArgs e)
    {
        await Model.ContinueAsync();
        if (Model.IsReview) { StepChanged?.Invoke(); NameInput.Focus(FocusState.Keyboard); }
    }
    private void Back_Click(object sender, RoutedEventArgs e) { Model.Back(); StepChanged?.Invoke(); ContinueButton.Focus(FocusState.Keyboard); }
    private void Create_Click(object sender, RoutedEventArgs e) { Model.CreatePrototype(); ReturnButton.Focus(FocusState.Keyboard); }
    private void Return_Click(object sender, RoutedEventArgs e) => Navigate?.Invoke(ShellPage.Home);
}
