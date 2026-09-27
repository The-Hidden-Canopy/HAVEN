using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    private static readonly string[] DiscoveryDeviceTypes =
        { "light", "thermostat", "switch", "fan", "cover", "camera", "zoneplayer" };

    private JsonElement _discoveryCandidates;

    private async Task LoadDiscoveryCandidatesAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetDiscoveryCandidatesAsync();
            _discoveryCandidates = result.GetProperty("candidates").Clone();
            DiscoveryErrorText.Text = "";
            RenderDiscoveryCandidates();
        }
        catch (Exception ex)
        {
            DiscoveryErrorText.Text = $"Could not load discovered devices: {ex.Message}";
        }
    }

    private async void OnDiscoveryScanClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        DiscoveryScanButton.IsEnabled = false;
        DiscoveryStatusText.Text = "Scanning nearby WiFi (SSDP + mDNS) and Bluetooth…";
        DiscoveryErrorText.Text = "";
        try
        {
            var result = await _client.ScanDiscoveryAsync();
            _discoveryCandidates = result.GetProperty("candidates").Clone();
            RenderDiscoveryCandidates();
        }
        catch (Exception ex)
        {
            DiscoveryErrorText.Text = $"Scan failed: {ex.Message}";
        }
        finally
        {
            DiscoveryStatusText.Text = "";
            DiscoveryScanButton.IsEnabled = true;
        }
    }

    private void RenderDiscoveryCandidates()
    {
        DiscoveryCandidatesList.Children.Clear();
        var candidates = Enumerate(_discoveryCandidates).ToList();
        if (candidates.Count == 0)
        {
            DiscoveryCandidatesList.Children.Add(new TextBlock
            {
                Text = "Nothing found yet. Press Scan to look for nearby WiFi (SSDP + mDNS) and Bluetooth devices.",
                Opacity = 0.72,
            });
            return;
        }
        foreach (var candidate in candidates)
        {
            DiscoveryCandidatesList.Children.Add(MakeDiscoveryCandidateCard(candidate));
        }
    }

    /// <summary>Small status pill for one of the four independent discovery
    /// statuses (native product-consolidation plan, P1 "Discovery UI" —
    /// "each result should expose four independent statuses: Seen,
    /// Identified, Provider available, and Enrolled/verified" instead of
    /// flattening every result to "device found").</summary>
    private static Border MakeStatusPill(string label, bool met)
    {
        return new Border
        {
            CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
            Padding = new Thickness(7, 2, 7, 2),
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                met ? "HavenSuccessTintBrush" : "HavenSurface1Brush"],
            Child = new TextBlock
            {
                Text = label,
                FontSize = 11,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    met ? "HavenSuccessBrush" : "HavenMutedTextBrush"],
            },
        };
    }

    private Border MakeDiscoveryCandidateCard(JsonElement candidate)
    {
        var candidateId = GetString(candidate, "candidate_id") ?? "unknown";
        var source = GetString(candidate, "source");
        var suggestedType = GetString(candidate, "suggested_device_type");
        var suggestedRoom = GetString(candidate, "suggested_room");
        var signal = GetDouble(candidate, "signal_strength");
        var enrolled = candidate.TryGetProperty("enrolled", out var enrolledValue)
            && enrolledValue.ValueKind == JsonValueKind.True;
        var supported = candidate.TryGetProperty("supported", out var supportedValue)
            && supportedValue.ValueKind == JsonValueKind.True;
        var correlatedWith = Enumerate(candidate, "correlated_with")
            .Select(v => v.GetString())
            .Where(v => !string.IsNullOrEmpty(v))
            .ToList();

        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var head = new StackPanel { Spacing = 4 };
        var idLine = candidateId + (source is not null ? " · " + source : "") + (signal is not null ? $" · signal {signal}" : "");
        head.Children.Add(new TextBlock
        {
            Text = idLine,
            TextWrapping = TextWrapping.Wrap,
            Style = (Style)Application.Current.Resources["HavenMonoTextStyle"],
        });
        head.Children.Add(new TextBlock
        {
            Text = (suggestedType ?? "unknown device") + (suggestedRoom is not null ? " · " + suggestedRoom : ""),
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        // Four independent statuses, evidence not a verdict: a device can be
        // Seen+Identified with no Provider available (nothing in HAVEN knows
        // how to control it yet), which is a materially different state from
        // "not found at all" and from "found, supported, not yet enrolled."
        var statusRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        statusRow.Children.Add(MakeStatusPill("Seen", true));
        statusRow.Children.Add(MakeStatusPill("Identified", suggestedType is not null));
        statusRow.Children.Add(MakeStatusPill("Provider available", supported));
        statusRow.Children.Add(MakeStatusPill("Enrolled", enrolled));
        head.Children.Add(statusRow);
        if (correlatedWith.Count > 0)
        {
            var correlationLine = new TextBlock
            {
                Text = $"Also seen via {correlatedWith.Count} other candidate(s) at the same address — likely the same device.",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            };
            ToolTipService.SetToolTip(correlationLine, string.Join(", ", correlatedWith));
            head.Children.Add(correlationLine);
        }
        grid.Children.Add(head);

        if (enrolled)
        {
            var badge = new TextBlock
            {
                Text = "Enrolled",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenSuccessBrush"],
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(badge, 1);
            grid.Children.Add(badge);
        }
        else if (!supported)
        {
            var badge = new TextBlock
            {
                Text = "No capability preset yet",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(badge, 1);
            grid.Children.Add(badge);
        }
        else
        {
            var typeBox = new ComboBox { MinWidth = 120 };
            foreach (var option in DiscoveryDeviceTypes)
            {
                typeBox.Items.Add(option);
            }
            typeBox.SelectedIndex = suggestedType is not null && DiscoveryDeviceTypes.Contains(suggestedType)
                ? Array.IndexOf(DiscoveryDeviceTypes, suggestedType)
                : 0;
            var room = new TextBox { PlaceholderText = "room", Text = suggestedRoom ?? "", MinWidth = 120 };
            var enroll = new Button { Content = "Enroll" };
            enroll.Click += async (_, _) =>
            {
                var deviceType = typeBox.SelectedItem as string ?? DiscoveryDeviceTypes[0];
                await EnrollDiscoveryCandidateAsync(candidateId, deviceType, room.Text.Trim());
            };
            var controls = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                Spacing = 8,
                VerticalAlignment = VerticalAlignment.Center,
            };
            controls.Children.Add(typeBox);
            controls.Children.Add(room);
            controls.Children.Add(enroll);
            Grid.SetColumn(controls, 1);
            grid.Children.Add(controls);
        }

        return new Border
        {
            Style = (Style)Application.Current.Resources["HavenCardStyle"],
            Child = grid,
        };
    }

    private async Task EnrollDiscoveryCandidateAsync(string candidateId, string deviceType, string room)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.EnrollDiscoveryCandidateAsync(
                candidateId, deviceType, string.IsNullOrWhiteSpace(room) ? null : room);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                DiscoveryErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to enroll the device."
                    : "HAVEN Core refused to enroll the device.";
                return;
            }
            DiscoveryErrorText.Text = "";
            if (result.ValueKind == JsonValueKind.Object && result.TryGetProperty("candidates", out var candidates))
            {
                _discoveryCandidates = candidates.Clone();
                RenderDiscoveryCandidates();
            }
            // Enrollment lands in the same device registry Rooms reads from.
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            DiscoveryErrorText.Text = ex.Message;
        }
    }
}
