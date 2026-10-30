using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop.Controls;

/// <summary>
/// Hinged gate-latch/hasp vector mark (base plate, hinge barrel, strap,
/// staple, T-handle) used by the Latch plugin. The same geometry serves the
/// page heading (full) and the navigation rail (simplified). Theming is
/// inherited: every part binds a Haven* ThemeResource token, so the mark
/// follows ThemeService's live Light/Dark/System swap with no palette of
/// its own.
/// </summary>
public sealed partial class LatchHaspMark : UserControl
{
    public LatchHaspMark()
    {
        InitializeComponent();
        ApplyMode();
    }

    /// <summary>True keeps only the strap/staple/handle silhouette for
    /// small surfaces (navigation rows); false renders the full hardware
    /// set (page heading).</summary>
    public bool Simplified
    {
        get => (bool)GetValue(SimplifiedProperty);
        set => SetValue(SimplifiedProperty, value);
    }

    public static readonly DependencyProperty SimplifiedProperty =
        DependencyProperty.Register(
            nameof(Simplified),
            typeof(bool),
            typeof(LatchHaspMark),
            new PropertyMetadata(false, OnSimplifiedChanged));

    private static void OnSimplifiedChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        if (sender is LatchHaspMark mark)
        {
            mark.ApplyMode();
        }
    }

    private void ApplyMode()
    {
        var simplified = Simplified;
        // Full mark: base plate, screws, hinge barrel and pin disappear;
        // the strap, staple and T-handle carry the simplified silhouette.
        BasePlate.Visibility = simplified ? Visibility.Collapsed : Visibility.Visible;
        ScrewTop.Visibility = simplified ? Visibility.Collapsed : Visibility.Visible;
        ScrewBottom.Visibility = simplified ? Visibility.Collapsed : Visibility.Visible;
        HingeBarrel.Visibility = simplified ? Visibility.Collapsed : Visibility.Visible;
        HingePin.Visibility = simplified ? Visibility.Collapsed : Visibility.Visible;
    }
}
