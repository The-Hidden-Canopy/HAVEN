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
    private JsonElement _resourceAutomations = default;
    private JsonElement _resourceScheduler = default;
    private JsonElement _resourceAutomationOptions = default;

    private async void OnAutomationsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadAutomationsAsync();
    }

    private async void OnResourceAutomationsRefreshClicked(object sender, RoutedEventArgs args)
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
            var resourceOptions = await _client.GetResourceAutomationOptionsAsync();
            var resourceResult = await _client.GetResourceAutomationsAsync();
            _automations = result.GetProperty("automations").Clone();
            _scheduler = result.GetProperty("scheduler").Clone();
            _automationOptions = options.GetProperty("options").Clone();
            _resourceAutomationOptions = resourceOptions.GetProperty("options").Clone();
            _resourceAutomations = resourceResult.GetProperty("automations").Clone();
            _resourceScheduler = resourceResult.GetProperty("scheduler").Clone();
            AutomationsErrorText.Text = "";
            AutomationsStatusText.Text = "";
            ResourceAutomationsErrorText.Text = "";
            ResourceAutomationsStatusText.Text = "";
            RenderAutomations();
            RenderResourceAutomations();
        }
        catch (Exception ex)
        {
            AutomationsStatusText.Text = "";
            AutomationsErrorText.Text = $"Could not load automations: {ex.Message} Use Refresh to retry.";
            ResourceAutomationsStatusText.Text = "";
            ResourceAutomationsErrorText.Text = $"Could not load cross-domain automations: {ex.Message} Use Refresh to retry.";
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
            AutomationsList.Children.Add(MakeEmptyState(
                "No automations yet. Connect a device or propose a rule to get started.",
                "Discover devices",
                () =>
                {
                    SelectNavigation("home");
                    SelectHomeTab("discover");
                    return Task.CompletedTask;
                }));
        }
    }

    private void RenderResourceAutomations()
    {
        ResourceAutomationsList.Children.Clear();
        foreach (var rule in Enumerate(_resourceAutomations))
        {
            ResourceAutomationsList.Children.Add(MakeResourceAutomationCard(rule));
        }
        if (ResourceAutomationsList.Children.Count == 0)
        {
            ResourceAutomationsList.Children.Add(new TextBlock
            {
                Text = "No cross-domain resource automations yet.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
        ResourceAutomationsStatusText.Text = $"{ResourceAutomationsList.Children.Count} durable resource automation(s)";
    }

    private async void OnResourceAutomationAddClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || _resourceAutomationOptions.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        var fields = new StackPanel { Spacing = 8, MinWidth = 420 };
        var source = new TextBox
        {
            PlaceholderText = "e.g. When the project deadline arrives, open the brief",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
        };
        fields.Children.Add(new TextBlock { Text = "What should this rule mean?" });
        fields.Children.Add(source);

        var triggerKinds = Enumerate(_resourceAutomationOptions, "trigger_kinds")
            .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() : null)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Cast<string>()
            .ToList();
        var trigger = new ComboBox { ItemsSource = triggerKinds };
        trigger.SelectedItem = triggerKinds.Contains("time") ? "time" : triggerKinds.FirstOrDefault();
        fields.Children.Add(new TextBlock { Text = "Trigger family" });
        fields.Children.Add(trigger);

        var time = new TextBox { Text = "09:00", PlaceholderText = "HH:MM" };
        var weekdays = new TextBox { PlaceholderText = "0,1,2,3,4 (blank = every day)" };
        var eventName = new TextBox { PlaceholderText = "e.g. task.status_changed" };
        var deadlineKind = new TextBox { PlaceholderText = "e.g. task or calendar" };
        var deadlineId = new TextBox { PlaceholderText = "optional exact source id" };
        var deadlineOffset = new TextBox { Text = "0", PlaceholderText = "minutes" };
        var evidenceKind = new TextBox { PlaceholderText = "presence, context, or device" };
        var evidenceConfidence = new TextBox { Text = "0", PlaceholderText = "0 through 1" };
        var externalProvider = new TextBox { PlaceholderText = "e.g. home_assistant" };
        var externalCondition = new TextBox { PlaceholderText = "e.g. reachable" };
        var externalValue = new TextBox { Text = "true", PlaceholderText = "JSON value" };
        fields.Children.Add(new TextBlock { Text = "Time (HH:MM)" });
        fields.Children.Add(time);
        fields.Children.Add(new TextBlock { Text = "Weekdays (0=Mon, comma-separated; blank = every day)" });
        fields.Children.Add(weekdays);
        fields.Children.Add(new TextBlock { Text = "Event name (for event triggers)" });
        fields.Children.Add(eventName);
        fields.Children.Add(new TextBlock { Text = "Deadline source kind / id / offset minutes" });
        fields.Children.Add(deadlineKind);
        fields.Children.Add(deadlineId);
        fields.Children.Add(deadlineOffset);
        fields.Children.Add(new TextBlock { Text = "Evidence kind / minimum confidence (for evidence triggers)" });
        fields.Children.Add(evidenceKind);
        fields.Children.Add(evidenceConfidence);
        fields.Children.Add(new TextBlock { Text = "Provider id / condition / value (for external-condition triggers)" });
        fields.Children.Add(externalProvider);
        fields.Children.Add(externalCondition);
        fields.Children.Add(externalValue);

        var selector = new TextBox
        {
            Text = "{}",
            AcceptsReturn = true,
            PlaceholderText = "Optional selector JSON object",
        };
        fields.Children.Add(new TextBlock { Text = "Selector filters (JSON object, optional)" });
        fields.Children.Add(selector);

        var actionOptions = Enumerate(_resourceAutomationOptions, "actions").ToList();
        var domains = actionOptions.Select(item => GetString(item, "domain"))
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Cast<string>()
            .ToList();
        var domain = new ComboBox { ItemsSource = domains };
        domain.SelectedIndex = 0;
        var action = new ComboBox();
        void RefreshActionChoices()
        {
            var chosenDomain = domain.SelectedItem as string;
            var chosen = actionOptions.FirstOrDefault(item => GetString(item, "domain") == chosenDomain);
            var actions = Enumerate(chosen, "actions")
                .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() : null)
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Cast<string>()
                .ToList();
            action.ItemsSource = actions;
            action.SelectedIndex = actions.Count > 0 ? 0 : -1;
        }
        domain.SelectionChanged += (_, _) => RefreshActionChoices();
        RefreshActionChoices();
        fields.Children.Add(new TextBlock { Text = "Action domain / action" });
        fields.Children.Add(domain);
        fields.Children.Add(action);

        var consequences = Enumerate(_resourceAutomationOptions, "consequence_classes")
            .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() : null)
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Cast<string>()
            .ToList();
        var consequence = new ComboBox { ItemsSource = consequences };
        consequence.SelectedItem = consequences.Contains("reversible_local")
            ? "reversible_local" : consequences.FirstOrDefault();
        fields.Children.Add(new TextBlock { Text = "Consequence class" });
        fields.Children.Add(consequence);

        var resourceId = new TextBox { PlaceholderText = "optional current resource id" };
        var parameters = new TextBox
        {
            Text = "{}",
            AcceptsReturn = true,
            PlaceholderText = "Optional action parameters JSON object",
        };
        fields.Children.Add(new TextBlock { Text = "Resource id (optional)" });
        fields.Children.Add(resourceId);
        fields.Children.Add(new TextBlock { Text = "Action parameters (JSON object, optional)" });
        fields.Children.Add(parameters);
        fields.Children.Add(new TextBlock
        {
            Text = "Creation only proposes the rule. An owner must approve it before the scheduler can act; confirmation-required actions remain blocked when unattended.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });

        var dialog = new ContentDialog
        {
            Title = "Propose resource automation",
            Content = fields,
            PrimaryButtonText = "Propose",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(source.Text))
        {
            return;
        }

        try
        {
            var triggerKind = trigger.SelectedItem as string;
            var chosenDomain = domain.SelectedItem as string;
            var chosenAction = action.SelectedItem as string;
            var chosenConsequence = consequence.SelectedItem as string;
            if (string.IsNullOrWhiteSpace(triggerKind) || string.IsNullOrWhiteSpace(chosenDomain)
                || string.IsNullOrWhiteSpace(chosenAction) || string.IsNullOrWhiteSpace(chosenConsequence))
            {
                throw new InvalidOperationException("Choose a trigger, action, and consequence class.");
            }

            var triggerParameters = new List<object[]>();
            if (triggerKind == "time")
            {
                var selectedDays = ParseResourceAutomationWeekdays(weekdays.Text);
                triggerParameters.Add(new object[] { "time_of_day", time.Text.Trim() });
                triggerParameters.Add(new object[] { "weekdays", selectedDays });
                triggerParameters.Add(new object[] { "window_minutes", 5 });
            }
            else if (triggerKind == "event")
            {
                if (string.IsNullOrWhiteSpace(eventName.Text))
                {
                    throw new InvalidOperationException("An event name is required for an event trigger.");
                }
                triggerParameters.Add(new object[] { "event_name", eventName.Text.Trim() });
            }
            else if (triggerKind == "deadline")
            {
                if (!string.IsNullOrWhiteSpace(deadlineKind.Text))
                {
                    triggerParameters.Add(new object[] { "source_kind", deadlineKind.Text.Trim() });
                }
                if (!string.IsNullOrWhiteSpace(deadlineId.Text))
                {
                    triggerParameters.Add(new object[] { "source_id", deadlineId.Text.Trim() });
                }
                if (!double.TryParse(deadlineOffset.Text.Trim(), out var offset))
                {
                    throw new InvalidOperationException("Deadline offset must be a number of minutes.");
                }
                triggerParameters.Add(new object[] { "offset_minutes", offset });
            }
            else if (triggerKind == "evidence")
            {
                if (string.IsNullOrWhiteSpace(evidenceKind.Text))
                {
                    throw new InvalidOperationException("An evidence kind is required for an evidence trigger.");
                }
                if (!double.TryParse(evidenceConfidence.Text.Trim(), out var confidence)
                    || confidence < 0 || confidence > 1)
                {
                    throw new InvalidOperationException("Minimum evidence confidence must be between 0 and 1.");
                }
                triggerParameters.Add(new object[] { "evidence_kind", evidenceKind.Text.Trim() });
                triggerParameters.Add(new object[] { "min_confidence", confidence });
            }
            else if (triggerKind == "external_condition")
            {
                if (string.IsNullOrWhiteSpace(externalProvider.Text)
                    || string.IsNullOrWhiteSpace(externalCondition.Text))
                {
                    throw new InvalidOperationException("Provider id and condition are required for an external-condition trigger.");
                }
                using var valueDocument = JsonDocument.Parse(
                    string.IsNullOrWhiteSpace(externalValue.Text) ? "null" : externalValue.Text.Trim());
                triggerParameters.Add(new object[] { "provider_id", externalProvider.Text.Trim() });
                triggerParameters.Add(new object[] { "condition", externalCondition.Text.Trim() });
                triggerParameters.Add(new object[] { "value", valueDocument.RootElement.Clone() });
            }

            var selectorParameters = ParseResourceAutomationObject(selector.Text, "Selector");
            var actionParameters = ParseResourceAutomationObject(parameters.Text, "Action parameters");
            if (!string.IsNullOrWhiteSpace(resourceId.Text))
            {
                actionParameters["resource_id"] = JsonSerializer.SerializeToElement(resourceId.Text.Trim());
            }
            var specId = $"native-{Guid.NewGuid():N}";
            await RunResourceAutomationMutationAsync(() => _client.CreateResourceAutomationAsync(
                $"resource-{specId}",
                specId,
                GetString(_resourceAutomationOptions, "household_id") ?? "",
                GetString(_resourceAutomationOptions, "created_by") ?? "",
                source.Text.Trim(),
                triggerKind,
                triggerParameters,
                ToParameterPairs(selectorParameters),
                chosenDomain,
                chosenAction,
                chosenConsequence,
                ToParameterPairs(actionParameters)));
        }
        catch (Exception ex)
        {
            ResourceAutomationsErrorText.Text = ex.Message;
        }
    }

    private static List<int> ParseResourceAutomationWeekdays(string text)
    {
        var values = text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var days = new List<int>();
        foreach (var value in values)
        {
            if (!int.TryParse(value, out var day) || day < 0 || day > 6)
            {
                throw new InvalidOperationException("Weekdays must be integers from 0 through 6.");
            }
            days.Add(day);
        }
        return days;
    }

    private static Dictionary<string, JsonElement> ParseResourceAutomationObject(string text, string label)
    {
        using var document = JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text);
        if (document.RootElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException($"{label} must be a JSON object.");
        }
        return document.RootElement.EnumerateObject()
            .ToDictionary(item => item.Name, item => item.Value.Clone());
    }

    private static object[] ToParameterPairs(Dictionary<string, JsonElement> values) =>
        values.Select(item => (object)new object[] { item.Key, item.Value }).ToArray();

    private Border MakeResourceAutomationCard(JsonElement rule)
    {
        var ruleId = GetString(rule, "rule_id") ?? "";
        var status = GetString(rule, "status") ?? "unknown";
        var enabled = rule.ValueKind == JsonValueKind.Object
            && rule.TryGetProperty("enabled", out var enabledValue)
            && enabledValue.ValueKind == JsonValueKind.True;
        var spec = rule.ValueKind == JsonValueKind.Object
            && rule.TryGetProperty("spec", out var specValue)
            ? specValue
            : default;
        var action = spec.ValueKind == JsonValueKind.Object
            && spec.TryGetProperty("action", out var actionValue)
            ? actionValue
            : default;
        var trigger = spec.ValueKind == JsonValueKind.Object
            && spec.TryGetProperty("trigger", out var triggerValue)
            ? triggerValue
            : default;
        var schedulerRow = Enumerate(_resourceScheduler).FirstOrDefault(row => GetString(row, "rule_id") == ruleId);

        var card = new StackPanel { Spacing = 4 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = $"{GetString(action, "domain") ?? "resource"}.{GetString(action, "action") ?? "action"}",
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
        if (status == "approved")
        {
            head.Children.Add(MakeStatusPill(enabled ? "Enabled" : "Paused", enabled));
        }
        card.Children.Add(head);
        card.Children.Add(new TextBlock
        {
            Text = GetString(spec, "source_text") ?? ruleId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        var details = new List<string>
        {
            $"Trigger: {GetString(trigger, "kind") ?? "unknown"}",
        };
        var lastOutcome = GetString(schedulerRow, "last_outcome");
        if (lastOutcome is not null)
        {
            details.Add($"Last outcome: {lastOutcome}");
        }
        card.Children.Add(new TextBlock
        {
            Text = string.Join("  ·  ", details),
            Opacity = 0.72,
            TextWrapping = TextWrapping.Wrap,
        });

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        if (status == "proposed")
        {
            var approve = new Button { Content = "Approve" };
            approve.Click += async (_, _) => await ApproveResourceAutomationAsync(ruleId);
            buttons.Children.Add(approve);
        }
        if (status == "approved")
        {
            var toggle = new Button { Content = enabled ? "Pause" : "Enable" };
            toggle.Click += async (_, _) => await SetResourceAutomationEnabledAsync(ruleId, !enabled);
            buttons.Children.Add(toggle);
        }
        if (status == "proposed" || status == "approved")
        {
            var revoke = new Button { Content = "Revoke" };
            revoke.Click += async (_, _) => await RevokeResourceAutomationAsync(ruleId);
            buttons.Children.Add(revoke);
        }
        if (buttons.Children.Count > 0)
        {
            card.Children.Add(buttons);
        }
        return WrapCard(card);
    }

    private async Task RunResourceAutomationMutationAsync(Func<Task<JsonElement>> operation)
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
                ResourceAutomationsErrorText.Text = GetString(envelope, "error")
                    ?? GetString(envelope, "reason")
                    ?? "HAVEN Core refused the resource automation change.";
                return;
            }
            ResourceAutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            ResourceAutomationsErrorText.Text = ex.Message;
        }
    }

    private async Task ApproveResourceAutomationAsync(string ruleId)
    {
        var justification = await PromptForJustificationAsync(
            "Approve this resource automation?",
            "Approval attaches owner authority to this cross-domain action rule.",
            "Why should this resource automation run?",
            "Approve");
        if (justification is null || _client is null)
        {
            return;
        }
        await RunResourceAutomationMutationAsync(() =>
            _client.ApproveResourceAutomationAsync(ruleId, justification));
    }

    private async Task RevokeResourceAutomationAsync(string ruleId)
    {
        var justification = await PromptForJustificationAsync(
            "Revoke this resource automation?",
            "The rule remains in the durable audit history as revoked.",
            "Why should this resource automation be revoked?",
            "Revoke");
        if (justification is null || _client is null)
        {
            return;
        }
        await RunResourceAutomationMutationAsync(() =>
            _client.RevokeResourceAutomationAsync(ruleId, justification));
    }

    private async Task SetResourceAutomationEnabledAsync(string ruleId, bool enabled)
    {
        var justification = await PromptForJustificationAsync(
            enabled ? "Enable this resource automation?" : "Pause this resource automation?",
            enabled
                ? "The approved rule may run when its trigger is due."
                : "Pausing preserves the approved rule but prevents scheduled dispatch.",
            enabled ? "Why should this resource automation resume?" : "Why should this resource automation pause?",
            enabled ? "Enable" : "Pause");
        if (justification is null || _client is null)
        {
            return;
        }
        await RunResourceAutomationMutationAsync(() =>
            _client.SetResourceAutomationEnabledAsync(ruleId, enabled, justification));
    }

    private Border MakeAutomationCard(JsonElement rule)
    {
        var ruleId = GetString(rule, "rule_id") ?? "";
        var status = GetString(rule, "status") ?? "unknown";
        var schedulerRow = Enumerate(_scheduler).FirstOrDefault(row => GetString(row, "rule_id") == ruleId);
        var schedulingEnabled = schedulerRow.ValueKind != JsonValueKind.Object
            || !schedulerRow.TryGetProperty("enabled", out var schedulingEnabledValue)
            || schedulingEnabledValue.GetBoolean();

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
        // Approval and scheduling are two independent questions ("is this
        // allowed to run" vs. "is it currently running on its schedule") --
        // a second pill instead of folding "Scheduling enabled/disabled"
        // into the plain-text details line below, the same "don't flatten
        // independent statuses into one label" pattern Discover's
        // Seen/Identified/Provider/Enrolled pills use.
        if (status == "approved" && schedulerRow.ValueKind == JsonValueKind.Object)
        {
            head.Children.Add(MakeStatusPill(schedulingEnabled ? "Scheduled" : "Paused", schedulingEnabled));
        }
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
        // Scheduling enabled/disabled is now the "Scheduled"/"Paused" pill
        // above, not repeated here as plain text.
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
            var toggle = new Button { Content = schedulingEnabled ? "Disable" : "Enable" };
            toggle.Click += async (_, _) => await SetAutomationEnabledAsync(ruleId, !schedulingEnabled);
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
        if (options.Count == 0)
        {
            var connectDialog = new ContentDialog
            {
                Title = "Connect a device first",
                Content = new TextBlock
                {
                    Text = "HAVEN needs a writable device capability before it can create an automation. Discover and enroll a device from Home, then come back here to propose the rule.",
                    TextWrapping = TextWrapping.Wrap,
                },
                PrimaryButtonText = "Open device discovery",
                CloseButtonText = "Cancel",
                XamlRoot = RootGrid().XamlRoot,
            };
            if (await connectDialog.ShowAsync() == ContentDialogResult.Primary)
            {
                SelectNavigation("home");
                SelectHomeTab("discover");
            }
            return;
        }

        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
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
        var justification = new TextBox { PlaceholderText = "Why should this automation run?" };
        var content = new StackPanel { Spacing = 8 };
        content.Children.Add(new TextBlock
        {
            Text = "Approval attaches your authority to exactly this draft; editing afterwards requires revoking and proposing anew.",
            TextWrapping = TextWrapping.Wrap,
        });
        content.Children.Add(new TextBlock { Text = "Justification" });
        content.Children.Add(justification);
        var dialog = new ContentDialog
        {
            Title = "Approve this automation?",
            Content = content,
            PrimaryButtonText = "Approve",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        if (string.IsNullOrWhiteSpace(justification.Text))
        {
            AutomationsErrorText.Text = "A justification is required.";
            return;
        }
        await RunAutomationMutationAsync(() => _client.ApproveAutomationAsync(ruleId, justification.Text.Trim()));
    }

    private async Task RevokeAutomationAsync(string ruleId, string verb)
    {
        if (_client is null)
        {
            return;
        }
        var justification = new TextBox { PlaceholderText = $"Why should this automation be {verb.ToLowerInvariant()}d?" };
        var content = new StackPanel { Spacing = 8 };
        content.Children.Add(new TextBlock
        {
            Text = "The rule stays in the ledger as revoked, with the revocation audited.",
            TextWrapping = TextWrapping.Wrap,
        });
        content.Children.Add(new TextBlock { Text = "Justification" });
        content.Children.Add(justification);
        var dialog = new ContentDialog
        {
            Title = $"{verb} this automation?",
            Content = content,
            PrimaryButtonText = verb,
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        if (string.IsNullOrWhiteSpace(justification.Text))
        {
            AutomationsErrorText.Text = "A justification is required.";
            return;
        }
        await RunAutomationMutationAsync(() => _client.RevokeAutomationAsync(ruleId, justification.Text.Trim()));
    }

    private async Task SetAutomationEnabledAsync(string ruleId, bool enabled)
    {
        if (_client is null)
        {
            return;
        }
        var justification = new TextBox
        {
            PlaceholderText = enabled ? "Why should this automation resume?" : "Why should this automation pause?",
        };
        var content = new StackPanel { Spacing = 8 };
        content.Children.Add(new TextBlock
        {
            Text = enabled
                ? "Resuming lets the approved automation run when its schedule is due."
                : "Pausing keeps the approved automation intact but prevents scheduled runs.",
            TextWrapping = TextWrapping.Wrap,
        });
        content.Children.Add(new TextBlock { Text = "Justification" });
        content.Children.Add(justification);
        var dialog = new ContentDialog
        {
            Title = enabled ? "Resume this automation?" : "Pause this automation?",
            Content = content,
            PrimaryButtonText = enabled ? "Resume" : "Pause",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        if (string.IsNullOrWhiteSpace(justification.Text))
        {
            AutomationsErrorText.Text = "A justification is required.";
            return;
        }
        try
        {
            await _client.SetAutomationEnabledAsync(ruleId, enabled, justification.Text.Trim());
            AutomationsErrorText.Text = "";
            await LoadAutomationsAsync();
        }
        catch (Exception ex)
        {
            AutomationsErrorText.Text = ex.Message;
        }
    }

}
