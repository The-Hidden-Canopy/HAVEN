using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    private StackPanel? _decisionRegister;
    private JsonElement _localProducts;
    partial void AddPrivatePluginCards(StackPanel section);
    partial void AddPrivateFeatureControls(string? id, StackPanel card);

    private static TextBlock WorkflowText(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };

    private Button WorkflowButton(string label, Func<Task> action)
    {
        var button = new Button { Content = label };
        button.Click += async (_, _) =>
        {
            button.IsEnabled = false;
            try { await action(); }
            catch (Exception ex) { await new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Action could not finish", Content = NativeFaults.Message(ex), CloseButtonText = "Close" }.ShowAsync(); }
            finally { button.IsEnabled = true; }
        };
        return button;
    }

    private static void WorkflowSuccess(JsonElement result)
    {
        if (result.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
            throw new NativeFaults.ReportedFaultException(result);
    }

    private async Task RenderDecisionRegisterAsync()
    {
        if (_client is null) return;
        _decisionRegister ??= new StackPanel { Spacing = 8 };
        if (!MemoryPanel.Children.Contains(_decisionRegister)) MemoryPanel.Children.Insert(1, _decisionRegister);
        _decisionRegister.Children.Clear();
        _decisionRegister.Children.Add(WorkflowText("Decision register"));
        try
        {
            var response = await _client.LocalWorkflowAsync("decisions.snapshot", new { });
            WorkflowSuccess(response);
            var snapshot = response.GetProperty("snapshot");
            var generation = snapshot.GetProperty("generation").GetInt32();
            _decisionRegister.Children.Add(WorkflowButton("Record a decision", async () =>
            {
                var problem = new TextBox { Header = "Decision to make" };
                var first = new TextBox { Header = "First option" };
                var second = new TextBox { Header = "Second option" };
                var reason = new TextBox { Header = "Reasoning", AcceptsReturn = true };
                var trigger = new TextBox { Header = "When should this be reconsidered?" };
                var form = new StackPanel { Spacing = 8 };
                foreach (var field in new[] { problem, first, second, reason, trigger }) form.Children.Add(field);
                var dialog = new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Record a decision", Content = form, PrimaryButtonText = "Save for review", CloseButtonText = "Cancel" };
                if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
                var result = await _client.LocalWorkflowAsync("decisions.propose", new
                {
                    expected_generation = generation, problem = problem.Text, rationale = reason.Text,
                    options = new[] { new[] { "first", first.Text }, new[] { "second", second.Text } },
                    reconsideration_triggers = string.IsNullOrWhiteSpace(trigger.Text) ? Array.Empty<string>() : new[] { trigger.Text }
                });
                WorkflowSuccess(result);
                await RenderDecisionRegisterAsync();
            }));
            foreach (var record in snapshot.GetProperty("decisions").EnumerateArray())
            {
                var revisions = record.GetProperty("revisions").EnumerateArray().ToList();
                var current = revisions[^1];
                var card = new StackPanel { Spacing = 6 };
                card.Children.Add(WorkflowText(GetString(current, "problem") ?? "Decision"));
                card.Children.Add(WorkflowText($"{GetString(current, "status")} · {revisions.Count} retained revisions"));
                card.Children.Add(WorkflowText(GetString(current, "rationale") ?? ""));
                if (GetString(current, "status") == "proposed")
                {
                    foreach (var option in current.GetProperty("options").EnumerateArray())
                    {
                        var optionId = option[0].GetString();
                        var decisionId = GetString(record, "decision_id");
                        var revision = current.GetProperty("revision").GetInt32();
                        card.Children.Add(WorkflowButton($"Accept: {option[1].GetString()}", async () =>
                        {
                            var rationale = new TextBox { Header = "Why is this the chosen direction?", AcceptsReturn = true };
                            if (await new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Review decision", Content = rationale, PrimaryButtonText = "Accept direction", CloseButtonText = "Cancel" }.ShowAsync() != ContentDialogResult.Primary) return;
                            var result = await _client.LocalWorkflowAsync("decisions.review", new { decision_id = decisionId, expected_generation = generation,
                                expected_revision = revision, status = "accepted", rationale = rationale.Text,
                                selected_option_id = optionId, review_ref = $"native-review:{Guid.NewGuid()}" });
                            WorkflowSuccess(result);
                            await RenderDecisionRegisterAsync();
                        }));
                    }
                }
                _decisionRegister.Children.Add(WrapCard(card));
            }
        }
        catch (Exception ex) { _decisionRegister.Children.Add(WorkflowText($"Decision register unavailable: {NativeFaults.Message(ex)}")); }
    }

    private async Task RenderBundledFeaturesAsync()
    {
        if (_client is null) return;
        var response = await _client.LocalWorkflowAsync("features.list", new { });
        WorkflowSuccess(response);
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(WorkflowText("Bundled feature modules"));
        foreach (var release in Enumerate(response, "features"))
        {
            var id = GetString(release, "feature_id");
            var version = GetString(release, "version");
            var card = new StackPanel { Spacing = 6 };
            card.Children.Add(WorkflowText($"{GetString(release, "display_name")} {version}"));
            var state = release.GetProperty("state");
            card.Children.Add(WorkflowText(GetString(state, "status") ?? "Available"));
            var currentVersion = state.TryGetProperty("committed", out var committed) && committed.ValueKind == JsonValueKind.True
                ? GetString(state, "version") : "not installed";
            card.Children.Add(WorkflowText($"Current: {currentVersion}; staged: {GetString(state, "pending_version") ?? "none"}"));
            var output = WorkflowText("");
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            foreach (var action in id is "haven.ghost-teacher" or "traceglass"
                ? new[] { "stage", "install", "enable", "disable", "update", "rollback", "uninstall" }
                : new[] { "stage", "install", "enable", "run", "disable", "update", "rollback", "uninstall" })
            {
                var selectedAction = action;
                actions.Children.Add(WorkflowButton(action == "run" ? "Inspect" : Sentence(action), async () =>
                {
                    var result = await _client.LocalWorkflowAsync("features.action", new { action = selectedAction, feature_id = id, version, command = "inspect", parameters = new { } });
                    WorkflowSuccess(result);
                    if (selectedAction == "run") output.Text = $"Inspector {GetString(result, "version")}\n" + JsonSerializer.Serialize(result.GetProperty("result"), new JsonSerializerOptions { WriteIndented = true });
                    else await RenderExtensionsAsync();
                }));
            }
            card.Children.Add(new ScrollViewer { Content = actions, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollMode = ScrollMode.Auto });
            card.Children.Add(output);
            AddPrivateFeatureControls(id,card);
            section.Children.Add(WrapCard(card));
        }
        AddPrivatePluginCards(section);
        section.Children.Add(SessionControls());
        ExtensionsHost.Children.Add(section);
    }

    private void RenderLocalProducts()
    {
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(WorkflowText("Local products"));
        if (_localProducts.ValueKind != JsonValueKind.Object) return;
        if (!_localProducts.TryGetProperty("products", out _))
        {
            section.Children.Add(WorkflowText(GetString(_localProducts, "error") ?? "Local products are unavailable."));
            MarketplaceList.Children.Add(section);
            return;
        }
        foreach (var product in Enumerate(_localProducts, "products"))
        {
            var id = GetString(product, "product_id");
            var card = new StackPanel { Spacing = 6 };
            card.Children.Add(WorkflowText($"{id} · {GetString(product, "installed_version") ?? "Available to install"}"));
            var enabled = product.TryGetProperty("enabled", out var enabledValue) && enabledValue.ValueKind == JsonValueKind.True;
            var healthy = product.TryGetProperty("observed_healthy", out var healthyValue) && healthyValue.ValueKind == JsonValueKind.True;
            card.Children.Add(WorkflowText(enabled ? (healthy ? "Enabled · opened successfully this session" : "Enabled · not opened this session") : "Disabled"));
            var output = WorkflowText("");
            foreach (var release in Enumerate(product, "releases"))
            {
                var version = GetString(release, "version");
                var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
                actions.Children.Add(WorkflowText(version ?? ""));
                foreach (var action in new[] { "download", "install", "update", "rollback" })
                {
                    var selectedAction = action;
                    actions.Children.Add(WorkflowButton(Sentence(action), async () =>
                    {
                        var result = await _client!.LocalWorkflowAsync("marketplace.product", new { action = selectedAction, product_id = id, version, operation_id = Guid.NewGuid().ToString() });
                        WorkflowSuccess(result);
                        await LoadMarketplaceAsync();
                    }));
                }
                card.Children.Add(actions);
            }
            var installedActions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            foreach (var action in new[] { "enable", "disable", "launch", "uninstall" })
            {
                var selectedAction = action;
                installedActions.Children.Add(WorkflowButton(action == "uninstall" ? "Uninstall, keep data" : Sentence(action), async () =>
                {
                    var result = await _client!.LocalWorkflowAsync("marketplace.product", new { action = selectedAction, product_id = id, data_policy = "preserve" });
                    WorkflowSuccess(result);
                    if (selectedAction == "launch") output.Text = GetString(result.GetProperty("result"), "message") ?? "Local product opened.";
                    else await LoadMarketplaceAsync();
                }));
            }
            card.Children.Add(installedActions);
            card.Children.Add(WorkflowButton("Uninstall and delete saved data", async () =>
            {
                var confirmation = new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Delete this product's saved data?",
                    Content = "The local product and its dedicated saved data will be removed.", PrimaryButtonText = "Delete product and data", CloseButtonText = "Cancel" };
                if (await confirmation.ShowAsync() != ContentDialogResult.Primary) return;
                var result = await _client!.LocalWorkflowAsync("marketplace.product", new { action = "uninstall", product_id = id, data_policy = "delete" });
                WorkflowSuccess(result);
                await LoadMarketplaceAsync();
            }));
            card.Children.Add(output);
            section.Children.Add(WrapCard(card));
        }
        MarketplaceList.Children.Add(section);
    }
}
