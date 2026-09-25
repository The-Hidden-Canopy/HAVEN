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

}
