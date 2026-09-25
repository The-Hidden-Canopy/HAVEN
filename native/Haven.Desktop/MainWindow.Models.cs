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
    // -- models ---------------------------------------------------------------

    private static readonly HashSet<string> ModelFailureStates = new()
    {
        "incomplete", "unsupported", "hash_mismatch", "backend_missing", "load_failed", "unreachable",
    };

    private JsonElement _models = default;
    private JsonElement _modelAssignments = default;
    private JsonElement _modelJobs = default;
    private JsonElement _discovered = default;
    private bool _assigningRoles;
    private string _modelsTab = "local";

    private void OnModelsTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            SelectModelsTab(tab);
        }
    }

    private void SelectModelsTab(string tab)
    {
        _modelsTab = tab;
        ModelsTabLocal.Visibility = tab == "local" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabDownloaded.Visibility = tab == "downloaded" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabExternal.Visibility = tab == "endpoint" ? Visibility.Visible : Visibility.Collapsed;
        ModelsTabDownloads.Visibility = tab == "downloads" ? Visibility.Visible : Visibility.Collapsed;
        foreach (var button in ModelsTabs.Children.OfType<Button>())
        {
            button.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            button.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
    }

    private async void OnModelsRefreshClicked(object sender, RoutedEventArgs args)
    {
        await LoadModelsAsync();
    }

    private async Task LoadModelsAsync(bool silent = false)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var overview = await _client.GetModelsOverviewAsync();
            var jobs = await _client.GetModelJobsAsync();
            _models = overview.GetProperty("models").Clone();
            _modelAssignments = overview.GetProperty("assignments").Clone();
            _discovered = overview.TryGetProperty("discovered", out var found) ? found.Clone() : default;
            _modelJobs = jobs.GetProperty("jobs").Clone();
            if (!silent)
            {
                ModelsErrorText.Text = "";
            }
            RenderModels();
            RenderModelRoles();
            RenderModelJobs();
            RenderDiscovered();
        }
        catch (Exception ex)
        {
            if (!silent)
            {
                ModelsErrorText.Text = ex.Message;
            }
        }
    }

    private void RenderModels()
    {
        // Spec 38 tabs: Local / Downloaded / External, fed by the record's source.
        var local = Enumerate(_models).Where(model => GetString(model, "source") == "local").ToList();
        var downloaded = Enumerate(_models).Where(model => GetString(model, "source") == "downloaded").ToList();
        var external = Enumerate(_models).Where(model => GetString(model, "source") == "endpoint").ToList();

        ModelsLocalList.Children.Clear();
        foreach (var model in local)
        {
            ModelsLocalList.Children.Add(MakeModelCard(model));
        }
        if (ModelsLocalList.Children.Count == 0)
        {
            ModelsLocalList.Children.Add(new TextBlock
            {
                Text = "No local models. Install a model folder or add a folder to scan.",
                Opacity = 0.72,
            });
        }

        ModelsList.Children.Clear();
        foreach (var model in downloaded)
        {
            ModelsList.Children.Add(MakeModelCard(model));
        }
        if (ModelsList.Children.Count == 0)
        {
            ModelsList.Children.Add(new TextBlock
            {
                Text = "No downloaded models yet. Install from a URL to see progress and verification here.",
                Opacity = 0.72,
            });
        }

        ModelsExternalList.Children.Clear();
        foreach (var model in external)
        {
            ModelsExternalList.Children.Add(MakeModelCard(model));
        }
        if (ModelsExternalList.Children.Count == 0)
        {
            ModelsExternalList.Children.Add(new TextBlock
            {
                Text = "No external endpoints. Add a provider endpoint to use hosted intelligence.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeModelCard(JsonElement model)
    {
        var modelId = GetString(model, "id") ?? "";
        var state = GetString(model, "state") ?? "unknown";

        var card = new StackPanel { Spacing = 4 };
        var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        head.Children.Add(new TextBlock
        {
            Text = modelId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        var (modelTint, modelForeground) = ModelFailureStates.Contains(state)
            ? ("HavenDangerTintBrush", "HavenDangerBrush")
            : state is "ready" or "loaded"
                ? ("HavenSuccessTintBrush", "HavenSuccessBrush")
                : ("HavenWarningTintBrush", "HavenWarningBrush");
        head.Children.Add(MakeChip(Sentence(state), modelTint, modelForeground));
        card.Children.Add(head);

        var details = new List<string>();
        foreach (var key in new[] { "backend", "architecture", "version", "license", "source" })
        {
            var value = GetString(model, key);
            if (value is not null)
            {
                details.Add(value);
            }
        }
        if (details.Count > 0)
        {
            card.Children.Add(new TextBlock { Text = string.Join(" · ", details), Opacity = 0.72 });
        }
        var capabilities = Enumerate(model, "capabilities").Select(item => item.GetString()).Where(item => item is not null);
        var languages = Enumerate(model, "languages").Select(item => item.GetString()).Where(item => item is not null);
        var summary = string.Join("   ", new[]
        {
            string.Join(", ", capabilities),
            string.Join(", ", languages),
        }.Where(part => part.Length > 0));
        if (summary.Length > 0)
        {
            card.Children.Add(new TextBlock { Text = summary, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
        }
        var loadedBackend = GetString(model, "loaded_backend");
        if (loadedBackend is not null)
        {
            card.Children.Add(new TextBlock { Text = $"Loaded via {loadedBackend}", Opacity = 0.72 });
        }

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        if (state != "loaded")
        {
            var load = new Button { Content = "Load" };
            load.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.LoadModelAsync(modelId));
            buttons.Children.Add(load);
        }
        if (state == "loaded" || loadedBackend is not null)
        {
            var unload = new Button { Content = "Unload" };
            unload.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.UnloadModelAsync(modelId));
            buttons.Children.Add(unload);
        }
        var remove = new Button { Content = "Remove" };
        remove.Click += async (_, _) => await RemoveModelAsync(modelId);
        buttons.Children.Add(remove);
        card.Children.Add(buttons);

        return WrapCard(card);
    }

    private void RenderModelRoles()
    {
        _assigningRoles = true;
        try
        {
            ModelRoles.Children.Clear();
            var modelIds = Enumerate(_models).Select(model => GetString(model, "id")).Where(id => id is not null).Cast<string>().ToList();
            foreach (var role in _modelAssignments.EnumerateObject())
            {
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                row.Children.Add(new TextBlock
                {
                    Text = role.Name,
                    Width = 110,
                    VerticalAlignment = VerticalAlignment.Center,
                });
                var select = new ComboBox { MinWidth = 240 };
                select.Items.Add("None");
                foreach (var modelId in modelIds)
                {
                    select.Items.Add(modelId);
                }
                select.SelectedIndex = role.Value.ValueKind == JsonValueKind.String
                    && role.Value.GetString() is { } assigned
                    && modelIds.Contains(assigned)
                        ? modelIds.IndexOf(assigned) + 1
                        : 0;
                var roleName = role.Name;
                select.SelectionChanged += async (_, _) =>
                {
                    if (_assigningRoles || select.SelectedIndex < 0)
                    {
                        return;
                    }
                    var chosen = select.SelectedIndex == 0 ? null : modelIds[select.SelectedIndex - 1];
                    await RunModelsMutationAsync(() => _client!.AssignModelAsync(roleName, chosen));
                };
                row.Children.Add(select);
                ModelRoles.Children.Add(row);
            }
        }
        finally
        {
            _assigningRoles = false;
        }
    }

    private void RenderModelJobs()
    {
        ModelJobs.Children.Clear();
        foreach (var job in Enumerate(_modelJobs))
        {
            var jobId = GetString(job, "job_id") ?? "";
            var state = GetString(job, "state") ?? "unknown";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(job, "manifest_id") ?? GetString(job, "url") ?? jobId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            var (jobTint, jobForeground) = state switch
            {
                "ready" => ("HavenSuccessTintBrush", "HavenSuccessBrush"),
                "failed" or "cancelled" => ("HavenDangerTintBrush", "HavenDangerBrush"),
                _ => ("HavenWarningTintBrush", "HavenWarningBrush"),
            };
            head.Children.Add(MakeChip(Sentence(state), jobTint, jobForeground));
            card.Children.Add(head);

            var received = job.TryGetProperty("received_bytes", out var receivedValue) ? receivedValue.GetInt64() : 0L;
            var total = job.TryGetProperty("total_bytes", out var totalValue) && totalValue.ValueKind == JsonValueKind.Number
                ? totalValue.GetInt64()
                : (long?)null;
            var currentFile = GetString(job, "current_file");
            var progress = total is > 0
                ? $"{received / (1024.0 * 1024.0):0.0} / {total.Value / (1024.0 * 1024.0):0.0} MB{(currentFile is not null ? " · " + currentFile : "")}"
                : received > 0
                    ? $"{received / (1024.0 * 1024.0):0.0} MB{(currentFile is not null ? " · " + currentFile : "")}"
                    : null;
            if (progress is not null)
            {
                card.Children.Add(new TextBlock { Text = progress, Opacity = 0.72 });
            }
            var error = GetString(job, "error");
            if (error is not null)
            {
                card.Children.Add(new TextBlock
                {
                    Text = error,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                });
            }
            if (state is "queued" or "downloading" or "verifying")
            {
                var cancel = new Button { Content = "Cancel" };
                cancel.Click += async (_, _) =>
                {
                    try
                    {
                        await _client!.CancelModelJobAsync(jobId);
                        await LoadModelsAsync();
                    }
                    catch (Exception ex)
                    {
                        ModelsErrorText.Text = ex.Message;
                    }
                };
                card.Children.Add(cancel);
            }
            ModelJobs.Children.Add(WrapCard(card));
        }
        if (ModelJobs.Children.Count == 0)
        {
            ModelJobs.Children.Add(new TextBlock { Text = "No download jobs.", Opacity = 0.72 });
        }
    }

    private void RenderDiscovered()
    {
        DiscoveredList.Children.Clear();
        var any = false;
        foreach (var candidate in Enumerate(_discovered))
        {
            any = true;
            var path = GetString(candidate, "path") ?? "";
            var state = GetString(candidate, "state") ?? "unknown";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(candidate, "id") ?? System.IO.Path.GetFileName(path),
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(new TextBlock { Text = state, Opacity = 0.72 });
            card.Children.Add(head);
            card.Children.Add(new TextBlock { Text = path, Opacity = 0.6, TextWrapping = TextWrapping.Wrap });
            var problems = Enumerate(candidate, "problems").Select(item => item.GetString()).Where(item => item is not null);
            foreach (var problem in problems)
            {
                card.Children.Add(new TextBlock
                {
                    Text = problem,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenDangerBrush"],
                });
            }
            // Scan discovers, never activates: registration is the explicit gate.
            var register = new Button { Content = "Register" };
            register.Click += async (_, _) => await RunModelsMutationAsync(() => _client!.RegisterModelAsync(path));
            card.Children.Add(register);
            DiscoveredList.Children.Add(WrapCard(card));
        }
        DiscoveredHeading.Visibility = any ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task RunModelsMutationAsync(Func<Task<JsonElement>> operation)
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
                ModelsErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async Task<string?> PickFolderAsync()
    {
        var handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var picker = new Windows.Storage.Pickers.FolderPicker();
        picker.FileTypeFilter.Add("*");
        picker.SuggestedStartLocation = Windows.Storage.Pickers.PickerLocationId.ComputerFolder;
        WinRT.Interop.InitializeWithWindow.Initialize(picker, handle);
        var folder = await picker.PickSingleFolderAsync();
        return folder?.Path;
    }

    private async void OnInstallFromUrlClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var url = new TextBox { PlaceholderText = "https://example.org/models/my-model/haven-model.json" };
        var fields = new StackPanel { Spacing = 8, MinWidth = 420 };
        fields.Children.Add(new TextBlock { Text = "Model manifest URL" });
        fields.Children.Add(url);
        fields.Children.Add(new TextBlock
        {
            Text = "Downloads, verifies published hashes, and registers the model in the background. Progress appears under Download jobs.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Install from URL",
            Content = fields,
            PrimaryButtonText = "Download",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(url.Text))
        {
            return;
        }
        try
        {
            var result = await _client.DownloadModelAsync(url.Text.Trim());
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ModelsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Download failed."
                    : "Download failed.";
                return;
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async void OnInstallLocalClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var folder = await PickFolderAsync();
        if (folder is null)
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.InstallLocalModelAsync(folder));
    }

    private async void OnAddEndpointClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var url = new TextBox { PlaceholderText = "https://inference.example.org/v1" };
        var fields = new StackPanel { Spacing = 8, MinWidth = 420 };
        fields.Children.Add(new TextBlock { Text = "Endpoint URL" });
        fields.Children.Add(url);
        fields.Children.Add(new TextBlock
        {
            Text = "Registers a remote endpoint as a model source. HAVEN talks to it only when the model is loaded.",
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.72,
        });
        var dialog = new ContentDialog
        {
            Title = "Add endpoint",
            Content = fields,
            PrimaryButtonText = "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(url.Text))
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.AddModelEndpointAsync(url.Text.Trim()));
    }

    private async void OnAddModelRootClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        var folder = await PickFolderAsync();
        if (folder is null)
        {
            return;
        }
        await RunModelsMutationAsync(() => _client.AddModelRootAsync(folder));
    }

    private async void OnScanModelRootsClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.ScanModelRootsAsync();
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ModelsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "Scan failed."
                    : "Scan failed.";
                return;
            }
            if (result.TryGetProperty("discovered", out var discovered))
            {
                _discovered = discovered.Clone();
            }
            ModelsErrorText.Text = "";
            await LoadModelsAsync();
        }
        catch (Exception ex)
        {
            ModelsErrorText.Text = ex.Message;
        }
    }

    private async Task RemoveModelAsync(string modelId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {modelId}?",
            Content = new TextBlock
            {
                Text = "HAVEN deletes the model's stored files and clears any role assignment pointing at it.",
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
        await RunModelsMutationAsync(() => _client.RemoveModelAsync(modelId));
    }

    /* State line per role, mirroring the web surface's deviceStateLine:
       is_on proxies the cover position; devices without an on/off concept
       render "—". */
}
