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
    // -- connection center (Product Pass phase 5) --------------------------------
    // The rail's connection indicator opens this dialog: per-channel status
    // (RPC + the push-only events pipe), manual reconnect for each, and a
    // per-domain fresh/pending-refresh readout built from the same
    // EventDomains vocabulary the invalidation pipeline already uses.  This
    // is a read-only diagnostic surface: it never mutates state itself
    // beyond the two reconnect actions, and it never forks the domain list
    // - both come from MainWindow.Events.cs so the two stay honest with each
    // other by construction.

    // A method, not a static field: a field initializer here would race
    // EventDomains's own static initializer in MainWindow.Events.cs, since
    // C# doesn't guarantee initializer order across partial-class files.
    private static string[] KnownDomains() =>
        EventDomains.Values.SelectMany(domains => domains).Distinct().OrderBy(d => d).ToArray();

    private async void OnConnectionCenterClicked(object sender, RoutedEventArgs args)
    {
        await ShowConnectionCenterAsync();
    }

    private async Task ShowConnectionCenterAsync()
    {
        var root = new StackPanel { Spacing = 16, MinWidth = 360 };

        root.Children.Add(BuildConnectionChannelSection());
        root.Children.Add(BuildDomainStatusSection());

        var diagnosticsLink = new Button
        {
            Content = "Open diagnostics",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            HorizontalAlignment = HorizontalAlignment.Left,
        };
        root.Children.Add(diagnosticsLink);

        var dialog = new ContentDialog
        {
            Title = "Connection",
            Content = new ScrollViewer { Content = root, MaxHeight = 480 },
            CloseButtonText = "Close",
            XamlRoot = RootGrid().XamlRoot,
        };
        diagnosticsLink.Click += (_, _) =>
        {
            dialog.Hide();
            SelectNavigation("settings");
        };

        await dialog.ShowAsync();
    }

    private StackPanel BuildConnectionChannelSection()
    {
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(new TextBlock { Text = "Channels", Style = (Style)Application.Current.Resources["HavenSectionTextStyle"] });

        section.Children.Add(ConnectionChannelRow(
            "HAVEN Core (RPC)",
            RpcChannelStatusText(),
            RpcChannelIsHealthy(),
            "Retry now",
            async () =>
            {
                if (_connection is null)
                {
                    return;
                }
                await _connection.RetryNowAsync();
                if (_connectionTask is { IsCompleted: true })
                {
                    _connectionTask = _connection.RunAsync();
                }
            }));

        section.Children.Add(ConnectionChannelRow(
            "Live updates (events)",
            _eventClient is { IsConnected: true } ? "Connected" : "Not connected",
            _eventClient is { IsConnected: true },
            "Reconnect",
            async () =>
            {
                EnsureEventClientAsync();
                await Task.CompletedTask;
            }));

        return section;
    }

    private string RpcChannelStatusText()
    {
        if (_connection is null)
        {
            return "Not started";
        }
        var baseText = _connection.State switch
        {
            ConnectionService.StateConnected => "Connected",
            ConnectionService.StateDegraded => "Connected with issues",
            ConnectionService.StateReconnecting => $"Reconnecting (attempt {_connection.Attempt}, retry in {_connection.NextRetryDelayMs}ms)",
            ConnectionService.StateConnecting => "Connecting…",
            ConnectionService.StateDisconnected => $"Disconnected after {_connection.Attempt} attempts",
            _ => "Starting…",
        };
        return baseText;
    }

    private bool RpcChannelIsHealthy() => _connection?.State == ConnectionService.StateConnected;

    private Border ConnectionChannelRow(string name, string status, bool healthy, string actionLabel, Func<Task> action)
    {
        var row = new Grid();
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var labels = new StackPanel { Spacing = 2 };
        labels.Children.Add(new TextBlock { Text = name, Style = (Style)Application.Current.Resources["HavenBodyTextStyle"] });
        labels.Children.Add(new TextBlock { Text = status, Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"], TextWrapping = TextWrapping.Wrap });
        Grid.SetColumn(labels, 0);
        row.Children.Add(labels);

        var chip = MakeChip(
            healthy ? "Online" : "Attention",
            healthy ? "HavenSuccessTintBrush" : "HavenWarningTintBrush",
            healthy ? "HavenSuccessBrush" : "HavenWarningBrush");
        chip.VerticalAlignment = VerticalAlignment.Center;
        chip.Margin = new Thickness(8, 0, 8, 0);
        Grid.SetColumn(chip, 1);
        row.Children.Add(chip);

        var button = new Button { Content = actionLabel, Style = (Style)Application.Current.Resources["HavenSecondaryButtonStyle"] };
        button.Click += async (_, _) => await action();
        Grid.SetColumn(button, 2);
        row.Children.Add(button);

        return WrapCard(new StackPanel { Children = { row } });
    }

    private StackPanel BuildDomainStatusSection()
    {
        var section = new StackPanel { Spacing = 8 };
        section.Children.Add(new TextBlock { Text = "Domains", Style = (Style)Application.Current.Resources["HavenSectionTextStyle"] });
        section.Children.Add(new TextBlock
        {
            Text = "Pending domains reconcile automatically the next time you open their page.",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            TextWrapping = TextWrapping.Wrap,
        });

        // StackPanel doesn't wrap; a fixed chunk size keeps this readable
        // without a WrapPanel dependency for ten short chips.
        var flow = new StackPanel { Spacing = 6 };
        foreach (var row in KnownDomains().Chunk(3))
        {
            var rowPanel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            foreach (var domain in row)
            {
                var pending = _dirtyDomains.Contains(domain);
                rowPanel.Children.Add(MakeChip(
                    domain,
                    pending ? "HavenWarningTintBrush" : "HavenSuccessTintBrush",
                    pending ? "HavenWarningBrush" : "HavenSuccessBrush"));
            }
            flow.Children.Add(rowPanel);
        }
        section.Children.Add(flow);

        return section;
    }
}
