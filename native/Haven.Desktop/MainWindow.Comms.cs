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

    private readonly Dictionary<string, JsonElement> _emailMessagesById = new();
    private bool _emailCanMutate;

    private async void OnCommsRefreshClicked(object sender, RoutedEventArgs args)
    {
        CommsStatusText.Text = "Refreshing…";
        await LoadCommsTabAsync();
    }

    private void OnCommsTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            _commsTab = tab;
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>().Where(item => item.Tag is string))
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
        CommsEmailPanel.Visibility = _commsTab == "email" ? Visibility.Visible : Visibility.Collapsed;
        CommsCalendarPanel.Visibility = _commsTab == "calendar" ? Visibility.Visible : Visibility.Collapsed;
        CommsTabContent.Visibility = _commsTab is "email" or "calendar" ? Visibility.Collapsed : Visibility.Visible;
        CommsErrorText.Text = "";
        if (_commsTab == "email")
        {
            await LoadEmailAsync();
            CommsStatusText.Text = "";
            return;
        }
        if (_commsTab == "calendar")
        {
            await LoadCalendarAsync();
            CommsStatusText.Text = "";
            return;
        }
        try
        {
            CommsTabContent.Children.Clear();
            if (_commsTab == "messages")
            {
                var messageState = new StackPanel { Spacing = 8 };
                messageState.Children.Add(new TextBlock
                {
                    Text = "No conversation provider is available in this build. HAVEN is not reading Slack, Teams, or other message threads. Email and calendar sources can be connected directly from their tabs.",
                    TextWrapping = TextWrapping.Wrap,
                    Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                });
                CommsTabContent.Children.Add(WrapCard(messageState));
            }
            else
            {
                // Grouped by browser (spec 29's "window/session grouping"):
                // the real grouping dimension the data actually carries.
                // BrowserTabSnapshot has no window/session id at all, so
                // inventing "Window 1"/"Window 2" labels would be a false
                // capability - each connected browser instance is the
                // honest analog to a session in this single-machine context.
                var tabs = await _client.GetBrowserTabsAsync();
                var status = tabs.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.Object ? s : default;
                var connected = status.ValueKind == JsonValueKind.Object
                    ? Enumerate(status, "connected_browsers").Count()
                    : 0;
                if (connected > 0)
                {
                    CommsTabContent.Children.Add(new TextBlock
                    {
                        Text = $"{connected} browser(s) connected. Tab identity, title, and URL only — no page content, no incognito.",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                    });
                }
                else
                {
                    var browserState = new StackPanel { Spacing = 8 };
                    browserState.Children.Add(new TextBlock
                    {
                        Text = "No browser connector is available in this build. HAVEN will show tab identity, title, and URL here when a supported connector is added; it never reads page content or incognito tabs.",
                        Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                    });
                    CommsTabContent.Children.Add(WrapCard(browserState));
                }
                var byBrowser = Enumerate(tabs, "tabs")
                    .GroupBy(tab => GetString(tab, "browser") ?? "Unknown browser")
                    .OrderBy(g => g.Key);
                var attachProjects = new List<JsonElement>();
                try
                {
                    attachProjects = Enumerate(await _client.GetProjectsAsync(), "projects").ToList();
                }
                catch (Exception)
                {
                    // Attach-to-project is a convenience on top of the tab
                    // list; a failed projects fetch shouldn't blank tabs
                    // that already loaded fine.
                }
                foreach (var group in byBrowser)
                {
                    CommsTabContent.Children.Add(new TextBlock
                    {
                        Text = $"{group.Key} · {group.Count()} tab(s)",
                        Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
                    });
                    foreach (var tab in group)
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
                            Text = GetString(tab, "domain") ?? "",
                            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                        });
                        if (BuildAttachToProjectRow(resourceId, attachProjects) is { } attachRow)
                        {
                            body.Children.Add(attachRow);
                        }
                        card.Children.Add(body);
                        var focus = new Button { Content = "Focus", VerticalAlignment = VerticalAlignment.Center };
                        focus.Click += async (_, _) =>
                        {
                            var justification = await PromptForJustificationAsync(
                                "Focus this browser tab?",
                                "HAVEN will ask the connected browser to bring this tab forward.",
                                "Why should this tab be focused?",
                                "Focus");
                            if (justification is not null)
                            {
                                await RunCommsMutationAsync(() => _client!.FocusBrowserTabAsync(resourceId, justification));
                            }
                        };
                        Grid.SetColumn(focus, 1);
                        card.Children.Add(focus);
                        var wrap = new StackPanel();
                        wrap.Children.Add(card);
                        CommsTabContent.Children.Add(WrapCard(wrap));
                    }
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

    // -- email: list + detail (spec 29) ------------------------------------------

    private async Task LoadEmailAsync(bool includeBody = false)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var status = await _client.GetEmailStatusAsync();
            var configured = status.TryGetProperty("configured", out var c) && c.GetBoolean();
            var capabilities = status.TryGetProperty("capabilities", out var capabilityValue)
                && capabilityValue.ValueKind == JsonValueKind.Object
                ? capabilityValue
                : default;
            var canSend = capabilities.ValueKind == JsonValueKind.Object
                && capabilities.TryGetProperty("send", out var send)
                && send.ValueKind == JsonValueKind.True;
            var bodyIndexingAvailable = status.TryGetProperty("full_body_indexing", out var bodyIndexing)
                && bodyIndexing.ValueKind == JsonValueKind.Object
                && bodyIndexing.TryGetProperty("available", out var bodyAvailable)
                && bodyAvailable.ValueKind == JsonValueKind.True;
            _emailCanMutate = capabilities.ValueKind == JsonValueKind.Object
                && capabilities.TryGetProperty("mutate", out var mutate)
                && mutate.ValueKind == JsonValueKind.True;
            EmailConnectionText.Text = configured
                ? $"Connected: {GetString(status, "provider")} ({(canSend ? "read + send" : "read-only")}{(_emailCanMutate ? " + delete" : "")})"
                : $"Not connected: {GetString(status, "detail") ?? "no email provider configured"}";
            EmailFolderButton.Content = configured ? "Change mail folder…" : "Connect a mail folder…";
            EmailFullBodyButton.Visibility = configured && bodyIndexingAvailable
                ? Visibility.Visible
                : Visibility.Collapsed;

            EmailMessages.Items.Clear();
            _emailMessagesById.Clear();
            RenderEmailDetail(null);
            if (!configured)
            {
                CommsEmailGrid.Visibility = Visibility.Collapsed;
                return;
            }
            CommsEmailGrid.Visibility = Visibility.Visible;
            var messages = Enumerate(await _client.GetEmailMessagesAsync(includeBody), "messages").ToList();
            foreach (var message in messages)
            {
                var messageId = GetString(message, "message_id") ?? "";
                _emailMessagesById[messageId] = message;
                var row = new StackPanel { Spacing = 2 };
                var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                head.Children.Add(new TextBlock
                {
                    Text = GetString(message, "subject") ?? "(no subject)",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    TextWrapping = TextWrapping.Wrap,
                });
                head.Children.Add(new TextBlock
                {
                    Text = GetString(message, "at") ?? "",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                });
                row.Children.Add(head);
                row.Children.Add(new TextBlock { Text = GetString(message, "sender") ?? "", Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] });
                EmailMessages.Items.Add(new ListViewItem { Tag = messageId, Content = row });
            }
            if (messages.Count == 0)
            {
                EmailMessages.Items.Add(new TextBlock { Text = "No messages in the configured folder.", Opacity = 0.72 });
            }
        }
        catch (Exception ex)
        {
            CommsErrorText.Text = $"Could not load email: {ex.Message} Use Refresh to retry.";
        }
    }

    private void OnEmailMessageSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        var messageId = EmailMessages.SelectedItem is ListViewItem item ? item.Tag as string : null;
        RenderEmailDetail(messageId is not null && _emailMessagesById.TryGetValue(messageId, out var message) ? message : null);
    }

    private void RenderEmailDetail(JsonElement? message)
    {
        EmailDetail.Children.Clear();
        if (message is null)
        {
            EmailDetail.Children.Add(new TextBlock { Text = "Select a message to read it.", TextWrapping = TextWrapping.Wrap });
            return;
        }
        var m = message.Value;
        EmailDetail.Children.Add(new TextBlock
        {
            Text = GetString(m, "subject") ?? "(no subject)",
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        var recipients = Enumerate(m, "recipients").Select(r => r.GetString()).Where(r => r is not null).ToList();
        EmailDetail.Children.Add(new TextBlock
        {
            Text = $"From {GetString(m, "sender")} · {GetString(m, "at")}"
                + (recipients.Count > 0 ? $"\nTo {string.Join(", ", recipients)}" : ""),
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        var labels = Enumerate(m, "labels").Select(l => l.GetString()).Where(l => l is { Length: > 0 }).ToList();
        if (labels.Count > 0)
        {
            var chips = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            foreach (var label in labels)
            {
                chips.Children.Add(MakeChip(label!, "HavenAccentTintBrush", "HavenAccentBrush"));
            }
            EmailDetail.Children.Add(chips);
        }
        var hasIndexedBody = m.TryGetProperty("body", out var bodyValue)
            && bodyValue.ValueKind == JsonValueKind.String;
        EmailDetail.Children.Add(new TextBlock
        {
            Text = hasIndexedBody ? bodyValue.GetString() ?? "" : GetString(m, "snippet") ?? "",
            Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        if (hasIndexedBody)
        {
            EmailDetail.Children.Add(new TextBlock
            {
                Text = "Full body indexed by explicit request in the personal scope.",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                TextWrapping = TextWrapping.Wrap,
            });
        }
        if (_emailCanMutate)
        {
            var messageId = GetString(m, "message_id") ?? "";
            var delete = new Button
            {
                Content = "Delete from mailbox…",
                HorizontalAlignment = HorizontalAlignment.Left,
                Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            };
            delete.Click += async (_, _) => await DeleteEmailMessageAsync(messageId, GetString(m, "subject") ?? "(no subject)");
            EmailDetail.Children.Add(delete);
        }
    }

    private async Task DeleteEmailMessageAsync(string messageId, string subject)
    {
        if (_client is null || string.IsNullOrWhiteSpace(messageId))
        {
            return;
        }
        var justification = await PromptForJustificationAsync(
            "Delete this email?",
            $"HAVEN will request deletion of \"{subject}\" from the connected mailbox. The local read-only maildir path never exposes this action.",
            "Why should this email be deleted?",
            "Request delete");
        if (justification is null)
        {
            return;
        }
        try
        {
            var pending = await _client.DeleteEmailMessageAsync(messageId, justification);
            if (pending.ValueKind == JsonValueKind.Object
                && pending.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                CommsErrorText.Text = GetString(pending, "error") ?? "HAVEN Core refused the email deletion.";
                return;
            }
            if (GetString(pending, "status") != "confirmation_required")
            {
                CommsStatusText.Text = "Done.";
                await LoadCommsTabAsync();
                return;
            }
            var requestId = GetString(pending, "request_id");
            if (string.IsNullOrWhiteSpace(requestId))
            {
                CommsErrorText.Text = "HAVEN returned a confirmation request without an id.";
                return;
            }
            var confirm = new ContentDialog
            {
                Title = "Confirm email deletion",
                Content = $"Delete \"{subject}\" from the connected mailbox? This cannot be undone by HAVEN.",
                PrimaryButtonText = "Delete",
                CloseButtonText = "Cancel",
                XamlRoot = RootGrid().XamlRoot,
            };
            if (await confirm.ShowAsync() == ContentDialogResult.Primary)
            {
                var result = await _client.ConfirmEmailMessageAsync(requestId);
                if (result.ValueKind == JsonValueKind.Object
                    && result.TryGetProperty("ok", out var confirmedOk)
                    && !confirmedOk.GetBoolean())
                {
                    CommsErrorText.Text = GetString(result, "error") ?? "HAVEN Core refused the confirmation.";
                    return;
                }
                CommsStatusText.Text = "Email deleted and verified.";
                await LoadCommsTabAsync();
            }
            else
            {
                await _client.DenyEmailMessageAsync(requestId);
                CommsStatusText.Text = "Email deletion cancelled.";
            }
        }
        catch (Exception ex)
        {
            CommsErrorText.Text = ex.Message;
        }
    }

    private async void OnEmailFolderClicked(object sender, RoutedEventArgs args)
    {
        await PickMaildirAsync();
    }

    private async void OnEmailFullBodyClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Index full email bodies?",
            Content = "This explicitly requests full plain-text bodies from the connected personal mailbox and adds them to HAVEN's local search index. Attachments and other MIME parts are not indexed.",
            PrimaryButtonText = "Index full bodies",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            CommsStatusText.Text = "Indexing full bodies…";
            var result = await _client.GetEmailMessagesAsync(includeBody: true);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                CommsErrorText.Text = GetString(result, "error") ?? "HAVEN Core refused full-body indexing.";
                return;
            }
            await LoadEmailAsync(includeBody: true);
            CommsStatusText.Text = "Full email bodies indexed for this view.";
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

    // -- calendar: week canvas + inspector (spec 29) -----------------------------

    private List<JsonElement> _calendarEvents = new();
    private DateOnly _calendarWeekStart = StartOfWeek(DateOnly.FromDateTime(DateTime.Today));
    private JsonElement? _calendarSelectedEvent;

    private static DateOnly StartOfWeek(DateOnly date)
    {
        var offset = ((int)date.DayOfWeek + 6) % 7; // Monday = 0
        return date.AddDays(-offset);
    }

    private async void OnAddIcsClicked(object sender, RoutedEventArgs args)
    {
        await PickIcsSourceAsync();
    }

    private async void OnRemoteCalendarClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var url = new TextBox { PlaceholderText = "https://calendar.example.org/private.ics", MinWidth = 360 };
        var authMode = new ComboBox { MinWidth = 180, ItemsSource = new[] { "Bearer token", "Basic username + password" }, SelectedIndex = 0 };
        var username = new TextBox { PlaceholderText = "Username (only for Basic)", MinWidth = 360 };
        var secret = new PasswordBox { PlaceholderText = "Token or password", MinWidth = 360 };
        var fields = new StackPanel { Spacing = 8 };
        fields.Children.Add(new TextBlock { Text = "Remote iCalendar URL" });
        fields.Children.Add(url);
        fields.Children.Add(new TextBlock { Text = "Authentication" });
        fields.Children.Add(authMode);
        fields.Children.Add(username);
        fields.Children.Add(secret);
        fields.Children.Add(new TextBlock
        {
            Text = "HAVEN stores the credential in protected storage. Remote calendars are observed only; writes are not sent to the provider.",
            TextWrapping = TextWrapping.Wrap,
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        var dialog = new ContentDialog
        {
            Title = "Connect remote iCalendar",
            Content = fields,
            PrimaryButtonText = "Connect",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary
            || string.IsNullOrWhiteSpace(url.Text)
            || string.IsNullOrWhiteSpace(secret.Password))
        {
            return;
        }
        var mode = authMode.SelectedIndex == 1 ? "basic" : "bearer";
        await RunCommsMutationAsync(() => _client.ConfigureRemoteCalendarAsync(
            url.Text.Trim(), secret.Password, username.Text.Trim(), mode));
    }

    private void OnCalendarPrevWeekClicked(object sender, RoutedEventArgs args)
    {
        _calendarWeekStart = _calendarWeekStart.AddDays(-7);
        RenderCalendarWeek();
    }

    private void OnCalendarNextWeekClicked(object sender, RoutedEventArgs args)
    {
        _calendarWeekStart = _calendarWeekStart.AddDays(7);
        RenderCalendarWeek();
    }

    private void OnCalendarTodayClicked(object sender, RoutedEventArgs args)
    {
        _calendarWeekStart = StartOfWeek(DateOnly.FromDateTime(DateTime.Today));
        RenderCalendarWeek();
    }

    private async Task LoadCalendarAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetCalendarEventsAsync();
            _calendarEvents = Enumerate(result, "events").ToList();
            var status = result.TryGetProperty("status", out var sourceStatus)
                && sourceStatus.ValueKind == JsonValueKind.Object
                ? sourceStatus
                : default;
            var sourceErrors = status.ValueKind == JsonValueKind.Object
                ? Enumerate(status, "errors").Select(item => item.GetString()).OfType<string>().Where(item => !string.IsNullOrWhiteSpace(item)).ToList()
                : new List<string>();
            CalendarSourceStatusText.Text = sourceErrors.Count > 0
                ? $"Calendar source unavailable: {string.Join("; ", sourceErrors)} No remote events were used for automation."
                : status.ValueKind == JsonValueKind.Object && GetString(status, "detail") is { Length: > 0 } detail
                    ? detail
                    : "Calendar observations are read-only for remote sources; local .ics files remain writable through governed confirmation.";
            RenderCalendarEventDetail(null);
            RenderCalendarWeek();
        }
        catch (Exception ex)
        {
            CommsErrorText.Text = $"Could not load calendar: {ex.Message} Use Refresh to retry.";
        }
    }

    /// <summary>Day columns, not a proportional hour-grid - each column lists
    /// that day's events in start-time order rather than positioning blocks
    /// by exact time-of-day, which would need pixel-precise layout math this
    /// pass can't verify visually anyway (WinUI compositing on the dev
    /// machine is intermittent).</summary>
    private void RenderCalendarWeek()
    {
        var weekEnd = _calendarWeekStart.AddDays(6);
        CalendarWeekRangeText.Text = _calendarWeekStart.Month == weekEnd.Month
            ? $"{_calendarWeekStart:MMM d}–{weekEnd:d}, {weekEnd:yyyy}"
            : $"{_calendarWeekStart:MMM d} – {weekEnd:MMM d, yyyy}";

        CalendarWeekGrid.Children.Clear();
        var byDay = _calendarEvents
            .Where(e => DateTimeOffset.TryParse(GetString(e, "start_at"), out _))
            .GroupBy(e => DateOnly.FromDateTime(DateTimeOffset.Parse(GetString(e, "start_at")!).LocalDateTime));

        for (var i = 0; i < 7; i++)
        {
            var day = _calendarWeekStart.AddDays(i);
            var column = new StackPanel { Spacing = 6, MinWidth = 148, MaxWidth = 148 };
            var isToday = day == DateOnly.FromDateTime(DateTime.Today);
            column.Children.Add(new TextBlock
            {
                Text = $"{day:ddd} {day.Day}",
                FontWeight = isToday ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[isToday ? "HavenAccentBrush" : "HavenMutedTextBrush"],
            });
            var dayEvents = byDay.FirstOrDefault(g => g.Key == day)?.OrderBy(e => GetString(e, "start_at")).ToList()
                ?? new List<JsonElement>();
            foreach (var e in dayEvents)
            {
                var eventId = GetString(e, "event_id") ?? "";
                var start = DateTimeOffset.TryParse(GetString(e, "start_at"), out var s) ? s.LocalDateTime.ToString("h:mm tt") : "";
                var chip = new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    Padding = new Microsoft.UI.Xaml.Thickness(8, 6, 8, 6),
                    BorderBrush = eventId == GetString(_calendarSelectedEvent, "event_id")
                        ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenAccentBrush"]
                        : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
                    Child = new StackPanel
                    {
                        Spacing = 1,
                        Children =
                        {
                            new TextBlock { Text = start, Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"] },
                            new TextBlock { Text = GetString(e, "title") ?? eventId, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap },
                        },
                    },
                };
                var capturedEvent = e;
                chip.Tapped += (_, _) =>
                {
                    _calendarSelectedEvent = capturedEvent;
                    RenderCalendarWeek();
                    RenderCalendarEventDetail(capturedEvent);
                };
                column.Children.Add(chip);
            }
            CalendarWeekGrid.Children.Add(column);
        }
    }

    private string? GetString(JsonElement? element, string property) =>
        element is { } e ? GetString(e, property) : null;

    private async void RenderCalendarEventDetail(JsonElement? evt)
    {
        CalendarEventDetail.Children.Clear();
        if (evt is null)
        {
            CalendarEventDetail.Children.Add(new TextBlock { Text = "Select an event to see details.", TextWrapping = TextWrapping.Wrap });
            return;
        }
        var e = evt.Value;
        var eventId = GetString(e, "event_id") ?? "";
        var resourceId = GetString(e, "resource_id") ?? "";

        CalendarEventDetail.Children.Add(new TextBlock
        {
            Text = GetString(e, "title") ?? eventId,
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });
        var when = new List<string>();
        if (DateTimeOffset.TryParse(GetString(e, "start_at"), out var start))
        {
            when.Add(start.LocalDateTime.ToString("dddd, MMM d · h:mm tt"));
        }
        if (DateTimeOffset.TryParse(GetString(e, "end_at"), out var end))
        {
            when.Add($"until {end.LocalDateTime:h:mm tt}");
        }
        if (GetString(e, "location") is { Length: > 0 } location)
        {
            when.Add(location);
        }
        CalendarEventDetail.Children.Add(new TextBlock { Text = string.Join(" · ", when), TextWrapping = TextWrapping.Wrap, Opacity = 0.85 });

        var attendees = Enumerate(e, "attendees").Select(a => a.GetString()).Where(a => a is { Length: > 0 }).ToList();
        if (attendees.Count > 0)
        {
            CalendarEventDetail.Children.Add(new TextBlock { Text = $"Attendees: {string.Join(", ", attendees)}", TextWrapping = TextWrapping.Wrap, Opacity = 0.85 });
        }

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var propose = new Button { Content = "Propose task", Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"] };
        propose.Click += async (_, _) => await RunCommsMutationAsync(() => _client!.ProposeTaskForEventAsync(eventId));
        actions.Children.Add(propose);
        CalendarEventDetail.Children.Add(actions);

        if (await BuildAttachToProjectRowAsync(resourceId) is { } attachRow)
        {
            CalendarEventDetail.Children.Add(attachRow);
        }
    }

    /// <summary>Single-item convenience: fetches projects once, then builds
    /// the row. Fine for a one-off (Calendar's selected event) but never use
    /// this inside a loop over many rows - it would refetch per row.</summary>
    private async Task<FrameworkElement?> BuildAttachToProjectRowAsync(string resourceId)
    {
        if (_client is null)
        {
            return null;
        }
        try
        {
            var projects = Enumerate(await _client.GetProjectsAsync(), "projects").ToList();
            return BuildAttachToProjectRow(resourceId, projects);
        }
        catch (Exception)
        {
            // A convenience on top of whatever detail view called this; a
            // failed projects fetch shouldn't blank details the caller
            // already rendered.
            return null;
        }
    }

    /// <summary>"Project relation" (spec 29): a forward-only attach control
    /// (not a reverse lookup of existing attachments, which the client has
    /// no cheap way to query) reusing the generic projects.attach seam with
    /// the caller's own resource_id - shared by Calendar's event inspector
    /// and Browser tabs' rows rather than duplicated per surface. Takes an
    /// already-fetched project list so a row-per-item list (Browser tabs)
    /// fetches once, not once per row.</summary>
    private FrameworkElement? BuildAttachToProjectRow(string resourceId, List<JsonElement> projects)
    {
        if (_client is null || projects.Count == 0)
        {
            return null;
        }
        var picker = new ComboBox
        {
            ItemsSource = projects.Select(p => GetString(p, "title") ?? GetString(p, "project_id")).ToList(),
            MinWidth = 180,
        };
        picker.SelectedIndex = 0;
        var attach = new Button { Content = "Attach to project", Style = (Style)Application.Current.Resources["HavenSecondaryButtonStyle"] };
        attach.Click += async (_, _) =>
        {
            var projectId = GetString(projects[picker.SelectedIndex], "project_id") ?? "";
            await RunCommsMutationAsync(() => _client!.AttachProjectResourceAsync(projectId, resourceId));
        };
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(picker);
        row.Children.Add(attach);
        return row;
    }

}
