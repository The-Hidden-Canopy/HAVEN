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
    // content padding tightens, and right-pane inspectors stack below their
    // lists instead of sitting beside them.

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

        // Memory: wide = list + right inspector; narrow = stacked single column.
        if (narrow)
        {
            MemorySplit.ColumnDefinitions.Clear();
            MemorySplit.RowDefinitions.Clear();
            MemorySplit.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            MemorySplit.RowDefinitions.Add(new RowDefinition { Height = Microsoft.UI.Xaml.GridLength.Auto });
            MemoryClaims.MaxHeight = 320;
            Grid.SetColumn(MemoryInspector, 0);
            Grid.SetRow(MemoryInspector, 1);
        }
        else
        {
            MemorySplit.RowDefinitions.Clear();
            MemorySplit.ColumnDefinitions.Clear();
            MemorySplit.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
            MemorySplit.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1.2, Microsoft.UI.Xaml.GridUnitType.Star) });
            MemoryClaims.MaxHeight = 560;
            Grid.SetRow(MemoryInspector, 0);
            Grid.SetColumn(MemoryInspector, 1);
        }
    }

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
