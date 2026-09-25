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
    private void SelectNavigation(string tag)
    {
        _currentTag = tag;
        // Rail selected state: filled blue rounded pill (spec 08).
        foreach (var button in NavItems.Children.OfType<Button>())
        {
            var selected = button.Tag?.ToString() == tag;
            button.Background = selected
                ? (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenAccentBrush"]
                : new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent);
            button.Foreground = selected
                ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White)
                : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenMutedTextBrush"];
            button.FontWeight = selected
                ? Microsoft.UI.Text.FontWeights.SemiBold
                : Microsoft.UI.Text.FontWeights.Normal;
        }
        ShowPage(tag);
        // Showing a page reloads its domains; drop their dirty marks.
        _dirtyDomains.ExceptWith(DomainsForPage(tag));
    }

    private void ShowPage(string tag)
    {
        TodayPanel.Visibility = tag == "today" ? Visibility.Visible : Visibility.Collapsed;
        SearchPanel.Visibility = tag == "search" ? Visibility.Visible : Visibility.Collapsed;
        ProjectsPanel.Visibility = tag == "projects" ? Visibility.Visible : Visibility.Collapsed;
        TasksPanel.Visibility = tag == "tasks" ? Visibility.Visible : Visibility.Collapsed;
        PeoplePanel.Visibility = tag == "people" ? Visibility.Visible : Visibility.Collapsed;
        MemoryPanel.Visibility = tag == "memory" ? Visibility.Visible : Visibility.Collapsed;
        ComputerPanel.Visibility = tag == "computer" ? Visibility.Visible : Visibility.Collapsed;
        CommunicationsPanel.Visibility = tag == "communications" ? Visibility.Visible : Visibility.Collapsed;
        HomePanel.Visibility = tag == "home" ? Visibility.Visible : Visibility.Collapsed;
        ModelsPanel.Visibility = tag == "models" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPanel.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed;
        (PageTitle.Text, PageDescription.Text) = tag switch
        {
            "today" => ("Today", "What matters now, and the fastest way to act on it."),
            "search" => ("Search your life", "Files, people, claims and anything else HAVEN can see, in one ranked list."),
            "projects" => ("Projects", "Bodies of work shared across files, tasks and people."),
            "tasks" => ("Tasks", "Commitments and next actions."),
            "people" => ("People", "Who lives here and how HAVEN senses their presence."),
            "memory" => ("Memory", "What HAVEN knows, where it came from, and how certain it is."),
            "computer" => ("Computer", "Your files, applications, windows and activity."),
            "communications" => ("Communications", "Email, messages and threads with their context."),
            "home" => ("Home", "Rooms, devices, automations and contexts. Rooms are yours even without a smart-home provider."),
            "models" => ("Models", "Local, downloaded and external intelligence. HAVEN runs on any mix of them."),
            "settings" => ("Settings", "Control boundaries: startup, storage, privacy, connections, about."),
            _ => ("HAVEN", ""),
        };
        if (tag == "models")
        {
            _ = LoadModelsAsync(silent: true);
        }
        switch (tag)
        {
            case "today":
                _ = LoadTodayAsync();
                break;
            case "search":
                SearchBox.Focus(FocusState.Programmatic);
                break;
            case "people":
                PeopleStatusText.Text = "Loading…";
                _ = LoadPeopleAsync();
                break;
            case "projects":
                ProjectsStatusText.Text = "Loading…";
                _ = LoadProjectsAsync();
                break;
            case "tasks":
                TasksStatusText.Text = "Loading…";
                _ = LoadTasksAsync();
                break;
            case "memory":
                _ = LoadMemoryAsync();
                break;
            case "computer":
                ComputerStatusText.Text = "Loading…";
                _ = LoadComputerTabAsync();
                break;
            case "communications":
                CommsStatusText.Text = "Loading…";
                _ = LoadCommsTabAsync();
                break;
            case "home":
                HomeStatusText.Text = "Loading…";
                _ = LoadRoomsAsync();
                _ = LoadAutomationsAsync();
                _ = LoadContextsAsync();
                break;
            case "models":
                _ = LoadModelsAsync();
                break;
            case "settings":
                SettingsStatusText.Text = "Loading…";
                _ = LoadSettingsAsync();
                break;
        }
    }

}
