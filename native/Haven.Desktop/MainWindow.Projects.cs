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
    // -- projects & tasks --------------------------------------------------------

    private string _projectFilter = "";
    private string _taskView = "today";
    private JsonElement _projects = default;
    private string? _openProjectId;

    private async Task LoadProjectsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetProjectsAsync(
                string.IsNullOrWhiteSpace(_projectFilter) ? null : _projectFilter);
            _projects = result.GetProperty("projects").Clone();
            ProjectsErrorText.Text = "";
            ProjectsStatusText.Text = "";
            RenderProjectGallery();
            if (_openProjectId is not null)
            {
                await RenderProjectDetailAsync(_openProjectId);
            }
        }
        catch (Exception ex)
        {
            ProjectsStatusText.Text = "";
            ProjectsErrorText.Text = $"Could not load projects: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderProjectGallery()
    {
        var selected = _openProjectId;
        ProjectGallery.Items.Clear();
        foreach (var project in Enumerate(_projects))
        {
            var projectId = GetString(project, "project_id") ?? "";
            var card = new StackPanel { Spacing = 6, MinWidth = 220 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(project, "title") ?? projectId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(ProjectStatusChip(GetString(project, "status") ?? "active"));
            card.Children.Add(head);
            var description = GetString(project, "description");
            if (!string.IsNullOrWhiteSpace(description))
            {
                card.Children.Add(new TextBlock
                {
                    Text = description,
                    Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                    MaxWidth = 260,
                });
            }
            var done = GetInt(project, "tasks_done");
            var total = GetInt(project, "tasks_total");
            if (total > 0)
            {
                card.Children.Add(new ProgressBar
                {
                    Value = total == 0 ? 0 : done * 100.0 / total,
                    Maximum = 100,
                    MinHeight = 4,
                    MaxHeight = 4,
                });
            }
            var counts = new List<string>
            {
                $"{GetInt(project, "files_count")} files",
                total == 1 ? "1 task" : $"{total} tasks",
            };
            if (GetInt(project, "tasks_blocked") > 0)
            {
                counts.Add($"{GetInt(project, "tasks_blocked")} blocked");
            }
            card.Children.Add(new TextBlock
            {
                Text = string.Join(" · ", counts),
                Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            });
            ProjectGallery.Items.Add(new ListViewItem
            {
                Tag = projectId,
                IsSelected = projectId == selected,
                Content = new Border
                {
                    Style = (Style)Application.Current.Resources["HavenCardStyle"],
                    MinWidth = 240,
                    Child = card,
                },
            });
        }
        if (ProjectGallery.Items.Count == 0)
        {
            ProjectGallery.Items.Add(new TextBlock
            {
                Text = "No projects here yet. New project starts one.",
                Opacity = 0.72,
            });
        }
    }

    private static Border ProjectStatusChip(string status)
    {
        var (tint, foreground, label) = status switch
        {
            "completed" => ("HavenSuccessTintBrush", "HavenSuccessBrush", "Completed"),
            "planning" => ("HavenVioletTintBrush", "HavenVioletBrush", "Planning"),
            "on_hold" => ("HavenWarningTintBrush", "HavenWarningBrush", "On hold"),
            "archived" => ("HavenStrokeBrush", "HavenMutedTextBrush", "Archived"),
            _ => ("HavenAccentTintBrush", "HavenAccentBrush", "Active"),
        };
        return MakeChip(label, tint, foreground);
    }

    private void OnProjectFilterClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button)
        {
            _projectFilter = button.Tag?.ToString() ?? "";
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            _ = LoadProjectsAsync();
        }
    }

    private async void OnProjectSelected(object sender, SelectionChangedEventArgs args)
    {
        if (ProjectGallery.SelectedItem is ListViewItem item && item.Tag is string projectId)
        {
            _openProjectId = projectId;
            await RenderProjectDetailAsync(projectId);
        }
    }

    private void OnCloseProjectDetailClicked(object sender, RoutedEventArgs args)
    {
        _openProjectId = null;
        ProjectDetailHost.Visibility = Visibility.Collapsed;
        ProjectGallery.Visibility = Visibility.Visible;
    }

    private async Task RenderProjectDetailAsync(string projectId)
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetProjectAsync(projectId);
            if (result.ValueKind == JsonValueKind.Object
                && result.TryGetProperty("ok", out var ok)
                && !ok.GetBoolean())
            {
                ProjectsErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the request."
                    : "HAVEN Core refused the request.";
                return;
            }
            var project = result.GetProperty("project");
            ProjectGallery.Visibility = Visibility.Collapsed;
            ProjectDetailHost.Visibility = Visibility.Visible;
            ProjectDetailHost.Children.Clear();

            var head = new StackPanel { Spacing = 4 };
            var back = new Button
            {
                Content = "← All projects",
                Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
                HorizontalAlignment = HorizontalAlignment.Left,
            };
            back.Click += OnCloseProjectDetailClicked;
            head.Children.Add(back);
            var titleRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
            titleRow.Children.Add(new TextBlock
            {
                Text = GetString(project, "title") ?? projectId,
                FontSize = 20,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            titleRow.Children.Add(ProjectStatusChip(GetString(project, "status") ?? "active"));
            head.Children.Add(titleRow);
            var description = GetString(project, "description");
            if (!string.IsNullOrWhiteSpace(description))
            {
                head.Children.Add(new TextBlock
                {
                    Text = description,
                    Style = (Style)Application.Current.Resources["HavenBodyTextStyle"],
                    TextWrapping = TextWrapping.Wrap,
                });
            }
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var addTask = new Button
            {
                Content = "Add task",
                Style = (Style)Application.Current.Resources["HavenPrimaryButtonStyle"],
            };
            addTask.Click += async (_, _) => await EditTaskDialogAsync(default, projectId);
            var edit = new Button
            {
                Content = "Edit",
                Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            };
            edit.Click += async (_, _) => await EditProjectDialogAsync(project);
            var archive = new Button
            {
                Content = "Archive",
                Style = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            };
            archive.Click += async (_, _) => await ArchiveProjectAsync(project);
            actions.Children.Add(addTask);
            actions.Children.Add(edit);
            actions.Children.Add(archive);
            head.Children.Add(actions);
            ProjectDetailHost.Children.Add(head);

            var tabs = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
            foreach (var (label, key) in new[] { ("Tasks", "tasks"), ("Files", "files"), ("People", "people") })
            {
                var tab = new Button
                {
                    Content = label,
                    Tag = key,
                    Style = (Style)Application.Current.Resources["HavenTabButtonStyle"],
                };
                tab.Click += async (_, _) => await RenderProjectTabAsync(project, key);
                tabs.Children.Add(tab);
            }
            ProjectDetailHost.Children.Add(tabs);

            var body = new StackPanel { Spacing = 8 };
            ProjectDetailHost.Children.Add(body);
            await RenderProjectTabAsync(project, "tasks");
        }
        catch (Exception ex)
        {
            ProjectsErrorText.Text = ex.Message;
        }
    }

    private async Task RenderProjectTabAsync(JsonElement project, string tab)
    {
        var body = ProjectDetailHost.Children.OfType<StackPanel>().LastOrDefault();
        if (body is null || _client is null)
        {
            return;
        }
        body.Children.Clear();
        var projectId = GetString(project, "project_id") ?? "";
        if (tab == "tasks")
        {
            var tasks = await _client.GetTasksAsync("all");
            foreach (var task in Enumerate(tasks.GetProperty("tasks")))
            {
                if (GetString(task, "project_id") == projectId)
                {
                    body.Children.Add(MakeTaskRow(task));
                }
            }
            if (body.Children.Count == 0)
            {
                body.Children.Add(new TextBlock { Text = "No tasks in this project yet.", Opacity = 0.72 });
            }
        }
        else if (tab == "files")
        {
            var attached = await _client.GetProjectResourcesAsync(projectId);
            foreach (var resource in Enumerate(attached.GetProperty("resources")))
            {
                var resourceId = GetString(resource, "resource_id") ?? "";
                var resourcePanel = new StackPanel { Spacing = 2 };
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                row.Children.Add(new TextBlock
                {
                    Text = GetString(resource, "title") ?? resourceId,
                    VerticalAlignment = VerticalAlignment.Center,
                });
                var detach = new Button { Content = "Detach", VerticalAlignment = VerticalAlignment.Center };
                detach.Click += async (_, _) =>
                {
                    await RunProjectsMutationAsync(() => _client!.DetachProjectResourceAsync(projectId, resourceId));
                    await RenderProjectDetailAsync(projectId);
                };
                row.Children.Add(detach);
                resourcePanel.Children.Add(row);
                resourcePanel.Children.Add(new TextBlock
                {
                    Text = $"{GetString(resource, "resource_type")} · {resourceId}",
                    Style = (Style)Application.Current.Resources["HavenMonoTextStyle"],
                });
                body.Children.Add(WrapCard(resourcePanel));
            }
            var attachBox = new TextBox { PlaceholderText = "Resource id to attach, e.g. file:proposal.docx", MinWidth = 320 };
            var attach = new Button { Content = "Attach" };
            attach.Click += async (_, _) =>
            {
                if (string.IsNullOrWhiteSpace(attachBox.Text))
                {
                    return;
                }
                await RunProjectsMutationAsync(() => _client!.AttachProjectResourceAsync(projectId, attachBox.Text.Trim()));
                await RenderProjectDetailAsync(projectId);
            };
            var attachRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            attachRow.Children.Add(attachBox);
            attachRow.Children.Add(attach);
            body.Children.Add(attachRow);
        }
        else
        {
            var people = Enumerate(project, "people_ids").Select(id => id.GetString()).Where(id => id is not null).ToList();
            if (people.Count == 0)
            {
                body.Children.Add(new TextBlock
                {
                    Text = "Assign tasks to people (edit a task) and they appear here.",
                    Opacity = 0.72,
                });
            }
            foreach (var personId in people)
            {
                body.Children.Add(MakeChip(personId!, "HavenAccentTintBrush", "HavenAccentBrush"));
            }
        }
    }

    private async Task RunProjectsMutationAsync(Func<Task<JsonElement>> operation)
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
                var message = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString()
                    : "HAVEN Core refused the change.";
                ProjectsErrorText.Text = message ?? "HAVEN Core refused the change.";
                TasksErrorText.Text = message ?? "HAVEN Core refused the change.";
                return;
            }
            ProjectsErrorText.Text = "";
            TasksErrorText.Text = "";
            await LoadProjectsAsync();
            await LoadTasksAsync();
        }
        catch (Exception ex)
        {
            ProjectsErrorText.Text = ex.Message;
            TasksErrorText.Text = ex.Message;
        }
    }

    private async void OnNewProjectClicked(object sender, RoutedEventArgs args)
    {
        await EditProjectDialogAsync(default);
    }

    private async Task EditProjectDialogAsync(JsonElement project)
    {
        if (_client is null)
        {
            return;
        }
        var editing = project.ValueKind == JsonValueKind.Object;
        var projectId = editing ? GetString(project, "project_id") ?? "" : "";
        var revision = editing ? (int)GetInt(project, "revision") : 0;
        var title = new TextBox { Text = editing ? GetString(project, "title") ?? "" : "", PlaceholderText = "Project title", MinWidth = 320 };
        var description = new TextBox
        {
            Text = editing ? GetString(project, "description") ?? "" : "",
            PlaceholderText = "What does done look like?",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 72,
        };
        var status = new ComboBox { MinWidth = 200 };
        foreach (var option in new[] { "active", "planning", "on_hold", "completed" })
        {
            status.Items.Add(Sentence(option));
        }
        var statusValues = new[] { "active", "planning", "on_hold", "completed" };
        status.SelectedIndex = editing
            ? Math.Max(0, Array.IndexOf(statusValues, GetString(project, "status") ?? "active"))
            : 0;
        var fields = new StackPanel { Spacing = 8, MinWidth = 360 };
        fields.Children.Add(new TextBlock { Text = "Title" });
        fields.Children.Add(title);
        fields.Children.Add(new TextBlock { Text = "Goal" });
        fields.Children.Add(description);
        fields.Children.Add(new TextBlock { Text = "Status" });
        fields.Children.Add(status);
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit project" : "New project",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Create",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(title.Text))
        {
            return;
        }
        var statusValue = statusValues[Math.Max(0, status.SelectedIndex)];
        if (editing)
        {
            await RunProjectsMutationAsync(() => _client.UpdateProjectAsync(
                projectId, revision, title.Text.Trim(), description.Text.Trim(), statusValue));
        }
        else
        {
            await RunProjectsMutationAsync(() => _client.CreateProjectAsync(
                title.Text.Trim(), description.Text.Trim(), statusValue));
        }
    }

    private async Task ArchiveProjectAsync(JsonElement project)
    {
        if (_client is null)
        {
            return;
        }
        var title = GetString(project, "title") ?? "this project";
        var dialog = new ContentDialog
        {
            Title = $"Archive {title}?",
            Content = new TextBlock
            {
                Text = "The project is tombstoned and leaves the default list. Its tasks and attached resources stay exactly where they are.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Archive",
            CloseButtonText = "Cancel",
            PrimaryButtonStyle = (Style)Application.Current.Resources["HavenDangerButtonStyle"],
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        _openProjectId = null;
        ProjectDetailHost.Visibility = Visibility.Collapsed;
        ProjectGallery.Visibility = Visibility.Visible;
        await RunProjectsMutationAsync(() => _client.ArchiveProjectAsync(GetString(project, "project_id") ?? ""));
    }

}
