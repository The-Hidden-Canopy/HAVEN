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
    private async Task LoadRoomsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetRoomsAsync();
            _rooms = result.GetProperty("rooms").Clone();
            _pending = result.GetProperty("pending").Clone();
            RoomsErrorText.Text = "";
            HomeStatusText.Text = "";
            HomeUpdatedText.Text = $"Updated {DateTime.Now:t}";
            RenderRooms();
            RenderHomeDevices();
        }
        catch (Exception ex)
        {
            HomeStatusText.Text = "";
            RoomsErrorText.Text = $"Could not load home state: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderRooms()
    {
        var selected = (RoomCards.SelectedItem as ListViewItem)?.Tag?.ToString();
        RoomCards.Items.Clear();
        foreach (var room in Enumerate(_rooms))
        {
            var roomId = GetString(room, "id") ?? "";
            var people = GetPeople(room);
            var deviceCount = Enumerate(room, "devices").Count();
            var card = new StackPanel { Spacing = 4, MinWidth = 200 };
            var name = new TextBlock
            {
                Text = GetString(room, "name") ?? roomId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            };
            card.Children.Add(name);
            card.Children.Add(new TextBlock
            {
                Text = people.Count > 0 ? string.Join(" · ", people) : "Empty",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            card.Children.Add(new TextBlock
            {
                Text = deviceCount == 0
                    ? "No devices"
                    : deviceCount == 1 ? "1 device" : $"{deviceCount} devices",
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            RoomCards.Items.Add(new ListViewItem
            {
                Tag = roomId,
                Content = new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    MinWidth = 210,
                    Child = card,
                },
            });
        }
        if (RoomCards.Items.Count == 0)
        {
            RoomCards.Items.Add(new TextBlock
            {
                Text = "No rooms yet. Rooms are yours to declare and do not require a device.",
                Opacity = 0.72,
            });
        }
        else if (selected is not null && RoomCards.Items.Any(item =>
            item is ListViewItem listItem && listItem.Tag?.ToString() == selected))
        {
            RoomCards.SelectedItem = RoomCards.Items.First(item =>
                item is ListViewItem listItem && listItem.Tag?.ToString() == selected);
        }
        else if (RoomCards.SelectedItem is null && RoomCards.Items[0] is ListViewItem)
        {
            RoomCards.SelectedItem = RoomCards.Items[0];
        }
        RenderPending();
        RenderRoomDetail();
    }

    private void RenderHomeDevices()
    {
        HomeDevicesList.Children.Clear();
        var any = false;
        foreach (var room in Enumerate(_rooms))
        {
            var devices = Enumerate(room, "devices").ToList();
            if (devices.Count == 0)
            {
                continue;
            }
            any = true;
            HomeDevicesList.Children.Add(new TextBlock
            {
                Text = GetString(room, "name") ?? GetString(room, "id") ?? "Room",
                Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            });
            foreach (var device in devices)
            {
                HomeDevicesList.Children.Add(MakeDeviceRow(device));
            }
        }
        if (!any)
        {
            HomeDevicesList.Children.Add(new TextBlock
            {
                Text = "No devices yet. Enroll a device from Setup → Connections and it appears here.",
                Opacity = 0.72,
            });
        }
    }

    private void RenderPending()
    {
        PendingList.Children.Clear();
        foreach (var request in Enumerate(_pending))
        {
            var requestId = GetString(request, "request_id") ?? "";
            var card = new StackPanel { Spacing = 4 };
            card.Children.Add(new TextBlock
            {
                Text = GetString(request, "title") ?? requestId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            var detail = GetString(request, "detail");
            if (!string.IsNullOrWhiteSpace(detail))
            {
                card.Children.Add(new TextBlock
                {
                    Text = detail,
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.72,
                });
            }
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var approve = new Button { Content = "Approve" };
            approve.Click += async (_, _) => await DecideRequestAsync(requestId, approve: true);
            var deny = new Button { Content = "Deny" };
            deny.Click += async (_, _) => await DecideRequestAsync(requestId, approve: false);
            buttons.Children.Add(approve);
            buttons.Children.Add(deny);
            card.Children.Add(buttons);
            PendingList.Children.Add(new Border
            {
                Padding = new Microsoft.UI.Xaml.Thickness(10),
                CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
                BorderThickness = new Microsoft.UI.Xaml.Thickness(1),
                BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
                Child = card,
            });
        }
        if (PendingList.Children.Count == 0)
        {
            PendingList.Children.Add(new TextBlock { Text = "Nothing waiting on you.", Opacity = 0.72 });
        }
    }

    private async void OnAddRoomClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var name = new TextBox { PlaceholderText = "e.g. Studio", MinWidth = 280 };
        var fields = new StackPanel { Spacing = 8 };
        fields.Children.Add(new TextBlock { Text = "Room name" });
        fields.Children.Add(name);
        fields.Children.Add(new TextBlock
        {
            Text = "A room is your declaration; it is valid before any device is connected and survives a provider being unavailable.",
            TextWrapping = TextWrapping.Wrap,
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        var dialog = new ContentDialog
        {
            Title = "Add room",
            Content = fields,
            PrimaryButtonText = "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        try
        {
            var result = await _client.AddRoomAsync(name.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to add the room."
                    : "HAVEN Core refused to add the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task RenameRoomAsync(string roomId, string currentName)
    {
        if (_client is null)
        {
            return;
        }
        var name = new TextBox { Text = currentName, MinWidth = 280 };
        var dialog = new ContentDialog
        {
            Title = $"Rename {currentName}",
            Content = name,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        try
        {
            var result = await _client.RenameRoomAsync(roomId, name.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to rename the room."
                    : "HAVEN Core refused to rename the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task RemoveRoomAsync(string roomId, string roomName)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {roomName}?",
            Content = new TextBlock
            {
                Text = $"The {roomName} declaration is removed from your household. Devices in it are not deleted; they show as unassigned until you declare the room again.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            PrimaryButtonStyle = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.RemoveRoomAsync(roomId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused to remove the room."
                    : "HAVEN Core refused to remove the room.";
                return;
            }
            RoomsErrorText.Text = "";
            await LoadRoomsAsync();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private async Task DecideRequestAsync(string requestId, bool approve)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = approve
                ? await _client.ApproveRequestAsync(requestId)
                : await _client.DenyRequestAsync(requestId);
            ApplyCommandState(result);
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private void RenderRoomDetail()
    {
        RoomDetail.Children.Clear();
        if (RoomCards.SelectedItem is not ListViewItem item || item.Tag is not string roomId)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "Select a room.", Opacity = 0.72 });
            return;
        }
        var room = Enumerate(_rooms).FirstOrDefault(candidate => GetString(candidate, "id") == roomId);
        if (room.ValueKind != JsonValueKind.Object)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "Select a room.", Opacity = 0.72 });
            return;
        }

        var head = new StackPanel { Spacing = 2 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(room, "name") ?? roomId,
            FontSize = 20,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        var people = GetPeople(room);
        head.Children.Add(new TextBlock
        {
            Text = people.Count > 0 ? string.Join(" · ", people) : "Empty",
            Opacity = 0.72,
        });
        var roomActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var rename = new Button
        {
            Content = "Rename",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
        };
        rename.Click += async (_, _) =>
            await RenameRoomAsync(roomId, GetString(room, "name") ?? roomId);
        var remove = new Button
        {
            Content = "Remove",
            Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
        };
        remove.Click += async (_, _) =>
            await RemoveRoomAsync(roomId, GetString(room, "name") ?? roomId);
        roomActions.Children.Add(rename);
        roomActions.Children.Add(remove);
        head.Children.Add(roomActions);
        RoomDetail.Children.Add(head);

        if (room.TryGetProperty("camera", out var camera) && camera.ValueKind == JsonValueKind.Object)
        {
            var parts = new List<string> { "camera · " + (GetString(camera, "label") ?? GetString(camera, "id") ?? "camera") };
            var online = !camera.TryGetProperty("online", out var onlineValue) || onlineValue.GetBoolean();
            if (online)
            {
                parts.Add(camera.TryGetProperty("motion", out var motion) && motion.GetBoolean() ? "motion" : "no motion");
            }
            else
            {
                parts.Add("offline");
            }
            RoomDetail.Children.Add(new TextBlock { Text = string.Join(" — ", parts), Opacity = 0.72 });
        }

        var devices = Enumerate(room, "devices").ToList();
        if (devices.Count == 0)
        {
            RoomDetail.Children.Add(new TextBlock { Text = "No devices in this room.", Opacity = 0.72 });
        }
        foreach (var device in devices)
        {
            RoomDetail.Children.Add(MakeDeviceRow(device));
        }
    }

    private Border MakeDeviceRow(JsonElement device)
    {
        var deviceId = GetString(device, "id") ?? "";
        var status = GetString(device, "status");
        var degraded = status is not null && status != "observed";

        var card = new StackPanel { Spacing = 6 };
        if (degraded)
        {
            card.Opacity = 0.55;
        }

        var head = new Grid();
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = Microsoft.UI.Xaml.GridLength.Auto });
        var label = new TextBlock
        {
            Text = (GetString(device, "role") ?? "device") + (deviceId.Length > 0 ? " · " + deviceId : ""),
            Opacity = 0.72,
            TextWrapping = TextWrapping.Wrap,
        };
        head.Children.Add(label);
        var stateText = new TextBlock
        {
            Text = DeviceStateLine(device),
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        if (degraded)
        {
            stateText.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenWarningBrush"];
        }
        Grid.SetColumn(stateText, 1);
        head.Children.Add(stateText);
        card.Children.Add(head);

        var controls = MakeDeviceControls(device, deviceId);
        if (controls is not null)
        {
            card.Children.Add(controls);
        }

        var provenance = DeviceProvenanceText(device);
        if (provenance.Length > 0)
        {
            card.Children.Add(new TextBlock { Text = provenance, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
        }

        return new Border
        {
            Padding = new Microsoft.UI.Xaml.Thickness(12),
            CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
            BorderThickness = new Microsoft.UI.Xaml.Thickness(1),
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenStrokeBrush"],
            Child = card,
        };
    }

    private StackPanel? MakeDeviceControls(JsonElement device, string deviceId)
    {
        var role = (GetString(device, "role") ?? "").ToLowerInvariant();
        var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var any = false;

        if (role == "light")
        {
            if (device.TryGetProperty("is_on", out var isOn) && isOn.ValueKind == JsonValueKind.True)
            {
                var turnOff = new Button { Content = "Turn off" };
                turnOff.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "light.turn_off");
                controls.Children.Add(turnOff);
                any = true;
            }
            if (device.TryGetProperty("brightness_pct", out var brightness) && brightness.ValueKind == JsonValueKind.Number)
            {
                var input = new TextBox
                {
                    Text = brightness.GetInt32().ToString(),
                    Width = 72,
                    IsSpellCheckEnabled = false,
                };
                var set = new Button { Content = "Set" };
                set.Click += async (_, _) =>
                {
                    var pct = Math.Clamp((int)Math.Round(double.TryParse(input.Text, out var value) ? value : 0), 0, 100);
                    await SendDeviceCommandAsync(deviceId, "light.set_brightness", pct);
                };
                controls.Children.Add(input);
                controls.Children.Add(set);
                any = true;
            }
        }
        else if (role == "cover")
        {
            var open = new Button { Content = "Open" };
            open.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "cover.open");
            var close = new Button { Content = "Close" };
            close.Click += async (_, _) => await SendDeviceCommandAsync(deviceId, "cover.close");
            controls.Children.Add(open);
            controls.Children.Add(close);
            any = true;
        }

        return any ? controls : null;
    }

    private async Task SendDeviceCommandAsync(string deviceId, string service, int? brightnessPct = null)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.SendDeviceCommandAsync(deviceId, service, brightnessPct);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                RoomsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the command."
                    : "HAVEN Core refused the command.";
                return;
            }
            ApplyCommandState(result);
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private void ApplyCommandState(JsonElement result)
    {
        if (result.ValueKind != JsonValueKind.Object || !result.TryGetProperty("state", out var state))
        {
            return;
        }
        if (state.TryGetProperty("rooms", out var rooms))
        {
            _rooms = rooms.Clone();
        }
        if (state.TryGetProperty("pending", out var pending))
        {
            _pending = pending.Clone();
        }
        RoomsErrorText.Text = "";
        RenderRooms();
        RenderHomeDevices();
    }

    private void OnRoomSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        RenderRoomDetail();
    }

    private static string DeviceStateLine(JsonElement device)
    {
        var role = (GetString(device, "role") ?? "").ToLowerInvariant();
        if (role == "cover")
        {
            var coverState = GetString(device, "cover_state");
            if (coverState is "opening" or "closing" or "open" or "closed")
            {
                return coverState switch
                {
                    "opening" => "Opening…",
                    "closing" => "Closing…",
                    "open" => "Open",
                    _ => "Closed",
                };
            }
            return device.TryGetProperty("is_on", out var coverOn) && coverOn.ValueKind == JsonValueKind.True
                ? "Open"
                : "Closed";
        }
        if (role == "lock")
        {
            var lockState = GetString(device, "lock_state");
            return lockState is "locked" or "unlocked"
                ? (lockState == "locked" ? "Locked" : "Unlocked")
                : "—";
        }
        if (role == "thermostat")
        {
            double? current = GetDouble(device, "current_temperature");
            double? target = GetDouble(device, "target_temperature");
            var mode = GetString(device, "climate_mode");
            if (current is null)
            {
                return mode ?? "—";
            }
            var line = $"{current}°";
            if (target is not null)
            {
                line += $" → {target}°";
            }
            return mode is not null ? $"{line} · {mode}" : line;
        }
        if (role == "camera")
        {
            if (device.TryGetProperty("camera_available", out var available) && available.ValueKind == JsonValueKind.False)
            {
                return "Unavailable";
            }
            if (device.TryGetProperty("motion_detected", out var motion) && motion.ValueKind == JsonValueKind.True)
            {
                return "Motion";
            }
            if (available.ValueKind == JsonValueKind.True)
            {
                return "Idle";
            }
            return "—";
        }
        if (role == "light")
        {
            if (device.TryGetProperty("is_on", out var isOn) && isOn.ValueKind == JsonValueKind.False)
            {
                return "Off";
            }
            var brightness = GetDouble(device, "brightness_pct");
            return brightness is not null ? $"On · {brightness}%" : "On";
        }
        if (device.TryGetProperty("is_on", out var on))
        {
            if (on.ValueKind == JsonValueKind.True)
            {
                return "On";
            }
            if (on.ValueKind == JsonValueKind.False)
            {
                return "Off";
            }
        }
        return "—";
    }

    private static string DeviceProvenanceText(JsonElement device)
    {
        var parts = new List<string>();
        var observedAt = GetString(device, "observed_at");
        if (!string.IsNullOrWhiteSpace(observedAt))
        {
            parts.Add("observed " + (FormatObservedAgo(observedAt) ?? observedAt));
        }
        var status = GetString(device, "status");
        if (status is not null)
        {
            parts.Add(status);
        }
        var changedBy = GetString(device, "changed_by");
        if (changedBy is not null)
        {
            parts.Add("by " + changedBy);
        }
        var confidence = GetDouble(device, "confidence");
        if (confidence is not null)
        {
            parts.Add("confidence " + confidence);
        }
        var source = GetString(device, "source");
        if (source is not null)
        {
            parts.Add(source);
        }
        return string.Join(" · ", parts);
    }

    private static string? FormatObservedAgo(string observedAt)
    {
        if (!DateTimeOffset.TryParse(observedAt, out var observed))
        {
            return null;
        }
        var ago = DateTimeOffset.UtcNow - observed.ToUniversalTime();
        if (ago < TimeSpan.Zero)
        {
            return null;
        }
        if (ago.TotalMinutes < 1)
        {
            return "just now";
        }
        if (ago.TotalHours < 1)
        {
            return $"{(int)ago.TotalMinutes} min ago";
        }
        if (ago.TotalDays < 1)
        {
            return $"{(int)ago.TotalHours} h ago";
        }
        return $"{(int)ago.TotalDays} d ago";
    }

}
