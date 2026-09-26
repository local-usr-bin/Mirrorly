using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Mirrorly.Desktop.Services;
using Windows.Foundation;

namespace Mirrorly.Desktop.Components;

public sealed partial class FolderBrowserPane
{
    // Called only by the opted-in, bounded logger. Property reads only: no layout,
    // template application, focus, scrolling, event subscription or retained containers.
    private string NavigationSnapshot(string point)
    {
        if (!DispatcherQueue.HasThreadAccess) return "HasThreadAccess=false ui=unavailable";
        var basic = $"HasThreadAccess=true pane_loaded={IsLoaded} selected_index={FolderList.SelectedIndex} selected_item_present={FolderList.SelectedItem is not null} " +
            $"list_ui=({ElementState(FolderList)}) ui_count={FolderList.Items.Count} focus={SafeRead(FocusStateText)}";
        if (!point.StartsWith('F')) return basic;
        var container = SafeRead(ContainerState);
        if (!(point.StartsWith("F2") || point.StartsWith("F3") || point.StartsWith("F5"))) return $"{basic} container0=({container})";
        return $"{basic} container0=({container}) inner_scroll=({SafeRead(() => ScrollState(FindInnerScroll()))}) " +
            $"outer_scroll=({SafeRead(() => ScrollState(FindOuterScroll()))})";
    }
    private static string Number(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);
    private static string ElementState(FrameworkElement element) =>
        $"type={element.GetType().Name} id={FolderNavigationDiagnostics.ObjectId(element)} loaded={element.IsLoaded} visibility={element.Visibility} opacity={Number(element.Opacity)} " +
        $"width={Number(element.ActualWidth)} height={Number(element.ActualHeight)} enabled={(element is Control control ? control.IsEnabled.ToString() : "na")}";
    private static string SafeRead(Func<string> read)
    {
        try { return read(); }
        catch (Exception error) { return $"unavailable exception={error.GetType().Name} hresult=0x{error.HResult:X8}"; }
    }
    private string FocusStateText()
    {
        var target = XamlRoot is null ? null : FocusManager.GetFocusedElement(XamlRoot);
        // Name is deliberately allowlisted; never log Automation.Name or text/content.
        var name = target is FrameworkElement element ? element.Name : "";
        name = name switch { "AddressInput" or "AddressButton" or "FolderList" or "BackButton" or "UpButton" or "UseCurrentButton" or "TogglePaneButton" or "NameInput" => name, _ => "other-or-none" };
        return $"{target?.GetType().Name ?? "none"}/{name}";
    }
    private string ContainerState()
    {
        if (FolderList.ContainerFromIndex(0) is not FrameworkElement container) return "exists=false";
        var bounds = SafeRead(() => RectText(container.TransformToVisual(FolderList).TransformBounds(new Rect(0, 0, container.ActualWidth, container.ActualHeight))));
        var clip = container.Clip;
        return $"exists=true {ElementState(container)} bounds={bounds} clip={clip?.GetType().Name ?? "none"}" +
            (clip is RectangleGeometry rectangle ? $" clip_rect={RectText(rectangle.Rect)}" : "");
    }
    private static string RectText(Rect rect) => $"{Number(rect.X)},{Number(rect.Y)},{Number(rect.Width)},{Number(rect.Height)}";
    private ScrollViewer? FindInnerScroll()
    {
        // Bounded template lookup, not a visual-tree dump. Failure is explicit and benign.
        var queue = new Queue<(DependencyObject Node, int Depth)>();
        queue.Enqueue((FolderList, 0));
        for (var visited = 0; queue.Count > 0 && visited < 32; visited++)
        {
            var (node, depth) = queue.Dequeue();
            if (node is ScrollViewer scroll) return scroll;
            if (depth >= 8) continue;
            var count = VisualTreeHelper.GetChildrenCount(node);
            for (var i = 0; i < count && queue.Count < 32; i++) queue.Enqueue((VisualTreeHelper.GetChild(node, i), depth + 1));
        }
        return null;
    }
    private ScrollViewer? FindOuterScroll()
    {
        DependencyObject? node = this;
        for (var depth = 0; node is not null && depth < 32; depth++)
        {
            node = VisualTreeHelper.GetParent(node);
            if (node is ScrollViewer scroll) return scroll;
        }
        return null;
    }
    private static string ScrollState(ScrollViewer? scroll) => scroll is null ? "found=false" :
        $"found=true {ElementState(scroll)} offset={Number(scroll.HorizontalOffset)},{Number(scroll.VerticalOffset)} " +
        $"viewport={Number(scroll.ViewportWidth)},{Number(scroll.ViewportHeight)} extent={Number(scroll.ExtentWidth)},{Number(scroll.ExtentHeight)} " +
        $"scrollable={Number(scroll.ScrollableWidth)},{Number(scroll.ScrollableHeight)} zoom={Number(scroll.ZoomFactor)} bring_focus={scroll.BringIntoViewOnFocusChange}";
}
