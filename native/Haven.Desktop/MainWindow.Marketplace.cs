using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    private JsonElement _marketplaceResult = default;

    private async Task LoadMarketplaceAsync(bool refresh = false)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            _marketplaceResult = refresh
                ? await _client.RefreshMarketplaceAsync()
                : await _client.GetMarketplaceAsync();
            _localProducts = await _client.LocalWorkflowAsync("marketplace.products", new { });
            RenderMarketplace();
        }
        catch (Exception ex)
        {
            MarketplaceErrorText.Text = $"Could not load Marketplace: {NativeFaults.Message(ex)}";
            MarketplaceList.Children.Clear();
        }
    }

    private async void OnMarketplaceRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadMarketplaceAsync(refresh: true);
    }

    private void OnMarketplaceSearchChanged(object sender, TextChangedEventArgs args)
    {
        RenderMarketplace();
    }

    private void OnMarketplaceFilterChanged(object sender, SelectionChangedEventArgs args)
    {
        RenderMarketplace();
    }

    private void RenderMarketplace()
    {
        // XAML can raise the default selection event while later controls
        // are still being constructed. The normal page load renders once
        // InitializeComponent has completed.
        if (!_viewsInitialized) return;
        MarketplaceList.Children.Clear();
        MarketplaceErrorText.Text = "";
        RenderLocalProducts();
        if (_marketplaceResult.ValueKind != JsonValueKind.Object
            || !_marketplaceResult.TryGetProperty("items", out var items)
            || items.ValueKind != JsonValueKind.Array)
        {
            MarketplaceNoticeText.Text = "Marketplace discovery is unavailable until HAVEN Core reconnects.";
            MarketplaceList.Children.Add(new TextBlock
            {
                Text = "No product catalog was returned.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }

        var source = GetString(_marketplaceResult, "source") ?? "none";
        var stale = _marketplaceResult.TryGetProperty("stale", out var staleValue)
            && staleValue.ValueKind == JsonValueKind.True;
        var verified = _marketplaceResult.TryGetProperty("signature_verified", out var verifiedValue)
            && verifiedValue.ValueKind == JsonValueKind.True;
        MarketplaceNoticeText.Text = source == "bundled_preview"
            ? "Draft catalog preview. Available local releases can be managed above."
            : verified
                ? "Signed catalog verified. Marketplace discovery is separate from installation and HAVEN authority."
                : "Catalog metadata is not verified. These catalog entries cannot be installed.";
        if (stale)
        {
            MarketplaceNoticeText.Text += " The last known catalog is stale; refresh when a trusted catalog is available.";
        }
        if (GetString(_marketplaceResult, "catalog_error") is { Length: > 0 } error)
        {
            MarketplaceErrorText.Text = $"Catalog status: {error}. Showing the last known catalog when available.";
        }

        var query = MarketplaceSearchBox.Text?.Trim() ?? "";
        var state = MarketplaceStateFilter.SelectedItem is ComboBoxItem selected
            ? selected.Tag?.ToString() ?? ""
            : "";
        var visible = Enumerate(items)
            .Where(item =>
            {
                var id = GetString(item, "product_id") ?? "";
                var name = GetString(item, "display_name") ?? "";
                var summary = GetString(item, "summary") ?? "";
                var releaseState = GetString(item, "release_state") ?? "";
                var matchesQuery = string.IsNullOrWhiteSpace(query)
                    || id.Contains(query, StringComparison.OrdinalIgnoreCase)
                    || name.Contains(query, StringComparison.OrdinalIgnoreCase)
                    || summary.Contains(query, StringComparison.OrdinalIgnoreCase);
                return matchesQuery && (string.IsNullOrEmpty(state) || releaseState == state);
            })
            .ToList();

        foreach (var item in visible)
        {
            MarketplaceList.Children.Add(MakeMarketplaceCard(item));
        }
        if (visible.Count == 0)
        {
            MarketplaceList.Children.Add(new TextBlock
            {
                Text = "No products match this view.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeMarketplaceCard(JsonElement item)
    {
        var state = GetString(item, "release_state") ?? "unknown";
        var stateColors = state switch
        {
            "available" => (Tint: "HavenSuccessTintBrush", Foreground: "HavenSuccessBrush"),
            "coming_soon" => (Tint: "HavenWarningTintBrush", Foreground: "HavenWarningBrush"),
            "deprecated" => (Tint: "HavenWarningTintBrush", Foreground: "HavenWarningBrush"),
            "security_hold" => (Tint: "HavenDangerTintBrush", Foreground: "HavenDangerBrush"),
            _ => (Tint: "HavenAccentTintBrush", Foreground: "HavenAccentBrush"),
        };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(item, "display_name") ?? GetString(item, "product_id") ?? "Unknown product",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        head.Children.Add(MakeChip(Sentence(state.Replace('_', ' ')), stateColors.Tint, stateColors.Foreground));
        var kind = GetString(item, "kind");
        if (kind is { Length: > 0 })
        {
            head.Children.Add(MakeChip(Sentence(kind.Replace('_', ' ')), "HavenStrokeBrush", "HavenMutedTextBrush"));
        }
        var integration = item.TryGetProperty("haven_integration", out var integrationValue)
            && integrationValue.ValueKind == JsonValueKind.Object
            && integrationValue.TryGetProperty("available", out var available)
            && available.ValueKind == JsonValueKind.True;
        if (integration)
        {
            head.Children.Add(MakeStatusPill("Optional HAVEN integration", true));
        }

        var content = new StackPanel { Spacing = 6 };
        content.Children.Add(head);
        var summary = GetString(item, "summary");
        content.Children.Add(new TextBlock
        {
            Text = summary ?? "No public summary has been approved for this draft entry.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.82,
        });
        var categories = Enumerate(item, "categories")
            .Where(value => value.ValueKind == JsonValueKind.String)
            .Select(value => Sentence(value.GetString()!.Replace('_', ' ')));
        var metadata = new List<string> { string.Join(" · ", categories) };
        var publisher = GetString(item, "publisher");
        if (publisher is { Length: > 0 }) metadata.Add(publisher);
        var execution = GetString(item, "execution");
        if (execution is { Length: > 0 }) metadata.Add($"Runs {Sentence(execution)}");
        content.Children.Add(new TextBlock
        {
            Text = string.Join(" · ", metadata.Where(value => value.Length > 0)),
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        content.Children.Add(new TextBlock
        {
            Text = "Catalog entry. Local releases have separate installation and launch controls.",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.78,
        });
        return WrapCard(content);
    }
}
