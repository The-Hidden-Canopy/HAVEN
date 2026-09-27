using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    private static readonly string[] ExternalAgentProviders = { "alexa_plus", "mcp_client" };
    private static readonly string[] ExternalAgentScopeOptions =
    {
        "world.read", "knowledge.read", "knowledge.correct", "actions.request",
        "automations.propose", "automations.approve", "history.read",
    };

    private JsonElement _externalAgentConnections;

    private async Task LoadExternalAgentsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetExternalAgentConnectionsAsync();
            _externalAgentConnections = result.GetProperty("connections").Clone();
            ExternalAgentsErrorText.Text = "";
            RenderExternalAgentConnections();
        }
        catch (Exception ex)
        {
            ExternalAgentsErrorText.Text = $"Could not load external agent connections: {ex.Message}";
        }
    }

    private void RenderExternalAgentConnections()
    {
        ExternalAgentsList.Children.Clear();
        var connections = Enumerate(_externalAgentConnections).ToList();
        if (connections.Count == 0)
        {
            ExternalAgentsList.Children.Add(new TextBlock
            {
                Text = "No external agent connections yet. \"Add connection\" creates one disabled; enabling it and binding a subject to a household person are separate, deliberate steps.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }
        foreach (var connection in connections)
        {
            ExternalAgentsList.Children.Add(MakeExternalAgentConnectionCard(connection));
        }
    }

    private Border MakeExternalAgentConnectionCard(JsonElement connection)
    {
        var connectionId = GetString(connection, "connection_id") ?? "";
        var provider = GetString(connection, "provider") ?? "unknown";
        var displayName = GetString(connection, "display_name") ?? connectionId;
        var enabled = connection.TryGetProperty("enabled", out var enabledValue) && enabledValue.ValueKind == JsonValueKind.True;
        var active = connection.TryGetProperty("active", out var activeValue) && activeValue.ValueKind == JsonValueKind.True;
        var revoked = GetString(connection, "revoked_at") is not null;

        var card = new StackPanel { Spacing = 8 };

        var head = new Grid { ColumnSpacing = 12 };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var headText = new StackPanel { Spacing = 2 };
        headText.Children.Add(new TextBlock { Text = displayName, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        headText.Children.Add(new TextBlock
        {
            Text = provider + (revoked ? " · revoked" : active ? " · connected" : enabled ? " · enabled" : " · disabled"),
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        head.Children.Add(headText);

        var badge = new TextBlock
        {
            Text = revoked ? "Revoked" : active ? "Active" : "Disabled",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                revoked ? "HavenDangerBrush" : active ? "HavenSuccessBrush" : "HavenMutedTextBrush"],
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(badge, 1);
        head.Children.Add(badge);
        card.Children.Add(head);

        if (!revoked)
        {
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var toggle = new Button { Content = enabled ? "Disable" : "Enable" };
            toggle.Click += async (_, _) => await SetExternalAgentConnectionEnabledAsync(connectionId, !enabled);
            var manageBindings = new Button { Content = "Manage bindings" };
            manageBindings.Click += async (_, _) => await ShowExternalAgentBindingsDialogAsync(connectionId, displayName);
            var revoke = new Button
            {
                Content = "Revoke",
                Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            };
            revoke.Click += async (_, _) => await RevokeExternalAgentConnectionAsync(connectionId, displayName);
            actions.Children.Add(toggle);
            actions.Children.Add(manageBindings);
            actions.Children.Add(revoke);
            card.Children.Add(actions);
        }

        return new Border { Style = (Style)Application.Current.Resources["HavenCardStyle"], Child = card };
    }

    private async void OnAddExternalAgentConnectionClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var providerBox = new ComboBox { MinWidth = 160 };
        foreach (var option in ExternalAgentProviders)
        {
            providerBox.Items.Add(option);
        }
        providerBox.SelectedIndex = 0;
        var name = new TextBox { PlaceholderText = "e.g. Alexa+", MinWidth = 240 };
        var credential = new PasswordBox { MinWidth = 240 };
        var fields = new StackPanel { Spacing = 8 };
        fields.Children.Add(new TextBlock { Text = "Provider" });
        fields.Children.Add(providerBox);
        fields.Children.Add(new TextBlock { Text = "Display name" });
        fields.Children.Add(name);
        fields.Children.Add(new TextBlock { Text = "Bearer credential (optional — needed for the MCP endpoint)" });
        fields.Children.Add(credential);
        var dialog = new ContentDialog
        {
            Title = "Add external agent connection",
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
            var provider = providerBox.SelectedItem as string ?? ExternalAgentProviders[0];
            var result = await _client.CreateExternalAgentConnectionAsync(
                provider,
                name.Text.Trim(),
                string.IsNullOrWhiteSpace(credential.Password) ? null : credential.Password.Trim());
            if (!ApplyExternalAgentResult(result, "connection"))
            {
                return;
            }
            await LoadExternalAgentsAsync();
        }
        catch (Exception ex)
        {
            ExternalAgentsErrorText.Text = ex.Message;
        }
    }

    private async Task SetExternalAgentConnectionEnabledAsync(string connectionId, bool enabled)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.SetExternalAgentConnectionEnabledAsync(connectionId, enabled);
            if (!ApplyExternalAgentResult(result, "connection"))
            {
                return;
            }
            await LoadExternalAgentsAsync();
        }
        catch (Exception ex)
        {
            ExternalAgentsErrorText.Text = ex.Message;
        }
    }

    private async Task RevokeExternalAgentConnectionAsync(string connectionId, string displayName)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Revoke {displayName}?",
            Content = new TextBlock
            {
                Text = "This permanently disables the connection. Every binding under it stops working; past receipts keep the provenance that existed when they ran.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Revoke",
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
            var result = await _client.RevokeExternalAgentConnectionAsync(connectionId);
            if (!ApplyExternalAgentResult(result, "connection"))
            {
                return;
            }
            await LoadExternalAgentsAsync();
        }
        catch (Exception ex)
        {
            ExternalAgentsErrorText.Text = ex.Message;
        }
    }

    private async Task ShowExternalAgentBindingsDialogAsync(string connectionId, string displayName)
    {
        if (_client is null)
        {
            return;
        }
        JsonElement bindings;
        JsonElement people = default;
        try
        {
            var bindingsResult = await _client.GetExternalAgentBindingsAsync(connectionId);
            bindings = bindingsResult.GetProperty("bindings").Clone();
            var peopleResult = await _client.GetPeopleAsync();
            if (peopleResult.TryGetProperty("people", out var peopleValue))
            {
                people = peopleValue.Clone();
            }
        }
        catch (Exception ex)
        {
            ExternalAgentsErrorText.Text = ex.Message;
            return;
        }

        var content = new StackPanel { Spacing = 10, MinWidth = 360 };
        var list = new StackPanel { Spacing = 6 };
        var bindingRows = Enumerate(bindings).ToList();
        foreach (var binding in bindingRows)
        {
            list.Children.Add(MakeExternalAgentBindingRow(binding));
        }
        if (bindingRows.Count == 0)
        {
            list.Children.Add(new TextBlock { Text = "No bindings yet.", Opacity = 0.72 });
        }
        content.Children.Add(list);

        content.Children.Add(new TextBlock
        {
            Text = "Bind a new subject",
            Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
        });
        var subjectKey = new TextBox { PlaceholderText = "subject key (from the connection's own identity)" };
        var subjectLabel = new TextBox { PlaceholderText = "label, e.g. \"Gerron's voice profile\"" };
        var personBox = new ComboBox { MinWidth = 200 };
        var personIds = new List<string>();
        foreach (var person in Enumerate(people))
        {
            var personId = GetString(person, "person_id");
            if (personId is null)
            {
                continue;
            }
            personIds.Add(personId);
            personBox.Items.Add(GetString(person, "name") ?? personId);
        }
        if (personBox.Items.Count > 0)
        {
            personBox.SelectedIndex = 0;
        }
        var scopeChecks = new StackPanel { Spacing = 4 };
        var scopeBoxes = new List<CheckBox>();
        foreach (var scope in ExternalAgentScopeOptions)
        {
            var box = new CheckBox { Content = scope, Tag = scope };
            scopeBoxes.Add(box);
            scopeChecks.Children.Add(box);
        }
        var addError = new TextBlock
        {
            Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
            TextWrapping = TextWrapping.Wrap,
        };
        var addBinding = new Button
        {
            Content = "Add binding",
            Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"],
        };
        addBinding.Click += async (_, _) =>
        {
            if (personIds.Count == 0 || personBox.SelectedIndex < 0)
            {
                addError.Text = "Declare a household person first (People tab).";
                return;
            }
            var scopes = scopeBoxes.Where(box => box.IsChecked == true).Select(box => (string)box.Tag);
            try
            {
                var result = await _client.UpsertExternalAgentBindingAsync(
                    connectionId,
                    subjectKey.Text.Trim(),
                    subjectLabel.Text.Trim(),
                    personIds[personBox.SelectedIndex],
                    scopes);
                if (result.ValueKind == JsonValueKind.Object
                    && result.TryGetProperty("ok", out var ok)
                    && !ok.GetBoolean())
                {
                    addError.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                        ? error.GetString() ?? "HAVEN Core refused the binding."
                        : "HAVEN Core refused the binding.";
                    return;
                }
                addError.Text = "";
                await LoadExternalAgentsAsync();
            }
            catch (Exception ex)
            {
                addError.Text = ex.Message;
            }
        };
        content.Children.Add(subjectKey);
        content.Children.Add(subjectLabel);
        content.Children.Add(personBox);
        content.Children.Add(scopeChecks);
        content.Children.Add(addBinding);
        content.Children.Add(addError);

        var dialog = new ContentDialog
        {
            Title = $"Bindings — {displayName}",
            Content = new ScrollViewer { Content = content, MaxHeight = 480 },
            CloseButtonText = "Close",
            XamlRoot = RootGrid().XamlRoot,
        };
        await dialog.ShowAsync();
    }

    private StackPanel MakeExternalAgentBindingRow(JsonElement binding)
    {
        var bindingId = GetString(binding, "binding_id") ?? "";
        var subjectLabel = GetString(binding, "subject_label") ?? "";
        var principalId = GetString(binding, "principal_id") ?? "";
        var scopes = Enumerate(binding, "scopes").Select(scope => scope.GetString()).Where(scope => scope is not null);

        var row = new StackPanel { Spacing = 2 };
        var head = new Grid { ColumnSpacing = 8 };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.Children.Add(new TextBlock { Text = $"{subjectLabel} → {principalId}", TextWrapping = TextWrapping.Wrap });
        var revoke = new Button
        {
            Content = "Revoke",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
        };
        revoke.Click += async (_, _) =>
        {
            if (_client is null)
            {
                return;
            }
            try
            {
                await _client.RevokeExternalAgentBindingAsync(bindingId);
                await LoadExternalAgentsAsync();
            }
            catch (Exception ex)
            {
                ExternalAgentsErrorText.Text = ex.Message;
            }
        };
        Grid.SetColumn(revoke, 1);
        head.Children.Add(revoke);
        row.Children.Add(head);
        row.Children.Add(new TextBlock
        {
            Text = string.Join(", ", scopes),
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
        });
        return row;
    }

    private bool ApplyExternalAgentResult(JsonElement result, string kind)
    {
        if (result.ValueKind == JsonValueKind.Object
            && result.TryGetProperty("ok", out var ok)
            && !ok.GetBoolean())
        {
            ExternalAgentsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                ? error.GetString() ?? $"HAVEN Core refused the {kind}."
                : $"HAVEN Core refused the {kind}.";
            return false;
        }
        ExternalAgentsErrorText.Text = "";
        return true;
    }
}
