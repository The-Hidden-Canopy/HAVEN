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

}
