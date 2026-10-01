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
    // -- tablet adaptation (spec 06/07/21) -------------------------------------
    // Below ~900px the rail collapses to icons, touch targets grow, the
    // content padding tightens, and list/detail surfaces become explicit
    // drill-in pages instead of forcing a long stacked split onto a tablet.

    private const double NarrowWidth = 900;
    private bool _narrow;

    private void OnRootSizeChanged(object sender, SizeChangedEventArgs args)
    {
        ApplyAdaptiveLayout(args.NewSize.Width);
    }

    private void ApplyAdaptiveLayout(double width)
    {
        var narrow = width < NarrowWidth;
        if (narrow == _narrow && width > 0)
        {
            return;
        }
        _narrow = narrow;
        // Touch-safe floor for every density-tokened control app-wide (nav,
        // lists, cards, buttons, tabs) - layered on top of whichever
        // density the user picked, not a replacement for it.
        _themeService.SetTouchOverlay(narrow, this);
        ShellRoot().ColumnDefinitions[0].Width = new Microsoft.UI.Xaml.GridLength(narrow ? 72 : 228);

        foreach (var button in NavItems.Children.OfType<Button>())
        {
            if (button.Content is StackPanel panel)
            {
                foreach (var text in panel.Children.OfType<TextBlock>())
                {
                    text.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
                }
            }
            button.Height = narrow ? 44 : 38;
        }
        ReopenSetupButton.Height = narrow ? 44 : double.NaN;
        ProfileText.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;

        ContentScroll.Padding = narrow
            ? new Microsoft.UI.Xaml.Thickness(16, 12, 16, 20)
            : new Microsoft.UI.Xaml.Thickness(30, 16, 30, 24);

        // Master-detail splits: wide = list + right inspector; narrow =
        // one surface at a time with an explicit back action. Shared by
        // every list+inspector page so a new split only needs one call here.
        AdaptSplitGrid(MemorySplit, MemoryClaims, MemoryInspector, narrow, wideListHeight: 560, narrowListHeight: 320);
        AdaptSplitGrid(PeopleSplit, PeopleDirectory, PersonInspector, narrow, wideListHeight: 560, narrowListHeight: 320);
        // Search: 60/40 results/inspector (spec 22) rather than the other
        // splits' ~45/55 - the list is the primary surface here.
        AdaptSplitGrid(SearchSplit, SearchResults, SearchInspector, narrow, wideListHeight: 560, narrowListHeight: 320, listStar: 1.5, inspectorStar: 1);
        ApplyDrillInState(MemoryClaims, MemoryInspector, MemoryBackButton, narrow, HasSelectedItem(MemoryClaims));
        ApplyDrillInState(PeopleDirectory, PersonInspector, PersonBackButton, narrow, HasSelectedItem(PeopleDirectory));
        ApplyDrillInState(SearchResults, SearchInspector, SearchBackButton, narrow, HasSelectedItem(SearchResults));

        // Project detail: not a list+inspector shape (the left side is the
        // tabbed work area, not a ListView), so it collapses inline rather
        // than through AdaptSplitGrid.
        if (narrow)
        {
            ProjectDetailHost.ColumnDefinitions.Clear();
            ProjectDetailHost.RowDefinitions.Clear();
            ProjectDetailHost.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            ProjectDetailHost.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            Grid.SetColumn(ProjectContextRailCard, 0);
            Grid.SetRow(ProjectContextRailCard, 1);
        }
        else
        {
            ProjectDetailHost.RowDefinitions.Clear();
            ProjectDetailHost.ColumnDefinitions.Clear();
            ProjectDetailHost.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1.6, Microsoft.UI.Xaml.GridUnitType.Star) });
            ProjectDetailHost.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
            Grid.SetRow(ProjectContextRailCard, 0);
            Grid.SetColumn(ProjectContextRailCard, 1);
        }
    }

    private static void AdaptSplitGrid(
        Grid split, ListView list, FrameworkElement inspector, bool narrow,
        double wideListHeight, double narrowListHeight, double listStar = 1, double inspectorStar = 1.2)
    {
        if (narrow)
        {
            split.ColumnDefinitions.Clear();
            split.RowDefinitions.Clear();
            split.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            split.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            list.MaxHeight = narrowListHeight;
            Grid.SetColumn(inspector, 0);
            Grid.SetRow(inspector, 1);
        }
        else
        {
            split.RowDefinitions.Clear();
            split.ColumnDefinitions.Clear();
            split.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(listStar, Microsoft.UI.Xaml.GridUnitType.Star) });
            split.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(inspectorStar, Microsoft.UI.Xaml.GridUnitType.Star) });
            list.MaxHeight = wideListHeight;
            Grid.SetRow(inspector, 0);
            Grid.SetColumn(inspector, 1);
        }
    }

    private static bool HasSelectedItem(ListView list) =>
        list.SelectedItem is ListViewItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag);

    private static void ApplyDrillInState(
        ListView list, FrameworkElement inspector, Button backButton, bool narrow, bool hasSelection)
    {
        if (!narrow)
        {
            list.Visibility = Visibility.Visible;
            inspector.Visibility = Visibility.Visible;
            backButton.Visibility = Visibility.Collapsed;
            return;
        }

        list.Visibility = hasSelection ? Visibility.Collapsed : Visibility.Visible;
        inspector.Visibility = hasSelection ? Visibility.Visible : Visibility.Collapsed;
        backButton.Visibility = hasSelection ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OnMemoryBackClicked(object sender, RoutedEventArgs args) => MemoryClaims.SelectedItem = null;

    private void OnPersonBackClicked(object sender, RoutedEventArgs args) => PeopleDirectory.SelectedItem = null;

    private void OnSearchBackClicked(object sender, RoutedEventArgs args) => SearchResults.SelectedItem = null;

    private Grid ShellRoot() => RootGrid();

    private void UpdateLogo()
    {
        var source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(
            new Uri(RootGrid().ActualTheme == ElementTheme.Dark
                ? "ms-appx:///Assets/haven-logo-light.png"
                : "ms-appx:///Assets/haven-logo-dark.png"));
        LogoImage.Source = source;
        SplashLogo.Source = source;
    }

    private string? _connectionPipeName;
    private string? _connectionToken;
    private ConnectionService? _connection;
    private Task? _connectionTask;

    private async void OnLoaded(object sender, RoutedEventArgs args)
    {
        var pipeName = CommandLineValue("--pipe-name") ?? Environment.GetEnvironmentVariable("HAVEN_IPC_PIPE");
        var token = await ResolveAuthTokenAsync();
        if (string.IsNullOrWhiteSpace(pipeName) || string.IsNullOrWhiteSpace(token))
        {
            ConnectionText.Text = "Native Core not connected";
            ComposerStatus.Text = "Start HAVEN Core with a named-pipe endpoint to connect this native client.";
            SplashOverlay.Visibility = Visibility.Collapsed;
            return;
        }

        _connectionPipeName = pipeName;
        _connectionToken = token;
        _connection = new ConnectionService(
            connectOnce: async () =>
            {
                // Reconnect lifecycle: drop the dead pipe before re-authenticating.
                await (_client?.DisconnectAsync() ?? Task.CompletedTask);
                var client = new HavenCoreClient(_connectionPipeName!, _connectionToken!);
                await client.ConnectAsync();
                _client = client;
                return client;
            },
            onStateChanged: state => DispatcherQueue.TryEnqueue(() => ApplyConnectionState(state)),
            onReconciled: async () =>
            {
                await EnsureSetupAsync();
                ShowPage(_currentTag);
                SplashOverlay.Visibility = Visibility.Collapsed;
                EnsureEventClientAsync();
                // The MCP endpoint rides the same core connection; ApplyAsync
                // no-ops when disabled or already serving the configured port.
                await EnsureMcpHostAsync();
            });
        _connectionTask = _connection.RunAsync();
    }

    private void ApplyConnectionState(string state)
    {
        ConnectionText.Text = state switch
        {
            ConnectionService.StateConnected => "Connected",
            ConnectionService.StateDegraded => "Connected with issues",
            ConnectionService.StateReconnecting => "Reconnecting…",
            ConnectionService.StateConnecting => "Connecting to HAVEN Core…",
            _ => "Core offline",
        };
        switch (state)
        {
            case ConnectionService.StateConnected:
                ConnectionBanner.Visibility = Visibility.Collapsed;
                break;
            case ConnectionService.StateDegraded:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "Connected with issues — some data may be stale.";
                break;
            case ConnectionService.StateReconnecting:
            case ConnectionService.StateConnecting:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "Reconnecting to HAVEN Core…";
                break;
            default:
                ConnectionBanner.Visibility = Visibility.Visible;
                ConnectionBannerText.Text = "HAVEN Core is unreachable. Changes are on hold; your input is kept.";
                break;
        }
        // One-shot composer control: submissions are allowed only on a healthy
        // connection (spec section 11).
        ComposerSendButton.IsEnabled = state == ConnectionService.StateConnected && !_composerBusy;
    }

    private async void OnConnectionRetryClicked(object sender, RoutedEventArgs args)
    {
        if (_connection is null)
        {
            return;
        }
        await _connection.RetryNowAsync();
        // Retry Now from DISCONNECTED: the loop already exited, so restart it.
        if (_connectionTask is { IsCompleted: true })
        {
            _connectionTask = _connection.RunAsync();
        }
    }

}
