namespace Haven.Desktop;

/// <summary>One explicit state root for native settings and diagnostics.</summary>
public static class DesktopPaths
{
    private static string? _installationRoot;
    private static bool _awaitingInstallation;
    private static bool UsesInstallationRoot =>
        Environment.GetEnvironmentVariable("HAVEN_DESKTOP_STATE_POLICY") == "installation"
        || (Environment.GetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR") is null
            && Environment.GetEnvironmentVariable("HAVEN_DATA_DIR") is not null);

    public static bool AwaitingInstallation => _awaitingInstallation;

    public static string Root
    {
        get
        {
            if (UsesInstallationRoot)
            {
                if (_awaitingInstallation)
                    throw new InvalidOperationException("Native state is awaiting the confirmed installation directory.");
                if (_installationRoot is not null) return _installationRoot;
            }
            return ResolveRoot(
                Environment.GetEnvironmentVariable("HAVEN_DESKTOP_DATA_DIR"),
                Environment.GetEnvironmentVariable("HAVEN_DATA_DIR"),
                () => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Haven.Desktop"));
        }
    }

    public static void BeginInstallationMove()
    {
        if (UsesInstallationRoot) _awaitingInstallation = true;
    }

    public static void ConfirmInstallation(string coreRoot)
    {
        if (!_awaitingInstallation) return;
        _installationRoot = Path.Combine(Absolute(coreRoot), "desktop");
        _awaitingInstallation = false;
    }

    public static string ResolveRoot(string? desktopRoot, string? coreRoot, Func<string> defaultRoot)
    {
        // An invalid explicit override must fail before any fallback reads a
        // real user's state. The Core always supplies an absolute installation.
        if (desktopRoot is not null)
            return Absolute(desktopRoot);
        if (coreRoot is not null)
            return Path.Combine(Absolute(coreRoot), "desktop");
        return Absolute(defaultRoot());
    }

    private static string Absolute(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || !Path.IsPathFullyQualified(value))
            throw new ArgumentException("Native state directory must be an absolute path.");
        return Path.GetFullPath(value);
    }

    public static string File(string name) => Path.Combine(Root, name);

    public static void AppendDiagnostic(string text)
    {
        var root = Root;
        Directory.CreateDirectory(root);
        System.IO.File.AppendAllText(Path.Combine(root, "native-diagnostics.log"), text);
    }
}
