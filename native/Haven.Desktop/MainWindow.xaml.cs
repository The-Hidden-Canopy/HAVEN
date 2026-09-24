using System.Text.Json;
using System.Text;
using Haven.Desktop.Setup;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow : Window
{
    private HavenCoreClient? _client;
    private string? _selectedClaimId;

    public MainWindow()
    {
        InitializeComponent();
        UpdateLogo();
        RootGrid().ActualThemeChanged += (_, _) => UpdateLogo();
        RootGrid().Loaded += OnLoaded;
    }

    private Grid RootGrid() => (Grid)Content;

    private void UpdateLogo()
    {
        LogoImage.Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(
            new Uri(RootGrid().ActualTheme == ElementTheme.Dark
                ? "ms-appx:///Assets/haven-logo-dark.png"
                : "ms-appx:///Assets/haven-logo-light.png"));
    }

    private async void OnLoaded(object sender, RoutedEventArgs args)
    {
        var pipeName = CommandLineValue("--pipe-name") ?? Environment.GetEnvironmentVariable("HAVEN_IPC_PIPE");
        var token = await ResolveAuthTokenAsync();
        if (string.IsNullOrWhiteSpace(pipeName) || string.IsNullOrWhiteSpace(token))
        {
            ConnectionText.Text = "Native Core not connected";
            ResponseText.Text = "Start HAVEN Core with a named-pipe endpoint to connect this native client.";
            return;
        }

        try
        {
            var client = new HavenCoreClient(pipeName, token);
            await client.ConnectAsync();
            _client = client;
            ConnectionText.Text = "Connected";
            var state = await _client.GetStateAsync();
            ResponseText.Text = state.GetRawText();
            await EnsureSetupAsync();
        }
        catch (Exception ex)
        {
            ConnectionText.Text = "Core unavailable";
            ResponseText.Text = ex.Message;
        }
    }

    private async Task EnsureSetupAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var status = await _client.GetSetupStatusAsync();
            if (!IsSetupComplete(status))
            {
                ShowSetupWizard();
                await SetupWizard.LoadAsync();
            }
            else
            {
                ReopenSetupButton.Visibility = Visibility.Visible;
            }
        }
        catch (Exception ex)
        {
            // Setup status is best-effort: the main window stays usable without it.
            ResponseText.Text = ex.Message;
        }
    }

    private static bool IsSetupComplete(JsonElement status) =>
        status.ValueKind == JsonValueKind.Object
        && status.TryGetProperty("setup", out var setup)
        && setup.TryGetProperty("completed", out var completed)
        && completed.ValueKind == JsonValueKind.True;

    private void ShowSetupWizard()
    {
        SetupWizard.Initialize(_client!, WinRT.Interop.WindowNative.GetWindowHandle(this));
        SetupOverlay.Visibility = Visibility.Visible;
    }

    private async void OnSetupCompleted(object sender, EventArgs args)
    {
        SetupOverlay.Visibility = Visibility.Collapsed;
        ReopenSetupButton.Visibility = Visibility.Visible;
        if (_client is not null)
        {
            try
            {
                var state = await _client.GetStateAsync();
                ResponseText.Text = state.GetRawText();
            }
            catch (Exception ex)
            {
                ResponseText.Text = ex.Message;
            }
        }
    }

    private async void OnReopenSetupClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var envelope = await _client.ReopenSetupAsync();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ResponseText.Text = envelope.TryGetProperty("error", out var error)
                    ? error.GetString()
                    : "HAVEN Core refused to reopen setup.";
                return;
            }
            ShowSetupWizard();
            await SetupWizard.LoadAsync();
        }
        catch (Exception ex)
        {
            ResponseText.Text = ex.Message;
        }
    }

    private async void OnSearchClicked(object sender, RoutedEventArgs args)
    {
        await SearchAsync();
    }

    private async void OnSearchKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            await SearchAsync();
        }
    }

    private async Task SearchAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(SearchBox.Text))
        {
            return;
        }
        try
        {
            var result = await _client.SearchAsync(SearchBox.Text.Trim());
            SearchResults.Items.Clear();
            foreach (var hit in result.GetProperty("hits").EnumerateArray())
            {
                var title = hit.TryGetProperty("resource", out var resource) && resource.ValueKind == JsonValueKind.Object
                    && resource.TryGetProperty("title", out var titleValue)
                    ? titleValue.GetString()
                    : hit.GetProperty("resource_id").GetString();
                SearchResults.Items.Add(new TextBlock { Text = $"{title}\n{hit.GetProperty("reason").GetString()}" });
            }
        }
        catch (Exception ex)
        {
            SearchResults.Items.Clear();
            SearchResults.Items.Add(new TextBlock { Text = ex.Message });
        }
    }

    private async Task LoadMemoryAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetKnowledgeClaimsAsync();
            MemoryClaims.Items.Clear();
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            foreach (var claim in result.GetProperty("claims").EnumerateArray())
            {
                var claimId = claim.GetProperty("claim_id").GetString();
                var proposition = claim.GetProperty("proposition").GetString() ?? "";
                var state = claim.GetProperty("state").GetString() ?? "unknown";
                var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
                    ? confidenceValue.GetDouble().ToString("P0")
                    : "—";
                var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
                    ? provenanceValue.GetString()
                    : "unknown";
                var summary = new StackPanel { Spacing = 3 };
                summary.Children.Add(new TextBlock
                {
                    Text = proposition,
                    TextWrapping = TextWrapping.Wrap,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
                summary.Children.Add(new TextBlock
                {
                    Text = $"{state.ToUpperInvariant()} · {confidence} · {provenance}",
                    Opacity = 0.72,
                });
                MemoryClaims.Items.Add(new ListViewItem { Tag = claimId, Content = summary });
            }
            if (MemoryClaims.Items.Count == 0)
            {
                MemoryClaims.Items.Add(new TextBlock
                {
                    Text = "HAVEN has not admitted any memories yet.",
                    Opacity = 0.72,
                });
                MemoryDetailText.Text = "Give HAVEN access to a text document, or tell it something directly, to begin building explainable memory.";
            }
            else
            {
                MemoryDetailText.Text = "Select a memory to inspect its evidence.";
                MemoryActionStatus.Text = "";
            }
        }
        catch (Exception ex)
        {
            MemoryClaims.Items.Clear();
            MemoryClaims.Items.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
            MemoryDetailText.Text = "HAVEN could not load memory evidence.";
            MemoryActionStatus.Text = "";
        }
    }

    private async void OnMemorySelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_client is null || MemoryClaims.SelectedItem is not ListViewItem item || item.Tag is not string claimId)
        {
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            return;
        }
        _selectedClaimId = claimId;
        SetMemoryActionButtons(true);
        try
        {
            var result = await _client.GetKnowledgeClaimAsync(claimId);
            MemoryDetailText.Text = FormatClaimDetail(result.GetProperty("claim"));
        }
        catch (Exception ex)
        {
            MemoryDetailText.Text = ex.Message;
        }
    }

    private void SetMemoryActionButtons(bool enabled)
    {
        MemoryCorrectButton.IsEnabled = enabled;
        MemoryStaleButton.IsEnabled = enabled;
        MemoryForgetButton.IsEnabled = enabled;
    }

    private async void OnMemoryCorrectClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var input = new TextBox
        {
            PlaceholderText = "What should HAVEN remember instead?",
            TextWrapping = TextWrapping.Wrap,
            AcceptsReturn = true,
            MinHeight = 90,
        };
        var dialog = new ContentDialog
        {
            Title = "Correct this memory",
            Content = input,
            PrimaryButtonText = "Save correction",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(input.Text))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.CorrectKnowledgeClaimAsync(_selectedClaimId, input.Text.Trim()),
            "Correction saved.");
    }

    private async void OnMemoryStaleClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.MarkKnowledgeClaimStaleAsync(_selectedClaimId),
            "Memory marked stale.");
    }

    private async void OnMemoryForgetClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Forget this memory?",
            Content = new TextBlock
            {
                Text = "HAVEN will keep the history as stale and suppress the same candidate from being re-admitted.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Forget",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.ForgetKnowledgeClaimAsync(_selectedClaimId),
            "Memory forgotten.");
    }

    private async Task RunMemoryMutationAsync(Func<Task<JsonElement>> operation, string successMessage)
    {
        SetMemoryActionButtons(false);
        try
        {
            await operation();
            MemoryActionStatus.Text = successMessage;
            await LoadMemoryAsync();
        }
        catch (Exception ex)
        {
            MemoryActionStatus.Text = ex.Message;
            SetMemoryActionButtons(!string.IsNullOrWhiteSpace(_selectedClaimId));
        }
    }

    private static string FormatClaimDetail(JsonElement claim)
    {
        var text = new StringBuilder();
        text.AppendLine(claim.GetProperty("proposition").GetString() ?? "");
        text.AppendLine();
        text.AppendLine($"STATE\n{claim.GetProperty("state").GetString()}");
        text.AppendLine();
        var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
            ? provenanceValue.GetString()
            : "unknown";
        var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
            ? confidenceValue.GetDouble().ToString("P0")
            : "—";
        text.AppendLine($"PROVENANCE\n{provenance}");
        text.AppendLine();
        text.AppendLine($"CONFIDENCE\n{confidence}");
        text.AppendLine();
        text.AppendLine("SOURCE");
        if (claim.TryGetProperty("sources", out var sources) && sources.GetArrayLength() > 0)
        {
            foreach (var source in sources.EnumerateArray())
            {
                var reference = source.GetProperty("ref").GetString() ?? "unknown source";
                var resource = source.TryGetProperty("resource", out var resourceValue)
                    && resourceValue.ValueKind == JsonValueKind.Object
                    ? resourceValue
                    : default;
                var title = resource.ValueKind == JsonValueKind.Object
                    && resource.TryGetProperty("title", out var titleValue)
                    ? titleValue.GetString()
                    : null;
                text.AppendLine($"• {title ?? reference}");
                if (title is not null && resource.TryGetProperty("locator", out var locatorValue))
                {
                    text.AppendLine($"  {locatorValue.GetString() ?? reference}");
                }
            }
        }
        else
        {
            text.AppendLine("None recorded");
        }
        AppendClaimGroup(text, "EVIDENCE", claim, "evidence_refs");
        AppendClaimGroup(text, "SUPERSEDES", claim, "supersedes");
        AppendClaimGroup(text, "CONTRADICTIONS", claim, "contradicts");
        AppendAuditHistory(text, claim);
        return text.ToString().TrimEnd();
    }

    private static void AppendClaimGroup(StringBuilder text, string label, JsonElement claim, string property)
    {
        text.AppendLine();
        text.AppendLine(label);
        if (!claim.TryGetProperty(property, out var values) || values.GetArrayLength() == 0)
        {
            text.AppendLine("None recorded");
            return;
        }
        foreach (var value in values.EnumerateArray())
        {
            text.AppendLine($"• {value.GetString()}");
        }
    }

    private static void AppendAuditHistory(StringBuilder text, JsonElement claim)
    {
        text.AppendLine();
        text.AppendLine("AUDIT HISTORY");
        if (!claim.TryGetProperty("audit", out var events) || events.GetArrayLength() == 0)
        {
            text.AppendLine("No user changes recorded");
            return;
        }
        foreach (var audit in events.EnumerateArray())
        {
            var action = audit.GetProperty("action").GetString() ?? "unknown";
            var actor = audit.GetProperty("actor_id").GetString() ?? "unknown actor";
            var at = audit.GetProperty("occurred_at").GetString() ?? "unknown time";
            text.AppendLine($"• {action} by {actor} at {at}");
        }
    }

    private async void OnComposerClicked(object sender, RoutedEventArgs args)
    {
        await AskAsync();
    }

    private async void OnComposerKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            await AskAsync();
        }
    }

    private async Task AskAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(ComposerBox.Text))
        {
            return;
        }
        try
        {
            var state = await _client.AskAsync(ComposerBox.Text.Trim());
            ResponseText.Text = state.GetRawText();
        }
        catch (Exception ex)
        {
            ResponseText.Text = ex.Message;
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

    private void OnNavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        var tag = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString();
        var search = tag == "search";
        var memory = tag == "memory";
        var rooms = tag == "rooms";
        var people = tag == "people";
        var automations = tag == "automations";
        SearchPanel.Visibility = search ? Visibility.Visible : Visibility.Collapsed;
        MemoryPanel.Visibility = memory ? Visibility.Visible : Visibility.Collapsed;
        RoomsPanel.Visibility = rooms ? Visibility.Visible : Visibility.Collapsed;
        PeoplePanel.Visibility = people ? Visibility.Visible : Visibility.Collapsed;
        AutomationsPanel.Visibility = automations ? Visibility.Visible : Visibility.Collapsed;
        HomePanel.Visibility = search || memory || rooms || people || automations
            ? Visibility.Collapsed
            : Visibility.Visible;
        PageTitle.Text = tag switch
        {
            "search" => "Search your life",
            "memory" => "Memory",
            "rooms" => "Rooms & devices",
            "people" => "People & contexts",
            "automations" => "Automations",
            "home" => "Home",
            "models" => "Models",
            "settings" => "Settings",
            _ => "HAVEN",
        };
        if (memory)
        {
            _ = LoadMemoryAsync();
        }
        if (rooms)
        {
            _ = LoadRoomsAsync();
        }
        if (people)
        {
            _ = LoadPeopleAsync();
        }
        if (automations)
        {
            _ = LoadAutomationsAsync();
        }
    }

    // -- rooms & devices ----------------------------------------------------

    private JsonElement _rooms = default;
    private JsonElement _pending = default;

    private async void OnRoomsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadRoomsAsync();
    }

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
            RenderRooms();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = ex.Message;
        }
    }

    private void RenderRooms()
    {
        var selected = (RoomsList.SelectedItem as ListViewItem)?.Tag?.ToString();
        RoomsList.Items.Clear();
        foreach (var room in Enumerate(_rooms))
        {
            var roomId = GetString(room, "id") ?? "";
            var summary = new StackPanel { Spacing = 3 };
            var name = new TextBlock
            {
                Text = GetString(room, "name") ?? roomId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            };
            if (GetPeople(room).Count > 0)
            {
                name.Text += "  ●";
            }
            summary.Children.Add(name);
            var deviceCount = Enumerate(room, "devices").Count();
            summary.Children.Add(new TextBlock
            {
                Text = deviceCount == 0
                    ? "No devices"
                    : deviceCount == 1 ? "1 device" : $"{deviceCount} devices",
                Opacity = 0.72,
            });
            RoomsList.Items.Add(new ListViewItem { Tag = roomId, Content = summary });
        }
        if (RoomsList.Items.Count == 0)
        {
            RoomsList.Items.Add(new TextBlock { Text = "No rooms.", Opacity = 0.72 });
        }
        else if (selected is not null && RoomsList.Items.Any(item =>
            item is ListViewItem listItem && listItem.Tag?.ToString() == selected))
        {
            RoomsList.SelectedItem = RoomsList.Items.First(item =>
                item is ListViewItem listItem && listItem.Tag?.ToString() == selected);
        }
        else if (RoomsList.SelectedItem is null && RoomsList.Items[0] is ListViewItem)
        {
            RoomsList.SelectedItem = RoomsList.Items[0];
        }
        RenderPending();
        RenderRoomDetail();
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
                BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                Child = card,
            });
        }
        if (PendingList.Children.Count == 0)
        {
            PendingList.Children.Add(new TextBlock { Text = "Nothing waiting on you.", Opacity = 0.72 });
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
        if (RoomsList.SelectedItem is not ListViewItem item || item.Tag is not string roomId)
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
            stateText.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["SystemFillColorCautionBrush"];
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
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
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
    }

    private void OnRoomSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        RenderRoomDetail();
    }

    // -- people & contexts --------------------------------------------------

    private JsonElement _people = default;
    private JsonElement _contexts = default;

    private async Task LoadPeopleAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var people = await _client.GetPeopleAsync();
            var contexts = await _client.GetContextsAsync();
            _people = people.GetProperty("people").Clone();
            _contexts = contexts.GetProperty("contexts").Clone();
            PeopleErrorText.Text = "";
            RenderPeople();
            RenderContexts();
        }
        catch (Exception ex)
        {
            PeopleErrorText.Text = ex.Message;
        }
    }

    private void RenderPeople()
    {
        PeopleList.Children.Clear();
        foreach (var person in Enumerate(_people))
        {
            var personId = GetString(person, "person_id") ?? "";
            var name = GetString(person, "name") ?? personId;
            var role = (GetString(person, "role") ?? "member").ToUpperInvariant();

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = name,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(new TextBlock { Text = role, Opacity = 0.72 });
            card.Children.Add(head);

            var present = person.TryGetProperty("present", out var presentValue)
                && presentValue.ValueKind == JsonValueKind.True;
            var room = GetString(person, "room");
            card.Children.Add(new TextBlock
            {
                Text = present
                    ? (room is not null ? $"Present · {room}" : "Present")
                    : "Not present",
                Opacity = 0.72,
            });

            var sources = Enumerate(person, "sources").ToList();
            if (sources.Count > 0)
            {
                card.Children.Add(new TextBlock
                {
                    Text = string.Join(" · ", sources.Select(source =>
                        $"presence: {GetString(source, "entity_id") ?? "?"} in {GetString(source, "room_id") ?? "?"}")),
                    Opacity = 0.6,
                    TextWrapping = TextWrapping.Wrap,
                });
            }

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditPersonAsync(person);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemovePersonAsync(personId, name);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            PeopleList.Children.Add(WrapCard(card));
        }
        if (PeopleList.Children.Count == 0)
        {
            PeopleList.Children.Add(new TextBlock
            {
                Text = "No one is declared yet. Add the people HAVEN should know about.",
                Opacity = 0.72,
            });
        }
    }

    private void RenderContexts()
    {
        ContextsList.Children.Clear();
        foreach (var context in Enumerate(_contexts))
        {
            var contextId = GetString(context, "context_id") ?? "";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(context, "label") ?? contextId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var active = context.TryGetProperty("active", out var activeValue)
                && activeValue.ValueKind == JsonValueKind.True;
            var state = new TextBlock
            {
                Text = active ? "Active" : "Inactive",
                Opacity = 0.72,
            };
            if (active)
            {
                state.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["SystemFillColorSuccessBrush"];
            }
            head.Children.Add(state);
            card.Children.Add(head);
            card.Children.Add(new TextBlock
            {
                Text = GetString(context, "entity_id") ?? "",
                Opacity = 0.6,
            });

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditContextAsync(context);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemoveContextAsync(contextId);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            ContextsList.Children.Add(WrapCard(card));
        }
        if (ContextsList.Children.Count == 0)
        {
            ContextsList.Children.Add(new TextBlock
            {
                Text = "No contexts declared. Contexts map a household entity's \"on\" to a meaning HAVEN can reason about.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private static Border WrapCard(StackPanel content) => new()
    {
        Padding = new Microsoft.UI.Xaml.Thickness(12),
        CornerRadius = new Microsoft.UI.Xaml.CornerRadius(8),
        BorderThickness = new Microsoft.UI.Xaml.Thickness(1),
        BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
        Child = content,
    };

    private async Task RunPeopleMutationAsync(Func<Task<JsonElement>> operation)
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
                PeopleErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            PeopleErrorText.Text = "";
            await LoadPeopleAsync();
        }
        catch (Exception ex)
        {
            PeopleErrorText.Text = ex.Message;
        }
    }

    private async void OnAddPersonClicked(object sender, RoutedEventArgs args)
    {
        await EditPersonAsync(default);
    }

    private async Task EditPersonAsync(JsonElement person)
    {
        if (_client is null)
        {
            return;
        }
        var editing = person.ValueKind == JsonValueKind.Object;
        var personId = editing ? GetString(person, "person_id") ?? "" : "";
        var name = new TextBox { PlaceholderText = "Name", Text = editing ? GetString(person, "name") ?? "" : "" };
        var role = new TextBox
        {
            PlaceholderText = "Role (owner or member)",
            Text = editing ? GetString(person, "role") ?? "" : "",
        };
        // The web PATCH for a person only edits name and role; entity/room
        // presence sources are declared at add time.
        StackPanel fields;
        TextBox? entityId = null;
        TextBox? roomId = null;
        if (editing)
        {
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
        }
        else
        {
            entityId = new TextBox { PlaceholderText = "Presence entity (optional)" };
            roomId = new TextBox { PlaceholderText = "Room (required with entity)" };
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
            fields.Children.Add(entityId);
            fields.Children.Add(roomId);
        }
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit person" : "Add person",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        var roleValue = string.IsNullOrWhiteSpace(role.Text) ? null : role.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdatePersonAsync(personId, name.Text.Trim(), roleValue));
        }
        else
        {
            var entityValue = string.IsNullOrWhiteSpace(entityId!.Text) ? null : entityId.Text.Trim();
            var roomValue = string.IsNullOrWhiteSpace(roomId!.Text) ? null : roomId.Text.Trim();
            await RunPeopleMutationAsync(() => _client.AddPersonAsync(name.Text.Trim(), roleValue ?? "member", entityValue, roomValue));
        }
    }

    private async Task RemovePersonAsync(string personId, string name)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {name}?",
            Content = new TextBlock
            {
                Text = "HAVEN will forget the declaration, including any presence sources attached to it.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPeopleMutationAsync(() => _client.RemovePersonAsync(personId));
    }

    private async void OnAddContextClicked(object sender, RoutedEventArgs args)
    {
        await EditContextAsync(default);
    }

    private async Task EditContextAsync(JsonElement context)
    {
        if (_client is null)
        {
            return;
        }
        var editing = context.ValueKind == JsonValueKind.Object;
        var contextId = editing ? GetString(context, "context_id") ?? "" : "";
        var label = new TextBox { PlaceholderText = "Label", Text = editing ? GetString(context, "label") ?? "" : "" };
        var entity = new TextBox
        {
            PlaceholderText = "Entity (e.g. input_boolean.working_late)",
            Text = editing ? GetString(context, "entity_id") ?? "" : "",
        };
        var fields = new StackPanel { Spacing = 8, MinWidth = 320 };
        fields.Children.Add(label);
        fields.Children.Add(entity);
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit context" : "Add context",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(label.Text))
        {
            return;
        }
        var entityValue = string.IsNullOrWhiteSpace(entity.Text) ? null : entity.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdateContextAsync(contextId, label.Text.Trim(), entityValue));
        }
        else
        {
            await RunPeopleMutationAsync(() => _client.AddContextAsync(label.Text.Trim(), entityValue ?? ""));
        }
    }

    private async Task RemoveContextAsync(string contextId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Remove this context?",
            Content = new TextBlock
            {
                Text = "HAVEN will stop reasoning about this context until it is declared again.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPeopleMutationAsync(() => _client.RemoveContextAsync(contextId));
    }

    // -- automations --------------------------------------------------------

    private static readonly string[] WeekdayNames = { "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" };

    private JsonElement _automations = default;
    private JsonElement _scheduler = default;
    private JsonElement _automationOptions = default;

    private async void OnAutomationsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadAutomationsAsync();
    }

    private async Task LoadAutomationsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetAutomationsAsync();
            var options = await _client.GetAutomationOptionsAsync();
            _automations = result.GetProperty("automations").Clone();
            _scheduler = result.GetProperty("scheduler").Clone();
            _automationOptions = options.GetProperty("options").Clone();
            AutomationsErrorText.Text = "";
            RenderAutomations();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

    private void RenderAutomations()
    {
        AutomationsList.Children.Clear();
        foreach (var rule in Enumerate(_automations))
        {
            AutomationsList.Children.Add(MakeAutomationCard(rule));
        }
        if (AutomationsList.Children.Count == 0)
        {
            AutomationsList.Children.Add(new TextBlock
            {
                Text = "No automations yet. Propose one and approve it to put the scheduler to work.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeAutomationCard(JsonElement rule)
    {
        var ruleId = GetString(rule, "rule_id") ?? "";
        var status = GetString(rule, "status") ?? "unknown";

        var card = new StackPanel { Spacing = 4 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = GetString(rule, "action") ?? "automation",
            Opacity = 0.72,
        });
        var badge = new TextBlock
        {
            Text = status.ToUpperInvariant(),
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        };
        var badgeBrush = status switch
        {
            "approved" => (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["SystemFillColorSuccessBrush"],
            "proposed" => (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["SystemFillColorCautionBrush"],
            _ => null,
        };
        if (badgeBrush is not null)
        {
            badge.Foreground = badgeBrush;
        }
        head.Children.Add(badge);
        var approvedAt = GetString(rule, "approved_at");
        if (approvedAt is not null)
        {
            head.Children.Add(new TextBlock { Text = approvedAt, Opacity = 0.6 });
        }
        card.Children.Add(head);

        card.Children.Add(new TextBlock
        {
            Text = GetString(rule, "summary") ?? ruleId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        var target = GetString(rule, "target");
        var schedule = rule.TryGetProperty("schedule", out var scheduleValue) && scheduleValue.ValueKind == JsonValueKind.Object
            ? scheduleValue
            : default;
        var details = new List<string>();
        if (target is not null)
        {
            details.Add(target);
        }
        if (schedule.ValueKind == JsonValueKind.Object)
        {
            var time = GetString(schedule, "time_of_day");
            var days = Enumerate(schedule, "weekdays")
                .Select(day => day.ValueKind == JsonValueKind.Number ? day.GetInt32() : -1)
                .Where(day => day >= 0 && day < WeekdayNames.Length)
                .Select(day => WeekdayNames[day])
                .ToList();
            var when = time ?? "?";
            if (schedule.TryGetProperty("weekdays", out var daysValue) && daysValue.ValueKind == JsonValueKind.Array && daysValue.GetArrayLength() > 0)
            {
                when += " · " + string.Join("/", days);
            }
            else
            {
                when += " · every day";
            }
            details.Add(when);
        }
        var schedulerRow = Enumerate(_scheduler).FirstOrDefault(
            row => GetString(row, "rule_id") == ruleId);
        if (status == "approved" && schedulerRow.ValueKind == JsonValueKind.Object)
        {
            var enabled = !schedulerRow.TryGetProperty("enabled", out var enabledValue) || enabledValue.GetBoolean();
            details.Add(enabled ? "Scheduling enabled" : "Scheduling disabled");
        }
        if (details.Count > 0)
        {
            card.Children.Add(new TextBlock
            {
                Text = string.Join("  ·  ", details),
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        if (status == "proposed")
        {
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditAutomationAsync(rule);
            var approve = new Button { Content = "Approve" };
            approve.Click += async (_, _) => await ApproveAutomationAsync(ruleId);
            var discard = new Button { Content = "Discard" };
            discard.Click += async (_, _) => await RevokeAutomationAsync(ruleId, "Discard");
            buttons.Children.Add(edit);
            buttons.Children.Add(approve);
            buttons.Children.Add(discard);
        }
        else if (status == "approved")
        {
            var enabled = schedulerRow.ValueKind != JsonValueKind.Object
                || !schedulerRow.TryGetProperty("enabled", out var value)
                || value.GetBoolean();
            var toggle = new Button { Content = enabled ? "Disable" : "Enable" };
            toggle.Click += async (_, _) => await SetAutomationEnabledAsync(ruleId, !enabled);
            var revoke = new Button { Content = "Revoke" };
            revoke.Click += async (_, _) => await RevokeAutomationAsync(ruleId, "Revoke");
            buttons.Children.Add(toggle);
            buttons.Children.Add(revoke);
        }
        if (buttons.Children.Count > 0)
        {
            card.Children.Add(buttons);
        }

        return WrapCard(card);
    }

    private async Task RunAutomationMutationAsync(Func<Task<JsonElement>> operation)
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
                var message = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString()
                    : null;
                if (message is null
                    && envelope.TryGetProperty("decision", out var decision)
                    && decision.ValueKind == JsonValueKind.Object
                    && decision.TryGetProperty("explanation", out var explanation)
                    && explanation.ValueKind == JsonValueKind.String)
                {
                    message = explanation.GetString();
                }
                AutomationsErrorText.Text = message ?? "HAVEN Core refused the change.";
                return;
            }
            AutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

    private static StackPanel MakeWeekdayPicker(IReadOnlyList<int> selected)
    {
        var days = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        for (var index = 0; index < WeekdayNames.Length; index += 1)
        {
            days.Children.Add(new CheckBox
            {
                Content = WeekdayNames[index],
                Tag = index,
                IsChecked = selected.Contains(index),
            });
        }
        return days;
    }

    private static List<int> SelectedWeekdays(StackPanel picker) =>
        picker.Children
            .OfType<CheckBox>()
            .Where(box => box.IsChecked == true)
            .Select(box => (int)box.Tag!)
            .ToList();

    private async void OnProposeAutomationClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var options = Enumerate(_automationOptions).ToList();
        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        if (options.Count == 0)
        {
            fields.Children.Add(new TextBlock
            {
                Text = "No writable device capabilities are available yet. Enroll a device during setup or connections first.",
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.72,
            });
        }
        var source = new TextBox { PlaceholderText = "e.g. Turn off the office light", AcceptsReturn = false };
        var time = new TextBox { PlaceholderText = "HH:MM", Text = "22:00" };
        var weekdays = MakeWeekdayPicker(Array.Empty<int>());
        fields.Children.Add(new TextBlock { Text = "What should HAVEN do?" });
        fields.Children.Add(source);
        fields.Children.Add(new TextBlock { Text = "At" });
        fields.Children.Add(time);
        fields.Children.Add(new TextBlock { Text = "Days (leave all unchecked for every day)" });
        fields.Children.Add(weekdays);
        ComboBox? device = null;
        if (options.Count > 0)
        {
            device = new ComboBox { ItemsSource = options.Select(option =>
                $"{(GetString(option, "room") is { Length: > 0 } room ? room + " · " : "")}{GetString(option, "device_id")} · {GetString(option, "capability")}").ToList() };
            device.SelectedIndex = 0;
            fields.Children.Add(new TextBlock { Text = "Device & control" });
            fields.Children.Add(device);
        }
        var note = new TextBlock
        {
            Text = "HAVEN will propose this rule first. An owner must approve it before the scheduler can act.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        };
        fields.Children.Add(note);
        var dialog = new ContentDialog
        {
            Title = "Propose automation",
            Content = fields,
            PrimaryButtonText = "Propose",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(source.Text))
        {
            return;
        }
        if (options.Count == 0 || device is null)
        {
            AutomationsErrorText.Text = "Choose a writable device capability first.";
            return;
        }
        var chosen = options[device.SelectedIndex];
        await RunAutomationMutationAsync(() => _client.CreateAutomationAsync(
            source.Text.Trim(),
            time.Text.Trim(),
            SelectedWeekdays(weekdays),
            GetString(chosen, "device_id") ?? "",
            GetString(chosen, "capability") ?? "",
            GetString(chosen, "service") ?? "",
            source.Text.Trim()));
    }

    private async Task EditAutomationAsync(JsonElement rule)
    {
        if (_client is null)
        {
            return;
        }
        var ruleId = GetString(rule, "rule_id") ?? "";
        var schedule = rule.TryGetProperty("schedule", out var scheduleValue) && scheduleValue.ValueKind == JsonValueKind.Object
            ? scheduleValue
            : default;
        var selectedDays = schedule.ValueKind == JsonValueKind.Object
            ? Enumerate(schedule, "weekdays")
                .Select(day => day.ValueKind == JsonValueKind.Number ? day.GetInt32() : -1)
                .Where(day => day >= 0 && day < WeekdayNames.Length)
                .ToList()
            : new List<int>();

        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        var source = new TextBox { PlaceholderText = "What should HAVEN do?", Text = GetString(rule, "summary") ?? "" };
        var currentTime = GetString(schedule, "time_of_day") ?? "22:00";
        var time = new TextBox
        {
            PlaceholderText = "HH:MM",
            Text = currentTime.Length > 5 ? currentTime[..5] : currentTime,
        };
        var weekdays = MakeWeekdayPicker(selectedDays);
        var justification = new TextBox { PlaceholderText = "Why is this change correct?" };
        fields.Children.Add(new TextBlock { Text = "What should HAVEN do?" });
        fields.Children.Add(source);
        fields.Children.Add(new TextBlock { Text = "At" });
        fields.Children.Add(time);
        fields.Children.Add(new TextBlock { Text = "Days (leave all unchecked for every day)" });
        fields.Children.Add(weekdays);
        fields.Children.Add(new TextBlock { Text = "Justification" });
        fields.Children.Add(justification);
        fields.Children.Add(new TextBlock
        {
            Text = "This proposal is not approved yet. Editing creates a new audited draft; approved automations stay immutable.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Edit proposed automation",
            Content = fields,
            PrimaryButtonText = "Save proposal",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(source.Text))
        {
            return;
        }
        var justificationValue = string.IsNullOrWhiteSpace(justification.Text) ? null : justification.Text.Trim();
        await RunAutomationMutationAsync(() => _client.UpdateAutomationAsync(
            ruleId,
            source.Text.Trim(),
            time.Text.Trim(),
            SelectedWeekdays(weekdays),
            source.Text.Trim(),
            justificationValue));
    }

    private async Task ApproveAutomationAsync(string ruleId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Approve this automation?",
            Content = new TextBlock
            {
                Text = "Approval attaches your authority to exactly this draft; editing afterwards requires revoking and proposing anew.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Approve",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunAutomationMutationAsync(() => _client.ApproveAutomationAsync(ruleId));
    }

    private async Task RevokeAutomationAsync(string ruleId, string verb)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"{verb} this automation?",
            Content = new TextBlock
            {
                Text = "The rule stays in the ledger as revoked, with the revocation audited.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = verb,
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunAutomationMutationAsync(() => _client.RevokeAutomationAsync(
            ruleId, $"owner revoked automation from HAVEN ({verb.ToLowerInvariant()})"));
    }

    private async Task SetAutomationEnabledAsync(string ruleId, bool enabled)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            await _client.SetAutomationEnabledAsync(ruleId, enabled);
            AutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

    /* State line per role, mirroring the web surface's deviceStateLine:
       is_on proxies the cover position; devices without an on/off concept
       render "—". */
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

    private static IEnumerable<JsonElement> Enumerate(JsonElement array, string property = "")
    {
        var source = array;
        if (property.Length > 0)
        {
            if (array.ValueKind != JsonValueKind.Object || !array.TryGetProperty(property, out var nested))
            {
                yield break;
            }
            source = nested;
        }
        if (source.ValueKind != JsonValueKind.Array)
        {
            yield break;
        }
        foreach (var item in source.EnumerateArray())
        {
            yield return item;
        }
    }

    private static IReadOnlyList<string> GetPeople(JsonElement room) =>
        Enumerate(room, "people")
            .Select(person => person.ValueKind == JsonValueKind.String ? person.GetString() ?? "" : "")
            .Where(name => name.Length > 0)
            .ToList();

    private static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static double? GetDouble(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : null;
}
