using Microsoft.UI.Xaml;
using Windows.UI.ViewManagement;

namespace Haven.Desktop;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        UnhandledException += (_, e) =>
        {
            var operation = _window is null ? "core.startup" : "desktop.request";
            var report = NativeFaults.Message(e.Exception, operation);
            try { DesktopPaths.AppendDiagnostic("UNHANDLED: " + report + System.Environment.NewLine); }
            catch { /* Original evidence stays at the selected root; no fallback. */ }
        };
        InitializeComponent();
    }

    internal static bool SystemIsLight() =>
        new UISettings().GetColorValue(UIColorType.Background).R >= 128;

    /// <summary>
    /// The bootstrap dictionaries <see cref="MergeDefaultTheme"/> merges
    /// before <see cref="ThemeService"/> exists, so MainWindow.xaml's
    /// ThemeResource/StaticResource lookups (color tokens *and* density
    /// tokens -- HavenNavItemStyle etc. need DensityNavItemHeight at parse
    /// time) have something to resolve; <see cref="ThemeService.Apply"/>
    /// removes them on its first run so exactly one of each stays merged.
    /// </summary>
    internal static ResourceDictionary? BootstrapThemeDictionary { get; private set; }
    internal static ResourceDictionary? BootstrapDensityDictionary { get; private set; }

    private void MergeDefaultTheme()
    {
        // App.xaml cannot know the system theme at parse time, so the first
        // flat theme dictionary is merged here; ThemeService swaps it live
        // afterwards (flat dictionaries only â€” swapping ThemeDictionaries
        // dictionaries at runtime breaks framework resource caching).
        //
        // Must be called from OnLaunched, never from this constructor:
        // mutating Application.Current.Resources.MergedDictionaries during
        // the App constructor crashes the process natively on this
        // WindowsAppSDK/unpackaged combination (no managed exception, no
        // debugger break -- just an access-violation-style exit). The
        // identical call from OnLaunched is safe.
        var appearance = SystemIsLight() ? "Light" : "Dark";
        var theme = Themes.ThemeDictionaries.CreateTheme(ThemeService.DefaultTheme, appearance);
        Application.Current.Resources.MergedDictionaries.Add(theme);
        BootstrapThemeDictionary = theme;

        var density = Themes.ThemeDictionaries.CreateDensity(ThemeService.DefaultDensity);
        Application.Current.Resources.MergedDictionaries.Add(density);
        BootstrapDensityDictionary = density;
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        MergeDefaultTheme();
        _window = new MainWindow();
        _window.Activate();
    }
}
