using Microsoft.UI.Xaml;
using Windows.UI.ViewManagement;

namespace Haven.Desktop;

public partial class App : Application
{
    private Window? _window;

    private static string DbgPath => System.IO.Path.Combine(System.IO.Path.GetTempPath(), "haven-dbg.txt");

    public App()
    {
        UnhandledException += (_, e) =>
        {
            System.IO.File.AppendAllText(DbgPath, "UNHANDLED: " + e.Exception + System.Environment.NewLine);
        };
        InitializeComponent();
        MergeDefaultTheme();
    }

    internal static bool SystemIsLight() =>
        new UISettings().GetColorValue(UIColorType.Background).R >= 128;

    private void MergeDefaultTheme()
    {
        // App.xaml cannot know the system theme at parse time, so the first
        // flat theme dictionary is merged here; ThemeService swaps it live
        // afterwards (flat dictionaries only — swapping ThemeDictionaries
        // dictionaries at runtime breaks framework resource caching).
        var appearance = SystemIsLight() ? "Light" : "Dark";
        Application.Current.Resources.MergedDictionaries.Add(
            new ResourceDictionary { Source = new Uri($"ms-appx:///Themes/Haven{appearance}.xaml") });
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        _window.Activate();
    }
}
