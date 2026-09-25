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
    // -- system (diagnostics, backups, service) -------------------------------

    private JsonElement _diagnostics = default;

    private async void OnSettingsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadSettingsAsync();
    }

    private async Task LoadSettingsAsync()
    {
        if (_client is null)
        {
            return;
        }
        SettingsStatusText.Text = "";
        SettingsErrorText.Text = "";

        JsonElement? backups = null;
        JsonElement? service = null;

        // Each section loads independently: one failing call must not blank
        // the whole control center.
        try
        {
            var diagnostics = await _client.GetSystemDiagnosticsAsync();
            _diagnostics = diagnostics.GetProperty("diagnostics").Clone();
            StorageDataDirText.Text = GetString(_diagnostics, "data_dir") ?? "Unknown";
            var voice = _diagnostics.TryGetProperty("voice", out var voiceValue) && voiceValue.ValueKind == JsonValueKind.Object
                ? voiceValue
                : default;
            var voiceEnabled = voice.ValueKind == JsonValueKind.Object
                && voice.TryGetProperty("enabled", out var enabledValue)
                && enabledValue.GetBoolean();
            IntelligenceVoiceText.Text = voiceEnabled
                ? $"Voice is enabled (state: {GetString(voice, "state") ?? "unknown"})"
                : "Voice is off. Models, routing and downloads live on the Models page.";
            var version = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version;
            AboutVersionText.Text = $"HAVEN Desktop {version?.Major}.{version?.Minor}.{version?.Build} — local-first; your data stays on this machine.";
            RenderDiagnostics();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load diagnostics: {ex.Message} Use Refresh to retry.";
        }

        try
        {
            await RenderExtensionsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load extensions: {ex.Message}";
        }

        try
        {
            backups = (await _client.GetBackupsAsync()).Clone();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load backups: {ex.Message} Use Refresh to retry.";
        }
        try
        {
            service = (await _client.GetServiceStatusAsync()).Clone();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = $"Could not load the startup service: {ex.Message} Use Refresh to retry.";
        }
        if (backups is not null)
        {
            RenderBackups(backups.Value.GetProperty("backups"));
        }
        if (service is not null)
        {
            RenderService(service.Value.GetProperty("service"));
        }
    }

    private async Task RenderExtensionsAsync()
    {
        if (_client is null)
        {
            return;
        }
        var result = await _client.GetExtensionsAsync();
        ExtensionsHost.Children.Clear();
        foreach (var group in Enumerate(result.GetProperty("classes")))
        {
            var className = GetString(group, "class") ?? "unknown";
            var title = className switch
            {
                "export_consumer" => "Export consumers",
                "intelligence" => "Intelligence services",
                "provider" => "Providers",
                "feature" => "Feature modules",
                _ => Sentence(className),
            };
            var card = new StackPanel { Spacing = 6 };
            card.Children.Add(new TextBlock
            {
                Text = title,
                Style = (Style)Application.Current.Resources["HavenSectionTextStyle"],
            });
            var extensions = Enumerate(group, "extensions").ToList();
            if (extensions.Count == 0)
            {
                card.Children.Add(new TextBlock
                {
                    Text = className switch
                    {
                        "feature" => "None yet. Feature modules will extend HAVEN through explicit contracts, calling application services only.",
                        "provider" => "No provider plugins are installed. Provider packages declare themselves through the haven.providers entry point.",
                        "export_consumer" => "Nothing in the catalog yet. Export consumers read only explicitly exported, redacted receipts — never live state.",
                        _ => "None registered.",
                    },
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
            }
            foreach (var extension in extensions)
            {
                var row = new StackPanel { Spacing = 2 };
                var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                head.Children.Add(new TextBlock
                {
                    Text = GetString(extension, "display_name") ?? GetString(extension, "extension_id") ?? "?",
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    TextWrapping = TextWrapping.Wrap,
                });
                var state = extension.TryGetProperty("state", out var stateValue) && stateValue.ValueKind == JsonValueKind.Object
                    ? stateValue
                    : default;
                var enabled = state.ValueKind == JsonValueKind.Object
                    && state.TryGetProperty("enabled", out var enabledValue)
                    && enabledValue.ValueKind == JsonValueKind.True
                    && enabledValue.GetBoolean();
                if (state.ValueKind == JsonValueKind.Object && state.TryGetProperty("enabled", out _))
                {
                    head.Children.Add(MakeChip(
                        enabled ? "Enabled" : "Disabled",
                        enabled ? "HavenSuccessTintBrush" : "HavenStrokeBrush",
                        enabled ? "HavenSuccessBrush" : "HavenMutedTextBrush"));
                }
                row.Children.Add(head);
                row.Children.Add(new TextBlock
                {
                    Text = $"{GetString(extension, "run_location")} · {GetString(extension, "access_boundary")}",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
                row.Children.Add(new TextBlock
                {
                    Text = GetString(extension, "authority_boundary") ?? "",
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.8,
                });
                if (GetString(extension, "detail") is { Length: > 0 } detail)
                {
                    row.Children.Add(new TextBlock
                    {
                        Text = detail,
                        Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                        TextWrapping = TextWrapping.Wrap,
                        Opacity = 0.8,
                    });
                }
                if (className == "export_consumer" && state.ValueKind == JsonValueKind.Object
                    && state.TryGetProperty("plugin_id", out var pluginIdValue)
                    && pluginIdValue.GetString() is { } pluginId)
                {
                    var toggle = new Button
                    {
                        Content = enabled ? "Disable" : "Enable",
                        Style = (Style)Application.Current.Resources[enabled ? "HavenDangerButtonStyle" : "HavenSecondaryButtonStyle"],
                        HorizontalAlignment = HorizontalAlignment.Left,
                    };
                    toggle.Click += async (_, _) =>
                    {
                        try
                        {
                            await _client!.SetExportConsumerEnabledAsync(pluginId, !enabled);
                            SettingsErrorText.Text = "";
                            await LoadSettingsAsync();
                        }
                        catch (Exception ex)
                        {
                            SettingsErrorText.Text = ex.Message;
                        }
                    };
                    row.Children.Add(toggle);
                }
                card.Children.Add(WrapCard(row));
            }
            ExtensionsHost.Children.Add(card);
        }
    }

    private void OnOpenModelsClicked(object sender, RoutedEventArgs args)
    {
        SelectNavigation("models");
    }

    private void RenderDiagnostics()
    {
        DiagnosticsList.Children.Clear();
        void Row(string label, string value)
        {
            var row = new Grid();
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(200) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
            var name = new TextBlock { Text = label, Opacity = 0.72 };
            var content = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap };
            row.Children.Add(name);
            Grid.SetColumn(content, 1);
            row.Children.Add(content);
            DiagnosticsList.Children.Add(row);
        }

        if (_diagnostics.ValueKind != JsonValueKind.Object)
        {
            DiagnosticsList.Children.Add(new TextBlock { Text = "Diagnostics unavailable.", Opacity = 0.72 });
            return;
        }
        Row("Data directory", GetString(_diagnostics, "data_dir") ?? "—");
        var world = GetString(_diagnostics, "world", "mode");
        Row("World mode", world ?? "—");
        var provider = _diagnostics.TryGetProperty("provider", out var providerValue) && providerValue.ValueKind == JsonValueKind.Object
            ? providerValue
            : default;
        Row("Provider", provider.ValueKind == JsonValueKind.Object
            ? (provider.TryGetProperty("configured", out var configured) && configured.GetBoolean()
                ? $"{GetString(provider, "kind")} · {GetString(provider, "base_url")}"
                : "not configured")
            : "—");
        var household = _diagnostics.TryGetProperty("household", out var householdValue) && householdValue.ValueKind == JsonValueKind.Object
            ? householdValue
            : default;
        if (household.ValueKind == JsonValueKind.Object)
        {
            Row("Household", $"{GetInt(household, "people")} people · {GetInt(household, "contexts")} contexts");
        }
        var devices = _diagnostics.TryGetProperty("devices", out var devicesValue) && devicesValue.ValueKind == JsonValueKind.Object
            ? devicesValue
            : default;
        if (devices.ValueKind == JsonValueKind.Object)
        {
            Row("Devices", $"{GetInt(devices, "enrolled")} enrolled · {GetInt(devices, "registered")} registered");
        }
        var rules = _diagnostics.TryGetProperty("rules", out var rulesValue) && rulesValue.ValueKind == JsonValueKind.Object
            ? rulesValue
            : default;
        if (rules.ValueKind == JsonValueKind.Object)
        {
            Row("Rules", $"{GetInt(rules, "total")} total · {GetInt(rules, "approved")} approved · {GetInt(rules, "proposed")} proposed");
        }
        var models = _diagnostics.TryGetProperty("models", out var modelsValue) && modelsValue.ValueKind == JsonValueKind.Object
            ? modelsValue
            : default;
        if (models.ValueKind == JsonValueKind.Object)
        {
            Row("Models", $"{GetInt(models, "registered")} registered · {GetInt(models, "loaded")} loaded");
        }
        var voice = _diagnostics.TryGetProperty("voice", out var voiceValue) && voiceValue.ValueKind == JsonValueKind.Object
            ? voiceValue
            : default;
        if (voice.ValueKind == JsonValueKind.Object)
        {
            var enabled = voice.TryGetProperty("enabled", out var voiceEnabled) && voiceEnabled.GetBoolean();
            Row("Voice", enabled ? $"enabled · {GetString(voice, "state") ?? "?"}" : "disabled");
        }
        Row("Uptime", $"{GetInt(_diagnostics, "uptime_seconds")} s");
    }

    private async void OnProbeProviderClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.ProbeSystemProviderAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ProbeResultText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Probe failed."
                    : "Probe failed.";
                return;
            }
            var reachable = result.TryGetProperty("reachable", out var reachableValue) && reachableValue.GetBoolean();
            ProbeResultText.Text = (reachable ? "Provider reachable" : "Provider unreachable")
                + (result.TryGetProperty("detail", out var detail) && detail.ValueKind == JsonValueKind.String
                    ? $" — {detail.GetString()}"
                    : "");
        }
        catch (Exception ex)
        {
            ProbeResultText.Text = ex.Message;
        }
    }

    private void RenderBackups(JsonElement backups)
    {
        BackupsList.Children.Clear();
        foreach (var backup in Enumerate(backups))
        {
            var backupId = GetString(backup, "id") ?? "";
            var card = new StackPanel { Spacing = 4 };
            card.Children.Add(new TextBlock
            {
                Text = backupId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var created = GetString(backup, "created_at");
            var files = Enumerate(backup, "files").Count();
            card.Children.Add(new TextBlock
            {
                Text = $"{created ?? "unknown time"} · {files} files",
                Opacity = 0.72,
            });
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var restore = new Button { Content = "Restore" };
            restore.Click += async (_, _) => await RestoreBackupAsync(backupId);
            var delete = new Button { Content = "Delete" };
            delete.Click += async (_, _) => await DeleteBackupAsync(backupId);
            buttons.Children.Add(restore);
            buttons.Children.Add(delete);
            card.Children.Add(buttons);
            BackupsList.Children.Add(WrapCard(card));
        }
        if (BackupsList.Children.Count == 0)
        {
            BackupsList.Children.Add(new TextBlock
            {
                Text = "No backups yet. Create one before changing providers or data directories.",
                Opacity = 0.72,
            });
        }
    }

    private async void OnCreateBackupClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.CreateBackupAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Backup failed."
                    : "Backup failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private async Task RestoreBackupAsync(string backupId)
    {
        if (_client is null)
        {
            return;
        }
        // Mirror the web surface's confirmation: a restore is high-consequence
        // and the running process keeps its in-memory state until a restart.
        var dialog = new ContentDialog
        {
            Title = $"Restore {backupId}?",
            Content = new TextBlock
            {
                Text = "Backup files replace the current installation files on disk. The running process keeps its in-memory rules, household, and enrollments until HAVEN restarts.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Restore",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.RestoreBackupAsync(backupId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Restore failed."
                    : "Restore failed.";
                return;
            }
            SettingsErrorText.Text = "Restore complete — restart HAVEN to apply it to the running process.";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private async Task DeleteBackupAsync(string backupId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Delete backup {backupId}?",
            Content = new TextBlock
            {
                Text = "The backup's files are removed permanently.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Delete",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        try
        {
            var result = await _client.DeleteBackupAsync(backupId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Delete failed."
                    : "Delete failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }

    private void RenderService(JsonElement service)
    {
        ServicePanel.Children.Clear();
        var installed = service.TryGetProperty("installed", out var installedValue) && installedValue.GetBoolean();
        var running = service.TryGetProperty("running", out var runningValue) && runningValue.GetBoolean();
        var detail = GetString(service, "detail") ?? "";
        var card = new StackPanel { Spacing = 4 };
        card.Children.Add(new TextBlock
        {
            Text = installed
                ? (running ? "HAVEN starts at logon (running)" : $"HAVEN starts at logon ({detail})")
                : $"Not installed to start at logon ({detail})",
            TextWrapping = TextWrapping.Wrap,
        });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var toggle = new Button { Content = installed ? "Uninstall" : "Install" };
        toggle.Click += async (_, _) => await ToggleServiceAsync(installed);
        buttons.Children.Add(toggle);
        card.Children.Add(buttons);
        ServicePanel.Children.Add(WrapCard(card));
    }

    private async Task ToggleServiceAsync(bool installed)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = installed
                ? await _client.UninstallServiceAsync()
                : await _client.InstallServiceAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                SettingsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Service change failed."
                    : "Service change failed.";
                return;
            }
            SettingsErrorText.Text = "";
            await LoadSettingsAsync();
        }
        catch (Exception ex)
        {
            SettingsErrorText.Text = ex.Message;
        }
    }


    private JsonElement _rooms = default;
    private JsonElement _pending = default;

    private async void OnRoomsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadRoomsAsync();
    }

}
