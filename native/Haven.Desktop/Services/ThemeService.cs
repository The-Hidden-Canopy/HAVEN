using System.Text.Json;
using Microsoft.UI.Xaml;
using Windows.UI.ViewManagement;

namespace Haven.Desktop;

/// <summary>
/// Appearance settings (spec section 40): theme palette, system/light/dark
/// mode, density and motion preference.  Persists to
/// <c>%LOCALAPPDATA%/Haven.Desktop/settings.json</c>; invalid content fails
/// closed to the Haven palette in system mode.  Applying a theme swaps one
/// merged resource dictionary, live, with no restart.
/// </summary>
public sealed class ThemeService
{
    public const string DefaultTheme = "haven";
    public const string DefaultMode = "system";
    public const string DefaultDensity = "comfortable";
    public const string DefaultMotion = "system";

    public static readonly string[] Themes = { "haven", "canopy", "ember", "mono" };
    public static readonly string[] Modes = { "system", "light", "dark" };
    public static readonly string[] Densities = { "comfortable", "compact" };
    public static readonly string[] Motions = { "system", "reduced" };

    private static string SettingsPath =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Haven.Desktop",
            "settings.json");

    public string Theme { get; private set; } = DefaultTheme;
    public string Mode { get; private set; } = DefaultMode;
    public string Density { get; private set; } = DefaultDensity;
    public string Motion { get; private set; } = DefaultMotion;

    public void Load()
    {
        try
        {
            if (!File.Exists(SettingsPath))
            {
                return;
            }
            using var document = JsonDocument.Parse(File.ReadAllText(SettingsPath));
            ApplyChoices(document.RootElement);
        }
        catch (Exception)
        {
            // Fail closed: a corrupt settings file never breaks the shell.
            Reset();
        }
    }

    public void SetAppearance(string theme, string mode, string density, string motion)
    {
        ApplyChoices(
            JsonSerializer.SerializeToElement(
                new { theme, mode, density, motion }));
    }

    private void ApplyChoices(JsonElement root)
    {
        Theme = ReadChoice(root, "theme", Themes, DefaultTheme);
        Mode = ReadChoice(root, "mode", Modes, DefaultMode);
        Density = ReadChoice(root, "density", Densities, DefaultDensity);
        Motion = ReadChoice(root, "motion", Motions, DefaultMotion);
    }

    private void Reset()
    {
        Theme = DefaultTheme;
        Mode = DefaultMode;
        Density = DefaultDensity;
        Motion = DefaultMotion;
    }

    private static string ReadChoice(JsonElement root, string name, string[] allowed, string fallback)
    {
        if (root.ValueKind == JsonValueKind.Object
            && root.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.String)
        {
            var text = value.GetString() ?? "";
            var match = allowed.FirstOrDefault(choice =>
                string.Equals(choice, text, StringComparison.OrdinalIgnoreCase));
            if (match is not null)
            {
                return match;
            }
        }
        return fallback;
    }

    /// <summary>Swap the merged flat theme + density dictionaries and re-apply chrome.</summary>
    public void Apply(MainWindow window)
    {
        var merged = Application.Current.Resources.MergedDictionaries;
        RemoveMerged(merged, "/Themes/", except: "/Themes/Density");

        var appearance = EffectiveAppearance();
        var themeName = string.Concat(Theme[..1].ToUpperInvariant(), Theme.AsSpan(1));
        // Add first (last-merged wins every key lookup), remove the previous
        // theme dictionary afterwards: removing a merged dictionary while it
        // is the active lookup target breaks theme-resource resolution.
        var incoming = new ResourceDictionary { Source = new Uri($"ms-appx:///Themes/{themeName}{appearance}.xaml") };
        merged.Add(incoming);
        RemoveMerged(merged, "/Themes/", except: "/Themes/Density", skipLast: true);

        var densityName = string.Concat(Density[..1].ToUpperInvariant(), Density.AsSpan(1));
        var incomingDensity = new ResourceDictionary { Source = new Uri($"ms-appx:///Themes/Density{densityName}.xaml") };
        merged.Add(incomingDensity);
        RemoveMerged(merged, "/Themes/Density", skipLast: true);

        // High contrast always wins: a flat canvas beats a decorative gradient.
        var flatCanvas = new AccessibilitySettings().HighContrast;
        window.SetRequestedTheme(appearance == "Dark" ? "dark" : "light");
        window.SetFlatCanvas(flatCanvas);
        window.ApplyTitleBarColors();
    }

    private static void RemoveMerged(
        IList<ResourceDictionary> merged, string sourceContains, string? except = null, bool skipLast = false)
    {
        var start = merged.Count - (skipLast ? 2 : 1);
        for (var index = start; index >= 0; index--)
        {
            var source = merged[index].Source?.OriginalString ?? "";
            if (source.Contains(sourceContains, StringComparison.OrdinalIgnoreCase)
                && (except is null || !source.Contains(except, StringComparison.OrdinalIgnoreCase)))
            {
                merged.RemoveAt(index);
            }
        }
    }

    private string EffectiveAppearance()
    {
        if (Mode == "dark")
        {
            return "Dark";
        }
        if (Mode == "light")
        {
            return "Light";
        }
        return App.SystemIsLight() ? "Light" : "Dark";
    }

    public async Task SaveAsync()
    {
        try
        {
            var directory = Path.GetDirectoryName(SettingsPath);
            if (directory is not null)
            {
                Directory.CreateDirectory(directory);
            }
            var payload = JsonSerializer.Serialize(
                new { mode = Mode, theme = Theme, density = Density, motion = Motion });
            await File.WriteAllTextAsync(SettingsPath, payload);
        }
        catch (Exception)
        {
            // Persistence is best-effort; the live appearance always applies.
        }
    }
}
