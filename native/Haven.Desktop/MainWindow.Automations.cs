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
            AutomationsStatusText.Text = "";
            RenderAutomations();
        }
        catch (Exception ex)
        {
            AutomationsStatusText.Text = "";
            AutomationsErrorText.Text = $"Could not load automations: {ex.Message} Use Refresh to retry.";
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
        var (chipTint, chipForeground) = status switch
        {
            "approved" => ("HavenSuccessTintBrush", "HavenSuccessBrush"),
            "proposed" => ("HavenWarningTintBrush", "HavenWarningBrush"),
            "revoked" => ("HavenStrokeBrush", "HavenMutedTextBrush"),
            _ => ("HavenAccentTintBrush", "HavenMutedTextBrush"),
        };
        head.Children.Add(MakeChip(Sentence(status), chipTint, chipForeground));
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

}
