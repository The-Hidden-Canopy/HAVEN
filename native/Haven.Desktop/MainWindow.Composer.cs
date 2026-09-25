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
    // -- persistent composer (spec 10) ----------------------------------------

    private bool _composerBusy;

    private async void OnComposerSendClicked(object sender, RoutedEventArgs args)
    {
        await SubmitComposerAsync();
    }

    private async void OnComposerKeyDown(object sender, KeyRoutedEventArgs args)
    {
        // Enter submits; Shift+Enter inserts a newline.
        var shift = Microsoft.UI.Input.InputKeyboardSource.GetKeyStateForCurrentThread(VirtualKey.Shift);
        if (args.Key == VirtualKey.Enter && (int)shift == 0)
        {
            args.Handled = true;
            await SubmitComposerAsync();
        }
    }

    private async Task SubmitComposerAsync()
    {
        if (_connection is { IsMutationsEnabled: false })
        {
            // Offline gate (spec section 11): the input is kept, not cleared.
            ComposerStatus.Text = "Core is offline — your message is kept; try again when connected.";
            return;
        }
        if (_client is null || _composerBusy || string.IsNullOrWhiteSpace(ComposerInput.Text))
        {
            return;
        }
        var text = ComposerInput.Text.Trim();
        _composerBusy = true;
        ComposerSendButton.IsEnabled = false;
        ComposerThinking.Visibility = Visibility.Visible;
        ComposerStatus.Text = "";
        try
        {
            var state = await _client.AskAsync(text);
            ComposerInput.Text = "";
            RenderComposerAnswer(state, text);
        }
        catch (Exception ex)
        {
            ComposerStatus.Text = $"Could not reach HAVEN: {ex.Message}";
        }
        finally
        {
            // One-shot control: the send button returns to idle however the
            // request ended (spec 12).
            _composerBusy = false;
            ComposerSendButton.IsEnabled = true;
            ComposerThinking.Visibility = Visibility.Collapsed;
        }
    }

    private void RenderComposerAnswer(JsonElement state, string question)
    {
        ComposerResults.Children.Clear();
        ComposerResults.Visibility = Visibility.Visible;

        string? answer = null;
        if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("conversation", out var conversation))
        {
            foreach (var entry in Enumerate(conversation))
            {
                if (GetString(entry, "from") == "haven")
                {
                    answer = GetString(entry, "text");
                }
            }
        }
        ComposerResults.Children.Add(new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            Child = new StackPanel
            {
                Spacing = 6,
                Children =
                {
                    new TextBlock { Text = question, Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"], TextWrapping = TextWrapping.Wrap },
                    new TextBlock { Text = answer ?? "HAVEN answered, but the reply did not include text.", Style = (Style)Application.Current.Resources["HavenBodyTextStyle"], TextWrapping = TextWrapping.Wrap },
                },
            },
        });

        // Mutation honesty (spec 10): anything action-like that this exchange
        // produced is a proposal awaiting review, never presented as done.
        var proposalNotes = new List<string>();
        if (state.ValueKind == JsonValueKind.Object)
        {
            var pending = state.TryGetProperty("pending", out var pendingValue) ? pendingValue.GetArrayLength() : 0;
            if (pending > 0)
            {
                proposalNotes.Add($"{pending} confirmation{(pending == 1 ? "" : "s")} awaiting your decision");
            }
            if (state.TryGetProperty("automations", out var automations))
            {
                var proposed = Enumerate(automations).Count(rule => GetString(rule, "status") == "proposed");
                if (proposed > 0)
                {
                    proposalNotes.Add($"{proposed} automation proposal{(proposed == 1 ? "" : "s")} awaiting approval");
                }
            }
        }
        foreach (var note in proposalNotes)
        {
            var banner = new Border
            {
                Style = (Style)Application.Current.Resources["HavenCardStyle"],
                BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenWarningBrush"],
                Child = new StackPanel
                {
                    Orientation = Orientation.Horizontal,
                    Spacing = 10,
                    Children =
                    {
                        new TextBlock { Text = $"Proposal — review required: {note}.", Style = (Style)Application.Current.Resources["HavenBodyTextStyle"], VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap },
                        new Button { Content = "Review in Home", VerticalAlignment = VerticalAlignment.Center },
                    },
                },
            };
            var review = (Button)((StackPanel)banner.Child).Children[1];
            review.Click += (_, _) => SelectNavigation("home");
            ComposerResults.Children.Add(banner);
        }
    }

    private static string? CommandLineValue(string key)
    {
        var args = Environment.GetCommandLineArgs();
        var index = Array.IndexOf(args, key);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static async Task<string?> ResolveAuthTokenAsync()
    {
        var token = Environment.GetEnvironmentVariable("HAVEN_IPC_TOKEN");
        if (!string.IsNullOrWhiteSpace(token))
        {
            return token;
        }

        var args = Environment.GetCommandLineArgs();
        if (!args.Contains("--ipc-token-stdin", StringComparer.Ordinal))
        {
            // The bearer token is intentionally never accepted from a
            // command-line argument. Manual launches use HAVEN_IPC_TOKEN;
            // the desktop launcher uses the inherited stdin handoff above.
            return null;
        }

        using var reader = new StreamReader(Console.OpenStandardInput());
        return (await reader.ReadLineAsync())?.Trim();
    }

    private void OnNavItemClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tag)
        {
            SelectNavigation(tag);
        }
    }

}
