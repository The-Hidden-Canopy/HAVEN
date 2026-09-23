using System.Text.Json;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace Haven.Desktop.Setup;

public sealed partial class SetupWizardView : UserControl
{
    private const int StepCount = 7;

    private static readonly (string Title, bool Optional)[] StepNames =
    {
        ("Welcome", false),
        ("Storage", false),
        ("You", false),
        ("This computer", true),
        ("Connections", true),
        ("Intelligence & voice", true),
        ("Ready", false),
    };

    private static readonly string[] DeviceTypes = { "light", "thermostat", "switch", "fan", "cover", "camera" };

    private HavenCoreClient? _client;
    private nint _windowHandle;
    private JsonElement? _setup;
    private string? _configError;
    private string? _error;
    private string? _info;
    private string _householdName = "";
    private int _step = 1;
    private int _furthest = 1;
    private bool _busy;

    public event EventHandler? SetupCompleted;

    public SetupWizardView()
    {
        InitializeComponent();
    }

    public void Initialize(HavenCoreClient client, nint windowHandle)
    {
        _client = client;
        _windowHandle = windowHandle;
    }

    public async Task LoadAsync()
    {
        await RunSetupAsync(() => Client().GetSetupStatusAsync());
        GotoStep(1);
    }

    private HavenCoreClient Client() => _client ?? throw new InvalidOperationException("Setup wizard is not connected to HAVEN Core.");

    private void OnBackClicked(object sender, RoutedEventArgs args)
    {
        if (_step > 1)
        {
            GotoStep(_step - 1);
        }
    }

    private async void OnNextClicked(object sender, RoutedEventArgs args)
    {
        if (_step < StepCount)
        {
            GotoStep(_step + 1);
            return;
        }
        var (success, _) = await RunSetupAsync(() => Client().CompleteSetupAsync());
        if (success)
        {
            SetupCompleted?.Invoke(this, EventArgs.Empty);
        }
    }

    private void GotoStep(int step)
    {
        _step = Math.Clamp(step, 1, StepCount);
        _furthest = Math.Max(_furthest, _step);
        _error = null;
        _info = null;
        Render();
    }

    private void SetBusy(bool busy)
    {
        _busy = busy;
        BusyRing.IsActive = busy;
        BackButton.IsEnabled = !busy && _step > 1;
        NextButton.IsEnabled = !busy;
    }

    private async Task<(bool Success, JsonElement Envelope)> RunSetupAsync(
        Func<Task<JsonElement>> call,
        bool refreshStatus = false)
    {
        SetBusy(true);
        _error = null;
        try
        {
            var envelope = await call();
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                _error = envelope.TryGetProperty("error", out var errorValue) && errorValue.ValueKind == JsonValueKind.String
                    ? errorValue.GetString()
                    : "Request failed.";
                Render();
                return (false, default);
            }
            if (refreshStatus)
            {
                var status = await Client().GetSetupStatusAsync();
                if (status.TryGetProperty("setup", out var freshSetup))
                {
                    _setup = freshSetup.Clone();
                }
            }
            else if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("setup", out var setup))
            {
                _setup = setup.Clone();
            }
            if (envelope.ValueKind == JsonValueKind.Object
                && envelope.TryGetProperty("config_error", out var configError)
                && configError.ValueKind == JsonValueKind.String)
            {
                _configError = configError.GetString();
            }
            Render();
            return (true, envelope);
        }
        catch (Exception ex)
        {
            _error = ex.Message;
            Render();
            return (false, default);
        }
        finally
        {
            SetBusy(false);
        }
    }

    // -- status helpers -----------------------------------------------------

    private JsonElement SetupSection(string name)
    {
        if (_setup is JsonElement setup
            && setup.ValueKind == JsonValueKind.Object
            && setup.TryGetProperty(name, out var section))
        {
            return section;
        }
        return default;
    }

    private static bool GetBool(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.True;

    private static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static IReadOnlyList<JsonElement> GetArray(JsonElement element, string property)
    {
        if (element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(property, out var value)
            && value.ValueKind == JsonValueKind.Array)
        {
            return value.EnumerateArray().ToList();
        }
        return Array.Empty<JsonElement>();
    }

    private static string AsString(JsonElement element) =>
        element.ValueKind == JsonValueKind.String ? element.GetString() ?? "" : "";

    // -- shared builders ----------------------------------------------------

    private static TextBlock Body(string text) => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        Opacity = 0.78,
    };

    private static TextBlock Caption(string text) => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        Opacity = 0.6,
        FontSize = 12,
    };

    private static TextBlock MicroHeading(string text) => new()
    {
        Text = text,
        FontSize = 12,
        FontWeight = FontWeights.SemiBold,
        Opacity = 0.6,
        Margin = new Thickness(0, 10, 0, 0),
    };

    private static Grid ContextRow(string label, string value)
    {
        var grid = new Grid { ColumnSpacing = 10 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var labelBlock = new TextBlock { Text = label, Opacity = 0.6, VerticalAlignment = VerticalAlignment.Bottom };
        var dots = new TextBlock { Text = "· · · · · · · · · ·", Opacity = 0.3, VerticalAlignment = VerticalAlignment.Bottom };
        var valueBlock = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.SemiBold };
        Grid.SetColumn(dots, 1);
        Grid.SetColumn(valueBlock, 2);
        grid.Children.Add(labelBlock);
        grid.Children.Add(dots);
        grid.Children.Add(valueBlock);
        return grid;
    }

    private Grid RemovalRow(string label, string value, Func<Task<JsonElement>> remove)
    {
        var grid = new Grid { ColumnSpacing = 10 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var labelBlock = new TextBlock { Text = label, Opacity = 0.6, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap };
        var valueBlock = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap };
        var removeButton = new Button { Content = "Remove" };
        removeButton.Click += async (_, _) => await RunSetupAsync(remove);
        Grid.SetColumn(valueBlock, 1);
        Grid.SetColumn(removeButton, 2);
        grid.Children.Add(labelBlock);
        grid.Children.Add(valueBlock);
        grid.Children.Add(removeButton);
        return grid;
    }

    private async Task<string?> PickFolderAsync()
    {
        if (_windowHandle == 0)
        {
            _error = "Native folder picker unavailable. You can enter a path instead.";
            Render();
            return null;
        }
        var picker = new FolderPicker();
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, _windowHandle);
        var folder = await picker.PickSingleFolderAsync();
        return folder?.Path;
    }

    // -- frame rendering ----------------------------------------------------

    private void Render()
    {
        StepLabel.Text = $"STEP {_step} OF {StepCount}";
        var (title, optional) = StepNames[_step - 1];
        StepTitle.Text = title;
        OptionalBadge.Visibility = optional ? Visibility.Visible : Visibility.Collapsed;
        ConfigWarning.Visibility = string.IsNullOrEmpty(_configError) ? Visibility.Collapsed : Visibility.Visible;
        ConfigWarning.Text = _configError ?? "";
        ErrorText.Text = _error ?? "";
        ErrorText.Visibility = string.IsNullOrEmpty(_error) ? Visibility.Collapsed : Visibility.Visible;
        RenderIndicator();
        StepContent.Children.Clear();
        switch (_step)
        {
            case 1: RenderWelcome(); break;
            case 2: RenderDataDir(); break;
            case 3: RenderHousehold(); break;
            case 4: RenderComputer(); break;
            case 5: RenderConnections(); break;
            case 6: RenderPreferences(); break;
            case 7: RenderFinish(); break;
        }
        BackButton.Visibility = _step == 1 ? Visibility.Collapsed : Visibility.Visible;
        BackButton.IsEnabled = !_busy && _step > 1;
        NextButton.Content = _step == StepCount ? "Enter HAVEN" : "Next";
        if (!string.IsNullOrEmpty(_info))
        {
            StepContent.Children.Insert(0, new TextBlock
            {
                Text = _info,
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["AccentTextFillColorPrimaryBrush"],
            });
        }
    }

    private void RenderIndicator()
    {
        StepIndicator.Children.Clear();
        for (var index = 1; index <= StepCount; index++)
        {
            var step = index;
            var button = new Button
            {
                Content = index.ToString(),
                Width = 34,
                Height = 34,
                CornerRadius = new CornerRadius(17),
                FontWeight = index == _step ? FontWeights.Bold : FontWeights.Normal,
                Opacity = index == _step ? 1.0 : 0.65,
                IsEnabled = index <= _furthest,
            };
            button.Click += (_, _) => GotoStep(step);
            StepIndicator.Children.Add(button);
        }
    }

    // -- step 1: welcome ----------------------------------------------------

    private void RenderWelcome()
    {
        StepContent.Children.Add(Body("HAVEN is a local-first assistant for your computer, your information, and the connected world around you."));
        StepContent.Children.Add(Body("This setup creates your local HAVEN installation. You can connect models, your computer, Home Assistant, devices, voice, and other providers now or add them later."));
        StepContent.Children.Add(Body("Your core configuration and history stay on this machine."));
        StepContent.Children.Add(Body("Every step can be skipped or left at its default. The only firm requirement is naming an owner once you have connected a real provider. This takes about two minutes."));
    }

    // -- step 2: data directory ----------------------------------------------

    private void RenderDataDir()
    {
        var dataDir = SetupSection("data_dir");
        StepContent.Children.Add(Body("HAVEN needs a local folder for its configuration, history, installed provider settings, automations, and other local state."));
        StepContent.Children.Add(Body("The default is recommended for most people."));
        StepContent.Children.Add(ContextRow("Current location", GetString(dataDir, "resolved") ?? "—"));
        StepContent.Children.Add(ContextRow("Source", GetString(dataDir, "source") == "chosen" ? "custom" : "default"));

        var input = new TextBox { PlaceholderText = "custom folder path…", MinWidth = 340 };
        var choose = new Button { Content = "Choose another folder" };
        choose.Click += async (_, _) => await RunSetupAsync(() => Client().ChooseSetupDataDirAsync(input.Text.Trim()));
        StepContent.Children.Add(FieldRow(input, choose));

        var browse = new Button { Content = "Browse for folder…" };
        browse.Click += async (_, _) =>
        {
            var path = await PickFolderAsync();
            if (path is not null)
            {
                await RunSetupAsync(() => Client().ChooseSetupDataDirAsync(path));
            }
        };
        var useDefault = new Button { Content = "Use recommended location" };
        useDefault.Click += async (_, _) => await RunSetupAsync(() => Client().ChooseSetupDataDirAsync(""));
        var defaultsRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        defaultsRow.Children.Add(browse);
        defaultsRow.Children.Add(useDefault);
        StepContent.Children.Add(defaultsRow);
    }

    private static StackPanel FieldRow(Control input, Button action)
    {
        input.VerticalAlignment = VerticalAlignment.Center;
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(input);
        row.Children.Add(action);
        return row;
    }

    // -- step 3: household ----------------------------------------------------

    private void RenderHousehold()
    {
        var household = SetupSection("household");
        var people = GetArray(household, "people");
        var contexts = GetArray(household, "contexts");

        StepContent.Children.Add(MicroHeading("Who uses this HAVEN?"));
        StepContent.Children.Add(Body("HAVEN needs to know who owns this installation so it knows who can approve important actions."));
        if (people.Count == 0)
        {
            StepContent.Children.Add(Body("No people declared yet."));
        }
        foreach (var person in people)
        {
            var name = GetString(person, "name") ?? GetString(person, "person_id") ?? "unknown";
            var personId = GetString(person, "person_id") ?? name;
            var nameRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            nameRow.Children.Add(new TextBlock { Text = name, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
            if (GetString(person, "role") == "owner")
            {
                nameRow.Children.Add(new TextBlock
                {
                    Text = "OWNER",
                    FontSize = 11,
                    Opacity = 0.8,
                    VerticalAlignment = VerticalAlignment.Center,
                });
            }
            StepContent.Children.Add(nameRow);
            foreach (var source in GetArray(person, "sources"))
            {
                var entityId = GetString(source, "entity_id") ?? "—";
                var roomId = GetString(source, "room_id") ?? "—";
                var personIdCapture = personId;
                StepContent.Children.Add(RemovalRow(entityId, roomId, () => Client().RemoveSetupPersonAsync(personIdCapture)));
            }
        }

        var nameBox = new TextBox { PlaceholderText = "Your name", Text = _householdName, MinWidth = 220 };
        var roleBox = new ComboBox { MinWidth = 110 };
        roleBox.Items.Add("Owner");
        roleBox.Items.Add("Member");
        roleBox.SelectedIndex = people.Count == 0 ? 0 : 1;
        var addPerson = new Button { Content = "Add person" };
        addPerson.Click += async (_, _) =>
        {
            _householdName = nameBox.Text;
            var role = roleBox.SelectedIndex == 0 ? "owner" : "member";
            await RunSetupAsync(() => Client().AddSetupPersonAsync(nameBox.Text.Trim(), role));
        };
        StepContent.Children.Add(FieldRow(nameBox, addPerson));

        var sensorExpander = new Expander { Header = "Connect presence sensors", HorizontalAlignment = HorizontalAlignment.Left, MinWidth = 520 };
        var sensorPanel = new StackPanel { Spacing = 10 };
        sensorPanel.Children.Add(Body("Tell HAVEN which occupancy sensors report who. A person appears in a room when their sensor says they are there."));
        if (people.Count == 0)
        {
            sensorPanel.Children.Add(Body("Add a person above first."));
        }
        else
        {
            var personBox = new ComboBox { MinWidth = 200, PlaceholderText = "Person" };
            foreach (var person in people)
            {
                var personName = GetString(person, "name") ?? "";
                var personRole = GetString(person, "role") ?? "member";
                var item = new ComboBoxItem { Content = personName, Tag = (personName, personRole) };
                personBox.Items.Add(item);
            }
            personBox.SelectedIndex = 0;
            var entityBox = new TextBox { PlaceholderText = "occupancy sensor entity id, e.g. binary_sensor.gerron_office_occupancy", MinWidth = 340 };
            var roomBox = new TextBox { PlaceholderText = "room", MinWidth = 140 };
            var addSensor = new Button { Content = "Add sensor" };
            addSensor.Click += async (_, _) =>
            {
                if (personBox.SelectedItem is not ComboBoxItem selected || selected.Tag is not (string personName, string personRole))
                {
                    return;
                }
                await RunSetupAsync(() => Client().AddSetupPersonAsync(
                    personName,
                    personRole,
                    entityBox.Text.Trim(),
                    roomBox.Text.Trim()));
            };
            var sensorForm = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            sensorForm.Children.Add(personBox);
            sensorForm.Children.Add(entityBox);
            sensorForm.Children.Add(roomBox);
            sensorForm.Children.Add(addSensor);
            sensorPanel.Children.Add(sensorForm);
        }
        sensorExpander.Content = sensorPanel;
        StepContent.Children.Add(sensorExpander);

        var contextExpander = new Expander { Header = "Advanced household context", HorizontalAlignment = HorizontalAlignment.Left, MinWidth = 520 };
        var contextPanel = new StackPanel { Spacing = 10 };
        contextPanel.Children.Add(Body("Name the household states HAVEN should track — an input_boolean that means working late, vacation mode, and so on."));
        if (contexts.Count == 0)
        {
            contextPanel.Children.Add(Body("No contexts declared yet."));
        }
        foreach (var context in contexts)
        {
            var label = GetString(context, "label") ?? GetString(context, "context_id") ?? "unknown";
            var entityId = GetString(context, "entity_id") ?? "—";
            var contextId = GetString(context, "context_id") ?? label;
            var contextIdCapture = contextId;
            contextPanel.Children.Add(RemovalRow(label, entityId, () => Client().RemoveSetupContextAsync(contextIdCapture)));
        }
        var contextLabel = new TextBox { PlaceholderText = "label, e.g. Working late", MinWidth = 200 };
        var contextEntity = new TextBox { PlaceholderText = "entity id, e.g. input_boolean.working_late", MinWidth = 300 };
        var addContext = new Button { Content = "Add context" };
        addContext.Click += async (_, _) =>
            await RunSetupAsync(() => Client().AddSetupContextAsync(contextLabel.Text.Trim(), contextEntity.Text.Trim()));
        var contextForm = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        contextForm.Children.Add(contextLabel);
        contextForm.Children.Add(contextEntity);
        contextForm.Children.Add(addContext);
        contextPanel.Children.Add(contextForm);
        contextExpander.Content = contextPanel;
        StepContent.Children.Add(contextExpander);
    }

    // -- step 4: computer access -----------------------------------------------

    private void RenderComputer()
    {
        var computer = SetupSection("computer");
        var roots = GetArray(computer, "allowed_roots").Select(AsString).ToList();
        var enabled = GetBool(computer, "enabled");
        var readOnly = GetBool(computer, "read_only");

        StepContent.Children.Add(MicroHeading("Computer access"));
        StepContent.Children.Add(Body("Choose the folders HAVEN may read and index for search, recall, and document understanding. Computer access is separate from permission to change files."));
        StepContent.Children.Add(Body("Reading is the safe default. File organization is an explicit second choice, and every change still crosses HAVEN authority."));

        if (roots.Count == 0)
        {
            StepContent.Children.Add(Body("No folders added yet."));
        }
        foreach (var root in roots)
        {
            var rootCapture = root;
            StepContent.Children.Add(RemovalRow(rootCapture, "", () => Client().RemoveSetupComputerRootAsync(rootCapture)));
        }

        var input = new TextBox { PlaceholderText = @"folder path, e.g. C:\Users\you\Documents", MinWidth = 340 };
        var addRoot = new Button { Content = "Add folder" };
        addRoot.Click += async (_, _) =>
        {
            await RunSetupAsync(() => Client().AddSetupComputerRootAsync(input.Text.Trim()));
            input.Text = "";
        };
        StepContent.Children.Add(FieldRow(input, addRoot));

        var browse = new Button { Content = "Browse for folder…" };
        browse.Click += async (_, _) =>
        {
            var path = await PickFolderAsync();
            if (path is not null)
            {
                await RunSetupAsync(() => Client().AddSetupComputerRootAsync(path));
            }
        };
        StepContent.Children.Add(browse);

        var enableSwitch = new ToggleSwitch
        {
            Header = "Allow HAVEN to read/index these folders",
            IsOn = enabled,
            IsEnabled = roots.Count > 0,
        };
        var organizeSwitch = new ToggleSwitch
        {
            Header = "Allow HAVEN to organize files",
            IsOn = !readOnly,
            IsEnabled = enabled,
        };
        var enablePanel = new StackPanel { Spacing = 2 };
        enablePanel.Children.Add(enableSwitch);
        enablePanel.Children.Add(Caption(roots.Count > 0
            ? "Scans the folders above so HAVEN can search and recall them."
            : "Add a folder above first."));
        var organizePanel = new StackPanel { Spacing = 2 };
        organizePanel.Children.Add(organizeSwitch);
        organizePanel.Children.Add(Caption("Enables write capabilities; each action still requires authority and approval."));
        StepContent.Children.Add(enablePanel);
        StepContent.Children.Add(organizePanel);
        enableSwitch.Toggled += async (_, _) =>
            await RunSetupAsync(() => Client().SetSetupComputerAsync(enableSwitch.IsOn, !organizeSwitch.IsOn));
        organizeSwitch.Toggled += async (_, _) =>
            await RunSetupAsync(() => Client().SetSetupComputerAsync(enableSwitch.IsOn, !organizeSwitch.IsOn));

        if (enabled)
        {
            var scan = new Button { Content = "Scan now" };
            scan.Click += async (_, _) =>
            {
                var (success, envelope) = await RunSetupAsync(() => Client().ScanSetupComputerAsync(), refreshStatus: true);
                if (success && envelope.TryGetProperty("scanned", out var scanned))
                {
                    _info = $"Scanned {scanned} item(s)."
                        + (envelope.TryGetProperty("staled", out var staled) ? $" {staled} no longer present." : "");
                    Render();
                }
            };
            StepContent.Children.Add(scan);
        }
    }

    // -- step 5: connections -----------------------------------------------------

    private void RenderConnections()
    {
        var provider = SetupSection("provider");
        var discovery = SetupSection("discovery");
        var enrolled = GetArray(discovery, "enrolled");
        var enrolledIds = new HashSet<string>(enrolled
            .Select(row => GetString(row, "candidate_id"))
            .Where(id => id is not null)
            .Cast<string>());
        var providerConfigured = GetBool(provider, "configured");

        StepContent.Children.Add(MicroHeading("Home Assistant"));
        StepContent.Children.Add(Body("Home Assistant is one optional connection for the devices and state around you. HAVEN also works without it."));
        if (providerConfigured)
        {
            StepContent.Children.Add(Body("Connected: " + (GetString(provider, "base_url") ?? GetString(provider, "kind") ?? "—")));
        }
        else
        {
            StepContent.Children.Add(Body("If you already use Home Assistant, HAVEN can connect to it to see and control your existing lights, switches, thermostats, covers, cameras, and other supported devices."));
            StepContent.Children.Add(Body("You do not need Home Assistant to use HAVEN. You can skip this and use HAVEN for your computer, models, files, voice, and other providers."));

            var url = new TextBox { PlaceholderText = "http://homeassistant.local:8123", MinWidth = 340 };
            var token = new PasswordBox { PlaceholderText = "access token", MinWidth = 260 };
            var connect = new Button { Content = "Connect Home Assistant" };
            connect.Click += async (_, _) =>
            {
                await RunSetupAsync(() => Client().ConnectSetupProviderAsync(url.Text.Trim(), token.Password));
                token.Password = "";
            };
            var urlRow = FieldRow(url, connect);
            StepContent.Children.Add(urlRow);
            StepContent.Children.Add(Caption("Home Assistant address — where HAVEN can reach your Home Assistant server."));
            StepContent.Children.Add(token);
            StepContent.Children.Add(Caption("Home Assistant access token — Profile → Security → Long-Lived Access Tokens → Create Token. The token stays on this computer."));

            var divider = new TextBlock { Text = "────── or ──────", Opacity = 0.4 };
            StepContent.Children.Add(divider);
            var skip = new Button { Content = "Skip home setup" };
            skip.Click += async (_, _) =>
            {
                var (success, _) = await RunSetupAsync(() => Client().SkipSetupProviderAsync());
                if (success)
                {
                    GotoStep(6);
                }
            };
            StepContent.Children.Add(skip);
            StepContent.Children.Add(Body("HAVEN works without Home Assistant. You can add Home Assistant or other home providers later from Settings."));
        }

        StepContent.Children.Add(MicroHeading("Devices"));
        if (!providerConfigured)
        {
            StepContent.Children.Add(Body("No home provider is connected yet."));
            StepContent.Children.Add(Body("HAVEN can also discover devices through supported local providers, such as Bluetooth and network discovery, as those providers are enabled."));
            StepContent.Children.Add(Body("You can skip this and add devices later."));
        }

        var scanRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var scan = new Button { Content = providerConfigured ? "Scan" : "Check for available devices" };
        scan.Click += async (_, _) => await RunSetupAsync(() => Client().ScanSetupDiscoveryAsync(), refreshStatus: true);
        var skipDevices = new Button { Content = "Skip devices" };
        skipDevices.Click += (_, _) => GotoStep(6);
        scanRow.Children.Add(scan);
        scanRow.Children.Add(skipDevices);
        StepContent.Children.Add(scanRow);

        var candidates = GetArray(discovery, "candidates");
        if (candidates.Count == 0)
        {
            StepContent.Children.Add(Body("No candidates yet — run a scan."));
            return;
        }
        foreach (var candidate in candidates)
        {
            StepContent.Children.Add(CandidateCard(candidate, enrolledIds));
        }
    }

    private Grid CandidateCard(JsonElement candidate, HashSet<string> enrolledIds)
    {
        var candidateId = GetString(candidate, "candidate_id") ?? "unknown";
        var source = GetString(candidate, "source");
        var suggestedType = GetString(candidate, "suggested_device_type");
        var suggestedRoom = GetString(candidate, "suggested_room");
        var signal = candidate.ValueKind == JsonValueKind.Object
            && candidate.TryGetProperty("signal_strength", out var signalValue)
            && signalValue.ValueKind == JsonValueKind.Number
                ? (double?)signalValue.GetDouble()
                : null;

        var card = new Grid
        {
            ColumnSpacing = 12,
            Padding = new Thickness(12),
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
        };
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var head = new StackPanel { Spacing = 2 };
        var parts = candidateId + (source is not null ? " · " + source : "") + (signal is not null ? $" · signal {signal}" : "");
        head.Children.Add(new TextBlock { Text = parts, TextWrapping = TextWrapping.Wrap, FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Consolas") });
        head.Children.Add(new TextBlock
        {
            Text = (suggestedType ?? "device") + (suggestedRoom is not null ? " · " + suggestedRoom : ""),
            Opacity = 0.6,
        });
        card.Children.Add(head);

        if (enrolledIds.Contains(candidateId))
        {
            var badge = new TextBlock
            {
                Text = "ENROLLED",
                FontSize = 11,
                Opacity = 0.8,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(badge, 1);
            card.Children.Add(badge);
            return card;
        }

        var typeBox = new ComboBox { MinWidth = 110 };
        var options = DeviceTypes.Contains(suggestedType) || suggestedType is null
            ? DeviceTypes
            : DeviceTypes.Append(suggestedType).ToArray();
        foreach (var option in options)
        {
            typeBox.Items.Add(option);
        }
        typeBox.SelectedIndex = suggestedType is not null ? Array.IndexOf(options, suggestedType) : 0;
        var room = new TextBox { PlaceholderText = "room", Text = suggestedRoom ?? "", MinWidth = 120 };
        var enroll = new Button { Content = "Enroll" };
        enroll.Click += async (_, _) =>
        {
            var deviceType = typeBox.SelectedItem as string ?? "light";
            await RunSetupAsync(() => Client().EnrollSetupCandidateAsync(candidateId, deviceType, room.Text.Trim()));
        };
        var controls = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
        controls.Children.Add(typeBox);
        controls.Children.Add(room);
        controls.Children.Add(enroll);
        Grid.SetColumn(controls, 1);
        card.Children.Add(controls);
        return card;
    }

    // -- step 6: preferences -------------------------------------------------------

    private void RenderPreferences()
    {
        var preferences = SetupSection("preferences");

        StepContent.Children.Add(MicroHeading("Intelligence & voice"));
        StepContent.Children.Add(Body("Choose which optional capabilities HAVEN should use on this machine. Models can be installed or connected later."));

        var voice = new ToggleSwitch { Header = "Enable voice", IsOn = GetBool(preferences, "voice") };
        var voicePanel = new StackPanel { Spacing = 2 };
        voicePanel.Children.Add(voice);
        voicePanel.Children.Add(Caption("Use a microphone, wake-word model, speech recognition, and speech output when those components are available."));
        var intelligence = new ToggleSwitch { Header = "Enable model intelligence", IsOn = GetBool(preferences, "intelligence") };
        var intelligencePanel = new StackPanel { Spacing = 2 };
        intelligencePanel.Children.Add(intelligence);
        intelligencePanel.Children.Add(Caption("Allow HAVEN to use assigned local models or model endpoints for conversation and understanding. HAVEN’s authority system remains separate from whichever model you use."));
        StepContent.Children.Add(voicePanel);
        StepContent.Children.Add(intelligencePanel);
        voice.Toggled += async (_, _) =>
            await RunSetupAsync(() => Client().SetSetupPreferencesAsync(voice.IsOn, intelligence.IsOn));
        intelligence.Toggled += async (_, _) =>
            await RunSetupAsync(() => Client().SetSetupPreferencesAsync(voice.IsOn, intelligence.IsOn));
    }

    // -- step 7: finish --------------------------------------------------------------

    private void RenderFinish()
    {
        var dataDir = SetupSection("data_dir");
        var provider = SetupSection("provider");
        var discovery = SetupSection("discovery");
        var computer = SetupSection("computer");
        var household = SetupSection("household");
        var preferences = SetupSection("preferences");

        StepContent.Children.Add(MicroHeading("HAVEN is ready"));
        StepContent.Children.Add(ContextRow("Data folder", GetString(dataDir, "source") == "chosen" ? "custom" : "default"));
        StepContent.Children.Add(ContextRow(
            "Home",
            GetBool(provider, "configured")
                ? "connected · " + (GetString(provider, "base_url") ?? "")
                : "not connected"));
        var enrolledCount = GetArray(discovery, "enrolled").Count;
        StepContent.Children.Add(ContextRow("Devices enrolled", enrolledCount.ToString()));
        var roots = GetArray(computer, "allowed_roots").Count;
        StepContent.Children.Add(ContextRow(
            "Computer",
            GetBool(computer, "enabled")
                ? $"ready · {roots} {(roots == 1 ? "folder" : "folders")}"
                : "not connected"));
        var people = GetArray(household, "people");
        var owner = people.FirstOrDefault(person => GetString(person, "role") == "owner");
        StepContent.Children.Add(ContextRow(
            "Owner",
            owner.ValueKind == JsonValueKind.Object
                ? GetString(owner, "name") ?? GetString(owner, "person_id") ?? "not declared"
                : "not declared"));
        var contextsCount = GetArray(household, "contexts").Count;
        StepContent.Children.Add(ContextRow(
            "Household",
            people.Count == 0 && contextsCount == 0
                ? "not declared"
                : $"{people.Count} people · {contextsCount} contexts declared"));
        StepContent.Children.Add(ContextRow("Voice control", GetBool(preferences, "voice") ? "on" : "off"));
        StepContent.Children.Add(ContextRow("Model intelligence", GetBool(preferences, "intelligence") ? "on" : "off"));
        StepContent.Children.Add(Body("You can change any of these later from Settings."));
    }
}
