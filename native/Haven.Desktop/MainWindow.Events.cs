using System.Text.Json;
using System.Text;
using Haven.Desktop.Setup;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
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

    // -- search: result list + inspector (spec 22) ------------------------------

    private string[] _searchTypeFilter = Array.Empty<string>();
    private readonly Dictionary<string, JsonElement> _searchHitsById = new();

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

    private async void OnSearchTypeFilterClicked(object sender, RoutedEventArgs args)
    {
        if (sender is not Button button || button.Tag is not string tag)
        {
            return;
        }
        _searchTypeFilter = string.IsNullOrEmpty(tag) ? Array.Empty<string>() : tag.Split(',');
        foreach (var item in SearchTypeFilters.Children.OfType<Button>())
        {
            item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
        if (!string.IsNullOrWhiteSpace(SearchBox.Text))
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
        SearchStatusText.Text = "Searching…";
        try
        {
            var result = await _client.SearchAsync(SearchBox.Text.Trim(), _searchTypeFilter);
            SearchResults.Items.Clear();
            _searchHitsById.Clear();
            var hits = Enumerate(result, "hits").ToList();
            foreach (var hit in hits)
            {
                var resourceId = GetString(hit, "resource_id") ?? "";
                _searchHitsById[resourceId] = hit;
                var resource = hit.TryGetProperty("resource", out var r) && r.ValueKind == JsonValueKind.Object ? r : (JsonElement?)null;
                var title = (resource is { } res ? GetString(res, "title") : null) ?? resourceId;

                var row = new StackPanel { Spacing = 2 };
                row.Children.Add(new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
                var meta = new List<string>();
                if (resource is { } res2 && GetString(res2, "resource_type") is { } type)
                {
                    meta.Add(Sentence(type));
                }
                meta.Add(GetString(hit, "reason") ?? "");
                row.Children.Add(new TextBlock { Text = string.Join(" · ", meta), Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
                SearchResults.Items.Add(new ListViewItem { Tag = resourceId, Content = row });
            }
            SearchStatusText.Text = hits.Count == 0 ? "No results." : $"{hits.Count} result(s).";
            RenderSearchDetail(null);
        }
        catch (Exception ex)
        {
            SearchResults.Items.Clear();
            SearchStatusText.Text = $"Could not search: {ex.Message}";
        }
    }

    private void OnSearchResultSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        var resourceId = SearchResults.SelectedItem is ListViewItem item ? item.Tag as string : null;
        RenderSearchDetail(resourceId is not null && _searchHitsById.TryGetValue(resourceId, out var hit) ? hit : null);
    }

    private void RenderSearchDetail(JsonElement? hit)
    {
        SearchDetail.Children.Clear();
        if (hit is null)
        {
            SearchDetail.Children.Add(new TextBlock { Text = "Search, then select a result to see why it matched.", TextWrapping = TextWrapping.Wrap });
            return;
        }
        var h = hit.Value;
        var resource = h.TryGetProperty("resource", out var r) && r.ValueKind == JsonValueKind.Object ? r : (JsonElement?)null;
        var resourceId = GetString(h, "resource_id") ?? "";
        var title = (resource is { } res ? GetString(res, "title") : null) ?? resourceId;

        SearchDetail.Children.Add(new TextBlock { Text = title, Style = (Style)Application.Current.Resources["HavenSectionTextStyle"], TextWrapping = TextWrapping.Wrap });

        SearchDetail.Children.Add(new TextBlock { Text = "Overview", Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
        var overview = new List<string>();
        string? resourceType = null;
        if (resource is { } res2)
        {
            resourceType = GetString(res2, "resource_type");
            if (resourceType is not null)
            {
                overview.Add(Sentence(resourceType));
            }
            if (GetString(res2, "locator") is { Length: > 0 } locator)
            {
                overview.Add(locator);
            }
            if (res2.TryGetProperty("stale", out var stale) && stale.ValueKind == JsonValueKind.True)
            {
                overview.Add("stale");
            }
        }
        SearchDetail.Children.Add(new TextBlock
        {
            Text = overview.Count > 0 ? string.Join(" · ", overview) : "No resource metadata available.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });

        SearchDetail.Children.Add(new TextBlock { Text = "Why this was found", Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
        SearchDetail.Children.Add(new TextBlock { Text = GetString(h, "reason") ?? "—", TextWrapping = TextWrapping.Wrap, Opacity = 0.85 });

        var matchedClaims = Enumerate(h, "matched_claims").ToList();
        if (matchedClaims.Count > 0)
        {
            SearchDetail.Children.Add(new TextBlock { Text = "Matched evidence", Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
            foreach (var claim in matchedClaims)
            {
                SearchDetail.Children.Add(new TextBlock
                {
                    Text = GetString(claim, "proposition") ?? "",
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.85,
                });
            }
        }

        var openTag = NavigationTagForResourceType(resourceType);
        if (openTag is not null)
        {
            var open = new Button { Content = "Open", Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"] };
            open.Click += (_, _) => SelectNavigation(openTag);
            SearchDetail.Children.Add(open);
        }
    }

    /// <summary>Search's default safe action is "go to the page that owns this
    /// resource" - not a governed filesystem open/reveal, which would need a
    /// per-type authority path this pass doesn't wire up. Null means no known
    /// destination, so no button renders (never a dead action).</summary>
    private static string? NavigationTagForResourceType(string? resourceType) => resourceType switch
    {
        "file" or "folder" or "application" or "window" => "computer",
        "browser_tab" or "calendar_event" or "email_message" => "communications",
        "project" => "projects",
        "task" => "tasks",
        _ => null,
    };

}
