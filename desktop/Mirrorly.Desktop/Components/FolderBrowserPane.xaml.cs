using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Mirrorly.Desktop.Services;
using Mirrorly.Desktop.ViewModels;
using Windows.System;
namespace Mirrorly.Desktop.Components;

public sealed partial class FolderBrowserPane : UserControl
{
    public FolderBrowserViewModel Model { get; }
    public FolderBrowserPane(FolderBrowserViewModel model)
    {
        Model = model;
        InitializeComponent();
        DataContext = model;
        AddressInput.Header = $"{model.Title} folder path";
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(AddressButton, $"{model.Title} address. Edit path (Ctrl+L)");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(BackButton, $"{model.Title}: Back");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(UpButton, $"{model.Title}: Up");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(FolderList, $"{model.Title} folders");
        model.PropertyChanged += (_, _) => Refresh();
        Refresh();
    }
    private void Refresh()
    {
        AddressButton.Visibility = Model.IsEditing ? Visibility.Collapsed : Visibility.Visible;
        AddressInput.Visibility = Model.IsEditing ? Visibility.Visible : Visibility.Collapsed;
        AddressButton.IsEnabled = AddressInput.IsEnabled = FolderList.IsEnabled = !Model.IsBrowsing;
        ErrorBar.IsOpen = Model.Error is not null;
        LoadingIndicator.Visibility = Model.IsBrowsing || Model.IsValidating ? Visibility.Visible : Visibility.Collapsed;
        UseCurrentButton.IsEnabled = Model.CurrentPath is not null && !Model.IsBrowsing;
        EmptyMessage.Visibility = Model.CurrentPath is not null && Model.Folders.Count == 0 && !Model.IsBrowsing && Model.Error is null
            ? Visibility.Visible : Visibility.Collapsed;
    }
    private void EditPath()
    {
        if (Model.IsBrowsing) return;
        AddressInput.Text = Model.CurrentPath ?? "";
        Model.BeginEdit();
        AddressInput.Focus(FocusState.Programmatic);
        AddressInput.SelectAll();
    }
    private void EditPath_Click(object sender, RoutedEventArgs e) => EditPath();
    private void EditPath_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { EditPath(); args.Handled = true; }
    private async void Back_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; await Model.BackAsync(); FocusAfterNavigation(); }
    private async void Up_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; await Model.UpAsync(); FocusAfterNavigation(); }
    private async void Back_Click(object sender, RoutedEventArgs e) { await Model.BackAsync(); FocusAfterNavigation(); }
    private async void Up_Click(object sender, RoutedEventArgs e) { await Model.UpAsync(); FocusAfterNavigation(); }
    private void FocusAfterNavigation()
    {
        // A disabled/removed focused row can move focus outside this pane during async refresh.
        // Wait for the new containers, then keep keyboard navigation in the initiating pane.
        DispatcherQueue.TryEnqueue(() =>
        {
            if (!IsLoaded) return;
            FolderList.UpdateLayout();
            if (Model.IsEditing) AddressInput.Focus(FocusState.Keyboard);
            else if (FolderList.ContainerFromIndex(0) is Control row) row.Focus(FocusState.Keyboard);
            else AddressButton.Focus(FocusState.Keyboard);
        });
    }
    private async void UseCurrent_Click(object sender, RoutedEventArgs e)
    {
        if (Model.CurrentPath is not { } path) return;
        FolderList.SelectedItem = null;
        Model.CancelEdit();
        await Model.SelectAsync(path);
    }
    private async void AddressInput_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter)
        {
            e.Handled = true;
            await Model.NavigateAsync(AddressInput.Text);
            FocusAfterNavigation();
        }
        else if (e.Key == VirtualKey.Escape) { e.Handled = true; Model.CancelEdit(); AddressButton.Focus(FocusState.Keyboard); }
    }
    private async void FolderList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (FolderList.SelectedItem is FolderEntry folder) await Model.SelectAsync(folder.Path);
    }
    private async void FolderList_DoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        // Only a row double-click enters a directory; blank list space must not activate an old selection.
        var element = e.OriginalSource as DependencyObject;
        while (element is not null && element is not ListViewItem) element = VisualTreeHelper.GetParent(element);
        if (element is ListViewItem row && FolderList.ItemFromContainer(row) is FolderEntry folder)
        { e.Handled = true; await Model.NavigateAsync(folder.Path); FocusAfterNavigation(); }
    }
    private async void FolderList_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter && FolderList.SelectedItem is FolderEntry folder)
        { e.Handled = true; await Model.NavigateAsync(folder.Path); FocusAfterNavigation(); }
    }
}
