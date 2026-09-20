using Microsoft.UI.Xaml.Controls;
namespace Mirrorly.Desktop.Views;
public sealed partial class PlaceholderView : UserControl
{
    public PlaceholderView(string title) { InitializeComponent(); Heading.Text = title; }
}
