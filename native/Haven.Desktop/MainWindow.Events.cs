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

}
