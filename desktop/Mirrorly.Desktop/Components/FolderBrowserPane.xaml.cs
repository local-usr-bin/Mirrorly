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
        model.Diagnostics.UiSnapshot = NavigationSnapshot;
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
    private async void Back_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; using var request = Model.Diagnostics.BeginRequest("back-key"); await Model.BackAsync(); FocusAfterNavigation(request.Sequence); }
    private async void Up_Invoked(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args) { args.Handled = true; using var request = Model.Diagnostics.BeginRequest("up-key"); await Model.UpAsync(); FocusAfterNavigation(request.Sequence); }
    private async void Back_Click(object sender, RoutedEventArgs e) { using var request = Model.Diagnostics.BeginRequest("back"); await Model.BackAsync(); FocusAfterNavigation(request.Sequence); }
    private async void Up_Click(object sender, RoutedEventArgs e) { using var request = Model.Diagnostics.BeginRequest("up"); await Model.UpAsync(); FocusAfterNavigation(request.Sequence); }
    private void FocusAfterNavigation(long navigation)
    {
        // A disabled/removed focused row can move focus outside this pane during async refresh.
        // Wait for the new containers, then keep keyboard navigation in the initiating pane.
        var diagnostics = Model.Diagnostics;
        var focusSequence = diagnostics.NextFocus();
        var queuedList = diagnostics.Enabled ? FolderNavigationDiagnostics.ObjectId(Model.Folders) : "off";
        string Correlation() => $"origin_nav={navigation} latest_nav={diagnostics.LatestNavigation} same_nav={navigation == diagnostics.LatestNavigation} focus_seq={focusSequence} queued_list={queuedList}";
        var enqueued = DispatcherQueue.TryEnqueue(() =>
        {
            diagnostics.Record("F1 callback entered", Correlation);
            if (!IsLoaded) return;
            diagnostics.Record("F2 before UpdateLayout", Correlation);
            FolderList.UpdateLayout();
            diagnostics.Record("F3 after UpdateLayout", Correlation);
            // Same branch order, same container query and same single Focus attempt as before.
            Control target;
            string requested;
            if (Model.IsEditing) { target = AddressInput; requested = "AddressInput"; }
            else if (FolderList.ContainerFromIndex(0) is Control row) { target = row; requested = "first-row"; }
            else { target = AddressButton; requested = "AddressButton"; }
            diagnostics.Record("F4 before Focus", () => $"{Correlation()} requested={requested} target={FolderNavigationDiagnostics.ObjectId(target)}");
            var focused = target.Focus(FocusState.Keyboard);
            diagnostics.Record("F5 after Focus", () => $"{Correlation()} requested={requested} focus_result={focused}");
        });
        diagnostics.Record("F0 focus queued", () => $"{Correlation()} enqueued={enqueued}");
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
            using var request = Model.Diagnostics.BeginRequest("address-enter");
            await Model.NavigateAsync(AddressInput.Text);
            FocusAfterNavigation(request.Sequence);
        }
        else if (e.Key == VirtualKey.Escape) { e.Handled = true; Model.CancelEdit(); AddressButton.Focus(FocusState.Keyboard); }
    }
    private async void FolderList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        Model.Diagnostics.Record("S0 SelectionChanged", () => $"added={e.AddedItems.Count} removed={e.RemovedItems.Count}");
        if (FolderList.SelectedItem is FolderEntry folder) await Model.SelectAsync(folder.Path);
    }
    private async void FolderList_DoubleTapped(object sender, DoubleTappedRoutedEventArgs e)
    {
        // Only a row double-click enters a directory; blank list space must not activate an old selection.
        var element = e.OriginalSource as DependencyObject;
        while (element is not null && element is not ListViewItem) element = VisualTreeHelper.GetParent(element);
        var folder = element is ListViewItem row ? FolderList.ItemFromContainer(row) as FolderEntry : null;
        using var request = Model.Diagnostics.BeginRequest("double-tap", folder is not null);
        if (folder is not null)
        { e.Handled = true; await Model.NavigateAsync(folder.Path); FocusAfterNavigation(request.Sequence); }
    }
    private async void FolderList_KeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter && FolderList.SelectedItem is FolderEntry folder)
        { e.Handled = true; using var request = Model.Diagnostics.BeginRequest("row-enter", true); await Model.NavigateAsync(folder.Path); FocusAfterNavigation(request.Sequence); }
    }
}
