using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Media.Animation;
using Mirrorly.Desktop.Presentation;
namespace Mirrorly.Desktop.Components;
public sealed partial class SpringSprig : UserControl
{
    private EventHandler<object>? flourishCompleted;
    public string AssetName { get; set; } = "spring-sprig-sidebar";
    public SpringSprig()
    {
        InitializeComponent();
        if (!HomePolicy.DecorationsEnabled) Visibility = Visibility.Collapsed;
        // ActualWidth bindings can retain zero when navigation first measures a collapsed footer.
        SizeChanged += (_, args) => { Artwork.Width = args.NewSize.Width; Artwork.Height = args.NewSize.Height; };
        Loaded += (_, _) => Artwork.Source = new SvgImageSource(new Uri($"ms-appx:///Assets/Decorations/{AssetName}.svg"));
    }
    public void PlayPetalFlourish(Action completed)
    {
        var storyboard = (Storyboard)Resources["PetalFlourish"];
        if (flourishCompleted is not null) storyboard.Completed -= flourishCompleted;
        storyboard.Stop();
        flourishCompleted = (_, _) => {
            storyboard.Completed -= flourishCompleted;
            flourishCompleted = null;
            completed();
        };
        storyboard.Completed += flourishCompleted;
        storyboard.Begin();
    }
}
