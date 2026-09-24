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

    // -- appearance (spec 40) ---------------------------------------------------

    private readonly ThemeService _themeService = new();
    private bool _appearanceReady;

    private void OnAppearanceChanged(object sender, RoutedEventArgs args)
    {
        if (_appearanceReady)
        {
            _ = ApplyAppearanceAsync();
        }
    }

    private void OnAppearanceSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_appearanceReady)
        {
            _ = ApplyAppearanceAsync();
        }
    }

    private async Task ApplyAppearanceAsync()
    {
        var mode = ModeDarkRadio.IsChecked == true
            ? "dark"
            : ModeLightRadio.IsChecked == true ? "light" : "system";
        var theme = (ThemePicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultTheme;
        var density = (DensityPicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultDensity;
        var motion = (MotionPicker.SelectedItem as ComboBoxItem)?.Tag as string ?? ThemeService.DefaultMotion;
        _themeService.SetAppearance(theme, mode, density, motion);
        _themeService.Apply(this);
        ApplyDensityPreview();
        await _themeService.SaveAsync();
    }

    private void ApplyDensityPreview()
    {
        // The global density token pass lands in phase 4; the preview block
        // shows the compact row height live.
        AppearancePreviewNav.Height = _themeService.Density == "compact" ? 30 : 38;
    }

    private void SyncAppearanceControls()
    {
        ModeSystemRadio.IsChecked = _themeService.Mode == "system";
        ModeLightRadio.IsChecked = _themeService.Mode == "light";
        ModeDarkRadio.IsChecked = _themeService.Mode == "dark";
        SelectAppearanceItem(ThemePicker, _themeService.Theme);
        SelectAppearanceItem(DensityPicker, _themeService.Density);
        SelectAppearanceItem(MotionPicker, _themeService.Motion);
        ApplyDensityPreview();
        _appearanceReady = true;
    }

    private static void SelectAppearanceItem(ComboBox picker, string tag)
    {
        foreach (var item in picker.Items.OfType<ComboBoxItem>())
        {
            if (item.Tag as string == tag)
            {
                picker.SelectedItem = item;
                return;
            }
        }
    }

    // -- tablet adaptation (spec 06/07/21) -------------------------------------
    // Below ~900px the rail collapses to icons, touch targets grow, the
    // content padding tightens, and right-pane inspectors stack below their
    // lists instead of sitting beside them.

    private const double NarrowWidth = 900;
    private bool _narrow;

    private void OnRootSizeChanged(object sender, SizeChangedEventArgs args)
    {
        ApplyAdaptiveLayout(args.NewSize.Width);
    }

    private void ApplyAdaptiveLayout(double width)
    {
        var narrow = width < NarrowWidth;
        if (narrow == _narrow && width > 0)
        {
            return;
        }
        _narrow = narrow;
        ShellRoot().ColumnDefinitions[0].Width = new Microsoft.UI.Xaml.GridLength(narrow ? 72 : 228);

        foreach (var button in NavItems.Children.OfType<Button>())
        {
            if (button.Content is StackPanel panel)
            {
                foreach (var text in panel.Children.OfType<TextBlock>())
                {
                    text.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
                }
            }
            button.Height = narrow ? 44 : 38;
        }
        ReopenSetupButton.Height = narrow ? 44 : double.NaN;
        ProfileText.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;

        ContentScroll.Padding = narrow
            ? new Microsoft.UI.Xaml.Thickness(16, 12, 16, 20)
            : new Microsoft.UI.Xaml.Thickness(30, 16, 30, 24);

        // Memory: wide = list + right inspector; narrow = stacked single column.
        if (narrow)
        {
            MemorySplit.ColumnDefinitions.Clear();
            MemorySplit.RowDefinitions.Clear();
            MemorySplit.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            MemorySplit.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            MemoryClaims.MaxHeight = 320;
            Grid.SetColumn(MemoryInspector, 0);
            Grid.SetRow(MemoryInspector, 1);
        }
        else
        {
            MemorySplit.RowDefinitions.Clear();
            MemorySplit.ColumnDefinitions.Clear();
            MemorySplit.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
            MemorySplit.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1.2, Microsoft.UI.Xaml.GridUnitType.Star) });
            MemoryClaims.MaxHeight = 560;
            Grid.SetRow(MemoryInspector, 0);
            Grid.SetColumn(MemoryInspector, 1);
        }
    }

    private Grid ShellRoot() => RootGrid();

    private void UpdateLogo()
    {
        var source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(
            new Uri(RootGrid().ActualTheme == ElementTheme.Dark
                ? "ms-appx:///Assets/haven-logo-light.png"
                : "ms-appx:///Assets/haven-logo-dark.png"));
        LogoImage.Source = source;
        SplashLogo.Source = source;
    }

    private string? _connectionPipeName;
    private string? _connectionToken;
    private ConnectionService? _connection;
    private Task? _connectionTask;

    private async void OnLoaded(object sender, RoutedEventArgs args)
    {
        var pipeName = CommandLineValue("--pipe-name") ?? Environment.GetEnvironmentVariable("HAVEN_IPC_PIPE");
        var token = await ResolveAuthTokenAsync();
        if (string.IsNullOrWhiteSpace(pipeName) || string.IsNullOrWhiteSpace(token))
        {
            ConnectionText.Text = "Native Core not connected";
            ComposerStatus.Text = "Start HAVEN Core with a named-pipe endpoint to connect this native client.";
            SplashOverlay.Visibility = Visibility.Collapsed;
            return;
        }

        _connectionPipeName = pipeName;
        _connectionToken = token;
        _connection = new ConnectionService(
            connectOnce: async () =>
            {
                // Reconnect lifecycle: drop the dead pipe before re-authenticating.
                await (_client?.DisconnectAsync() ?? Task.CompletedTask);
                var client = new HavenCoreClient(_connectionPipeName!, _connectionToken!);
                await client.ConnectAsync();
                _client = client;
                return client;
            },
            onStateChanged: state => DispatcherQueue.TryEnqueue(() => ApplyConnectionState(state)),
            onReconciled: async () =>
            {
                await EnsureSetupAsync();
                ShowPage(_currentTag);
                SplashOverlay.Visibility = Visibility.Collapsed;
                EnsureEventClientAsync();
            });
        _connectionTask = _connection.RunAsync();
    }

    private void ApplyConnectionState(string state)
    {
        ConnectionText.Text = state switch
        {
            ConnectionService.StateConnected => "Connected",
            ConnectionService.StateDegraded => "Connected with issues",
            ConnectionService.StateReconnecting => "Reconnecting…",
            ConnectionService.StateConnecting => "Connecting to HAVEN Core…",
            _ => "Core offline",
        };
        switch (state)
        {
            case ConnectionService.StateConnected:
                ConnectionBanner.Visibility = Visibility.Collapsed;
                break;
            case ConnectionService.StateDegraded:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "Connected with issues — some data may be stale.";
                break;
            case ConnectionService.StateReconnecting:
            case ConnectionService.StateConnecting:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "Reconnecting to HAVEN Core…";
                break;
            default:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "HAVEN Core is unreachable. Changes are on hold; your input is kept.";
                break;
        }
        // One-shot composer control: submissions are allowed only on a healthy
        // connection (spec section 11).
        ComposerSendButton.IsEnabled = state == ConnectionService.StateConnected && !_composerBusy;
    }

    private async void OnConnectionRetryClicked(object sender, RoutedEventArgs args)
    {
        if (_connection is null)
        {
            return;
        }
        await _connection.RetryNowAsync();
        // Retry Now from DISCONNECTED: the loop already exited, so restart it.
        if (_connectionTask is { IsCompleted: true })
        {
            _connectionTask = _connection.RunAsync();
        }
    }

    // -- domain invalidation via the events pipe (spec 15-17) ------------------

    private HavenEventClient? _eventClient;
    private readonly HashSet<string> _dirtyDomains = new();
    private bool _eventRefreshInFlight;
    private bool _eventRefreshRequested;

    private static readonly Dictionary<string, string[]> EventDomains = new()
    {
        ["tasks.changed"] = new[] { "tasks" },
        ["projects.changed"] = new[] { "projects" },
        ["relationships.changed"] = new[] { "relationships" },
        ["memory.changed"] = new[] { "memory" },
        ["search.index.changed"] = new[] { "search" },
        ["computer.files.changed"] = new[] { "computer" },
        ["computer.windows.changed"] = new[] { "computer" },
        ["computer.activity.changed"] = new[] { "computer" },
        ["email.changed"] = new[] { "comms" },
        ["calendar.changed"] = new[] { "comms" },
        ["browser.tabs.changed"] = new[] { "comms" },
        ["home.state.changed"] = new[] { "home", "authority" },
        ["authority.pending.changed"] = new[] { "home", "authority" },
        ["models.changed"] = new[] { "models" },
        ["model.job.progress"] = new[] { "models" },
        ["core.shutdown"] = Array.Empty<string>(),
    };

    private static string[] DomainsForPage(string tag) => tag switch
    {
        "today" => new[] { "tasks", "projects", "relationships", "comms", "models", "home" },
        "tasks" => new[] { "tasks", "projects" },
        "projects" => new[] { "projects", "tasks", "relationships" },
        "people" => new[] { "home", "relationships" },
        "memory" => new[] { "memory", "search" },
        "computer" => new[] { "computer" },
        "communications" => new[] { "comms" },
        "home" => new[] { "home", "authority" },
        "models" => new[] { "models" },
        "search" => new[] { "search" },
        _ => Array.Empty<string>(),
    };

    private void EnsureEventClientAsync()
    {
        if (_eventClient is { IsConnected: true }
            || string.IsNullOrWhiteSpace(_connectionPipeName)
            || string.IsNullOrWhiteSpace(_connectionToken))
        {
            return;
        }
        var client = new HavenEventClient(_connectionPipeName, _connectionToken!)
        {
            EventReceived = (eventName, _payload) =>
                DispatcherQueue.TryEnqueue(() => OnDomainEvent(eventName)),
            ConnectionLost = () => DispatcherQueue.TryEnqueue(async () => await OnEventConnectionLostAsync()),
        };
        _eventClient = client;
        _ = Task.Run(async () =>
        {
            try
            {
                await client.ConnectAsync();
            }
            catch (Exception)
            {
                // The RPC channel's liveness probe reports core health; the
                // events channel reconnects quietly on the next reconcile.
            }
        });
    }

    private async Task OnEventConnectionLostAsync()
    {
        if (_eventClient is null || _connection is not { IsOnline: true })
        {
            return;
        }
        MarkAllDomainsDirty();
        await Task.Delay(2000);
        EnsureEventClientAsync();
    }

    private void OnDomainEvent(string eventName)
    {
        if (eventName == "core.shutdown")
        {
            MarkAllDomainsDirty();
            return;
        }
        if (!EventDomains.TryGetValue(eventName, out var domains))
        {
            return;
        }
        if (domains.Length == 0)
        {
            return;
        }
        var visible = DomainsForPage(_currentTag);
        if (domains.Any(visible.Contains))
        {
            // The changed domain is on screen: reconcile it now.
            RequestEventRefresh();
        }
        else
        {
            // Background domain: invalidate and reconcile on next activation.
            _dirtyDomains.UnionWith(domains);
        }
    }

    private void MarkAllDomainsDirty()
    {
        foreach (var domains in EventDomains.Values)
        {
            _dirtyDomains.UnionWith(domains);
        }
    }

    private void RequestEventRefresh()
    {
        if (_eventRefreshInFlight)
        {
            // Collapse bursts: one coalesced re-render of the visible page.
            _eventRefreshRequested = true;
            return;
        }
        _eventRefreshInFlight = true;
        var tag = _currentTag;
        _ = Task.Run(async () =>
        {
            try
            {
                var completion = new TaskCompletionSource();
                if (!DispatcherQueue.TryEnqueue(() =>
                {
                    try
                    {
                        ShowPage(tag);
                        completion.TrySetResult();
                    }
                    catch (Exception ex)
                    {
                        completion.TrySetException(ex);
                    }
                }))
                {
                    completion.TrySetCanceled();
                }
                await completion.Task;
            }
            catch (Exception)
            {
                // A failed refresh leaves the domain dirty for the next pass.
            }
            finally
            {
                var again = _eventRefreshRequested;
                _eventRefreshRequested = false;
                _eventRefreshInFlight = false;
                if (again)
                {
                    RequestEventRefresh();
                }
            }
        });
    }

    private async Task EnsureSetupAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var status = await _client.GetSetupStatusAsync();
            if (!IsSetupComplete(status))
            {
                ShowSetupWizard();
                await SetupWizard.LoadAsync();
            }
            else
            {
                ReopenSetupButton.Visibility = Visibility.Visible;
            }
        }
        catch (Exception ex)
        {
            // Setup status is best-effort: the main window stays usable without it.
            ComposerStatus.Text = ex.Message;
        }
    }

    private static bool IsSetupComplete(JsonElement status) =>
        status.ValueKind == JsonValueKind.Object
        && status.TryGetProperty("setup", out var setup)
        && setup.TryGetProperty("completed", out var completed)
        && completed.ValueKind == JsonValueKind.True;

    private void ShowSetupWizard()
    {
        SetupWizard.Initialize(_client!, WinRT.Interop.WindowNative.GetWindowHandle(this));
        SetupOverlay.Visibility = Visibility.Visible;
    }

    private void OnSetupCompleted(object sender, EventArgs args)
    {
        SetupOverlay.Visibility = Visibility.Collapsed;
        ReopenSetupButton.Visibility = Visibility.Visible;
    }

    private async void OnReopenSetupClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await _client.ReopenSetupAsync();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ComposerStatus.Text = envelope.TryGetProperty("error", out var error)
                    ? error.GetString()
                    : "HAVEN Core refused to reopen setup.";
                return;
            }
            ShowSetupWizard();
            await SetupWizard.LoadAsync();
        }
        catch (Exception ex)
        {
            ComposerStatus.Text = ex.Message;
        }
    }

    private async void OnSearchClicked(object sender, RoutedEventArgs args)
    {
        await SearchAsync();
    }

    private async void OnSearchKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            await SearchAsync();
        }
    }

    private async Task SearchAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(SearchBox.Text))
        {
            return;
        }
        try
        {
            var result = await _client.SearchAsync(SearchBox.Text.Trim());
            SearchResults.Items.Clear();
            foreach (var hit in result.GetProperty("hits").EnumerateArray())
            {
                var title = hit.TryGetProperty("resource", out var resource) && resource.ValueKind == JsonValueKind.Object
                    && resource.TryGetProperty("title", out var titleValue)
                    ? titleValue.GetString()
                    : hit.GetProperty("resource_id").GetString();
                SearchResults.Items.Add(new TextBlock { Text = $"{title}\n{hit.GetProperty("reason").GetString()}" });
            }
        }
        catch (Exception ex)
        {
            SearchResults.Items.Clear();
            SearchResults.Items.Add(new TextBlock { Text = ex.Message });
        }
    }

    private async Task LoadMemoryAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetKnowledgeClaimsAsync();
            MemoryClaims.Items.Clear();
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            foreach (var claim in result.GetProperty("claims").EnumerateArray())
            {
                var claimId = claim.GetProperty("claim_id").GetString();
                var proposition = claim.GetProperty("proposition").GetString() ?? "";
                var state = claim.GetProperty("state").GetString() ?? "unknown";
                var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
                    ? confidenceValue.GetDouble().ToString("P0")
                    : "—";
                var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
                    ? provenanceValue.GetString()
                    : "unknown";
                var summary = new StackPanel { Spacing = 3 };
                summary.Children.Add(new TextBlock
                {
                    Text = proposition,
                    TextWrapping = TextWrapping.Wrap,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
                summary.Children.Add(new TextBlock
                {
                    Text = $"{Sentence(state)} · {confidence} · {provenance}",
                    Opacity = 0.72,
                });
                MemoryClaims.Items.Add(new ListViewItem { Tag = claimId, Content = summary });
            }
            if (MemoryClaims.Items.Count == 0)
            {
                MemoryClaims.Items.Add(new TextBlock
                {
                    Text = "HAVEN has not admitted any memories yet.",
                    Opacity = 0.72,
                });
                MemoryDetailText.Text = "Give HAVEN access to a text document, or tell it something directly, to begin building explainable memory.";
            }
            else
            {
                MemoryDetailText.Text = "Select a memory to inspect its evidence.";
                MemoryActionStatus.Text = "";
            }
        }
        catch (Exception ex)
        {
            MemoryClaims.Items.Clear();
            MemoryClaims.Items.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
            MemoryDetailText.Text = "HAVEN could not load memory evidence.";
            MemoryActionStatus.Text = "";
        }
    }

    private async void OnMemorySelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_client is null || MemoryClaims.SelectedItem is not ListViewItem item || item.Tag is not string claimId)
        {
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            return;
        }
        _selectedClaimId = claimId;
        SetMemoryActionButtons(true);
        try
        {
            var result = await _client.GetKnowledgeClaimAsync(claimId);
            MemoryDetailText.Text = FormatClaimDetail(result.GetProperty("claim"));
        }
        catch (Exception ex)
        {
            MemoryDetailText.Text = ex.Message;
        }
    }

    private void SetMemoryActionButtons(bool enabled)
    {
        MemoryCorrectButton.IsEnabled = enabled;
        MemoryStaleButton.IsEnabled = enabled;
        MemoryForgetButton.IsEnabled = enabled;
    }

    private async void OnMemoryCorrectClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var input = new TextBox
        {
            PlaceholderText = "What should HAVEN remember instead?",
            TextWrapping = TextWrapping.Wrap,
            AcceptsReturn = true,
            MinHeight = 90,
        };
        var dialog = new ContentDialog
        {
            Title = "Correct this memory",
            Content = input,
            PrimaryButtonText = "Save correction",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(input.Text))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.CorrectKnowledgeClaimAsync(_selectedClaimId, input.Text.Trim()),
            "Correction saved.");
    }

    private async void OnMemoryStaleClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.MarkKnowledgeClaimStaleAsync(_selectedClaimId),
            "Memory marked stale.");
    }

    private async void OnMemoryForgetClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Forget this memory?",
            Content = new TextBlock
            {
                Text = "HAVEN will keep the history as stale and suppress the same candidate from being re-admitted.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Forget",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.ForgetKnowledgeClaimAsync(_selectedClaimId),
            "Memory forgotten.");
    }

    private async Task RunMemoryMutationAsync(Func<Task<JsonElement>> operation, string successMessage)
    {
        SetMemoryActionButtons(false);
        try
        {
            await operation();
            MemoryActionStatus.Text = successMessage;
            await LoadMemoryAsync();
        }
        catch (Exception ex)
        {
            MemoryActionStatus.Text = ex.Message;
            SetMemoryActionButtons(!string.IsNullOrWhiteSpace(_selectedClaimId));
        }
    }

    private static string FormatClaimDetail(JsonElement claim)
    {
        var text = new StringBuilder();
        text.AppendLine(claim.GetProperty("proposition").GetString() ?? "");
        text.AppendLine();
        text.AppendLine($"STATE\n{claim.GetProperty("state").GetString()}");
        text.AppendLine();
        var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
            ? provenanceValue.GetString()
            : "unknown";
        var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
            ? confidenceValue.GetDouble().ToString("P0")
            : "—";
        text.AppendLine($"PROVENANCE\n{provenance}");
        text.AppendLine();
        text.AppendLine($"CONFIDENCE\n{confidence}");
        text.AppendLine();
        text.AppendLine("SOURCE");
        if (claim.TryGetProperty("sources", out var sources) && sources.GetArrayLength() > 0)
        {
            foreach (var source in sources.EnumerateArray())
            {
                var reference = source.GetProperty("ref").GetString() ?? "unknown source";
                var resource = source.TryGetProperty("resource", out var resourceValue)
                    && resourceValue.ValueKind == JsonValueKind.Object
                    ? resourceValue
                    : default;
                var title = resource.ValueKind == JsonValueKind.Object
                    && resource.TryGetProperty("title", out var titleValue)
                    ? titleValue.GetString()
                    : null;
                text.AppendLine($"• {title ?? reference}");
                if (title is not null && resource.TryGetProperty("locator", out var locatorValue))
                {
                    text.AppendLine($"  {locatorValue.GetString() ?? reference}");
                }
            }
        }
        else
        {
            text.AppendLine("None recorded");
        }
        AppendClaimGroup(text, "EVIDENCE", claim, "evidence_refs");
        AppendClaimGroup(text, "SUPERSEDES", claim, "supersedes");
        AppendClaimGroup(text, "CONTRADICTIONS", claim, "contradicts");
        AppendAuditHistory(text, claim);
        return text.ToString().TrimEnd();
    }

    private static void AppendClaimGroup(StringBuilder text, string label, JsonElement claim, string property)
    {
        text.AppendLine();
        text.AppendLine(label);
        if (!claim.TryGetProperty(property, out var values) || values.GetArrayLength() == 0)
        {
            text.AppendLine("None recorded");
            return;
        }
        foreach (var value in values.EnumerateArray())
        {
            text.AppendLine($"• {value.GetString()}");
        }
    }

    private static void AppendAuditHistory(StringBuilder text, JsonElement claim)
    {
        text.AppendLine();
        text.AppendLine("AUDIT HISTORY");
        if (!claim.TryGetProperty("audit", out var events) || events.GetArrayLength() == 0)
        {
            text.AppendLine("No user changes recorded");
            return;
        }
        foreach (var audit in events.EnumerateArray())
        {
            var action = audit.GetProperty("action").GetString() ?? "unknown";
            var actor = audit.GetProperty("actor_id").GetString() ?? "unknown actor";
            var at = audit.GetProperty("occurred_at").GetString() ?? "unknown time";
            text.AppendLine($"• {action} by {actor} at {at}");
        }
    }

    // -- persistent composer (spec 10) ----------------------------------------

    private bool _composerBusy;

    private async void OnComposerSendClicked(object sender, RoutedEventArgs args)
    {
        await SubmitComposerAsync();
    }

    private async void OnComposerKeyDown(object sender, KeyRoutedEventArgs args)
    {
        // Enter submits; Shift+Enter inserts a newline.
        var shift = Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Shift);
        if (args.Key == VirtualKey.Enter && (int)shift == 0)
        {
            args.Handled = true;
            await SubmitComposerAsync();
        }
    }

    private async Task SubmitComposerAsync()
    {
        if (_connection is { IsMutationsEnabled: false })
        {
            // Offline gate (spec section 11): the input is kept, not cleared.
            ComposerStatus.Text = "Core is offline — your message is kept; try again when connected.";
            return;
        }
        if (_client is null || _composerBusy || string.IsNullOrWhiteSpace(ComposerInput.Text))
        {
            return;
        }
        var text = ComposerInput.Text.Trim();
        _composerBusy = true;
        ComposerSendButton.IsEnabled = false;
        ComposerThinking.Visibility = Visibility.Visible;
        ComposerStatus.Text = "";
        try
        {
            var state = await _client.AskAsync(text);
            ComposerInput.Text = "";
            RenderComposerAnswer(state, text);
        }
        catch (Exception ex)
        {
            ComposerStatus.Text = $"Could not reach HAVEN: {ex.Message}";
        }
        finally
        {
            // One-shot control: the send button returns to idle however the
            // request ended (spec 12).
            _composerBusy = false;
            ComposerSendButton.IsEnabled = true;
            ComposerThinking.Visibility = Visibility.Collapsed;
        }
    }

    private void RenderComposerAnswer(JsonElement state, string question)
    {
        ComposerResults.Children.Clear();
        ComposerResults.Visibility = Visibility.Visible;

        string? answer = null;
        if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("conversation", out var conversation))
        {
            foreach (var entry in Enumerate(conversation))
            {
                if (GetString(entry, "from") == "haven")
                {
                    answer = GetString(entry, "text");
                }
            }
        }
        ComposerResults.Children.Add(new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            Child = new StackPanel
            {
                Spacing = 6,
                Children =
                {
                    new TextBlock { Text = question, Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"], TextWrapping = TextWrapping.Wrap },
                    new TextBlock { Text = answer ?? "HAVEN answered, but the reply did not include text.", Style = (Style)Application.Current.Resources["HavenBodyTextStyle"], TextWrapping = TextWrapping.Wrap },
                },
            },
        });

        // Mutation honesty (spec 10): anything action-like that this exchange
        // produced is a proposal awaiting review, never presented as done.
        var proposalNotes = new List<string>();
        if (state.ValueKind == JsonValueKind.Object)
        {
            var pending = state.TryGetProperty("pending", out var pendingValue) ? pendingValue.GetArrayLength() : 0;
            if (pending > 0)
            {
                proposalNotes.Add($"{pending} confirmation{(pending == 1 ? "" : "s")} awaiting your decision");
            }
            if (state.TryGetProperty("automations", out var automations))
            {
                var proposed = Enumerate(automations).Count(rule => GetString(rule, "status") == "proposed");
                if (proposed > 0)
                {
                    proposalNotes.Add($"{proposed} automation proposal{(proposed == 1 ? "" : "s")} awaiting approval");
                }
            }
        }
        foreach (var note in proposalNotes)
        {
            var banner = new Border
            {
                Style = (Style)Application.Current.Resources["HavenCardStyle"],
                BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenWarningBrush"],
                Child = new StackPanel
                {
                    Orientation = Orientation.Horizontal,
                    Spacing = 10,
                    Children =
                    {
                        new TextBlock { Text = $"Proposal — review required: {note}.", Style = (Style)Application.Current.Resources["HavenBodyTextStyle"], VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap },
                        new Button { Content = "Review in Home", VerticalAlignment = VerticalAlignment.Center },
                    },
                },
            };
            var review = (Button)((StackPanel)banner.Child).Children[1];
            review.Click += (_, _) => SelectNavigation("home");
            ComposerResults.Children.Add(banner);
        }
    }

    private static string? CommandLineValue(string key)
    {
        var args = Environment.GetCommandLineArgs();
        var index = Array.IndexOf(args, key);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static async Task<string?> ResolveAuthTokenAsync()
    {
        var token = Environment.GetEnvironmentVariable("HAVEN_IPC_TOKEN");
        if (!string.IsNullOrWhiteSpace(token))
        {
            return token;
        }

        var args = Environment.GetCommandLineArgs();
        if (!args.Contains("--ipc-token-stdin", StringComparer.Ordinal))
        {
            // The bearer token is intentionally never accepted from a
            // command-line argument. Manual launches use HAVEN_IPC_TOKEN;
            // the desktop launcher uses the inherited stdin handoff above.
            return null;
        }

        using var reader = new StreamReader(Console.OpenStandardInput());
        return (await reader.ReadLineAsync())?.Trim();
    }

    private void OnNavItemClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tag)
        {
            SelectNavigation(tag);
        }
    }

    private void SelectNavigation(string tag)
    {
        _currentTag = tag;
        // Rail selected state: filled blue rounded pill (spec 08).
        foreach (var button in NavItems.Children.OfType<Button>())
        {
            var selected = button.Tag?.ToString() == tag;
            button.Background = selected
                ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenAccentBrush"]
                : new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent);
            button.Foreground = selected
                ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White)
                : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenMutedTextBrush"];
            button.FontWeight = selected
                ? Microsoft.UI.Text.FontWeights.SemiBold
                : Microsoft.UI.Text.FontWeights.Normal;
        }
        ShowPage(tag);
        // Showing a page reloads its domains; drop their dirty marks.
        _dirtyDomains.ExceptWith(DomainsForPage(tag));
    }

    private void ShowPage(string tag)
    {
        TodayPanel.Visibility = tag == "today" ? Visibility.Visible : Visibility.Collapsed;
        SearchPanel.Visibility = tag == "search" ? Visibility.Visible : Visibility.Collapsed;
        ProjectsPanel.Visibility = tag == "projects" ? Visibility.Visible : Visibility.Collapsed;
        TasksPanel.Visibility = tag == "tasks" ? Visibility.Visible : Visibility.Collapsed;
        PeoplePanel.Visibility = tag == "people" ? Visibility.Visible : Visibility.Collapsed;
        MemoryPanel.Visibility = tag == "memory" ? Visibility.Visible : Visibility.Collapsed;
        ComputerPanel.Visibility = tag == "computer" ? Visibility.Visible : Visibility.Collapsed;
        CommunicationsPanel.Visibility = tag == "communications" ? Visibility.Visible : Visibility.Collapsed;
        HomePanel.Visibility = tag == "home" ? Visibility.Visible : Visibility.Collapsed;
        ModelsPanel.Visibility = tag == "models" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPanel.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
        (PageTitle.Text, PageDescription.Text) = tag switch
        {
            "today" => ("Today", "What matters now, and the fastest way to act on it."),
            "search" => ("Search your life", "Files, people, claims and anything else HAVEN can see, in one ranked list."),
            "projects" => ("Projects", "Bodies of work shared across files, tasks and people."),
            "tasks" => ("Tasks", "Commitments and next actions."),
            "people" => ("People", "Who lives here and how HAVEN senses their presence."),
            "memory" => ("Memory", "What HAVEN knows, where it came from, and how certain it is."),
            "computer" => ("Computer", "Your files, applications, windows and activity."),
            "communications" => ("Communications", "Email, messages and threads with their context."),
            "home" => ("Home", "Rooms, devices, automations and contexts. Rooms are yours even without a smart-home provider."),
            "models" => ("Models", "Local, downloaded and external intelligence. HAVEN runs on any mix of them."),
            "settings" => ("Settings", "Control boundaries: startup, storage, privacy, connections, about."),
            _ => ("HAVEN", ""),
        };
        if (tag == "models")
        {
            _ = LoadModelsAsync(silent: true);
        }
        switch (tag)
        {
            case "today":
                _ = LoadTodayAsync();
                break;
            case "search":
                SearchBox.Focus(FocusState.Programmatic);
                break;
            case "people":
                PeopleStatusText.Text = "Loading…";
                _ = LoadPeopleAsync();
                break;
            case "projects":
                ProjectsStatusText.Text = "Loading…";
                _ = LoadProjectsAsync();
                break;
            case "tasks":
                TasksStatusText.Text = "Loading…";
                _ = LoadTasksAsync();
                break;
            case "memory":
                _ = LoadMemoryAsync();
                break;
            case "computer":
                ComputerStatusText.Text = "Loading…";
                _ = LoadComputerTabAsync();
                break;
            case "communications":
                CommsStatusText.Text = "Loading…";
                _ = LoadCommsTabAsync();
                break;
            case "home":
                HomeStatusText.Text = "Loading…";
                _ = LoadRoomsAsync();
                _ = LoadAutomationsAsync();
                _ = LoadContextsAsync();
                break;
            case "models":
                _ = LoadModelsAsync();
                break;
            case "settings":
                SettingsStatusText.Text = "Loading…";
                _ = LoadSettingsAsync();
                break;
        }
    }

    // -- today ----------------------------------------------------------------

    private async Task LoadTodayAsync()
    {
        var hour = DateTime.Now.Hour;
        TodayGreeting.Text = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
        TodayDate.Text = DateTime.Now.ToString("dddd, MMMM d");
        TodayStatus.Text = "";
        if (_client is null)
        {
            return;
        }
        try
        {
            var state = await _client.GetStateAsync();
            var pending = state.TryGetProperty("pending", out var pendingValue) ? pendingValue.GetArrayLength() : 0;
            TodayPendingBanner.Visibility = pending > 0 ? Visibility.Visible : Visibility.Collapsed;
            TodayPendingText.Text = pending > 0
                ? $"{pending} action{(pending == 1 ? " is" : "s are")} waiting for your approval."
                : "";
        }
        catch (Exception ex)
        {
            TodayStatus.Text = $"Could not load today's summary: {ex.Message}";
        }
        await LoadTodayCardsAsync();
    }

    // -- today cards (spec page 25) ------------------------------------------------

    private async Task LoadTodayCardsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetTodayCardsAsync();
            RenderTodayCards(result.GetProperty("cards"));
        }
        catch (Exception ex)
        {
            TodayCardsHost.Children.Clear();
            TodayCardsHost.Children.Add(new TextBlock
            {
                Text = $"Could not load Today: {ex.Message}",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private void RenderTodayCards(JsonElement cards)
    {
        TodayCardsHost.Children.Clear();
        var rows = Enumerate(cards).ToList();
        if (rows.Count == 0)
        {
            TodayCardsHost.Children.Add(new Border
            {
                Style = (Style)Application.Current.Resources["HavenCardStyle"],
                Child = new TextBlock
                {
                    Text = "Nothing needs you right now. Tasks with due dates, pending decisions, and upcoming commitments will appear here.",
                    TextWrapping = TextWrapping.Wrap,
                    Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                },
            });
            return;
        }

        var focus = rows[0];
        var rest = rows.Skip(1).ToList();
        TodayCardsHost.Children.Add(MakeTodaySection("Focus", new List<JsonElement> { focus }, focusStyle: true));
        foreach (var (title, group) in new[]
        {
            ("Decisions", "authority"),
            ("Upcoming", "deadline"),
            ("Commitments", "commitment"),
            ("Suggestions", "suggestion"),
        })
        {
            var sectionCards = rest.Where(card => GetString(card, "group") == group).ToList();
            if (sectionCards.Count > 0)
            {
                TodayCardsHost.Children.Add(MakeTodaySection(title, sectionCards));
            }
        }
    }

    private Border MakeTodaySection(string title, List<JsonElement> cards, bool focusStyle = false)
    {
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(new TextBlock
        {
            Text = title,
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
        });
        foreach (var card in cards)
        {
            section.Children.Add(MakeTodayCard(card, focusStyle));
        }
        return new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            Child = section,
        };
    }

    private Border MakeTodayCard(JsonElement card, bool focusStyle)
    {
        var suggestion = card.TryGetProperty("suggestion", out var s) && s.GetBoolean();
        var group = GetString(card, "group") ?? "commitment";
        var cardId = GetString(card, "card_id") ?? "";
        var route = GetString(card, "next_action", "route") ?? "today";

        var layout = new StackPanel { Spacing = 6 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(card, "title") ?? "Untitled",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            FontSize = focusStyle ? 16 : 13.5,
            TextWrapping = TextWrapping.Wrap,
        });
        var (chipTint, chipForeground, chipLabel) = group switch
        {
            "authority" => ("HavenOrangeTintBrush", "HavenOrangeBrush", "Needs your decision"),
            "deadline" => ("HavenDangerTintBrush", "HavenDangerBrush", GetString(card, "why_now") ?? "Deadline"),
            "suggestion" => ("HavenVioletTintBrush", "HavenVioletBrush", "Suggestion"),
            _ => ("HavenAccentTintBrush", "HavenAccentBrush", "Why now"),
        };
        head.Children.Add(MakeChip(chipLabel, chipTint, chipForeground));
        layout.Children.Add(head);

        if (group is not ("deadline"))
        {
            layout.Children.Add(new TextBlock
            {
                Text = GetString(card, "why_now") ?? "",
                Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.85,
            });
        }
        var evidenceCount = Enumerate(card, "evidence_refs").Count();
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var open = new Button
        {
            Content = GetString(card, "next_action", "label") ?? "Open",
            Style = (Style)Application.Current.Resources[suggestion ? "HavenSecondaryButtonStyle" : "HavenPrimaryButtonStyle"],
        };
        open.Click += (_, _) => SelectNavigation(route);
        actions.Children.Add(open);
        if (suggestion)
        {
            var dismiss = new Button
            {
                Content = "Dismiss",
                Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            };
            dismiss.Click += async (_, _) =>
            {
                if (_client is null || string.IsNullOrWhiteSpace(cardId))
                {
                    return;
                }
                try
                {
                    await _client.DismissTodayCardAsync(cardId);
                    await LoadTodayCardsAsync();
                }
                catch (Exception ex)
                {
                    TodayStatus.Text = ex.Message;
                }
            };
            actions.Children.Add(dismiss);
        }
        if (evidenceCount > 0)
        {
            actions.Children.Add(new TextBlock
            {
                Text = $"{evidenceCount} evidence ref(s)",
                VerticalAlignment = VerticalAlignment.Center,
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
        }
        layout.Children.Add(actions);
        if (suggestion)
        {
            layout.Opacity = 0.9;
        }
        return new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            BorderBrush = suggestion
                ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenVioletTintBrush"]
                : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
            Child = layout,
        };
    }


    private void OnTodayPendingClicked(object sender, RoutedEventArgs args)
    {
        SelectHomeTab("rooms");
        SelectNavigation("home");
    }

    private void OnQuickActionTaskClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("tasks");
    }

    private void OnQuickActionProjectClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("projects");
    }

    private void OnQuickActionNoteClicked(object sender, RoutedEventArgs args)
    {
        // The honest route to "Add note" today: the memory surface, where
        // telling HAVEN something creates a correctable, source-backed claim.
        SelectNavigation("memory");
        ComposerInput.Focus(FocusState.Programmatic);
    }

    private void OnQuickActionFindClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("search");
    }

    private void OnQuickActionOpenClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("computer");
    }

    private void OnLensAskClicked(object sender, RoutedEventArgs args)
    {
        ComposerInput.Focus(FocusState.Programmatic);
    }

    // -- projects & tasks --------------------------------------------------------

    private string _projectFilter = "";
    private string _taskView = "today";
    private JsonElement _projects = default;
    private string? _openProjectId;

    private async Task LoadProjectsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetProjectsAsync(
                string.IsNullOrWhiteSpace(_projectFilter) ? null : _projectFilter);
            _projects = result.GetProperty("projects").Clone();
            ProjectsErrorText.Text = "";
            ProjectsStatusText.Text = "";
            RenderProjectGallery();
            if (_openProjectId is not null)
            {
                await RenderProjectDetailAsync(_openProjectId);
            }
        }
        catch (Exception ex)
        {
            ProjectsStatusText.Text = "";
            ProjectsErrorText.Text = $"Could not load projects: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderProjectGallery()
    {
        var selected = _openProjectId;
        ProjectGallery.Items.Clear();
        foreach (var project in Enumerate(_projects))
        {
            var projectId = GetString(project, "project_id") ?? "";
            var card = new StackPanel { Spacing = 6, MinWidth = 220 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(project, "title") ?? projectId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(ProjectStatusChip(GetString(project, "status") ?? "active"));
            card.Children.Add(head);
            var description = GetString(project, "description");
            if (!string.IsNullOrWhiteSpace(description))
            {
                card.Children.Add(new TextBlock
                {
                    Text = description,
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                    MaxWidth = 260,
                });
            }
            var done = GetInt(project, "tasks_done");
            var total = GetInt(project, "tasks_total");
            if (total > 0)
            {
                card.Children.Add(new ProgressBar
                {
                    Value = total == 0 ? 0 : done * 100.0 / total,
                    Maximum = 100,
                    MinHeight = 4,
                    MaxHeight = 4,
                });
            }
            var counts = new List<string>
            {
                $"{GetInt(project, "files_count")} files",
                total == 1 ? "1 task" : $"{total} tasks",
            };
            if (GetInt(project, "tasks_blocked") > 0)
            {
                counts.Add($"{GetInt(project, "tasks_blocked")} blocked");
            }
            card.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", counts),
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            ProjectGallery.Items.Add(new ListViewItem
            {
                Tag = projectId,
                IsSelected = projectId == selected,
                Content = new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    MinWidth = 240,
                    Child = card,
                },
            });
        }
        if (ProjectGallery.Items.Count == 0)
        {
            ProjectGallery.Items.Add(new TextBlock
            {
                Text = "No projects here yet. New project starts one.",
                Opacity = 0.72,
            });
        }
    }

    private static Border ProjectStatusChip(string status)
    {
        var (tint, foreground, label) = status switch
        {
            "completed" => ("HavenSuccessTintBrush", "HavenSuccessBrush", "Completed"),
            "planning" => ("HavenVioletTintBrush", "HavenVioletBrush", "Planning"),
            "on_hold" => ("HavenWarningTintBrush", "HavenWarningBrush", "On hold"),
            "archived" => ("HavenStrokeBrush", "HavenMutedTextBrush", "Archived"),
            _ => ("HavenAccentTintBrush", "HavenAccentBrush", "Active"),
        };
        return MakeChip(label, tint, foreground);
    }

    private void OnProjectFilterClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button)
        {
            _projectFilter = button.Tag?.ToString() ?? "";
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            _ = LoadProjectsAsync();
        }
    }

    private async void OnProjectSelected(object sender, SelectionChangedEventArgs args)
    {
        if (ProjectGallery.SelectedItem is ListViewItem item && item.Tag is string projectId)
        {
            _openProjectId = projectId;
            await RenderProjectDetailAsync(projectId);
        }
    }

    private void OnCloseProjectDetailClicked(object sender, RoutedEventArgs args)
    {
        _openProjectId = null;
        ProjectDetailHost.Visibility = Visibility.Collapsed;
        ProjectGallery.Visibility = Visibility.Visible;
    }

    private async Task RenderProjectDetailAsync(string projectId)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetProjectAsync(projectId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ProjectsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the request."
                    : "HAVEN Core refused the request.";
                return;
            }
            var project = result.GetProperty("project");
            ProjectGallery.Visibility = Visibility.Collapsed;
            ProjectDetailHost.Visibility = Visibility.Visible;
            ProjectDetailHost.Children.Clear();

            var head = new StackPanel { Spacing = 4 };
            var back = new Button
            {
                Content = "← All projects",
                Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
                HorizontalAlignment = HorizontalAlignment.Left,
            };
            back.Click += OnCloseProjectDetailClicked;
            head.Children.Add(back);
            var titleRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
            titleRow.Children.Add(new TextBlock
            {
                Text = GetString(project, "title") ?? projectId,
                FontSize = 20,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            titleRow.Children.Add(ProjectStatusChip(GetString(project, "status") ?? "active"));
            head.Children.Add(titleRow);
            var description = GetString(project, "description");
            if (!string.IsNullOrWhiteSpace(description))
            {
                head.Children.Add(new TextBlock
                {
                    Text = description,
                    Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
            }
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var addTask = new Button
            {
                Content = "Add task",
                Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"],
            };
            addTask.Click += async (_, _) => await EditTaskDialogAsync(default, projectId);
            var edit = new Button
            {
                Content = "Edit",
                Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            };
            edit.Click += async (_, _) => await EditProjectDialogAsync(project);
            var archive = new Button
            {
                Content = "Archive",
                Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            };
            archive.Click += async (_, _) => await ArchiveProjectAsync(project);
            actions.Children.Add(addTask);
            actions.Children.Add(edit);
            actions.Children.Add(archive);
            head.Children.Add(actions);
            ProjectDetailHost.Children.Add(head);

            var tabs = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
            foreach (var (label, key) in new[] { ("Tasks", "tasks"), ("Files", "files"), ("People", "people") })
            {
                var tab = new Button
                {
                    Content = label,
                    Tag = key,
                    Style = (Style)Application.Current.Resources["HavenTabButtonStyle"],
                };
                tab.Click += async (_, _) => await RenderProjectTabAsync(project, key);
                tabs.Children.Add(tab);
            }
            ProjectDetailHost.Children.Add(tabs);

            var body = new StackPanel { Spacing = 8 };
            ProjectDetailHost.Children.Add(body);
            await RenderProjectTabAsync(project, "tasks");
        }
        catch (Exception ex)
        {
            ProjectsErrorText.Text = ex.Message;
        }
    }

    private async Task RenderProjectTabAsync(JsonElement project, string tab)
    {
        var body = ProjectDetailHost.Children.OfType<StackPanel>().LastOrDefault();
        if (body is null || _client is null)
        {
            return;
        }
        body.Children.Clear();
        var projectId = GetString(project, "project_id") ?? "";
        if (tab == "tasks")
        {
            var tasks = await _client.GetTasksAsync("all");
            foreach (var task in Enumerate(tasks.GetProperty("tasks")))
            {
                if (GetString(task, "project_id") == projectId)
                {
                    body.Children.Add(MakeTaskRow(task));
                }
            }
            if (body.Children.Count == 0)
            {
                body.Children.Add(new TextBlock { Text = "No tasks in this project yet.", Opacity = 0.72 });
            }
        }
        else if (tab == "files")
        {
            var attached = await _client.GetProjectResourcesAsync(projectId);
            foreach (var resource in Enumerate(attached.GetProperty("resources")))
            {
                var resourceId = GetString(resource, "resource_id") ?? "";
                var resourcePanel = new StackPanel { Spacing = 2 };
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                row.Children.Add(new TextBlock
                {
                    Text = GetString(resource, "title") ?? resourceId,
                    VerticalAlignment = VerticalAlignment.Center,
                });
                var detach = new Button { Content = "Detach", VerticalAlignment = VerticalAlignment.Center };
                detach.Click += async (_, _) =>
                {
                    await RunProjectsMutationAsync(() => _client!.DetachProjectResourceAsync(projectId, resourceId));
                    await RenderProjectDetailAsync(projectId);
                };
                row.Children.Add(detach);
                resourcePanel.Children.Add(row);
                resourcePanel.Children.Add(new TextBlock
                {
                    Text = $"{GetString(resource, "resource_type")} · {resourceId}",
                    Style = (Style)Application.Current.Resources["HavenMonoTextStyle"],
                });
                body.Children.Add(WrapCard(resourcePanel));
            }
            var attachBox = new TextBox { PlaceholderText = "Resource id to attach, e.g. file:proposal.docx", MinWidth = 320 };
            var attach = new Button { Content = "Attach" };
            attach.Click += async (_, _) =>
            {
                if (string.IsNullOrWhiteSpace(attachBox.Text))
                {
                    return;
                }
                await RunProjectsMutationAsync(() => _client!.AttachProjectResourceAsync(projectId, attachBox.Text.Trim()));
                await RenderProjectDetailAsync(projectId);
            };
            var attachRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            attachRow.Children.Add(attachBox);
            attachRow.Children.Add(attach);
            body.Children.Add(attachRow);
        }
        else
        {
            var people = Enumerate(project, "people_ids").Select(id => id.GetString()).Where(id => id is not null).ToList();
            if (people.Count == 0)
            {
                body.Children.Add(new TextBlock
                {
                    Text = "Assign tasks to people (edit a task) and they appear here.",
                    Opacity = 0.72,
                });
            }
            foreach (var personId in people)
            {
                body.Children.Add(MakeChip(personId!, "HavenAccentTintBrush", "HavenAccentBrush"));
            }
        }
    }

    private async Task RunProjectsMutationAsync(Func<Task<JsonElement>> operation)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await operation();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                var message = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString()
                    : "HAVEN Core refused the change.";
                ProjectsErrorText.Text = message ?? "HAVEN Core refused the change.";
                TasksErrorText.Text = message ?? "HAVEN Core refused the change.";
                return;
            }
            ProjectsErrorText.Text = "";
            TasksErrorText.Text = "";
            await LoadProjectsAsync();
            await LoadTasksAsync();
        }
        catch (Exception ex)
        {
            ProjectsErrorText.Text = ex.Message;
            TasksErrorText.Text = ex.Message;
        }
    }

    private async void OnNewProjectClicked(object sender, RoutedEventArgs args)
    {
        await EditProjectDialogAsync(default);
    }

    private async Task EditProjectDialogAsync(JsonElement project)
    {
        if (_client is null)
        {
            return;
        }
        var editing = project.ValueKind == JsonValueKind.Object;
        var projectId = editing ? GetString(project, "project_id") ?? "" : "";
        var revision = editing ? (int)GetInt(project, "revision") : 0;
        var title = new TextBox { Text = editing ? GetString(project, "title") ?? "" : "", PlaceholderText = "Project title", MinWidth = 320 };
        var description = new TextBox
        {
            Text = editing ? GetString(project, "description") ?? "" : "",
            PlaceholderText = "What does done look like?",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 72,
        };
        var status = new ComboBox { MinWidth = 200 };
        foreach (var option in new[] { "active", "planning", "on_hold", "completed" })
        {
            status.Items.Add(Sentence(option));
        }
        var statusValues = new[] { "active", "planning", "on_hold", "completed" };
        status.SelectedIndex = editing
            ? Math.Max(0, Array.IndexOf(statusValues, GetString(project, "status") ?? "active"))
            : 0;
        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        fields.Children.Add(new TextBlock { Text = "Title" });
        fields.Children.Add(title);
        fields.Children.Add(new TextBlock { Text = "Goal" });
        fields.Children.Add(description);
        fields.Children.Add(new TextBlock { Text = "Status" });
        fields.Children.Add(status);
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit project" : "New project",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Create",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(title.Text))
        {
            return;
        }
        var statusValue = statusValues[Math.Max(0, status.SelectedIndex)];
        if (editing)
        {
            await RunProjectsMutationAsync(() => _client.UpdateProjectAsync(
                projectId, revision, title.Text.Trim(), description.Text.Trim(), statusValue));
        }
        else
        {
            await RunProjectsMutationAsync(() => _client.CreateProjectAsync(
                title.Text.Trim(), description.Text.Trim(), statusValue));
        }
    }

    private async Task ArchiveProjectAsync(JsonElement project)
    {
        if (_client is null)
        {
            return;
        }
        var title = GetString(project, "title") ?? "this project";
        var dialog = new ContentDialog
        {
            Title = $"Archive {title}?",
            Content = new TextBlock
            {
                Text = "The project is tombstoned and leaves the default list. Its tasks and attached resources stay exactly where they are.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Archive",
            CloseButtonText = "Cancel",
            PrimaryButtonStyle = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        _openProjectId = null;
        ProjectDetailHost.Visibility = Visibility.Collapsed;
        ProjectGallery.Visibility = Visibility.Visible;
        await RunProjectsMutationAsync(() => _client.ArchiveProjectAsync(GetString(project, "project_id") ?? ""));
    }

    // -- tasks -------------------------------------------------------------------

    private async Task LoadTasksAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetTasksAsync(_taskView);
            TasksErrorText.Text = "";
            TasksStatusText.Text = "";
            RenderTasks(result.GetProperty("tasks"));
        }
        catch (Exception ex)
        {
            TasksStatusText.Text = "";
            TasksErrorText.Text = $"Could not load tasks: {ex.Message} Use Refresh to retry.";
        }
    }

    private void OnTaskViewClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string view)
        {
            _taskView = view;
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            _ = LoadTasksAsync();
        }
    }

    private void RenderTasks(JsonElement tasks)
    {
        TasksListPanel.Children.Clear();
        foreach (var task in Enumerate(tasks))
        {
            TasksListPanel.Children.Add(MakeTaskRow(task));
        }
        if (TasksListPanel.Children.Count == 0)
        {
            TasksListPanel.Children.Add(new TextBlock
            {
                Text = _taskView == "completed"
                    ? "Nothing completed yet."
                    : "Nothing here. Add a task above, or ask HAVEN in the composer.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeTaskRow(JsonElement task)
    {
        var taskId = GetString(task, "task_id") ?? "";
        var revision = (int)GetInt(task, "revision");
        var state = GetString(task, "state") ?? "open";
        var terminal = state is "done" or "cancelled";

        var card = new Grid();
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(36) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });

        var complete = new CheckBox
        {
            IsChecked = state == "done",
            IsEnabled = !terminal,
            VerticalAlignment = VerticalAlignment.Center,
        };
        complete.Checked += async (_, _) =>
        {
            if (_client is null)
            {
                return;
            }
            try
            {
                var result = await _client.CompleteTaskAsync(taskId, revision);
                if (result.ValueKind == JsonValueKind.Object
                    && result.TryGetProperty("ok", out var ok) && !ok.GetBoolean())
                {
                    TasksErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                        ? error.GetString() ?? "Completion refused."
                        : "Completion refused.";
                    await LoadTasksAsync();
                    return;
                }
                if (result.TryGetProperty("woken_task_ids", out var woken) && woken.GetArrayLength() > 0)
                {
                    TasksStatusText.Text = $"Unblocked {woken.GetArrayLength()} dependent task(s).";
                }
                await LoadTasksAsync();
                await LoadProjectsAsync();
            }
            catch (Exception ex)
            {
                TasksErrorText.Text = ex.Message;
            }
        };
        card.Children.Add(complete);

        var body = new StackPanel { Spacing = 2 };
        var titleRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        titleRow.Children.Add(new TextBlock
        {
            Text = GetString(task, "title") ?? taskId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        if (state == "blocked")
        {
            var blockedCount = Enumerate(task, "blocked_by").Count();
            titleRow.Children.Add(MakeChip(
                blockedCount > 0 ? $"Blocked · {blockedCount}" : "Blocked",
                "HavenWarningTintBrush", "HavenWarningBrush"));
        }
        else if (state == "proposed")
        {
            titleRow.Children.Add(MakeChip("Proposed", "HavenVioletTintBrush", "HavenVioletBrush"));
        }
        var priority = GetString(task, "priority");
        if (priority is not null)
        {
            var (tint, foreground) = priority switch
            {
                "high" => ("HavenOrangeTintBrush", "HavenOrangeBrush"),
                "medium" => ("HavenWarningTintBrush", "HavenWarningBrush"),
                _ => ("HavenStrokeBrush", "HavenMutedTextBrush"),
            };
            titleRow.Children.Add(MakeChip(Sentence(priority), tint, foreground));
        }
        body.Children.Add(titleRow);
        var meta = new List<string>();
        var projectTitle = GetString(task, "project_title");
        if (projectTitle is not null)
        {
            meta.Add(projectTitle);
        }
        var due = FormatDue(GetString(task, "due_at"));
        if (due is not null)
        {
            meta.Add(due);
        }
        if (terminal && GetString(task, "completed_at") is { } completedAt)
        {
            meta.Add($"completed {FormatDue(completedAt) ?? completedAt}");
        }
        if (meta.Count > 0)
        {
            body.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", meta),
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
        }
        Grid.SetColumn(body, 1);
        card.Children.Add(body);

        var edit = new Button
        {
            Content = "Edit",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            VerticalAlignment = VerticalAlignment.Center,
        };
        edit.Click += async (_, _) => await EditTaskDialogAsync(task, GetString(task, "project_id"));
        Grid.SetColumn(edit, 2);
        card.Children.Add(edit);

        var wrap = new StackPanel();
        wrap.Children.Add(card);
        return WrapCard(wrap);
    }

    private static string? FormatDue(string? iso)
    {
        if (iso is null || !DateTimeOffset.TryParse(iso, out var parsed))
        {
            return null;
        }
        var local = parsed.LocalDateTime;
        return local.Date == DateTime.Today
            ? local.ToString("h:mm tt")
            : local.ToString("MMM d");
    }

    private async void OnTaskFastAddClicked(object sender, RoutedEventArgs args)
    {
        await FastAddTaskAsync();
    }

    private async void OnTaskFastAddKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            args.Handled = true;
            await FastAddTaskAsync();
        }
    }

    private async Task FastAddTaskAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(TaskFastAddBox.Text))
        {
            return;
        }
        var title = TaskFastAddBox.Text.Trim();
        await RunProjectsMutationAsync(() => _client.CreateTaskAsync(title));
        TaskFastAddBox.Text = "";
    }

    private async Task EditTaskDialogAsync(JsonElement task, string? presetProjectId)
    {
        if (_client is null)
        {
            return;
        }
        var editing = task.ValueKind == JsonValueKind.Object;
        var taskId = editing ? GetString(task, "task_id") ?? "" : "";
        var revision = editing ? (int)GetInt(task, "revision") : 0;
        var title = new TextBox { Text = editing ? GetString(task, "title") ?? "" : "", PlaceholderText = "What needs doing?", MinWidth = 320 };
        var detail = new TextBox
        {
            Text = editing ? GetString(task, "detail") ?? "" : "",
            PlaceholderText = "Detail (optional)",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 64,
        };
        var priority = new ComboBox { MinWidth = 200 };
        priority.Items.Add("None");
        foreach (var option in new[] { "high", "medium", "low" })
        {
            priority.Items.Add(Sentence(option));
        }
        var currentPriority = editing ? GetString(task, "priority") : null;
        priority.SelectedIndex = currentPriority is null ? 0 : new[] { "high", "medium", "low" }.ToList().IndexOf(currentPriority) + 1;

        var project = new ComboBox { MinWidth = 200 };
        project.Items.Add("No project");
        var projectIds = new List<string>();
        foreach (var candidate in Enumerate(_projects))
        {
            var id = GetString(candidate, "project_id");
            if (id is null)
            {
                continue;
            }
            projectIds.Add(id);
            project.Items.Add(GetString(candidate, "title") ?? id);
        }
        var selectedProject = editing ? GetString(task, "project_id") : presetProjectId;
        project.SelectedIndex = selectedProject is null ? 0 : Math.Max(0, projectIds.IndexOf(selectedProject) + 1);

        var due = new TextBox
        {
            Text = editing ? (GetString(task, "due_at") ?? "") : "",
            PlaceholderText = "Due (ISO, optional) e.g. 2026-10-01T17:00:00+00:00",
            MinWidth = 320,
        };

        var fields = new StackPanel { Spacing = 8, MinWidth = 380 };
        fields.Children.Add(new TextBlock { Text = "Title" });
        fields.Children.Add(title);
        fields.Children.Add(new TextBlock { Text = "Detail" });
        fields.Children.Add(detail);
        fields.Children.Add(new TextBlock { Text = "Priority" });
        fields.Children.Add(priority);
        fields.Children.Add(new TextBlock { Text = "Project" });
        fields.Children.Add(project);
        fields.Children.Add(new TextBlock { Text = "Due" });
        fields.Children.Add(due);

        ComboBox? state = null;
        if (editing)
        {
            var stateValues = new[] { "open", "in_progress", "blocked", "proposed" };
            state = new ComboBox { MinWidth = 200 };
            foreach (var option in stateValues)
            {
                state.Items.Add(Sentence(option));
            }
            state.SelectedIndex = Math.Max(0, Array.IndexOf(stateValues, GetString(task, "state") ?? "open"));
            fields.Children.Add(new TextBlock { Text = "State" });
            fields.Children.Add(state);
        }

        var dialog = new ContentDialog
        {
            Title = editing ? "Edit task" : "New task",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add task",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(title.Text))
        {
            return;
        }
        var projectValue = project.SelectedIndex <= 0 ? null : projectIds[project.SelectedIndex - 1];
        var priorityValue = priority.SelectedIndex <= 0 ? null : new[] { "high", "medium", "low" }[priority.SelectedIndex - 1];
        var dueValue = string.IsNullOrWhiteSpace(due.Text) ? null : due.Text.Trim();
        if (editing && state is not null)
        {
            var stateValues = new[] { "open", "in_progress", "blocked", "proposed" };
            var stateValue = stateValues[Math.Max(0, state.SelectedIndex)];
            await RunProjectsMutationAsync(() => _client.UpdateTaskAsync(
                taskId, revision, title.Text.Trim(), detail.Text.Trim(), stateValue,
                priorityValue, dueValue, projectValue));
        }
        else
        {
            await RunProjectsMutationAsync(() => _client.CreateTaskAsync(
                title.Text.Trim(), projectValue, detail.Text.Trim(), priorityValue, dueValue));
        }
    }

    // -- computer (files / applications / windows / activity) -------------------

    private string _computerTab = "files";

    private void OnComputerTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            _computerTab = tab;
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            ComputerStatusText.Text = "Loading…";
            _ = LoadComputerTabAsync();
        }
    }

    private async Task LoadComputerTabAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            ComputerTabContent.Children.Clear();
            if (_computerTab == "files")
            {
                var result = await _client.GetComputerFilesAsync();
                var files = result.GetProperty("files");
                var any = false;
                foreach (var file in Enumerate(files))
                {
                    any = true;
                    var card = new StackPanel { Spacing = 2 };
                    card.Children.Add(new TextBlock
                    {
                        Text = GetString(file, "title") ?? GetString(file, "resource_id") ?? "?",
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        TextWrapping = TextWrapping.Wrap,
                    });
                    card.Children.Add(new TextBlock
                    {
                        Text = GetString(file, "locator") ?? "",
                        Style = (Style)Application.Current.Resources["HavenMonoTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                    });
                    if (file.TryGetProperty("stale", out var stale) && stale.GetBoolean())
                    {
                        card.Opacity = 0.55;
                    }
                    ComputerTabContent.Children.Add(WrapCard(card));
                }
                if (!any)
                {
                    ComputerTabContent.Children.Add(new TextBlock
                    {
                        Text = "No authorized files observed yet. Enable the computer provider in Setup and scan an allowed folder.",
                        Opacity = 0.72,
                        TextWrapping = TextWrapping.Wrap,
                    });
                }
            }
            else if (_computerTab == "apps")
            {
                var result = await _client.GetComputerAppsAsync();
                var any = false;
                foreach (var app in Enumerate(result.GetProperty("apps")))
                {
                    any = true;
                    var titles = Enumerate(app, "titles").Select(title => title.GetString()).Where(title => title is not null).ToList();
                    var card = new StackPanel { Spacing = 2 };
                    card.Children.Add(new TextBlock
                    {
                        Text = GetString(app, "app") ?? "?",
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    });
                    card.Children.Add(new TextBlock
                    {
                        Text = $"{GetInt(app, "windows")} window(s){(titles.Count > 0 ? " · " + string.Join("; ", titles) : "")}",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                    });
                    ComputerTabContent.Children.Add(WrapCard(card));
                }
                if (!any)
                {
                    ComputerTabContent.Children.Add(new TextBlock { Text = "No applications observed.", Opacity = 0.72 });
                }
            }
            else if (_computerTab == "windows")
            {
                var result = await _client.GetComputerWindowsAsync();
                var any = false;
                foreach (var window in Enumerate(result.GetProperty("windows")))
                {
                    any = true;
                    var resourceId = GetString(window, "resource_id") ?? "";
                    var card = new Grid();
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });
                    var body = new StackPanel { Spacing = 2 };
                    body.Children.Add(new TextBlock
                    {
                        Text = GetString(window, "title") ?? "?",
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        TextWrapping = TextWrapping.Wrap,
                    });
                    body.Children.Add(new TextBlock
                    {
                        Text = GetString(window, "process") ?? "",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    });
                    card.Children.Add(body);
                    var focus = new Button
                    {
                        Content = "Bring to front",
                        VerticalAlignment = VerticalAlignment.Center,
                    };
                    focus.Click += async (_, _) => await FocusWindowAsync(resourceId);
                    Grid.SetColumn(focus, 1);
                    card.Children.Add(focus);
                    var wrap = new StackPanel();
                    wrap.Children.Add(card);
                    ComputerTabContent.Children.Add(WrapCard(wrap));
                }
                if (!any)
                {
                    ComputerTabContent.Children.Add(new TextBlock { Text = "No visible windows right now.", Opacity = 0.72 });
                }
            }
            else
            {
                var result = await _client.GetComputerActivityAsync();
                var observation = result.GetProperty("observation");
                var enabled = observation.TryGetProperty("enabled", out var enabledValue) && enabledValue.GetBoolean();
                var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
                header.Children.Add(new TextBlock
                {
                    Text = enabled
                        ? $"Foreground observation is on ({GetString(observation, "observed_events") ?? "0"} events, retention {GetInt(observation, "retention")})."
                        : "Foreground observation is off. Nothing is recorded until you enable it.",
                    VerticalAlignment = VerticalAlignment.Center,
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                });
                var toggle = new Button { Content = enabled ? "Disable observation" : "Enable observation" };
                toggle.Click += async (_, _) =>
                {
                    try
                    {
                        await _client!.SetComputerObservationAsync(!enabled);
                        ComputerErrorText.Text = "";
                        await LoadComputerTabAsync();
                    }
                    catch (Exception ex)
                    {
                        ComputerErrorText.Text = ex.Message;
                    }
                };
                header.Children.Add(toggle);
                ComputerTabContent.Children.Add(header);
                var events = Enumerate(result.GetProperty("events")).ToList();
                if (events.Count == 0)
                {
                    ComputerTabContent.Children.Add(new TextBlock
                    {
                        Text = "No foreground history yet. Suppressed apps never appear here.",
                        Opacity = 0.72,
                    });
                }
                foreach (var entry in events)
                {
                    var card = new StackPanel { Spacing = 2 };
                    card.Children.Add(new TextBlock
                    {
                        Text = GetString(entry, "title") ?? "?",
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    });
                    card.Children.Add(new TextBlock
                    {
                        Text = $"{GetString(entry, "app")} · {GetString(entry, "at")}",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    });
                    ComputerTabContent.Children.Add(WrapCard(card));
                }
            }
            ComputerStatusText.Text = "";
            ComputerErrorText.Text = "";
        }
        catch (Exception ex)
        {
            ComputerStatusText.Text = "";
            ComputerErrorText.Text = $"Could not load computer state: {ex.Message} Use Refresh to retry.";
        }
    }

    private async Task FocusWindowAsync(string resourceId)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.FocusWindowAsync(resourceId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ComputerErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Focus refused."
                    : "Focus refused.";
                return;
            }
            var success = result.TryGetProperty("success", out var successValue) && successValue.GetBoolean();
            ComputerErrorText.Text = "";
            ComputerStatusText.Text = success
                ? "Window brought to the foreground (receipt recorded)."
                : $"The OS refused the focus request: {GetString(result, "detail") ?? "no detail"}";
            await LoadComputerTabAsync();
        }
        catch (Exception ex)
        {
            ComputerErrorText.Text = ex.Message;
        }
    }

    // -- communications (email / messages / calendar / browser tabs) ------------

    private string _commsTab = "email";

    private void OnCommsTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            _commsTab = tab;
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            CommsStatusText.Text = "Loading…";
            _ = LoadCommsTabAsync();
        }
    }

    private async Task LoadCommsTabAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            CommsTabContent.Children.Clear();
            CommsErrorText.Text = "";
            if (_commsTab == "email")
            {
                var status = await _client.GetEmailStatusAsync();
                var configured = status.TryGetProperty("configured", out var c) && c.GetBoolean();
                var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
                header.Children.Add(new TextBlock
                {
                    Text = configured
                        ? $"Connected: {GetString(status, "provider")} (read-only — sending needs a credentialed provider)"
                        : $"Not connected: {GetString(status, "detail") ?? "no email provider configured"}",
                    VerticalAlignment = VerticalAlignment.Center,
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                });
                var setFolder = new Button { Content = configured ? "Change mail folder…" : "Connect a mail folder…" };
                setFolder.Click += async (_, _) => await PickMaildirAsync();
                header.Children.Add(setFolder);
                CommsTabContent.Children.Add(header);
                if (configured)
                {
                    var messages = await _client.GetEmailMessagesAsync();
                    var any = false;
                    foreach (var message in Enumerate(messages.GetProperty("messages")))
                    {
                        any = true;
                        var card = new StackPanel { Spacing = 2 };
                        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                        head.Children.Add(new TextBlock
                        {
                            Text = GetString(message, "subject") ?? "(no subject)",
                            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                            TextWrapping = TextWrapping.Wrap,
                        });
                        foreach (var label in Enumerate(message, "labels"))
                        {
                            if (label.GetString() is { } text && text.Length > 0)
                            {
                                head.Children.Add(MakeChip(text, "HavenAccentTintBrush", "HavenAccentBrush"));
                            }
                        }
                        card.Children.Add(head);
                        card.Children.Add(new TextBlock
                        {
                            Text = $"{GetString(message, "sender")} · {GetString(message, "at")}",
                            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                        });
                        if (GetString(message, "snippet") is { Length: > 0 } snippet)
                        {
                            card.Children.Add(new TextBlock
                            {
                                Text = snippet,
                                Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                                TextWrapping = TextWrapping.Wrap,
                                Opacity = 0.8,
                            });
                        }
                        CommsTabContent.Children.Add(WrapCard(card));
                    }
                    if (!any)
                    {
                        CommsTabContent.Children.Add(new TextBlock { Text = "No messages in the configured folder.", Opacity = 0.72 });
                    }
                }
            }
            else if (_commsTab == "messages")
            {
                CommsTabContent.Children.Add(new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    Child = new TextBlock
                    {
                        Text = "Message threads (Slack/Teams-style) arrive with a credentialed conversation provider. Nothing is connected, and HAVEN reads nothing until you add one.",
                        TextWrapping = TextWrapping.Wrap,
                        Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                    },
                });
            }
            else if (_commsTab == "calendar")
            {
                var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
                header.Children.Add(new TextBlock
                {
                    Text = "Local calendar sources (.ics files). External calendars stay authoritative; changes are confirmed and verified by re-read.",
                    VerticalAlignment = VerticalAlignment.Center,
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
                var add = new Button { Content = "Add .ics file…" };
                add.Click += async (_, _) => await PickIcsSourceAsync();
                header.Children.Add(add);
                CommsTabContent.Children.Add(header);

                var events = await _client.GetCalendarEventsAsync();
                var anyEvent = false;
                foreach (var entry in Enumerate(events.GetProperty("events")))
                {
                    anyEvent = true;
                    var eventId = GetString(entry, "event_id") ?? "";
                    var card = new Grid();
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });
                    var body = new StackPanel { Spacing = 2 };
                    body.Children.Add(new TextBlock
                    {
                        Text = GetString(entry, "title") ?? eventId,
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        TextWrapping = TextWrapping.Wrap,
                    });
                    var meta = new List<string>();
                    if (GetString(entry, "start_at") is { } start)
                    {
                        meta.Add(FormatDue(start) ?? start);
                    }
                    if (GetString(entry, "location") is { Length: > 0 } location)
                    {
                        meta.Add(location);
                    }
                    if (Enumerate(entry, "attendees").Count() > 0)
                    {
                        meta.Add($"{Enumerate(entry, "attendees").Count()} attendee(s)");
                    }
                    body.Children.Add(new TextBlock
                    {
                        Text = string.Join(" · ", meta),
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    });
                    card.Children.Add(body);
                    var propose = new Button { Content = "Propose task", VerticalAlignment = VerticalAlignment.Center };
                    propose.Click += async (_, _) =>
                    {
                        await RunCommsMutationAsync(() => _client!.ProposeTaskForEventAsync(eventId));
                    };
                    Grid.SetColumn(propose, 1);
                    card.Children.Add(propose);
                    var wrap = new StackPanel();
                    wrap.Children.Add(card);
                    CommsTabContent.Children.Add(WrapCard(wrap));
                }
                if (!anyEvent)
                {
                    CommsTabContent.Children.Add(new TextBlock
                    {
                        Text = "No events. Add a .ics file above to see your calendar here.",
                        Opacity = 0.72,
                    });
                }
            }
            else
            {
                var tabs = await _client.GetBrowserTabsAsync();
                var status = tabs.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.Object ? s : default;
                var connected = status.ValueKind == JsonValueKind.Object
                    ? Enumerate(status, "connected_browsers").Count()
                    : 0;
                CommsTabContent.Children.Add(new TextBlock
                {
                    Text = connected > 0
                        ? $"{connected} browser(s) connected. Tab identity, title, and URL only — no page content, no incognito."
                        : "No browser connected. Install the experimental connector (browser/ in the HAVEN repo) to see tabs here.",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
                foreach (var tab in Enumerate(tabs.GetProperty("tabs")))
                {
                    var resourceId = GetString(tab, "resource_id") ?? "";
                    var card = new Grid();
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
                    card.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });
                    var body = new StackPanel { Spacing = 2 };
                    body.Children.Add(new TextBlock
                    {
                        Text = GetString(tab, "title") ?? "?",
                        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                        TextWrapping = TextWrapping.Wrap,
                    });
                    body.Children.Add(new TextBlock
                    {
                        Text = $"{GetString(tab, "browser")} · {GetString(tab, "domain")}",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    });
                    card.Children.Add(body);
                    var focus = new Button { Content = "Focus", VerticalAlignment = VerticalAlignment.Center };
                    focus.Click += async (_, _) => await RunCommsMutationAsync(() => _client!.FocusBrowserTabAsync(resourceId));
                    Grid.SetColumn(focus, 1);
                    card.Children.Add(focus);
                    var wrap = new StackPanel();
                    wrap.Children.Add(card);
                    CommsTabContent.Children.Add(WrapCard(wrap));
                }
            }
            CommsStatusText.Text = "";
        }
        catch (Exception ex)
        {
            CommsStatusText.Text = "";
            CommsErrorText.Text = $"Could not load communications state: {ex.Message} Use Refresh to retry.";
        }
    }

    private async Task RunCommsMutationAsync(Func<Task<JsonElement>> operation)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await operation();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                CommsErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            CommsErrorText.Text = "";
            CommsStatusText.Text = "Done.";
            await LoadCommsTabAsync();
        }
        catch (Exception ex)
        {
            CommsErrorText.Text = ex.Message;
        }
    }

    private async Task PickMaildirAsync()
    {
        if (_client is null)
        {
            return;
        }
        var folder = await PickFolderAsync();
        if (folder is not null)
        {
            await RunCommsMutationAsync(() => _client.SetEmailMaildirAsync(folder));
        }
    }

    private async Task PickIcsSourceAsync()
    {
        if (_client is null)
        {
            return;
        }
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var picker = new Windows.Storage.Pickers.FileOpenPicker();
        picker.FileTypeFilter.Add(".ics");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, handle);
        var file = await picker.PickSingleFileAsync();
        if (file is not null)
        {
            await RunCommsMutationAsync(() => _client.AddCalendarSourceAsync(file.Path));
        }
    }

    // -- home tabs --------------------------------------------------------------

    private string _homeTab = "rooms";

    private void OnHomeTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            SelectHomeTab(tab);
        }
    }

    private void SelectHomeTab(string tab)
    {
        _homeTab = tab;
        HomeTabRooms.Visibility = tab == "rooms" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabDevices.Visibility = tab == "devices" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabAutomations.Visibility = tab == "automations" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabContexts.Visibility = tab == "contexts" ? Visibility.Visible : Visibility.Collapsed;
        foreach (var button in HomeTabs.Children.OfType<Button>())
        {
            button.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            button.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
    }


    // -- system (diagnostics, backups, service) -------------------------------

    private JsonElement _diagnostics = default;

    private async void OnSettingsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadSettingsAsync();
    }

    private async Task LoadSettingsAsync()
    {
        if (_client is null)
        {
            return;
        }
        SettingsStatusText.Text = "";
        SettingsErrorText.Text = "";

        JsonElement? backups = null;
        JsonElement? service = null;

        // Each section loads independently: one failing call must not blank
        // the whole control center.
        try
        {
            var diagnostics = await _client.GetSystemDiagnosticsAsync();
            _diagnostics = diagnostics.GetProperty("diagnostics").Clone();
            StorageDataDirText.Text = GetString(_diagnostics, "data_dir") ?? "Unknown";
            var voice = _diagnostics.TryGetProperty("voice", out var voiceValue) && voiceValue.ValueKind == JsonValueKind.Object
                ? voiceValue
                : default;
            var voiceEnabled = voice.ValueKind == JsonValueKind.Object
                && voice.TryGetProperty("enabled", out var enabledValue)
                && enabledValue.GetBoolean();
            IntelligenceVoiceText.Text = voiceEnabled
                ? $"Voice is enabled (state: {GetString(voice, "state") ?? "unknown"})"
                : "Voice is off. Models, routing and downloads live on the Models page.";
            var version = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version;
            AboutVersionText.Text = $"HAVEN Desktop {version?.Major}.{version?.Minor}.{version?.Build} — local-first; your data stays on this machine.";
            RenderDiagnostics();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load diagnostics: {ex.Message} Use Refresh to retry.";
        }

        try
        {
            await RenderExtensionsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load extensions: {ex.Message}";
        }

        try
        {
            backups = (await _client.GetBackupsAsync()).Clone();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load backups: {ex.Message} Use Refresh to retry.";
        }
        try
        {
            service = (await _client.GetServiceStatusAsync()).Clone();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load the startup service: {ex.Message} Use Refresh to retry.";
        }
        if (backups is not null)
        {
            RenderBackups(backups.Value.GetProperty("backups"));
        }
        if (service is not null)
        {
            RenderService(service.Value.GetProperty("service"));
        }
    }

    private async Task RenderExtensionsAsync()
    {
        if (_client is null)
        {
            return;
        }
        var result = await _client.GetExtensionsAsync();
        ExtensionsHost.Children.Clear();
        foreach (var group in Enumerate(result.GetProperty("classes")))
        {
            var className = GetString(group, "class") ?? "unknown";
            var title = className switch
            {
                "export_consumer" => "Export consumers",
                "intelligence" => "Intelligence services",
                "provider" => "Providers",
                "feature" => "Feature modules",
                _ => Sentence(className),
            };
            var card = new StackPanel { Spacing = 6 };
            card.Children.Add(new TextBlock
            {
                Text = title,
                Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            });
            var extensions = Enumerate(group, "extensions").ToList();
            if (extensions.Count == 0)
            {
                card.Children.Add(new TextBlock
                {
                    Text = className switch
                    {
                        "feature" => "None yet. Feature modules will extend HAVEN through explicit contracts, calling application services only.",
                        "provider" => "No provider plugins are installed. Provider packages declare themselves through the haven.providers entry point.",
                        "export_consumer" => "Nothing in the catalog yet. Export consumers read only explicitly exported, redacted receipts — never live state.",
                        _ => "None registered.",
                    },
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
            }
            foreach (var extension in extensions)
            {
                var row = new StackPanel { Spacing = 2 };
                var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                head.Children.Add(new TextBlock
                {
                    Text = GetString(extension, "display_name") ?? GetString(extension, "extension_id") ?? "?",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    TextWrapping = TextWrapping.Wrap,
                });
                var state = extension.TryGetProperty("state", out var stateValue) && stateValue.ValueKind == JsonValueKind.Object
                    ? stateValue
                    : default;
                var enabled = state.ValueKind == JsonValueKind.Object
                    && state.TryGetProperty("enabled", out var enabledValue)
                    && enabledValue.ValueKind == JsonValueKind.True
                    && enabledValue.GetBoolean();
                if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("enabled", out _))
                {
                    head.Children.Add(MakeChip(
                        enabled ? "Enabled" : "Disabled",
                        enabled ? "HavenSuccessTintBrush" : "HavenStrokeBrush",
                        enabled ? "HavenSuccessBrush" : "HavenMutedTextBrush"));
                }
                row.Children.Add(head);
                row.Children.Add(new TextBlock
                {
                    Text = $"{GetString(extension, "run_location")} · {GetString(extension, "access_boundary")}",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
                row.Children.Add(new TextBlock
                {
                    Text = GetString(extension, "authority_boundary") ?? "",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.8,
                });
                if (GetString(extension, "detail") is { Length: > 0 } detail)
                {
                    row.Children.Add(new TextBlock
                    {
                        Text = detail,
                        Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                        Opacity = 0.8,
                    });
                }
                if (className == "export_consumer" && state.ValueKind == JsonValueKind.Object
                    && state.TryGetProperty("plugin_id", out var pluginIdValue)
                    && pluginIdValue.GetString() is { } pluginId)
                {
                    var toggle = new Button
                    {
                        Content = enabled ? "Disable" : "Enable",
                        Style = (Style)Application.Current.Resources[enabled ? "HavenDangerButtonStyle" : "HavenSecondaryButtonStyle"],
                        HorizontalAlignment = HorizontalAlignment.Left,
                    };
                    toggle.Click += async (_, _) =>
                    {
                        try
                        {
                            await _client!.SetExportConsumerEnabledAsync(pluginId, !enabled);
                            SettingsErrorText.Text = "";
                            await LoadSettingsAsync();
                        }
                        catch (Exception ex)
                        {
                            SettingsErrorText.Text = ex.Message;
                        }
                    };
                    row.Children.Add(toggle);
                }
                card.Children.Add(WrapCard(row));
            }
            ExtensionsHost.Children.Add(card);
        }
    }

    private void OnOpenModelsClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("models");
    }

    private void RenderDiagnostics()
    {
        DiagnosticsList.Children.Clear();
        void Row(string label, string value)
        {
            var row = new Grid();
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(200) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
            var name = new TextBlock { Text = label, Opacity = 0.72 };
            var content = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap };
            row.Children.Add(name);
            Grid.SetColumn(content, 1);
            row.Children.Add(content);
            DiagnosticsList.Children.Add(row);
        }

        if (_diagnostics.ValueKind != JsonValueKind.Object)
        {
            DiagnosticsList.Children.Add(new TextBlock { Text = "Diagnostics unavailable.", Opacity = 0.72 });
            return;
        }
        Row("Data directory", GetString(_diagnostics, "data_dir") ?? "—");
        var world = GetString(_diagnostics, "world", "mode");
        Row("World mode", world ?? "—");
        var provider = _diagnostics.TryGetProperty("provider", out var providerValue) && providerValue.ValueKind == JsonValueKind.Object
            ? providerValue
            : default;
        Row("Provider", provider.ValueKind == JsonValueKind.Object
            ? (provider.TryGetProperty("configured", out var configured) && configured.GetBoolean()
                ? $"{GetString(provider, "kind")} · {GetString(provider, "base_url")}"
                : "not configured")
            : "—");
        var household = _diagnostics.TryGetProperty("household", out var householdValue) && householdValue.ValueKind == JsonValueKind.Object
            ? householdValue
            : default;
        if (household.ValueKind == JsonValueKind.Object)
        {
            Row("Household", $"{GetInt(household, "people")} people · {GetInt(household, "contexts")} contexts");
        }
        var devices = _diagnostics.TryGetProperty("devices", out var devicesValue) && devicesValue.ValueKind == JsonValueKind.Object
            ? devicesValue
            : default;
        if (devices.ValueKind == JsonValueKind.Object)
        {
            Row("Devices", $"{GetInt(devices, "enrolled")} enrolled · {GetInt(devices, "registered")} registered");
        }
        var rules = _diagnostics.TryGetProperty("rules", out var rulesValue) && rulesValue.ValueKind == JsonValueKind.Object
            ? rulesValue
            : default;
        if (rules.ValueKind == JsonValueKind.Object)
        {
            Row("Rules", $"{GetInt(rules, "total")} total · {GetInt(rules, "approved")} approved · {GetInt(rules, "proposed")} proposed");
        }
        var models = _diagnostics.TryGetProperty("models", out var modelsValue) && modelsValue.ValueKind == JsonValueKind.Object
            ? modelsValue
            : default;
        if (models.ValueKind == JsonValueKind.Object)
        {
            Row("Models", $"{GetInt(models, "registered")} registered · {GetInt(models, "loaded")} loaded");
        }
        var voice = _diagnostics.TryGetProperty("voice", out var voiceValue) && voiceValue.ValueKind == JsonValueKind.Object
            ? voiceValue
            : default;
        if (voice.ValueKind == JsonValueKind.Object)
        {
            var enabled = voice.TryGetProperty("enabled", out var voiceEnabled) && voiceEnabled.GetBoolean();
            Row("Voice", enabled ? $"enabled · {GetString(voice, "state") ?? "?"}" : "disabled");
        }
        Row("Uptime", $"{GetInt(_diagnostics, "uptime_seconds")} s");
    }

    private async void OnProbeProviderClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.ProbeSystemProviderAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ProbeResultText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Probe failed."
                    : "Probe failed.";
                return;
            }
            var reachable = result.TryGetProperty("reachable", out var reachableValue) && reachableValue.GetBoolean();
            ProbeResultText.Text = (reachable ? "Provider reachable" : "Provider unreachable")
                + (result.TryGetProperty("detail", out var detail) && detail.ValueKind == JsonValueKind.String
                    ? $" — {detail.GetString()}"
                    : "");
        }
        catch (Exception ex)
        {
            ProbeResultText.Text = ex.Message;
        }
    }

    private void RenderBackups(JsonElement backups)
    {
        BackupsList.Children.Clear();
        foreach (var backup in Enumerate(backups))
        {
            var backupId = GetString(backup, "id") ?? "";
            var card = new StackPanel { Spacing = 4 };
            card.Children.Add(new TextBlock
            {
                Text = backupId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var created = GetString(backup, "created_at");
            var files = Enumerate(backup, "files").Count();
            card.Children.Add(new TextBlock
            {
                Text = $"{created ?? "unknown time"} · {files} files",
                Opacity = 0.72,
            });
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var restore = new Button { Content = "Restore" };
            restore.Click += async (_, _) => await RestoreBackupAsync(backupId);
            var delete = new Button { Content = "Delete" };
            delete.Click += async (_, _) => await DeleteBackupAsync(backupId);
            buttons.Children.Add(restore);
            buttons.Children.Add(delete);
            card.Children.Add(buttons);
            BackupsList.Children.Add(WrapCard(card));
        }
        if (BackupsList.Children.Count == 0)
        {
            BackupsList.Children.Add(new TextBlock
            {
                Text = "No backups yet. Create one before changing providers or data directories.",
                Opacity = 0.72,
            });
        }
    }

    private async void OnCreateBackupClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.CreateBackupAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Backup failed."
                    : "Backup failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private async Task RestoreBackupAsync(string backupId)
    {
        if (_client is null)
        {
            return;
        }
        // Mirror the web surface's confirmation: a restore is high-consequence
        // and the running process keeps its in-memory state until a restart.
        var dialog = new ContentDialog
        {
            Title = $"Restore {backupId}?",
            Content = new TextBlock
            {
                Text = "Backup files replace the current installation files on disk. The running process keeps its in-memory rules, household, and enrollments until HAVEN restarts.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Restore",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.RestoreBackupAsync(backupId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Restore failed."
                    : "Restore failed.";
                return;
            }
            SettingsErrorText.Text = "Restore complete — restart HAVEN to apply it to the running process.";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private async Task DeleteBackupAsync(string backupId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Delete backup {backupId}?",
            Content = new TextBlock
            {
                Text = "The backup's files are removed permanently.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Delete",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.DeleteBackupAsync(backupId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Delete failed."
                    : "Delete failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private void RenderService(JsonElement service)
    {
        ServicePanel.Children.Clear();
        var installed = service.TryGetProperty("installed", out var installedValue) && installedValue.GetBoolean();
        var running = service.TryGetProperty("running", out var runningValue) && runningValue.GetBoolean();
        var detail = GetString(service, "detail") ?? "";
        var card = new StackPanel { Spacing = 4 };
        card.Children.Add(new TextBlock
        {
            Text = installed
                ? (running ? "HAVEN starts at logon (running)" : $"HAVEN starts at logon ({detail})")
                : $"Not installed to start at logon ({detail})",
            TextWrapping = TextWrapping.Wrap,
        });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var toggle = new Button { Content = installed ? "Uninstall" : "Install" };
        toggle.Click += async (_, _) => await ToggleServiceAsync(installed);
        buttons.Children.Add(toggle);
        card.Children.Add(buttons);
        ServicePanel.Children.Add(WrapCard(card));
    }

    private async Task ToggleServiceAsync(bool installed)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = installed
                ? await _client.UninstallServiceAsync()
                : await _client.InstallServiceAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Service change failed."
                    : "Service change failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }


    private JsonElement _rooms = default;
    private JsonElement _pending = default;

    private async void OnRoomsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadRoomsAsync();
    }

    private async Task LoadRoomsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetRoomsAsync();
            _rooms = result.GetProperty("rooms").Clone();
            _pending = result.GetProperty("pending").Clone();
            RoomsErrorText.Text = "";
            HomeStatusText.Text = "";
            HomeUpdatedText.Text = $"Updated {DateTime.Now:t}";
            RenderRooms();
            RenderHomeDevices();
        }
        catch (Exception ex)
        {
            HomeStatusText.Text = "";
            RoomsErrorText.Text = $"Could not load home state: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderRooms()
    {
        var selected = (RoomCards.SelectedItem as ListViewItem)?.Tag?.ToString();
        RoomCards.Items.Clear();
        foreach (var room in Enumerate(_rooms))
        {
            var roomId = GetString(room, "id") ?? "";
            var people = GetPeople(room);
            var deviceCount = Enumerate(room, "devices").Count();
            var card = new StackPanel { Spacing = 4, MinWidth = 200 };
            var name = new TextBlock
            {
                Text = GetString(room, "name") ?? roomId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            };
            card.Children.Add(name);
            card.Children.Add(new TextBlock
            {
                Text = people.Count > 0 ? string.Join(" · ", people) : "Empty",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            card.Children.Add(new TextBlock
            {
                Text = deviceCount == 0
                    ? "No devices"
                    : deviceCount == 1 ? "1 device" : $"{deviceCount} devices",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            RoomCards.Items.Add(new ListViewItem
            {
                Tag = roomId,
                Content = new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    MinWidth = 210,
                    Child = card,
                },
            });
        }
        if (RoomCards.Items.Count == 0)
        {
            RoomCards.Items.Add(new TextBlock
            {
                Text = "No rooms yet. Rooms are yours to declare and do not require a device.",
                Opacity = 0.72,
            });
        }
        else if (selected is not null && RoomCards.Items.Any(item =>
            item is ListViewItem listItem && listItem.Tag?.ToString() == selected))
        {
            RoomCards.SelectedItem = RoomCards.Items.First(item =>
                item is ListViewItem listItem && listItem.Tag?.ToString() == selected);
        }
        else if (RoomCards.SelectedItem is null && RoomCards.Items[0] is ListViewItem)
        {
            RoomCards.SelectedItem = RoomCards.Items[0];
        }
        RenderPending();
        RenderRoomDetail();
    }

    private void RenderHomeDevices()
    {
        HomeDevicesList.Children.Clear();
        var any = false;
        foreach (var room in Enumerate(_rooms))
        {
            var devices = Enumerate(room, "devices").ToList();
            if (devices.Count == 0)
            {
                continue;
            }
            any = true;
            HomeDevicesList.Children.Add(new TextBlock
            {
                Text = GetString(room, "name") ?? GetString(room, "id") ?? "Room",
                Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            });
            foreach (var device in devices)
            {
                HomeDevicesList.Children.Add(MakeDeviceRow(device));
            }
        }
        if (!any)
        {
            HomeDevicesList.Children.Add(new TextBlock
            {
                Text = "No devices yet. Enroll a device from Setup → Connections and it appears here.",
                Opacity = 0.72,
            });
        }
    }

    private void RenderPending()
    {
        PendingList.Children.Clear();
        foreach (var request in Enumerate(_pending))
        {
            var requestId = GetString(request, "request_id") ?? "";
            var card = new StackPanel { Spacing = 4 };
            card.Children.Add(new TextBlock
            {
                Text = GetString(request, "title") ?? requestId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            var detail = GetString(request, "detail");
            if (!string.IsNullOrWhiteSpace(detail))
            {
                card.Children.Add(new TextBlock
                {
                    Text = detail,
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.72,
                });
            }
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var approve = new Button { Content = "Approve" };
            approve.Click += async (_, _) => await DecideRequestAsync(requestId, approve: true);
            var deny = new Button { Content = "Deny" };
            deny.Click += async (_, _) => await DecideRequestAsync(requestId, approve: false);
            buttons.Children.Add(approve);
            buttons.Children.Add(deny);
            card.Children.Add(buttons);
            PendingList.Children.Add(new Border
            {
                Padding = new Microsoft.UI.Xaml.Thickness(10),
                CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
                BorderThickness = new Microsoft.UI.Xaml.Thickness(1),
                BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
                Child = card,
            });
        }
        if (PendingList.Children.Count == 0)
        {
            PendingList.Children.Add(new TextBlock { Text = "Nothing waiting on you.", Opacity = 0.72 });
        }
    }

    private async void OnAddRoomClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var name = new TextBox { PlaceholderText = "e.g. Studio", MinWidth = 280 };
        var fields = new StackPanel { Spacing = 8 };
        fields.Children.Add(new TextBlock { Text = "Room name" });
        fields.Children.Add(name);
        fields.Children.Add(new TextBlock
        {
            Text = "A room is your declaration; it is valid before any device is connected and survives a provider being unavailable.",
            TextWrapping = TextWrapping.Wrap,
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        var dialog = new ContentDialog
        {
            Title = "Add room",
            Content = fields,
            PrimaryButtonText = "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        try
        {
            var result = await _client.AddRoomAsync(name.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to add the room."
                    : "HAVEN Core refused to add the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task RenameRoomAsync(string roomId, string currentName)
    {
        if (_client is null)
        {
            return;
        }
        var name = new TextBox { Text = currentName, MinWidth = 280 };
        var dialog = new ContentDialog
        {
            Title = $"Rename {currentName}",
            Content = name,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        try
        {
            var result = await _client.RenameRoomAsync(roomId, name.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to rename the room."
                    : "HAVEN Core refused to rename the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task RemoveRoomAsync(string roomId, string roomName)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {roomName}?",
            Content = new TextBlock
            {
                Text = $"The {roomName} declaration is removed from your household. Devices in it are not deleted; they show as unassigned until you declare the room again.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            PrimaryButtonStyle = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.RemoveRoomAsync(roomId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to remove the room."
                    : "HAVEN Core refused to remove the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task DecideRequestAsync(string requestId, bool approve)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = approve
                ? await _client.ApproveRequestAsync(requestId)
                : await _client.DenyRequestAsync(requestId);
            ApplyCommandState(result);
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private void RenderRoomDetail()
    {
        RoomDetail.Children.Clear();
        if (RoomCards.SelectedItem is not ListViewItem item || item.Tag is not string roomId)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "Select a room.", Opacity = 0.72 });
            return;
        }
        var room = Enumerate(_rooms).FirstOrDefault(candidate => GetString(candidate, "id") == roomId);
        if (room.ValueKind != JsonValueKind.Object)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "Select a room.", Opacity = 0.72 });
            return;
        }

        var head = new StackPanel { Spacing = 2 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(room, "name") ?? roomId,
            FontSize = 20,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        var people = GetPeople(room);
        head.Children.Add(new TextBlock
        {
            Text = people.Count > 0 ? string.Join(" · ", people) : "Empty",
            Opacity = 0.72,
        });
        var roomActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var rename = new Button
        {
            Content = "Rename",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
        };
        rename.Click += async (_, _) =>
            await RenameRoomAsync(roomId, GetString(room, "name") ?? roomId);
        var remove = new Button
        {
            Content = "Remove",
            Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
        };
        remove.Click += async (_, _) =>
            await RemoveRoomAsync(roomId, GetString(room, "name") ?? roomId);
        roomActions.Children.Add(rename);
        roomActions.Children.Add(remove);
        head.Children.Add(roomActions);
        RoomDetail.Children.Add(head);

        if (room.TryGetProperty("camera", out var camera) && camera.ValueKind == JsonValueKind.Object)
        {
            var parts = new List<string> { "camera · " + (GetString(camera, "label") ?? GetString(camera, "id") ?? "camera") };
            var online = !camera.TryGetProperty("online", out var onlineValue) || onlineValue.GetBoolean();
            if (online)
            {
                parts.Add(camera.TryGetProperty("motion", out var motion) && motion.GetBoolean() ? "motion" : "no motion");
            }
            else
            {
                parts.Add("offline");
            }
            RoomDetail.Children.Add(new TextBlock { Text = string.Join(" — ", parts), Opacity = 0.72 });
        }

        var devices = Enumerate(room, "devices").ToList();
        if (devices.Count == 0)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "No devices in this room.", Opacity = 0.72 });
        }
        foreach (var device in devices)
        {
            RoomDetail.Children.Add(MakeDeviceRow(device));
        }
    }

    private Border MakeDeviceRow(JsonElement device)
    {
        var deviceId = GetString(device, "id") ?? "";
        var status = GetString(device, "status");
        var degraded = status is not null && status != "observed";

        var card = new StackPanel { Spacing = 6 };
        if (degraded)
        {
            card.Opacity = 0.55;
        }

        var head = new Grid();
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });
        var label = new TextBlock
        {
            Text = (GetString(device, "role") ?? "device") + (deviceId.Length > 0 ? " · " + deviceId : ""),
            Opacity = 0.72,
            TextWrapping = TextWrapping.Wrap,
        };
        head.Children.Add(label);
        var stateText = new TextBlock
        {
            Text = DeviceStateLine(device),
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        if (degraded)
        {
            stateText.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenWarningBrush"];
        }
        Grid.SetColumn(stateText, 1);
        head.Children.Add(stateText);
        card.Children.Add(head);

        var controls = MakeDeviceControls(device, deviceId);
        if (controls is not null)
        {
            card.Children.Add(controls);
        }

        var provenance = DeviceProvenanceText(device);
        if (provenance.Length > 0)
        {
            card.Children.Add(new TextBlock { Text = provenance, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
        }

        return new Border
        {
            Padding = new Microsoft.UI.Xaml.Thickness(12),
            CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
            BorderThickness = new Microsoft.UI.Xaml.Thickness(1),
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
            Child = card,
        };
    }

    private StackPanel? MakeDeviceControls(JsonElement device, string deviceId)
    {
        var role = (GetString(device, "role") ?? "").ToLowerInvariant();
        var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var any = false;

        if (role == "light")
        {
            if (device.TryGetProperty("is_on", out var isOn) && isOn.ValueKind == JsonValueKind.True)
            {
                var turnOff = new Button { Content = "Turn off" };
                turnOff.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "light.turn_off");
                controls.Children.Add(turnOff);
                any = true;
            }
            if (device.TryGetProperty("brightness_pct", out var brightness) && brightness.ValueKind == JsonValueKind.Number)
            {
                var input = new TextBox
                {
                    Text = brightness.GetInt32().ToString(),
                    Width = 72,
                    IsSpellCheckEnabled = false,
                };
                var set = new Button { Content = "Set" };
                set.Click += async (_, _) =>
                {
                    var pct = Math.Clamp((int)Math.Round(double.TryParse(input.Text, out var value) ? value : 0), 0, 100);
                    await SendDeviceCommandAsync(deviceId, "light.set_brightness", pct);
                };
                controls.Children.Add(input);
                controls.Children.Add(set);
                any = true;
            }
        }
        else if (role == "cover")
        {
            var open = new Button { Content = "Open" };
            open.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "cover.open");
            var close = new Button { Content = "Close" };
            close.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "cover.close");
            controls.Children.Add(open);
            controls.Children.Add(close);
            any = true;
        }

        return any ? controls : null;
    }

    private async Task SendDeviceCommandAsync(string deviceId, string service, int? brightnessPct = null)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.SendDeviceCommandAsync(deviceId, service, brightnessPct);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the command."
                    : "HAVEN Core refused the command.";
                return;
            }
            ApplyCommandState(result);
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private void ApplyCommandState(JsonElement result)
    {
        if (result.ValueKind != JsonValueKind.Object || !result.TryGetProperty("state", out var state))
        {
            return;
        }
        if (state.TryGetProperty("rooms", out var rooms))
        {
            _rooms = rooms.Clone();
        }
        if (state.TryGetProperty("pending", out var pending))
        {
            _pending = pending.Clone();
        }
        RoomsErrorText.Text = "";
        RenderRooms();
        RenderHomeDevices();
    }

    private void OnRoomSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        RenderRoomDetail();
    }

    // -- people & contexts --------------------------------------------------

    private JsonElement _people = default;
    private JsonElement _contexts = default;

    private async Task LoadContextsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var contexts = await _client.GetContextsAsync();
            _contexts = contexts.GetProperty("contexts").Clone();
            RenderContexts();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = $"Could not load contexts: {ex.Message}";
        }
    }

    private async Task LoadPeopleAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var people = await _client.GetPeopleAsync();
            _people = people.GetProperty("people").Clone();
            PeopleErrorText.Text = "";
            PeopleStatusText.Text = "";
            RenderPeople();
        }
        catch (Exception ex)
        {
            PeopleStatusText.Text = "";
            PeopleErrorText.Text = $"Could not load people: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderPeople()
    {
        PeopleList.Children.Clear();
        foreach (var person in Enumerate(_people))
        {
            var personId = GetString(person, "person_id") ?? "";
            var name = GetString(person, "name") ?? personId;
            var role = Sentence(GetString(person, "role") ?? "member");

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = name,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(new TextBlock { Text = role, Opacity = 0.72 });
            card.Children.Add(head);

            var present = person.TryGetProperty("present", out var presentValue)
                && presentValue.ValueKind == JsonValueKind.True;
            var room = GetString(person, "room");
            card.Children.Add(new TextBlock
            {
                Text = present
                    ? (room is not null ? $"Present · {room}" : "Present")
                    : "Not present",
                Opacity = 0.72,
            });

            var sources = Enumerate(person, "sources").ToList();
            if (sources.Count > 0)
            {
                card.Children.Add(new TextBlock
                {
                    Text = string.Join(" · ", sources.Select(source =>
                        $"presence: {GetString(source, "entity_id") ?? "?"} in {GetString(source, "room_id") ?? "?"}")),
                    Opacity = 0.6,
                    TextWrapping = TextWrapping.Wrap,
                });
            }

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditPersonAsync(person);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemovePersonAsync(personId, name);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            PeopleList.Children.Add(WrapCard(card));
        }
        if (PeopleList.Children.Count == 0)
        {
            PeopleList.Children.Add(new TextBlock
            {
                Text = "No one is declared yet. Add the people HAVEN should know about.",
                Opacity = 0.72,
            });
        }
    }

    private void RenderContexts()
    {
        ContextsList.Children.Clear();
        foreach (var context in Enumerate(_contexts))
        {
            var contextId = GetString(context, "context_id") ?? "";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(context, "label") ?? contextId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var active = context.TryGetProperty("active", out var activeValue)
                && activeValue.ValueKind == JsonValueKind.True;
            var state = new TextBlock
            {
                Text = active ? "Active" : "Inactive",
                Opacity = 0.72,
            };
            if (active)
            {
                state.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenSuccessBrush"];
            }
            head.Children.Add(state);
            card.Children.Add(head);
            card.Children.Add(new TextBlock
            {
                Text = GetString(context, "entity_id") ?? "",
                Opacity = 0.6,
            });

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditContextAsync(context);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemoveContextAsync(contextId);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            ContextsList.Children.Add(WrapCard(card));
        }
        if (ContextsList.Children.Count == 0)
        {
            ContextsList.Children.Add(new TextBlock
            {
                Text = "No contexts declared. Contexts map a household entity's \"on\" to a meaning HAVEN can reason about.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private static Border WrapCard(StackPanel content) => new()
    {
        Style = (Style)Application.Current.Resources["HavenCardStyle"],
        Child = content,
    };

    /* Status chip (spec 13): small rounded pill, ~20% tinted background,
       solid accent text. */
    private static Border MakeChip(string text, string tintResource, string foregroundResource)
    {
        var chip = new Border
        {
            CornerRadius = new Microsoft.UI.Xaml.CornerRadius(10),
            Padding = new Microsoft.UI.Xaml.Thickness(8, 2, 8, 2),
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[tintResource],
            Child = new TextBlock
            {
                Text = text,
                FontSize = 11.5,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[foregroundResource],
            },
        };
        ToolTipService.SetToolTip(chip, text);
        return chip;
    }

    private async Task RunPeopleMutationAsync(Func<Task<JsonElement>> operation)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await operation();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                PeopleErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            PeopleErrorText.Text = "";
            await LoadPeopleAsync();
        }
        catch (Exception ex)
        {
            PeopleErrorText.Text = ex.Message;
        }
    }

    private async void OnAddPersonClicked(object sender, RoutedEventArgs args)
    {
        await EditPersonAsync(default);
    }

    private async Task EditPersonAsync(JsonElement person)
    {
        if (_client is null)
        {
            return;
        }
        var editing = person.ValueKind == JsonValueKind.Object;
        var personId = editing ? GetString(person, "person_id") ?? "" : "";
        var name = new TextBox { PlaceholderText = "Name", Text = editing ? GetString(person, "name") ?? "" : "" };
        var role = new TextBox
        {
            PlaceholderText = "Role (owner or member)",
            Text = editing ? GetString(person, "role") ?? "" : "",
        };
        // The web PATCH for a person only edits name and role; entity/room
        // presence sources are declared at add time.
        StackPanel fields;
        TextBox? entityId = null;
        TextBox? roomId = null;
        if (editing)
        {
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
        }
        else
        {
            entityId = new TextBox { PlaceholderText = "Presence entity (optional)" };
            roomId = new TextBox { PlaceholderText = "Room (required with entity)" };
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
            fields.Children.Add(entityId);
            fields.Children.Add(roomId);
        }
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit person" : "Add person",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        var roleValue = string.IsNullOrWhiteSpace(role.Text) ? null : role.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdatePersonAsync(personId, name.Text.Trim(), roleValue));
        }
        else
        {
            var entityValue = string.IsNullOrWhiteSpace(entityId!.Text) ? null : entityId.Text.Trim();
            var roomValue = string.IsNullOrWhiteSpace(roomId!.Text) ? null : roomId.Text.Trim();
            await RunPeopleMutationAsync(() => _client.AddPersonAsync(name.Text.Trim(), roleValue ?? "member", entityValue, roomValue));
        }
    }

    private async Task RemovePersonAsync(string personId, string name)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {name}?",
            Content = new TextBlock
            {
                Text = "HAVEN will forget the declaration, including any presence sources attached to it.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPeopleMutationAsync(() => _client.RemovePersonAsync(personId));
    }

    private async void OnAddContextClicked(object sender, RoutedEventArgs args)
    {
        await EditContextAsync(default);
    }

    private async Task EditContextAsync(JsonElement context)
    {
        if (_client is null)
        {
            return;
        }
        var editing = context.ValueKind == JsonValueKind.Object;
        var contextId = editing ? GetString(context, "context_id") ?? "" : "";
        var label = new TextBox { PlaceholderText = "Label", Text = editing ? GetString(context, "label") ?? "" : "" };
        var entity = new TextBox
        {
            PlaceholderText = "Entity (e.g. input_boolean.working_late)",
            Text = editing ? GetString(context, "entity_id") ?? "" : "",
        };
        var fields = new StackPanel { Spacing = 8, MinWidth = 320 };
        fields.Children.Add(label);
        fields.Children.Add(entity);
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit context" : "Add context",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(label.Text))
        {
            return;
        }
        var entityValue = string.IsNullOrWhiteSpace(entity.Text) ? null : entity.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdateContextAsync(contextId, label.Text.Trim(), entityValue));
        }
        else
        {
            await RunPeopleMutationAsync(() => _client.AddContextAsync(label.Text.Trim(), entityValue ?? ""));
        }
    }

    private async Task RemoveContextAsync(string contextId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Remove this context?",
            Content = new TextBlock
            {
                Text = "HAVEN will stop reasoning about this context until it is declared again.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPeopleMutationAsync(() => _client.RemoveContextAsync(contextId));
    }

    // -- automations --------------------------------------------------------

    private static readonly string[] WeekdayNames = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" };

    private JsonElement _automations = default;
    private JsonElement _scheduler = default;
    private JsonElement _automationOptions = default;

    private async void OnAutomationsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadAutomationsAsync();
    }

    private async Task LoadAutomationsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetAutomationsAsync();
            var options = await _client.GetAutomationOptionsAsync();
            _automations = result.GetProperty("automations").Clone();
            _scheduler = result.GetProperty("scheduler").Clone();
            _automationOptions = options.GetProperty("options").Clone();
            AutomationsErrorText.Text = "";
            AutomationsStatusText.Text = "";
            RenderAutomations();
        }
        catch (Exception ex)
        {
            AutomationsStatusText.Text = "";
            AutomationsErrorText.Text = $"Could not load automations: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderAutomations()
    {
        AutomationsList.Children.Clear();
        foreach (var rule in Enumerate(_automations))
        {
            AutomationsList.Children.Add(MakeAutomationCard(rule));
        }
        if (AutomationsList.Children.Count == 0)
        {
            AutomationsList.Children.Add(new TextBlock
            {
                Text = "No automations yet. Propose one and approve it to put the scheduler to work.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeAutomationCard(JsonElement rule)
    {
        var ruleId = GetString(rule, "rule_id") ?? "";
        var status = GetString(rule, "status") ?? "unknown";

        var card = new StackPanel { Spacing = 4 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(rule, "action") ?? "automation",
            Opacity = 0.72,
        });
        var (chipTint, chipForeground) = status switch
        {
            "approved" => ("HavenSuccessTintBrush", "HavenSuccessBrush"),
            "proposed" => ("HavenWarningTintBrush", "HavenWarningBrush"),
            "revoked" => ("HavenStrokeBrush", "HavenMutedTextBrush"),
            _ => ("HavenAccentTintBrush", "HavenMutedTextBrush"),
        };
        head.Children.Add(MakeChip(Sentence(status), chipTint, chipForeground));
        var approvedAt = GetString(rule, "approved_at");
        if (approvedAt is not null)
        {
            head.Children.Add(new TextBlock { Text = approvedAt, Opacity = 0.6 });
        }
        card.Children.Add(head);

        card.Children.Add(new TextBlock
        {
            Text = GetString(rule, "summary") ?? ruleId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        var target = GetString(rule, "target");
        var schedule = rule.TryGetProperty("schedule", out var scheduleValue) && scheduleValue.ValueKind == JsonValueKind.Object
            ? scheduleValue
            : default;
        var details = new List<string>();
        if (target is not null)
        {
            details.Add(target);
        }
        if (schedule.ValueKind == JsonValueKind.Object)
        {
            var time = GetString(schedule, "time_of_day");
            var days = Enumerate(schedule, "weekdays")
                .Select(day => day.ValueKind == JsonValueKind.Number ? day.GetInt32() : -1)
                .Where(day => day >= 0 && day < WeekdayNames.Length)
                .Select(day => WeekdayNames[day])
                .ToList();
            var when = time ?? "?";
            if (schedule.TryGetProperty("weekdays", out var daysValue) && daysValue.ValueKind == JsonValueKind.Array && daysValue.GetArrayLength() > 0)
            {
                when += " · " + string.Join("/", days);
            }
            else
            {
                when += " · every day";
            }
            details.Add(when);
        }
        var schedulerRow = Enumerate(_scheduler).FirstOrDefault(
            row => GetString(row, "rule_id") == ruleId);
        if (status == "approved" && schedulerRow.ValueKind == JsonValueKind.Object)
        {
            var enabled = !schedulerRow.TryGetProperty("enabled", out var enabledValue) || enabledValue.GetBoolean();
            details.Add(enabled ? "Scheduling enabled" : "Scheduling disabled");
        }
        if (details.Count > 0)
        {
            card.Children.Add(new TextBlock
            {
                Text = string.Join("  ·  ", details),
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        if (status == "proposed")
        {
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditAutomationAsync(rule);
            var approve = new Button { Content = "Approve" };
            approve.Click += async (_, _) => await ApproveAutomationAsync(ruleId);
            var discard = new Button { Content = "Discard" };
            discard.Click += async (_, _) => await RevokeAutomationAsync(ruleId, "Discard");
            buttons.Children.Add(edit);
            buttons.Children.Add(approve);
            buttons.Children.Add(discard);
        }
        else if (status == "approved")
        {
            var enabled = schedulerRow.ValueKind != JsonValueKind.Object
                || !schedulerRow.TryGetProperty("enabled", out var value)
                || value.GetBoolean();
            var toggle = new Button { Content = enabled ? "Disable" : "Enable" };
            toggle.Click += async (_, _) => await SetAutomationEnabledAsync(ruleId, !enabled);
            var revoke = new Button { Content = "Revoke" };
            revoke.Click += async (_, _) => await RevokeAutomationAsync(ruleId, "Revoke");
            buttons.Children.Add(toggle);
            buttons.Children.Add(revoke);
        }
        if (buttons.Children.Count > 0)
        {
            card.Children.Add(buttons);
        }

        return WrapCard(card);
    }

    private async Task RunAutomationMutationAsync(Func<Task<JsonElement>> operation)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await operation();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                var message = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString()
                    : null;
                if (message is null
                    && envelope.TryGetProperty("decision", out var decision)
                    && decision.ValueKind == JsonValueKind.Object
                    && decision.TryGetProperty("explanation", out var explanation)
                    && explanation.ValueKind == JsonValueKind.String)
                {
                    message = explanation.GetString();
                }
                AutomationsErrorText.Text = message ?? "HAVEN Core refused the change.";
                return;
            }
            AutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

    private static StackPanel MakeWeekdayPicker(IReadOnlyList<int> selected)
    {
        var days = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        for (var index = 0; index < WeekdayNames.Length; index += 1)
        {
            days.Children.Add(new CheckBox
            {
                Content = WeekdayNames[index],
                Tag = index,
                IsChecked = selected.Contains(index),
            });
        }
        return days;
    }

    private static List<int> SelectedWeekdays(StackPanel picker) =>
        picker.Children
            .OfType<CheckBox>()
            .Where(box => box.IsChecked == true)
            .Select(box => (int)box.Tag!)
            .ToList();

    private async void OnProposeAutomationClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var options = Enumerate(_automationOptions).ToList();
        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        if (options.Count == 0)
        {
            fields.Children.Add(new TextBlock
            {
                Text = "No writable device capabilities are available yet. Enroll a device during setup or connections first.",
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.72,
            });
        }
        var source = new TextBox { PlaceholderText = "e.g. Turn off the office light", AcceptsReturn = false };
        var time = new TextBox { PlaceholderText = "HH:MM", Text = "22:00" };
        var weekdays = MakeWeekdayPicker(Array.Empty<int>());
        fields.Children.Add(new TextBlock { Text = "What should HAVEN do?" });
        fields.Children.Add(source);
        fields.Children.Add(new TextBlock { Text = "At" });
        fields.Children.Add(time);
        fields.Children.Add(new TextBlock { Text = "Days (leave all unchecked for every day)" });
        fields.Children.Add(weekdays);
        ComboBox? device = null;
        if (options.Count > 0)
        {
            device = new ComboBox { ItemsSource = options.Select(option =>
                $"{(GetString(option, "room") is { Length: > 0 } room ? room + " · " : "")}{GetString(option, "device_id")} · {GetString(option, "capability")}").ToList() };
            device.SelectedIndex = 0;
            fields.Children.Add(new TextBlock { Text = "Device & control" });
            fields.Children.Add(device);
        }
        var note = new TextBlock
        {
            Text = "HAVEN will propose this rule first. An owner must approve it before the scheduler can act.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        };
        fields.Children.Add(note);
        var dialog = new ContentDialog
        {
            Title = "Propose automation",
            Content = fields,
            PrimaryButtonText = "Propose",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(source.Text))
        {
            return;
        }
        if (options.Count == 0 || device is null)
        {
            AutomationsErrorText.Text = "Choose a writable device capability first.";
            return;
        }
        var chosen = options[device.SelectedIndex];
        await RunAutomationMutationAsync(() => _client.CreateAutomationAsync(
            source.Text.Trim(),
            time.Text.Trim(),
            SelectedWeekdays(weekdays),
            GetString(chosen, "device_id") ?? "",
            GetString(chosen, "capability") ?? "",
            GetString(chosen, "service") ?? "",
            source.Text.Trim()));
    }

    private async Task EditAutomationAsync(JsonElement rule)
    {
        if (_client is null)
        {
            return;
        }
        var ruleId = GetString(rule, "rule_id") ?? "";
        var schedule = rule.TryGetProperty("schedule", out var scheduleValue) && scheduleValue.ValueKind == JsonValueKind.Object
            ? scheduleValue
            : default;
        var selectedDays = schedule.ValueKind == JsonValueKind.Object
            ? Enumerate(schedule, "weekdays")
                .Select(day => day.ValueKind == JsonValueKind.Number ? day.GetInt32() : -1)
                .Where(day => day >= 0 && day < WeekdayNames.Length)
                .ToList()
            : new List<int>();

        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        var source = new TextBox { PlaceholderText = "What should HAVEN do?", Text = GetString(rule, "summary") ?? "" };
        var currentTime = GetString(schedule, "time_of_day") ?? "22:00";
        var time = new TextBox
        {
            PlaceholderText = "HH:MM",
            Text = currentTime.Length > 5 ? currentTime[..5] : currentTime,
        };
        var weekdays = MakeWeekdayPicker(selectedDays);
        var justification = new TextBox { PlaceholderText = "Why is this change correct?" };
        fields.Children.Add(new TextBlock { Text = "What should HAVEN do?" });
        fields.Children.Add(source);
        fields.Children.Add(new TextBlock { Text = "At" });
        fields.Children.Add(time);
        fields.Children.Add(new TextBlock { Text = "Days (leave all unchecked for every day)" });
        fields.Children.Add(weekdays);
        fields.Children.Add(new TextBlock { Text = "Justification" });
        fields.Children.Add(justification);
        fields.Children.Add(new TextBlock
        {
            Text = "This proposal is not approved yet. Editing creates a new audited draft; approved automations stay immutable.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Edit proposed automation",
            Content = fields,
            PrimaryButtonText = "Save proposal",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(source.Text))
        {
            return;
        }
        var justificationValue = string.IsNullOrWhiteSpace(justification.Text) ? null : justification.Text.Trim();
        await RunAutomationMutationAsync(() => _client.UpdateAutomationAsync(
            ruleId,
            source.Text.Trim(),
            time.Text.Trim(),
            SelectedWeekdays(weekdays),
            source.Text.Trim(),
            justificationValue));
    }

    private async Task ApproveAutomationAsync(string ruleId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Approve this automation?",
            Content = new TextBlock
            {
                Text = "Approval attaches your authority to exactly this draft; editing afterwards requires revoking and proposing anew.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Approve",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunAutomationMutationAsync(() => _client.ApproveAutomationAsync(ruleId));
    }

    private async Task RevokeAutomationAsync(string ruleId, string verb)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"{verb} this automation?",
            Content = new TextBlock
            {
                Text = "The rule stays in the ledger as revoked, with the revocation audited.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = verb,
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunAutomationMutationAsync(() => _client.RevokeAutomationAsync(
            ruleId, $"owner revoked automation from HAVEN ({verb.ToLowerInvariant()})"));
    }

    private async Task SetAutomationEnabledAsync(string ruleId, bool enabled)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            await _client.SetAutomationEnabledAsync(ruleId, enabled);
            AutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

    // -- models ---------------------------------------------------------------

    private static readonly HashSet<string> ModelFailureStates = new()
    {
        "incomplete", "unsupported", "hash_mismatch", "backend_missing", "load_failed", "unreachable",
    };

    private JsonElement _models = default;
    private JsonElement _modelAssignments = default;
    private JsonElement _modelJobs = default;
    private JsonElement _discovered = default;
    private bool _assigningRoles;
    private string _modelsTab = "local";

    private void OnModelsTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            SelectModelsTab(tab);
        }
    }

    private void SelectModelsTab(string tab)
    {
        _modelsTab = tab;
        ModelsTabLocal.Visibility = tab == "local" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabDownloaded.Visibility = tab == "downloaded" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabExternal.Visibility = tab == "endpoint" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabDownloads.Visibility = tab == "downloads" ? Visibility.Visible : Visibility.Collapsed;
        foreach (var button in ModelsTabs.Children.OfType<Button>())
        {
            button.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            button.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
    }

    private async void OnModelsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadModelsAsync();
    }

    private async Task LoadModelsAsync(bool silent = false)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var overview = await _client.GetModelsOverviewAsync();
            var jobs = await _client.GetModelJobsAsync();
            _models = overview.GetProperty("models").Clone();
            _modelAssignments = overview.GetProperty("assignments").Clone();
            _discovered = overview.TryGetProperty("discovered", out var found) ? found.Clone() : default;
            _modelJobs = jobs.GetProperty("jobs").Clone();
            if (!silent)
            {
                ModelsErrorText.Text = "";
            }
            RenderModels();
            RenderModelRoles();
            RenderModelJobs();
            RenderDiscovered();
        }
        catch (Exception ex)
        {
            if (!silent)
            {
                ModelsErrorText.Text = ex.Message;
            }
        }
    }

    private void RenderModels()
    {
        // Spec 38 tabs: Local / Downloaded / External, fed by the record's source.
        var local = Enumerate(_models).Where(model => GetString(model, "source") == "local").ToList();
        var downloaded = Enumerate(_models).Where(model => GetString(model, "source") == "downloaded").ToList();
        var external = Enumerate(_models).Where(model => GetString(model, "source") == "endpoint").ToList();

        ModelsLocalList.Children.Clear();
        foreach (var model in local)
        {
            ModelsLocalList.Children.Add(MakeModelCard(model));
        }
        if (ModelsLocalList.Children.Count == 0)
        {
            ModelsLocalList.Children.Add(new TextBlock
            {
                Text = "No local models. Install a model folder or add a folder to scan.",
                Opacity = 0.72,
            });
        }

        ModelsList.Children.Clear();
        foreach (var model in downloaded)
        {
            ModelsList.Children.Add(MakeModelCard(model));
        }
        if (ModelsList.Children.Count == 0)
        {
            ModelsList.Children.Add(new TextBlock
            {
                Text = "No downloaded models yet. Install from a URL to see progress and verification here.",
                Opacity = 0.72,
            });
        }

        ModelsExternalList.Children.Clear();
        foreach (var model in external)
        {
            ModelsExternalList.Children.Add(MakeModelCard(model));
        }
        if (ModelsExternalList.Children.Count == 0)
        {
            ModelsExternalList.Children.Add(new TextBlock
            {
                Text = "No external endpoints. Add a provider endpoint to use hosted intelligence.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeModelCard(JsonElement model)
    {
        var modelId = GetString(model, "id") ?? "";
        var state = GetString(model, "state") ?? "unknown";

        var card = new StackPanel { Spacing = 4 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = modelId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        var (modelTint, modelForeground) = ModelFailureStates.Contains(state)
            ? ("HavenDangerTintBrush", "HavenDangerBrush")
            : state is "ready" or "loaded"
                ? ("HavenSuccessTintBrush", "HavenSuccessBrush")
                : ("HavenWarningTintBrush", "HavenWarningBrush");
        head.Children.Add(MakeChip(Sentence(state), modelTint, modelForeground));
        card.Children.Add(head);

        var details = new List<string>();
        foreach (var key in new[] { "backend", "architecture", "version", "license", "source" })
        {
            var value = GetString(model, key);
            if (value is not null)
            {
                details.Add(value);
            }
        }
        if (details.Count > 0)
        {
            card.Children.Add(new TextBlock { Text = string.Join(" · ", details), Opacity = 0.72 });
        }
        var capabilities = Enumerate(model, "capabilities").Select(item => item.GetString()).Where(item => item is not null);
        var languages = Enumerate(model, "languages").Select(item => item.GetString()).Where(item => item is not null);
        var summary = string.Join("   ", new[]
        {
            string.Join(", ", capabilities),
            string.Join(", ", languages),
        }.Where(part => part.Length > 0));
        if (summary.Length > 0)
        {
            card.Children.Add(new TextBlock { Text = summary, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
        }
        var loadedBackend = GetString(model, "loaded_backend");
        if (loadedBackend is not null)
        {
            card.Children.Add(new TextBlock { Text = $"Loaded via {loadedBackend}", Opacity = 0.72 });
        }

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        if (state != "loaded")
        {
            var load = new Button { Content = "Load" };
            load.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.LoadModelAsync(modelId));
            buttons.Children.Add(load);
        }
        if (state == "loaded" || loadedBackend is not null)
        {
            var unload = new Button { Content = "Unload" };
            unload.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.UnloadModelAsync(modelId));
            buttons.Children.Add(unload);
        }
        var remove = new Button { Content = "Remove" };
        remove.Click += async (_, _) => await RemoveModelAsync(modelId);
        buttons.Children.Add(remove);
        card.Children.Add(buttons);

        return WrapCard(card);
    }

    private void RenderModelRoles()
    {
        _assigningRoles = true;
        try
        {
            ModelRoles.Children.Clear();
            var modelIds = Enumerate(_models).Select(model => GetString(model, "id")).Where(id => id is not null).Cast<string>().ToList();
            foreach (var role in _modelAssignments.EnumerateObject())
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                row.Children.Add(new TextBlock
                {
                    Text = role.Name,
                    Width = 110,
                    VerticalAlignment = VerticalAlignment.Center,
                });
                var select = new ComboBox { MinWidth = 240 };
                select.Items.Add("None");
                foreach (var modelId in modelIds)
                {
                    select.Items.Add(modelId);
                }
                select.SelectedIndex = role.Value.ValueKind == JsonValueKind.String
                    && role.Value.GetString() is { } assigned
                    && modelIds.Contains(assigned)
                        ? modelIds.IndexOf(assigned) + 1
                        : 0;
                var roleName = role.Name;
                select.SelectionChanged += async (_, _) =>
                {
                    if (_assigningRoles || select.SelectedIndex < 0)
                    {
                        return;
                    }
                    var chosen = select.SelectedIndex == 0 ? null : modelIds[select.SelectedIndex - 1];
                    await RunModelsMutationAsync(() => _client!.AssignModelAsync(roleName, chosen));
                };
                row.Children.Add(select);
                ModelRoles.Children.Add(row);
            }
        }
        finally
        {
            _assigningRoles = false;
        }
    }

    private void RenderModelJobs()
    {
        ModelJobs.Children.Clear();
        foreach (var job in Enumerate(_modelJobs))
        {
            var jobId = GetString(job, "job_id") ?? "";
            var state = GetString(job, "state") ?? "unknown";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(job, "manifest_id") ?? GetString(job, "url") ?? jobId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            var (jobTint, jobForeground) = state switch
            {
                "ready" => ("HavenSuccessTintBrush", "HavenSuccessBrush"),
                "failed" or "cancelled" => ("HavenDangerTintBrush", "HavenDangerBrush"),
                _ => ("HavenWarningTintBrush", "HavenWarningBrush"),
            };
            head.Children.Add(MakeChip(Sentence(state), jobTint, jobForeground));
            card.Children.Add(head);

            var received = job.TryGetProperty("received_bytes", out var receivedValue) ? receivedValue.GetInt64() : 0L;
            var total = job.TryGetProperty("total_bytes", out var totalValue) && totalValue.ValueKind == JsonValueKind.Number
                ? totalValue.GetInt64()
                : (long?)null;
            var currentFile = GetString(job, "current_file");
            var progress = total is > 0
                ? $"{received / (1024.0 * 1024.0):0.0} / {total.Value / (1024.0 * 1024.0):0.0} MB{(currentFile is not null ? " · " + currentFile : "")}"
                : received > 0
                    ? $"{received / (1024.0 * 1024.0):0.0} MB{(currentFile is not null ? " · " + currentFile : "")}"
                    : null;
            if (progress is not null)
            {
                card.Children.Add(new TextBlock { Text = progress, Opacity = 0.72 });
            }
            var error = GetString(job, "error");
            if (error is not null)
            {
                card.Children.Add(new TextBlock
                {
                    Text = error,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                });
            }
            if (state is "queued" or "downloading" or "verifying")
            {
                var cancel = new Button { Content = "Cancel" };
                cancel.Click += async (_, _) =>
                {
                    try
                    {
                        await _client!.CancelModelJobAsync(jobId);
                        await LoadModelsAsync();
                    }
                    catch (Exception ex)
                    {
                        ModelsErrorText.Text = ex.Message;
                    }
                };
                card.Children.Add(cancel);
            }
            ModelJobs.Children.Add(WrapCard(card));
        }
        if (ModelJobs.Children.Count == 0)
        {
            ModelJobs.Children.Add(new TextBlock { Text = "No download jobs.", Opacity = 0.72 });
        }
    }

    private void RenderDiscovered()
    {
        DiscoveredList.Children.Clear();
        var any = false;
        foreach (var candidate in Enumerate(_discovered))
        {
            any = true;
            var path = GetString(candidate, "path") ?? "";
            var state = GetString(candidate, "state") ?? "unknown";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(candidate, "id") ?? System.IO.Path.GetFileName(path),
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(new TextBlock { Text = state, Opacity = 0.72 });
            card.Children.Add(head);
            card.Children.Add(new TextBlock { Text = path, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
            var problems = Enumerate(candidate, "problems").Select(item => item.GetString()).Where(item => item is not null);
            foreach (var problem in problems)
            {
                card.Children.Add(new TextBlock
                {
                    Text = problem,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                });
            }
            // Scan discovers, never activates: registration is the explicit gate.
            var register = new Button { Content = "Register" };
            register.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.RegisterModelAsync(path));
            card.Children.Add(register);
            DiscoveredList.Children.Add(WrapCard(card));
        }
        DiscoveredHeading.Visibility = any ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task RunModelsMutationAsync(Func<Task<JsonElement>> operation)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await operation();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ModelsErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async Task<string?> PickFolderAsync()
    {
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var picker = new Windows.Storage.Pickers.FolderPicker();
        picker.FileTypeFilter.Add("*");
        picker.SuggestedStartLocation = Windows.Storage.Pickers.PickerLocationId.ComputerFolder;
        WinRT.Interop.InitializeWithWindow.Initialize(picker, handle);
        var folder = await picker.PickSingleFolderAsync();
        return folder?.Path;
    }

    private async void OnInstallFromUrlClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var url = new TextBox { PlaceholderText = "https://example.org/models/my-model/haven-model.json" };
        var fields = new StackPanel { Spacing = 8, MinWidth = 420 };
        fields.Children.Add(new TextBlock { Text = "Model manifest URL" });
        fields.Children.Add(url);
        fields.Children.Add(new TextBlock
        {
            Text = "Downloads, verifies published hashes, and registers the model in the background. Progress appears under Download jobs.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Install from URL",
            Content = fields,
            PrimaryButtonText = "Download",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(url.Text))
        {
            return;
        }
        try
        {
            var result = await _client.DownloadModelAsync(url.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ModelsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Download failed."
                    : "Download failed.";
                return;
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async void OnInstallLocalClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var folder = await PickFolderAsync();
        if (folder is null)
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.InstallLocalModelAsync(folder));
    }

    private async void OnAddEndpointClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var url = new TextBox { PlaceholderText = "https://inference.example.org/v1" };
        var fields = new StackPanel { Spacing = 8, MinWidth = 420 };
        fields.Children.Add(new TextBlock { Text = "Endpoint URL" });
        fields.Children.Add(url);
        fields.Children.Add(new TextBlock
        {
            Text = "Registers a remote endpoint as a model source. HAVEN talks to it only when the model is loaded.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Add endpoint",
            Content = fields,
            PrimaryButtonText = "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(url.Text))
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.AddModelEndpointAsync(url.Text.Trim()));
    }

    private async void OnAddModelRootClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var folder = await PickFolderAsync();
        if (folder is null)
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.AddModelRootAsync(folder));
    }

    private async void OnScanModelRootsClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.ScanModelRootsAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ModelsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Scan failed."
                    : "Scan failed.";
                return;
            }
            if (result.TryGetProperty("discovered", out var discovered))
            {
                _discovered = discovered.Clone();
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async Task RemoveModelAsync(string modelId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {modelId}?",
            Content = new TextBlock
            {
                Text = "HAVEN deletes the model's stored files and clears any role assignment pointing at it.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.RemoveModelAsync(modelId));
    }

    /* State line per role, mirroring the web surface's deviceStateLine:
       is_on proxies the cover position; devices without an on/off concept
       render "—". */
    private static string DeviceStateLine(JsonElement device)
    {
        var role = (GetString(device, "role") ?? "").ToLowerInvariant();
        if (role == "cover")
        {
            var coverState = GetString(device, "cover_state");
            if (coverState is "opening" or "closing" or "open" or "closed")
            {
                return coverState switch
                {
                    "opening" => "Opening…",
                    "closing" => "Closing…",
                    "open" => "Open",
                    _ => "Closed",
                };
            }
            return device.TryGetProperty("is_on", out var coverOn) && coverOn.ValueKind == JsonValueKind.True
                ? "Open"
                : "Closed";
        }
        if (role == "lock")
        {
            var lockState = GetString(device, "lock_state");
            return lockState is "locked" or "unlocked"
                ? (lockState == "locked" ? "Locked" : "Unlocked")
                : "—";
        }
        if (role == "thermostat")
        {
            double? current = GetDouble(device, "current_temperature");
            double? target = GetDouble(device, "target_temperature");
            var mode = GetString(device, "climate_mode");
            if (current is null)
            {
                return mode ?? "—";
            }
            var line = $"{current}°";
            if (target is not null)
            {
                line += $" → {target}°";
            }
            return mode is not null ? $"{line} · {mode}" : line;
        }
        if (role == "camera")
        {
            if (device.TryGetProperty("camera_available", out var available) && available.ValueKind == JsonValueKind.False)
            {
                return "Unavailable";
            }
            if (device.TryGetProperty("motion_detected", out var motion) && motion.ValueKind == JsonValueKind.True)
            {
                return "Motion";
            }
            if (available.ValueKind == JsonValueKind.True)
            {
                return "Idle";
            }
            return "—";
        }
        if (role == "light")
        {
            if (device.TryGetProperty("is_on", out var isOn) && isOn.ValueKind == JsonValueKind.False)
            {
                return "Off";
            }
            var brightness = GetDouble(device, "brightness_pct");
            return brightness is not null ? $"On · {brightness}%" : "On";
        }
        if (device.TryGetProperty("is_on", out var on))
        {
            if (on.ValueKind == JsonValueKind.True)
            {
                return "On";
            }
            if (on.ValueKind == JsonValueKind.False)
            {
                return "Off";
            }
        }
        return "—";
    }

    private static string DeviceProvenanceText(JsonElement device)
    {
        var parts = new List<string>();
        var observedAt = GetString(device, "observed_at");
        if (!string.IsNullOrWhiteSpace(observedAt))
        {
            parts.Add("observed " + (FormatObservedAgo(observedAt) ?? observedAt));
        }
        var status = GetString(device, "status");
        if (status is not null)
        {
            parts.Add(status);
        }
        var changedBy = GetString(device, "changed_by");
        if (changedBy is not null)
        {
            parts.Add("by " + changedBy);
        }
        var confidence = GetDouble(device, "confidence");
        if (confidence is not null)
        {
            parts.Add("confidence " + confidence);
        }
        var source = GetString(device, "source");
        if (source is not null)
        {
            parts.Add(source);
        }
        return string.Join(" · ", parts);
    }

    private static string? FormatObservedAgo(string observedAt)
    {
        if (!DateTimeOffset.TryParse(observedAt, out var observed))
        {
            return null;
        }
        var ago = DateTimeOffset.UtcNow - observed.ToUniversalTime();
        if (ago < TimeSpan.Zero)
        {
            return null;
        }
        if (ago.TotalMinutes < 1)
        {
            return "just now";
        }
        if (ago.TotalHours < 1)
        {
            return $"{(int)ago.TotalMinutes} min ago";
        }
        if (ago.TotalDays < 1)
        {
            return $"{(int)ago.TotalHours} h ago";
        }
        return $"{(int)ago.TotalDays} d ago";
    }

    private static IEnumerable<JsonElement> Enumerate(JsonElement array, string property = "")
    {
        var source = array;
        if (property.Length > 0)
        {
            if (array.ValueKind != JsonValueKind.Object || !array.TryGetProperty(property, out var nested))
            {
                yield break;
            }
            source = nested;
        }
        if (source.ValueKind != JsonValueKind.Array)
        {
            yield break;
        }
        foreach (var item in source.EnumerateArray())
        {
            yield return item;
        }
    }

    private static IReadOnlyList<string> GetPeople(JsonElement room) =>
        Enumerate(room, "people")
            .Select(person => person.ValueKind == JsonValueKind.String ? person.GetString() ?? "" : "")
            .Where(name => name.Length > 0)
            .ToList();

    private static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    /* Sentence case per spec 05: "Proposed", never "PROPOSED". */
    private static string Sentence(string value) =>
        value.Length == 0 ? value : char.ToUpperInvariant(value[0]) + value[1..];

    private static string? GetString(JsonElement element, string property, string nested) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
            ? GetString(value, nested)
            : null;

    private static long GetInt(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetInt64()
            : 0;

    private static double? GetDouble(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : null;
}
