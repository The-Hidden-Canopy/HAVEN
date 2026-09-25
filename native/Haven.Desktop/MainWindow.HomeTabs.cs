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
    // -- home tabs --------------------------------------------------------------

    private string _homeTab = "rooms";

    private void OnHomeTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string tab)
        {
            SelectHomeTab(tab);
        }
    }

    private void SelectHomeTab(string tab)
    {
        _homeTab = tab;
        HomeTabRooms.Visibility = tab == "rooms" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabDevices.Visibility = tab == "devices" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabAutomations.Visibility = tab == "automations" ? Visibility.Visible : Visibility.Collapsed;
        HomeTabContexts.Visibility = tab == "contexts" ? Visibility.Visible : Visibility.Collapsed;
        foreach (var button in HomeTabs.Children.OfType<Button>())
        {
            button.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            button.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                button.Tag?.ToString() == tab ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
    }


}
