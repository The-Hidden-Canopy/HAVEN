using System.Text.Json;
using System.Text;
using Haven.Desktop.Models;
using Haven.Desktop.Services;
using Haven.Desktop.Setup;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    // -- today ----------------------------------------------------------------

    // (Greeting, context line) per daypart -- Needs You spec Appendix B.
    // Copy stays calm and descriptive; no emotional/productivity judgments.
    private static (string Greeting, string Context) HeroCopyFor(TemporalDaypart daypart) => daypart switch
    {
        TemporalDaypart.Dawn => ("Good morning", "Start quietly. HAVEN has the first signals of your day ready."),
        TemporalDaypart.Morning => ("Good morning", "Start with the signal that deserves your first attention."),
        TemporalDaypart.Midday => ("Good afternoon", "Keep momentum with what matters most now."),
        TemporalDaypart.Afternoon => ("Good afternoon", "Protect the work already in motion and close the next important loop."),
        TemporalDaypart.Evening => ("Good evening", "Close the day with a calm view of what still needs you."),
        TemporalDaypart.Night => ("Good evening", "Wind down, clear what is waiting, and leave tomorrow with a clean starting point."),
        _ => ("Welcome", "Your most important signals, in one calm view."),
    };

    private TemporalVisualService? _temporalVisualService;

    private async Task LoadTodayAsync()
    {
        EnsureTemporalVisualService();
        TodayDate.Text = DateTime.Now.ToString("dddd, MMMM d");
        TodayStatus.Text = "";
        await LoadTodaySnapshotAsync();
    }

    /// <summary>Starts the six-daypart temporal hero (Needs You spec 20) the
    /// first time Today loads; a no-op on later navigations back to Today.
    /// One boundary timer for the app's lifetime, not one per page visit.</summary>
    private void EnsureTemporalVisualService()
    {
        if (_temporalVisualService is not null)
        {
            return;
        }
        _temporalVisualService = new TemporalVisualService(DispatcherQueue);
        _temporalVisualService.StateChanged += (_, state) =>
            DispatcherQueue.TryEnqueue(() => ApplyTemporalState(state));
        _temporalVisualService.Start();
        ApplyTemporalState(_temporalVisualService.GetCurrentState());
    }

    /// <summary>Binds one resolved <see cref="TemporalVisualState"/> to the
    /// hero: greeting/date copy, the daypart image (or a flat surface in
    /// high contrast, spec 21.1), and a crossfade that respects reduced
    /// motion (spec 15).</summary>
    private void ApplyTemporalState(TemporalVisualState state)
    {
        var (greeting, context) = HeroCopyFor(state.Daypart);
        TodayGreeting.Text = greeting;
        TodayHeroContext.Text = context;

        var highContrast = new Windows.UI.ViewManagement.AccessibilitySettings().HighContrast;
        TodayHeroFlatBackground.Visibility = highContrast ? Visibility.Visible : Visibility.Collapsed;
        TodayHeroImage.Visibility = highContrast ? Visibility.Collapsed : Visibility.Visible;
        if (highContrast)
        {
            return;
        }

        var bitmap = new BitmapImage(state.AssetUri);
        if (!state.MotionAllowed)
        {
            TodayHeroImage.Opacity = 1;
            TodayHeroImage.Source = bitmap;
            return;
        }
        TodayHeroImage.Opacity = 0;
        TodayHeroImage.Source = bitmap;
        var fade = new DoubleAnimation { From = 0, To = 1, Duration = TimeSpan.FromMilliseconds(320) };
        Storyboard.SetTarget(fade, TodayHeroImage);
        Storyboard.SetTargetProperty(fade, "Opacity");
        var storyboard = new Storyboard();
        storyboard.Children.Add(fade);
        storyboard.Begin();
    }

    // -- today regions (Needs You spec 17) ------------------------------------------------
    // Seven named regions: Focus / Needs You / Tasks / Upcoming / Recent
    // files / Recent activity / Pending replies (collapsed unless a real
    // evidence source exists). One typed snapshot establishes a coherent
    // generated-at boundary, while each region keeps its own partial-failure
    // and empty-data behavior - no fabricated rows.

    private async Task LoadTodayCardsAsync()
    {
        await LoadTodaySnapshotAsync();
    }

    private async Task LoadTodaySnapshotAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetTodaySnapshotAsync();
            var snapshot = result.GetProperty("snapshot");
            var regions = snapshot.GetProperty("regions");

            // One snapshot preserves a coherent generated-at boundary while
            // each region keeps its own partial-failure semantics. Focus,
            // Needs You and Upcoming are distinct regions/cards (Needs You
            // spec 3, 18, 19) - they are never collapsed into one ranked list.
            var focusTask = RenderFocusAsync(regions.GetProperty("focus"));
            var needsYouTask = RenderNeedsYouModuleAsync(regions.GetProperty("needs_you"));
            var upcomingTask = RenderUpcomingAsync(regions.GetProperty("upcoming"));
            var tasksTask = RenderTodayRegionAsync(TodayTasksCard, TodayTasksHost, Task.FromResult(regions.GetProperty("tasks")), "tasks", MakeTodayTaskRow, take: 6);
            var filesTask = RenderTodayRegionAsync(TodayFilesCard, TodayFilesHost, Task.FromResult(regions.GetProperty("files")), "files", MakeTodayFileRow, take: 5);
            var activityTask = RenderTodayRegionAsync(TodayActivityCard, TodayActivityHost, Task.FromResult(regions.GetProperty("activity")), "events", MakeTodayActivityRow, take: 6);
            RenderReplies(regions.GetProperty("replies"));

            var hasContent = await Task.WhenAll(focusTask, needsYouTask, upcomingTask, tasksTask, filesTask, activityTask);
            TodayGrid.Visibility = Visibility.Visible;
            TodayEmptyCard.Visibility = hasContent.Any(x => x) ? Visibility.Collapsed : Visibility.Visible;
            var errors = snapshot.TryGetProperty("errors", out var errorValues)
                ? errorValues.GetArrayLength()
                : 0;
            TodayStatus.Text = errors > 0
                ? $"{errors} Today section{(errors == 1 ? " is" : "s are")} temporarily unavailable."
                : "";
        }
        catch (Exception)
        {
            TodayStatus.Text = "Could not load today's summary. Try again when HAVEN Core is connected.";
        }
    }

    private void RenderReplies(JsonElement replies)
    {
        // Collapsed by default; shown only when a real evidence source is
        // connected (spec Appendix C: never advertise the missing
        // capability with a permanent card).
        var available = replies.TryGetProperty("available", out var a) && a.ValueKind == JsonValueKind.True;
        TodayRepliesHost.Children.Clear();
        if (!available)
        {
            TodayRepliesCard.Visibility = Visibility.Collapsed;
            return;
        }
        TodayRepliesCard.Visibility = Visibility.Visible;
        foreach (var reply in Enumerate(replies, "replies"))
        {
            TodayRepliesHost.Children.Add(MakeTodayActivityRow(reply));
        }
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
            if (result.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
            {
                throw new InvalidOperationException("This section is temporarily unavailable.");
            }
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
        catch (Exception)
        {
            card.Visibility = Visibility.Visible;
            host.Children.Add(new TextBlock
            {
                Text = "This section is temporarily unavailable. Try again when HAVEN Core is connected.",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return true; // an error is shown, not silently treated as "empty".
        }
    }

    /// <summary>Focus is 0-1 item, never a list (Needs You spec 18) - the
    /// single continuation that answers "what should I do first?".</summary>
    private Task<bool> RenderFocusAsync(JsonElement focusRegion)
    {
        TodayFocusHost.Children.Clear();
        if (focusRegion.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
        {
            TodayFocusCard.Visibility = Visibility.Visible;
            TodayFocusHost.Children.Add(new TextBlock
            {
                Text = "Focus is temporarily unavailable.",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return Task.FromResult(true);
        }
        if (!focusRegion.TryGetProperty("item", out var item) || item.ValueKind != JsonValueKind.Object)
        {
            TodayFocusCard.Visibility = Visibility.Collapsed;
            return Task.FromResult(false);
        }
        TodayFocusCard.Visibility = Visibility.Visible;
        TodayFocusHost.Children.Add(MakeTodayCard(item, focusStyle: true));
        return Task.FromResult(true);
    }

    /// <summary>Upcoming: purely time-bound evidence, chronological, bounded
    /// (Needs You spec 19) - never failures or blocked work.</summary>
    private Task<bool> RenderUpcomingAsync(JsonElement upcomingRegion)
    {
        TodayUpcomingHost.Children.Clear();
        if (upcomingRegion.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
        {
            TodayUpcomingCard.Visibility = Visibility.Visible;
            TodayUpcomingHost.Children.Add(new TextBlock
            {
                Text = "Upcoming is temporarily unavailable.",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return Task.FromResult(true);
        }
        var items = Enumerate(upcomingRegion, "items").ToList();
        if (items.Count == 0)
        {
            TodayUpcomingCard.Visibility = Visibility.Collapsed;
            return Task.FromResult(false);
        }
        TodayUpcomingCard.Visibility = Visibility.Visible;
        foreach (var item in items)
        {
            TodayUpcomingHost.Children.Add(MakeTodayCard(item, focusStyle: false));
        }
        return Task.FromResult(true);
    }

    /// <summary>The Today-module slice of Needs You (spec 8.1): up to 3
    /// items, a trustworthy count chip (spec 5.2 - never inflated), and a
    /// "Review all" entry into the full pane. The shell-level pending
    /// banner's own count is the authority-only subset (spec 9.1).</summary>
    private Task<bool> RenderNeedsYouModuleAsync(JsonElement needsYouRegion)
    {
        TodayNeedsYouHost.Children.Clear();
        if (needsYouRegion.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
        {
            TodayNeedsYouCard.Visibility = Visibility.Visible;
            TodayNeedsYouCountChip.Visibility = Visibility.Collapsed;
            TodayPendingBanner.Visibility = Visibility.Collapsed;
            ShellNeedsYouBadge.Visibility = Visibility.Collapsed;
            HeroNeedsYouButton.Visibility = Visibility.Collapsed;
            TodayNeedsYouHost.Children.Add(new TextBlock
            {
                Text = "Needs You is temporarily unavailable.",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
            return Task.FromResult(true);
        }

        var items = Enumerate(needsYouRegion, "items").ToList();
        var count = (int)GetInt(needsYouRegion, "count");

        var authorityCount = items.Count(item => GetString(item, "kind") == "authority");
        TodayPendingBanner.Visibility = authorityCount > 0 ? Visibility.Visible : Visibility.Collapsed;
        TodayPendingText.Text = authorityCount > 0
            ? $"{authorityCount} action{(authorityCount == 1 ? " is" : "s are")} waiting for your approval."
            : "";

        ShellNeedsYouBadge.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        ShellNeedsYouBadgeText.Text = count.ToString();
        HeroNeedsYouButton.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        HeroNeedsYouCountText.Text = count.ToString();

        if (count == 0)
        {
            TodayNeedsYouCard.Visibility = Visibility.Collapsed;
            TodayNeedsYouCountChip.Visibility = Visibility.Collapsed;
            return Task.FromResult(false);
        }
        TodayNeedsYouCard.Visibility = Visibility.Visible;
        TodayNeedsYouCountChip.Visibility = Visibility.Visible;
        TodayNeedsYouCountText.Text = count.ToString();
        foreach (var item in items.Take(3))
        {
            TodayNeedsYouHost.Children.Add(MakeNeedsYouRow(item, showSnooze: true));
        }
        return Task.FromResult(true);
    }

    /// <summary>One Needs You row, shared by the Today module (top 3) and
    /// the full review pane (all open items).</summary>
    private FrameworkElement MakeNeedsYouRow(JsonElement item, bool showSnooze)
    {
        var sourceRef = GetString(item, "source_ref") ?? "";
        var dismissibility = GetString(item, "dismissibility") ?? "none";
        var kind = GetString(item, "kind") ?? "blocker";
        var page = GetString(item, "route", "page") ?? "today";

        var layout = new StackPanel { Spacing = 6 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(item, "title") ?? "Untitled",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        var (chipTint, chipForeground) = kind switch
        {
            "authority" => ("HavenOrangeTintBrush", "HavenOrangeBrush"),
            "conflict" or "ambiguity" => ("HavenVioletTintBrush", "HavenVioletBrush"),
            _ => ("HavenWarningTintBrush", "HavenWarningBrush"),
        };
        head.Children.Add(MakeChip(Sentence(kind), chipTint, chipForeground));
        layout.Children.Add(head);
        layout.Children.Add(new TextBlock
        {
            Text = GetString(item, "why_now") ?? "",
            Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var review = new Button { Content = "Review", Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"] };
        review.Click += (_, _) =>
        {
            CloseNeedsYouPane();
            SelectNavigation(page);
        };
        actions.Children.Add(review);
        if (showSnooze && dismissibility != "none")
        {
            var snooze = new Button { Content = "Snooze", Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"] };
            snooze.Click += async (_, _) =>
            {
                if (_client is null || string.IsNullOrWhiteSpace(sourceRef))
                {
                    return;
                }
                try
                {
                    await _client.SnoozeNeedsYouAsync(sourceRef);
                    await LoadTodaySnapshotAsync();
                    if (NeedsYouOverlay.Visibility == Visibility.Visible)
                    {
                        await RenderNeedsYouPaneAsync();
                    }
                }
                catch (Exception)
                {
                    TodayStatus.Text = "That item could not be snoozed. Try again when HAVEN Core is connected.";
                }
            };
            actions.Children.Add(snooze);
        }
        layout.Children.Add(actions);

        return new Border { Style = (Style)Application.Current.Resources["HavenCardStyle"], Child = layout };
    }

    // -- Needs You review pane (spec 8.2) --------------------------------------

    private async void OnNeedsYouReviewAllClicked(object sender, RoutedEventArgs args) => await OpenNeedsYouPaneAsync(authorityOnly: false);

    private void OnNeedsYouCloseClicked(object sender, RoutedEventArgs args) => CloseNeedsYouPane();

    private void CloseNeedsYouPane()
    {
        NeedsYouOverlay.Visibility = Visibility.Collapsed;
    }

    private bool _needsYouPaneAuthorityOnly;

    private async Task OpenNeedsYouPaneAsync(bool authorityOnly)
    {
        _needsYouPaneAuthorityOnly = authorityOnly;
        NeedsYouPaneTitle.Text = authorityOnly ? "Needs You — decisions waiting" : "Needs You";
        NeedsYouOverlay.Visibility = Visibility.Visible;
        await RenderNeedsYouPaneAsync();
    }

    private async Task RenderNeedsYouPaneAsync()
    {
        NeedsYouPaneHost.Children.Clear();
        NeedsYouPaneStatus.Text = "Loading…";
        if (_client is null)
        {
            NeedsYouPaneStatus.Text = "HAVEN Core is not connected.";
            return;
        }
        try
        {
            var result = await _client.GetNeedsYouAsync();
            var items = Enumerate(result, "items")
                .Where(item => !_needsYouPaneAuthorityOnly || GetString(item, "kind") == "authority")
                .ToList();
            if (items.Count == 0)
            {
                NeedsYouPaneStatus.Text = _needsYouPaneAuthorityOnly
                    ? "Nothing is waiting for your approval right now."
                    : "Nothing is waiting on you right now.";
                return;
            }
            NeedsYouPaneStatus.Text = $"{items.Count} item{(items.Count == 1 ? "" : "s")}.";
            foreach (var item in items)
            {
                NeedsYouPaneHost.Children.Add(MakeNeedsYouRow(item, showSnooze: true));
            }
        }
        catch (Exception)
        {
            NeedsYouPaneStatus.Text = "Could not load Needs You. Try again when HAVEN Core is connected.";
        }
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
                catch (Exception)
                {
                    TodayStatus.Text = "The card could not be dismissed. Try again when HAVEN Core is connected.";
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


    private async void OnTodayPendingClicked(object sender, RoutedEventArgs args)
    {
        // Retired (Needs You spec 9.1): this used to route every pending
        // request straight to Home/Rooms, which was wrong for a file
        // mutation, model permission, or external-agent request. It now
        // opens the Needs You pane filtered to authority items; each item
        // routes to its own origin from there.
        await OpenNeedsYouPaneAsync(authorityOnly: true);
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
