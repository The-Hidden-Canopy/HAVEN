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
        ComputerFilesGrid.Visibility = _computerTab == "files" ? Visibility.Visible : Visibility.Collapsed;
        ComputerTabContent.Visibility = _computerTab == "files" ? Visibility.Collapsed : Visibility.Visible;
        if (_computerTab == "files")
        {
            await LoadComputerFilesExplorerAsync();
            ComputerStatusText.Text = "";
            ComputerErrorText.Text = "";
            return;
        }
        try
        {
            ComputerTabContent.Children.Clear();
            if (_computerTab == "apps")
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

    // -- computer: Files explorer (locations + table + inspector, spec 28) ------

    private readonly Dictionary<string, JsonElement> _computerFilesById = new();
    private string? _computerLocationFilter;

    private async Task LoadComputerFilesExplorerAsync()
    {
        try
        {
            var result = await _client!.GetComputerFilesAsync();
            var files = Enumerate(result, "files").ToList();
            _computerFilesById.Clear();
            foreach (var file in files)
            {
                var id = GetString(file, "resource_id");
                if (id is not null)
                {
                    _computerFilesById[id] = file;
                }
            }
            RenderComputerLocations(files);
            RenderComputerFilesTable(files);
            RenderComputerFileDetail(null);
        }
        catch (Exception ex)
        {
            ComputerErrorText.Text = $"Could not load files: {ex.Message} Use Refresh to retry.";
        }
    }

    /// <summary>Real folders derived from the paths HAVEN has actually
    /// observed - not a browsable filesystem tree, since HAVEN has no
    /// capability to enumerate arbitrary unauthorized folders (no false
    /// capability, spec 06).</summary>
    private void RenderComputerLocations(List<JsonElement> files)
    {
        var previous = _computerLocationFilter;
        ComputerLocations.Items.Clear();
        var all = new ListViewItem { Tag = null, Content = new TextBlock { Text = $"All files ({files.Count})" } };
        ComputerLocations.Items.Add(all);
        var byFolder = files
            .Select(f => GetString(f, "locator"))
            .Where(l => l is { Length: > 0 })
            .Select(l => System.IO.Path.GetDirectoryName(l))
            .Where(dir => dir is { Length: > 0 })
            .GroupBy(dir => dir!)
            .OrderByDescending(g => g.Count())
            .ThenBy(g => g.Key);
        ListViewItem? restore = null;
        foreach (var group in byFolder)
        {
            var item = new ListViewItem
            {
                Tag = group.Key,
                Content = new TextBlock { Text = $"{group.Key} ({group.Count()})", TextTrimming = TextTrimming.CharacterEllipsis },
            };
            ComputerLocations.Items.Add(item);
            if (group.Key == previous)
            {
                restore = item;
            }
        }
        ComputerLocations.SelectedItem = restore ?? all;
    }

    private void RenderComputerFilesTable(List<JsonElement> files)
    {
        var visible = _computerLocationFilter is null
            ? files
            : files.Where(f => System.IO.Path.GetDirectoryName(GetString(f, "locator")) == _computerLocationFilter).ToList();
        ComputerFilesTable.Items.Clear();
        foreach (var file in visible)
        {
            var resourceId = GetString(file, "resource_id") ?? "";
            var row = new StackPanel { Spacing = 2 };
            row.Children.Add(new TextBlock
            {
                Text = GetString(file, "title") ?? resourceId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            var meta = new List<string> { Sentence(GetString(file, "resource_type") ?? "file") };
            if (FormatObservedAgo(GetString(file, "observed_at") ?? "") is { Length: > 0 } observed)
            {
                meta.Add(observed);
            }
            if (file.TryGetProperty("stale", out var stale) && stale.ValueKind == JsonValueKind.True)
            {
                meta.Add("stale");
            }
            row.Children.Add(new TextBlock { Text = string.Join(" · ", meta), Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
            ComputerFilesTable.Items.Add(new ListViewItem { Tag = resourceId, Content = row });
        }
        if (visible.Count == 0)
        {
            ComputerFilesTable.Items.Add(new TextBlock
            {
                Text = "No authorized files observed yet. Enable the computer provider in Setup and scan an allowed folder.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private void OnComputerLocationSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        _computerLocationFilter = ComputerLocations.SelectedItem is ListViewItem item ? item.Tag as string : null;
        RenderComputerFilesTable(_computerFilesById.Values.ToList());
    }

    private void OnComputerFileSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        var resourceId = ComputerFilesTable.SelectedItem is ListViewItem item ? item.Tag as string : null;
        RenderComputerFileDetail(resourceId is not null && _computerFilesById.TryGetValue(resourceId, out var file) ? file : null);
    }

    private void RenderComputerFileDetail(JsonElement? file)
    {
        ComputerFileDetail.Children.Clear();
        if (file is null)
        {
            ComputerFileDetail.Children.Add(new TextBlock { Text = "Select a file to see why HAVEN has it and what you can do.", TextWrapping = TextWrapping.Wrap });
            return;
        }
        var f = file.Value;
        var resourceId = GetString(f, "resource_id") ?? "";
        ComputerFileDetail.Children.Add(new TextBlock
        {
            Text = GetString(f, "title") ?? resourceId,
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        ComputerFileDetail.Children.Add(new TextBlock
        {
            Text = GetString(f, "locator") ?? "",
            Style = (Style)Application.Current.Resources["HavenMonoTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        var why = new List<string> { $"Provider: {GetString(f, "provider_id") ?? "unknown"}" };
        if (FormatObservedAgo(GetString(f, "observed_at") ?? "") is { Length: > 0 } observed)
        {
            why.Add($"Observed {observed}");
        }
        ComputerFileDetail.Children.Add(new TextBlock
        {
            Text = "Why here",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        ComputerFileDetail.Children.Add(new TextBlock { Text = string.Join(" · ", why), TextWrapping = TextWrapping.Wrap, Opacity = 0.85 });

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var open = new Button { Content = "Open", Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"] };
        open.Click += async (_, _) => await RunComputerFileActionAsync("filesystem.open", resourceId);
        var reveal = new Button { Content = "Reveal", Style = (Style)Application.Current.Resources["HavenSecondaryButtonStyle"] };
        reveal.Click += async (_, _) => await RunComputerFileActionAsync("filesystem.reveal", resourceId);
        actions.Children.Add(open);
        actions.Children.Add(reveal);
        ComputerFileDetail.Children.Add(actions);
    }

    private async Task RunComputerFileActionAsync(string action, string resourceId)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.RequestComputerActionAsync(action, resourceId);
            ComputerErrorText.Text = result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok) && !ok.GetBoolean()
                ? (result.TryGetProperty("error", out var error) ? error.GetString() : "Action refused.") ?? "Action refused."
                : "";
        }
        catch (Exception ex)
        {
            ComputerErrorText.Text = ex.Message;
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

}
