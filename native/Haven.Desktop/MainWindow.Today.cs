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

    private async Task LoadTodayCardsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetTodayCardsAsync();
            RenderTodayCards(result.GetProperty("cards"));
        }
        catch (Exception ex)
        {
            TodayCardsHost.Children.Clear();
            TodayCardsHost.Children.Add(new TextBlock
            {
                Text = $"Could not load Today: {ex.Message}",
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private void RenderTodayCards(JsonElement cards)
    {
        TodayCardsHost.Children.Clear();
        var rows = Enumerate(cards).ToList();
        if (rows.Count == 0)
        {
            TodayCardsHost.Children.Add(new Border
            {
                Style = (Style)Application.Current.Resources["HavenCardStyle"],
                Child = new TextBlock
                {
                    Text = "Nothing needs you right now. Tasks with due dates, pending decisions, and upcoming commitments will appear here.",
                    TextWrapping = TextWrapping.Wrap,
                    Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                },
            });
            return;
        }

        var focus = rows[0];
        var rest = rows.Skip(1).ToList();
        TodayCardsHost.Children.Add(MakeTodaySection("Focus", new List<JsonElement> { focus }, focusStyle: true));
        foreach (var (title, group) in new[]
        {
            ("Decisions", "authority"),
            ("Upcoming", "deadline"),
            ("Commitments", "commitment"),
            ("Suggestions", "suggestion"),
        })
        {
            var sectionCards = rest.Where(card => GetString(card, "group") == group).ToList();
            if (sectionCards.Count > 0)
            {
                TodayCardsHost.Children.Add(MakeTodaySection(title, sectionCards));
            }
        }
    }

    private Border MakeTodaySection(string title, List<JsonElement> cards, bool focusStyle = false)
    {
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(new TextBlock
        {
            Text = title,
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
        });
        foreach (var card in cards)
        {
            section.Children.Add(MakeTodayCard(card, focusStyle));
        }
        return new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            Child = section,
        };
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
