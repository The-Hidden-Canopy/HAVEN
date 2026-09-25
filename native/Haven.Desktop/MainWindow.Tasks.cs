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
    // -- tasks -------------------------------------------------------------------

    private async Task LoadTasksAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetTasksAsync(_taskView);
            TasksErrorText.Text = "";
            TasksStatusText.Text = "";
            RenderTasks(result.GetProperty("tasks"));
        }
        catch (Exception ex)
        {
            TasksStatusText.Text = "";
            TasksErrorText.Text = $"Could not load tasks: {ex.Message} Use Refresh to retry.";
        }
    }

    private void OnTaskViewClicked(object sender, RoutedEventArgs args)
    {
        if (sender is Button button && button.Tag is string view)
        {
            _taskView = view;
            foreach (var item in ((StackPanel)button.Parent).Children.OfType<Button>())
            {
                item.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenMutedTextBrush"];
                item.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
                    item == button ? "HavenAccentBrush" : "HavenStrokeBrush"];
            }
            _ = LoadTasksAsync();
        }
    }

    private void RenderTasks(JsonElement tasks)
    {
        TasksListPanel.Children.Clear();
        foreach (var task in Enumerate(tasks))
        {
            TasksListPanel.Children.Add(MakeTaskRow(task));
        }
        if (TasksListPanel.Children.Count == 0)
        {
            TasksListPanel.Children.Add(new TextBlock
            {
                Text = _taskView == "completed"
                    ? "Nothing completed yet."
                    : "Nothing here. Add a task above, or ask HAVEN in the composer.",
                Opacity = 0.72,
            });
        }
    }

    private Border MakeTaskRow(JsonElement task)
    {
        var taskId = GetString(task, "task_id") ?? "";
        var revision = (int)GetInt(task, "revision");
        var state = GetString(task, "state") ?? "open";
        var terminal = state is "done" or "cancelled";

        // Column widths mirror the static header Grid in MainWindow.xaml's
        // TasksPanel exactly (36 / * / 140 / 90 / 90 / 52) so rows and header
        // stay pixel-aligned - a real table, not a stack of cards (spec 25).
        var card = new Grid { ColumnSpacing = 10 };
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(36) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(1, Microsoft.UI.Xaml.GridUnitType.Star) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(140) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(90) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(90) });
        card.ColumnDefinitions.Add(new ColumnDefinition { Width = new Microsoft.UI.Xaml.GridLength(52) });

        var complete = new CheckBox
        {
            IsChecked = state == "done",
            IsEnabled = !terminal,
            VerticalAlignment = VerticalAlignment.Center,
        };
        complete.Checked += async (_, _) =>
        {
            if (_client is null)
            {
                return;
            }
            try
            {
                var result = await _client.CompleteTaskAsync(taskId, revision);
                if (result.ValueKind == JsonValueKind.Object
                    && result.TryGetProperty("ok", out var ok) && !ok.GetBoolean())
                {
                    TasksErrorText.Text = result.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                        ? error.GetString() ?? "Completion refused."
                        : "Completion refused.";
                    await LoadTasksAsync();
                    return;
                }
                if (result.TryGetProperty("woken_task_ids", out var woken) && woken.GetArrayLength() > 0)
                {
                    TasksStatusText.Text = $"Unblocked {woken.GetArrayLength()} dependent task(s).";
                }
                await LoadTasksAsync();
                await LoadProjectsAsync();
            }
            catch (Exception ex)
            {
                TasksErrorText.Text = ex.Message;
            }
        };
        card.Children.Add(complete);

        // Task column: title + inline state chips only (no project/due text
        // buried here anymore - those get their own columns below).
        var titleRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
        titleRow.Children.Add(new TextBlock
        {
            Text = GetString(task, "title") ?? taskId,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        });
        if (state == "blocked")
        {
            var blockedCount = Enumerate(task, "blocked_by").Count();
            titleRow.Children.Add(MakeChip(
                blockedCount > 0 ? $"Blocked · {blockedCount}" : "Blocked",
                "HavenWarningTintBrush", "HavenWarningBrush"));
        }
        else if (state == "proposed")
        {
            titleRow.Children.Add(MakeChip("Proposed", "HavenVioletTintBrush", "HavenVioletBrush"));
        }
        Grid.SetColumn(titleRow, 1);
        card.Children.Add(titleRow);

        // Project column.
        var projectTitle = GetString(task, "project_title");
        var projectText = new TextBlock
        {
            Text = projectTitle ?? "—",
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            VerticalAlignment = VerticalAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis,
        };
        Grid.SetColumn(projectText, 2);
        card.Children.Add(projectText);

        // Priority column.
        var priority = GetString(task, "priority");
        if (priority is not null)
        {
            var (tint, foreground) = priority switch
            {
                "high" => ("HavenOrangeTintBrush", "HavenOrangeBrush"),
                "medium" => ("HavenWarningTintBrush", "HavenWarningBrush"),
                _ => ("HavenStrokeBrush", "HavenMutedTextBrush"),
            };
            var priorityChip = MakeChip(Sentence(priority), tint, foreground);
            priorityChip.HorizontalAlignment = HorizontalAlignment.Left;
            priorityChip.VerticalAlignment = VerticalAlignment.Center;
            Grid.SetColumn(priorityChip, 3);
            card.Children.Add(priorityChip);
        }

        // Due column - completed tasks show their completion date instead.
        var dueText = terminal && GetString(task, "completed_at") is { } completedAt
            ? $"Done {FormatDue(completedAt) ?? completedAt}"
            : FormatDue(GetString(task, "due_at")) ?? "—";
        var due = new TextBlock
        {
            Text = dueText,
            Style = (Style)Application.Current.Resources["HavenMetadataTextStyle"],
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(due, 4);
        card.Children.Add(due);

        var edit = new Button
        {
            Content = "Edit",
            Style = (Style)Application.Current.Resources["HavenQuietButtonStyle"],
            VerticalAlignment = VerticalAlignment.Center,
            Padding = new Microsoft.UI.Xaml.Thickness(8, 4, 8, 4),
        };
        edit.Click += async (_, _) => await EditTaskDialogAsync(task, GetString(task, "project_id"));
        Grid.SetColumn(edit, 5);
        card.Children.Add(edit);

        var wrap = new StackPanel();
        wrap.Children.Add(card);
        return WrapCard(wrap);
    }

    private static string? FormatDue(string? iso)
    {
        if (iso is null || !DateTimeOffset.TryParse(iso, out var parsed))
        {
            return null;
        }
        var local = parsed.LocalDateTime;
        return local.Date == DateTime.Today
            ? local.ToString("h:mm tt")
            : local.ToString("MMM d");
    }

    private async void OnTaskFastAddClicked(object sender, RoutedEventArgs args)
    {
        await FastAddTaskAsync();
    }

    private async void OnTaskFastAddKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key == VirtualKey.Enter)
        {
            args.Handled = true;
            await FastAddTaskAsync();
        }
    }

    private async Task FastAddTaskAsync()
    {
        if (_client is null || string.IsNullOrWhiteSpace(TaskFastAddBox.Text))
        {
            return;
        }
        var title = TaskFastAddBox.Text.Trim();
        await RunProjectsMutationAsync(() => _client.CreateTaskAsync(title));
        TaskFastAddBox.Text = "";
    }

    private async Task EditTaskDialogAsync(JsonElement task, string? presetProjectId)
    {
        if (_client is null)
        {
            return;
        }
        var editing = task.ValueKind == JsonValueKind.Object;
        var taskId = editing ? GetString(task, "task_id") ?? "" : "";
        var revision = editing ? (int)GetInt(task, "revision") : 0;
        var title = new TextBox { Text = editing ? GetString(task, "title") ?? "" : "", PlaceholderText = "What needs doing?", MinWidth = 320 };
        var detail = new TextBox
        {
            Text = editing ? GetString(task, "detail") ?? "" : "",
            PlaceholderText = "Detail (optional)",
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 64,
        };
        var priority = new ComboBox { MinWidth = 200 };
        priority.Items.Add("None");
        foreach (var option in new[] { "high", "medium", "low" })
        {
            priority.Items.Add(Sentence(option));
        }
        var currentPriority = editing ? GetString(task, "priority") : null;
        priority.SelectedIndex = currentPriority is null ? 0 : new[] { "high", "medium", "low" }.ToList().IndexOf(currentPriority) + 1;

        var project = new ComboBox { MinWidth = 200 };
        project.Items.Add("No project");
        var projectIds = new List<string>();
        foreach (var candidate in Enumerate(_projects))
        {
            var id = GetString(candidate, "project_id");
            if (id is null)
            {
                continue;
            }
            projectIds.Add(id);
            project.Items.Add(GetString(candidate, "title") ?? id);
        }
        var selectedProject = editing ? GetString(task, "project_id") : presetProjectId;
        project.SelectedIndex = selectedProject is null ? 0 : Math.Max(0, projectIds.IndexOf(selectedProject) + 1);

        var due = new TextBox
        {
            Text = editing ? (GetString(task, "due_at") ?? "") : "",
            PlaceholderText = "Due (ISO, optional) e.g. 2026-10-01T17:00:00+00:00",
            MinWidth = 320,
        };

        var fields = new StackPanel { Spacing = 8, MinWidth = 380 };
        fields.Children.Add(new TextBlock { Text = "Title" });
        fields.Children.Add(title);
        fields.Children.Add(new TextBlock { Text = "Detail" });
        fields.Children.Add(detail);
        fields.Children.Add(new TextBlock { Text = "Priority" });
        fields.Children.Add(priority);
        fields.Children.Add(new TextBlock { Text = "Project" });
        fields.Children.Add(project);
        fields.Children.Add(new TextBlock { Text = "Due" });
        fields.Children.Add(due);

        ComboBox? state = null;
        if (editing)
        {
            var stateValues = new[] { "open", "in_progress", "blocked", "proposed" };
            state = new ComboBox { MinWidth = 200 };
            foreach (var option in stateValues)
            {
                state.Items.Add(Sentence(option));
            }
            state.SelectedIndex = Math.Max(0, Array.IndexOf(stateValues, GetString(task, "state") ?? "open"));
            fields.Children.Add(new TextBlock { Text = "State" });
            fields.Children.Add(state);
        }

        var dialog = new ContentDialog
        {
            Title = editing ? "Edit task" : "New task",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add task",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(title.Text))
        {
            return;
        }
        var projectValue = project.SelectedIndex <= 0 ? null : projectIds[project.SelectedIndex - 1];
        var priorityValue = priority.SelectedIndex <= 0 ? null : new[] { "high", "medium", "low" }[priority.SelectedIndex - 1];
        var dueValue = string.IsNullOrWhiteSpace(due.Text) ? null : due.Text.Trim();
        if (editing && state is not null)
        {
            var stateValues = new[] { "open", "in_progress", "blocked", "proposed" };
            var stateValue = stateValues[Math.Max(0, state.SelectedIndex)];
            await RunProjectsMutationAsync(() => _client.UpdateTaskAsync(
                taskId, revision, title.Text.Trim(), detail.Text.Trim(), stateValue,
                priorityValue, dueValue, projectValue));
        }
        else
        {
            await RunProjectsMutationAsync(() => _client.CreateTaskAsync(
                title.Text.Trim(), projectValue, detail.Text.Trim(), priorityValue, dueValue));
        }
    }

}
