using System.Text.Json;
using System.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow : Window
{
    private HavenCoreClient? _client;
    private string? _selectedClaimId;

    public MainWindow()
    {
        InitializeComponent();
        UpdateLogo();
        RootGrid().ActualThemeChanged += (_, _) => UpdateLogo();
        RootGrid().Loaded += OnLoaded;
    }

    private Grid RootGrid() => (Grid)Content;

    private void UpdateLogo()
    {
        LogoImage.Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(
            new Uri(RootGrid().ActualTheme == ElementTheme.Dark
                ? "ms-appx:///Assets/haven-logo-dark.png"
                : "ms-appx:///Assets/haven-logo-light.png"));
    }

    private async void OnLoaded(object sender, RoutedEventArgs args)
    {
        var pipeName = CommandLineValue("--pipe-name") ?? Environment.GetEnvironmentVariable("HAVEN_IPC_PIPE");
        var token = await ResolveAuthTokenAsync();
        if (string.IsNullOrWhiteSpace(pipeName) || string.IsNullOrWhiteSpace(token))
        {
            ConnectionText.Text = "Native Core not connected";
            ResponseText.Text = "Start HAVEN Core with a named-pipe endpoint to connect this native client.";
            return;
        }

        try
        {
            var client = new HavenCoreClient(pipeName, token);
            await client.ConnectAsync();
            _client = client;
            ConnectionText.Text = "Connected";
            var state = await _client.GetStateAsync();
            ResponseText.Text = state.GetRawText();
        }
        catch (Exception ex)
        {
            ConnectionText.Text = "Core unavailable";
            ResponseText.Text = ex.Message;
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
                    Text = $"{state.ToUpperInvariant()} · {confidence} · {provenance}",
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

    private async void OnComposerClicked(object sender, RoutedEventArgs args)
    {
        await AskAsync();
    }

    private async void OnComposerKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            await AskAsync();
        }
    }

    private async Task AskAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(ComposerBox.Text))
        {
            return;
        }
        try
        {
            var state = await _client.AskAsync(ComposerBox.Text.Trim());
            ResponseText.Text = state.GetRawText();
        }
        catch (Exception ex)
        {
            ResponseText.Text = ex.Message;
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

    private void OnNavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        var tag = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString();
        var search = tag == "search";
        var memory = tag == "memory";
        SearchPanel.Visibility = search ? Visibility.Visible : Visibility.Collapsed;
        MemoryPanel.Visibility = memory ? Visibility.Visible : Visibility.Collapsed;
        HomePanel.Visibility = search || memory ? Visibility.Collapsed : Visibility.Visible;
        PageTitle.Text = tag switch
        {
            "search" => "Search your life",
            "memory" => "Memory",
            "home" => "Home",
            "models" => "Models",
            "settings" => "Settings",
            _ => "HAVEN",
        };
        if (memory)
        {
            _ = LoadMemoryAsync();
        }
    }
}
