using Microsoft.UI.Xaml;

namespace Haven.Desktop.Themes;

/// <summary>
/// Compiled theme/density dictionaries, instantiated directly instead of
/// loaded via ms-appx:///XamlReader.Load: dynamic XAML parsing crashes
/// natively in this app's unpackaged deployment mode (WindowsPackageType
/// None has no package identity for the XAML metadata resolver dynamic
/// loading depends on). Merging a pre-compiled instance is the supported
/// unpackaged path -- the same mechanism MainWindow.xaml itself already
/// relies on.
/// </summary>
internal static class ThemeDictionaries
{
    public static ResourceDictionary CreateTheme(string themeName, string appearance) =>
        (themeName.ToLowerInvariant(), appearance) switch
        {
            ("haven", "Light") => new HavenLightDictionary(),
            ("haven", "Dark") => new HavenDarkDictionary(),
            ("canopy", "Light") => new CanopyLightDictionary(),
            ("canopy", "Dark") => new CanopyDarkDictionary(),
            ("ember", "Light") => new EmberLightDictionary(),
            ("ember", "Dark") => new EmberDarkDictionary(),
            ("mono", "Light") => new MonoLightDictionary(),
            ("mono", "Dark") => new MonoDarkDictionary(),
            _ => throw new ArgumentOutOfRangeException(
                nameof(themeName), themeName, $"Unknown theme/appearance combination '{themeName}'/'{appearance}'"),
        };

    public static ResourceDictionary CreateDensity(string densityName) =>
        densityName.ToLowerInvariant() switch
        {
            "comfortable" => new DensityComfortableDictionary(),
            "compact" => new DensityCompactDictionary(),
            "touch" => new DensityTouchDictionary(),
            _ => throw new ArgumentOutOfRangeException(
                nameof(densityName), densityName, $"Unknown density '{densityName}'"),
        };
}
