using System.Text.Json;
using System.Text;
using Haven.Desktop.Setup;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow : Window
{
    private HavenCoreClient? _client;
    private string? _selectedClaimId;
    private string _currentTag = "today";

    public MainWindow()
    {
        InitializeComponent();
        _themeService.Load();
        // Defer the first apply until the window is loaded: swapping merged
        // dictionaries during InitializeComponent breaks the framework's
        // theme-resource cache.
        DispatcherQueue.TryEnqueue(() => _themeService.Apply(this));
        // The shell draws its own title bar: system caption buttons stay,
        // everything else is HAVEN's canvas (mockup 3's seamless chrome).
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(DragStrip);
        ApplyTitleBarColors();
        UpdateLogo();
        RootGrid().ActualThemeChanged += (_, _) =>
        {
            UpdateLogo();
            ApplyTitleBarColors();
        };
        RootGrid().Loaded += OnLoaded;
        RootGrid().SizeChanged += OnRootSizeChanged;
        SelectHomeTab("rooms");
        SelectModelsTab("local");
        SelectNavigation("today");
        // Debug/screenshot affordance: open a specific page at launch.
        var startPage = Environment.GetEnvironmentVariable("HAVEN_START_PAGE");
        if (!string.IsNullOrWhiteSpace(startPage))
        {
            SelectNavigation(startPage.Trim());
        }
        var searchAccelerator = new KeyboardAccelerator
        {
            Key = VirtualKey.K,
            Modifiers = VirtualKeyModifiers.Control,
        };
        searchAccelerator.Invoked += (_, _) => SelectNavigation("search");
        RootGrid().KeyboardAccelerators.Add(searchAccelerator);
        SyncAppearanceControls();
    }

    private Grid RootGrid() => (Grid)Content;

    public void ApplyTitleBarColors()
    {
        var titleBar = AppWindow.TitleBar;
        titleBar.ButtonBackgroundColor = Microsoft.UI.Colors.Transparent;
        titleBar.ButtonInactiveBackgroundColor = Microsoft.UI.Colors.Transparent;
        var dark = RootGrid().ActualTheme == ElementTheme.Dark;
        titleBar.ButtonHoverBackgroundColor = dark
            ? Microsoft.UI.ColorHelper.FromArgb(0x33, 0xFF, 0xFF, 0xFF)
            : Microsoft.UI.ColorHelper.FromArgb(0x33, 0x00, 0x00, 0x00);
        titleBar.ButtonForegroundColor = dark ? Microsoft.UI.Colors.White : Microsoft.UI.Colors.Black;
    }

    /// <summary>Flat-canvas fallback for high contrast (spec section 34).</summary>
    public void SetFlatCanvas(bool flat)
    {
        Root.Background = flat
            ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenCanvasBrush"]
            : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenCanvasGradient"];
    }

    /// <summary>Set the resolved appearance on the root element.</summary>
    public void SetRequestedTheme(string appearance)
    {
        // The effective appearance is always resolved by ThemeService
        // (system mode included); ElementTheme.Default is never assigned.
        Root.RequestedTheme = appearance == "dark" ? ElementTheme.Dark : ElementTheme.Light;
    }

}
