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
    // Dense dashboard grid (Native Product Pass spec 21): the once-flat
    // TodayCardsHost stack is now six named regions (Focus / Upcoming / Tasks
    // / Recent files / Pending replies / Recent activity). Independent data
    // sources fetch in parallel; each region's card collapses on empty data
    // rather than leaving a dead rectangle - no region is ever populated
    // with a fabricated placeholder ("no false capability").

    private async Task LoadTodayCardsAsync()
    {
        if (_client is null)
        {
            return;
        }
        // Independent evidence sources: one failing or coming back empty
        // never hides the others ("Today cards should degrade individually
        // based on their evidence sources rather than making the entire
        // dashboard fail" - spec 14). Fired together, awaited together.
        var cardsTask = RenderTodayCardsAsync(_client.GetTodayCardsAsync());
        var tasksTask = RenderTodayRegionAsync(TodayTasksCard, TodayTasksHost, _client.GetTasksAsync("today"), "tasks", MakeTodayTaskRow, take: 6);
        var filesTask = RenderTodayRegionAsync(TodayFilesCard, TodayFilesHost, _client.GetComputerFilesAsync(), "files", MakeTodayFileRow, take: 5);
        var activityTask = RenderTodayRegionAsync(TodayActivityCard, TodayActivityHost, _client.GetComputerActivityAsync(), "events", MakeTodayActivityRow, take: 6);

        // Pending replies: an honest gap, not a fabricated feed - HAVEN has no
        // reply-tracking evidence source yet (no comms provider surfaces
        // "awaiting your reply" state). Say so rather than showing nothing
        // unexplained or inventing data.
        TodayRepliesHost.Children.Clear();
        TodayRepliesHost.Children.Add(new TextBlock
        {
            Text = "HAVEN doesn't have a reply-tracking evidence source yet, so this can't be populated honestly.",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });

        var hasContent = await Task.WhenAll(cardsTask, tasksTask, filesTask, activityTask);
        TodayGrid.Visibility = Visibility.Visible;
        TodayEmptyCard.Visibility = hasContent.Any(x => x) ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>Fetch one region's data, render up to <paramref name="take"/> rows via
    /// <paramref name="makeRow"/>, and collapse the card entirely when the
    /// result is empty - "modules that have no data collapse rather than
    /// leave dead rectangles" (spec 21). Returns whether the region ended up
    /// with visible content, so the caller can decide whether *every* region
    /// came back empty (only then does the page-level empty state show).</summary>
    private async Task<bool> RenderTodayRegionAsync(
        FrameworkElement card,
        Panel host,
        Task<JsonElement> fetch,
        string arrayProperty,
        Func<JsonElement, FrameworkElement> makeRow,
        int take)
    {
        host.Children.Clear();
        try
        {
            var result = await fetch;
            var rows = Enumerate(result.GetProperty(arrayProperty)).Take(take).ToList();
            if (rows.Count == 0)
            {
                card.Visibility = Visibility.Collapsed;
                return false;
            }
            card.Visibility = Visibility.Visible;
            foreach (var row in rows)
            {
                host.Children.Add(makeRow(row));
            }
            return true;
        }
        catch (Exception ex)
        {
            card.Visibility = Visibility.Visible;
            host.Children.Add(new TextBlock
            {
                Text = ex.Message,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return true; // an error is shown, not silently treated as "empty".
        }
    }

    private async Task<bool> RenderTodayCardsAsync(Task<JsonElement> fetch)
    {
        TodayFocusHost.Children.Clear();
        TodayUpcomingHost.Children.Clear();
        List<JsonElement> rows;
        try
        {
            rows = Enumerate((await fetch).GetProperty("cards")).ToList();
        }
        catch (Exception ex)
        {
            TodayFocusCard.Visibility = Visibility.Visible;
            TodayUpcomingCard.Visibility = Visibility.Collapsed;
            TodayFocusHost.Children.Add(new TextBlock
            {
                Text = $"Could not load Today's cards: {ex.Message}",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return true;
        }
        if (rows.Count == 0)
        {
            TodayFocusCard.Visibility = Visibility.Collapsed;
            TodayUpcomingCard.Visibility = Visibility.Collapsed;
            return false;
        }

        var focus = rows[0];
        var rest = rows.Skip(1).ToList();
        TodayFocusCard.Visibility = Visibility.Visible;
        TodayFocusHost.Children.Add(MakeTodayCard(focus, focusStyle: true));

        // Every remaining card (deadline/authority/commitment/suggestion)
        // shares the Upcoming region - each card already self-labels its
        // kind via MakeTodayCard's inline chip, so a second grouping layer
        // of section headers would only multiply decoration (spec 06).
        TodayUpcomingCard.Visibility = rest.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        foreach (var card in rest)
        {
            TodayUpcomingHost.Children.Add(MakeTodayCard(card, focusStyle: false));
        }
        return true;
    }

    private FrameworkElement MakeTodayTaskRow(JsonElement task)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(new TextBlock
        {
            Text = GetString(task, "title") ?? "Untitled",
            TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        });
        var meta = new List<string>();
        if (GetString(task, "project_title") is { } project)
        {
            meta.Add(project);
        }
        if (FormatDue(GetString(task, "due_at")) is { } due)
        {
            meta.Add(due);
        }
        if (meta.Count > 0)
        {
            row.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", meta),
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                VerticalAlignment = VerticalAlignment.Center,
            });
        }
        return row;
    }

    private FrameworkElement MakeTodayFileRow(JsonElement file)
    {
        var row = new StackPanel { Spacing = 1 };
        row.Children.Add(new TextBlock
        {
            Text = GetString(file, "title") ?? GetString(file, "resource_id") ?? "?",
            TextWrapping = TextWrapping.Wrap,
        });
        row.Children.Add(new TextBlock
        {
            Text = GetString(file, "locator") ?? "",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextTrimming = TextTrimming.CharacterEllipsis,
        });
        return row;
    }

    private FrameworkElement MakeTodayActivityRow(JsonElement entry)
    {
        var row = new StackPanel { Spacing = 1 };
        row.Children.Add(new TextBlock
        {
            Text = GetString(entry, "title") ?? "?",
            TextWrapping = TextWrapping.Wrap,
        });
        row.Children.Add(new TextBlock
        {
            Text = $"{GetString(entry, "app")} · {GetString(entry, "at")}",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        return row;
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

}
