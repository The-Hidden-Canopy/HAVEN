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
    // -- people & contexts --------------------------------------------------

    private JsonElement _people = default;
    private JsonElement _contexts = default;

    private async Task LoadContextsAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var contexts = await _client.GetContextsAsync();
            _contexts = contexts.GetProperty("contexts").Clone();
            RenderContexts();
        }
        catch (Exception ex)
        {
            RoomsErrorText.Text = $"Could not load contexts: {ex.Message}";
        }
    }

    private async Task LoadPeopleAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var people = await _client.GetPeopleAsync();
            _people = people.GetProperty("people").Clone();
            PeopleErrorText.Text = "";
            PeopleStatusText.Text = "";
            RenderPeople();
        }
        catch (Exception ex)
        {
            PeopleStatusText.Text = "";
            PeopleErrorText.Text = $"Could not load people: {ex.Message} Use Refresh to retry.";
        }
    }

    private void RenderPeople()
    {
        PeopleList.Children.Clear();
        foreach (var person in Enumerate(_people))
        {
            var personId = GetString(person, "person_id") ?? "";
            var name = GetString(person, "name") ?? personId;
            var role = Sentence(GetString(person, "role") ?? "member");

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = name,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            head.Children.Add(new TextBlock { Text = role, Opacity = 0.72 });
            card.Children.Add(head);

            var present = person.TryGetProperty("present", out var presentValue)
                && presentValue.ValueKind == JsonValueKind.True;
            var room = GetString(person, "room");
            card.Children.Add(new TextBlock
            {
                Text = present
                    ? (room is not null ? $"Present · {room}" : "Present")
                    : "Not present",
                Opacity = 0.72,
            });

            var sources = Enumerate(person, "sources").ToList();
            if (sources.Count > 0)
            {
                card.Children.Add(new TextBlock
                {
                    Text = string.Join(" · ", sources.Select(source =>
                        $"presence: {GetString(source, "entity_id") ?? "?"} in {GetString(source, "room_id") ?? "?"}")),
                    Opacity = 0.6,
                    TextWrapping = TextWrapping.Wrap,
                });
            }

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditPersonAsync(person);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemovePersonAsync(personId, name);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            PeopleList.Children.Add(WrapCard(card));
        }
        if (PeopleList.Children.Count == 0)
        {
            PeopleList.Children.Add(new TextBlock
            {
                Text = "No one is declared yet. Add the people HAVEN should know about.",
                Opacity = 0.72,
            });
        }
    }

    private void RenderContexts()
    {
        ContextsList.Children.Clear();
        foreach (var context in Enumerate(_contexts))
        {
            var contextId = GetString(context, "context_id") ?? "";

            var card = new StackPanel { Spacing = 4 };
            var head = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            head.Children.Add(new TextBlock
            {
                Text = GetString(context, "label") ?? contextId,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            });
            var active = context.TryGetProperty("active", out var activeValue)
                && activeValue.ValueKind == JsonValueKind.True;
            var state = new TextBlock
            {
                Text = active ? "Active" : "Inactive",
                Opacity = 0.72,
            };
            if (active)
            {
                state.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["HavenSuccessBrush"];
            }
            head.Children.Add(state);
            card.Children.Add(head);
            card.Children.Add(new TextBlock
            {
                Text = GetString(context, "entity_id") ?? "",
                Opacity = 0.6,
            });

            var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var edit = new Button { Content = "Edit" };
            edit.Click += async (_, _) => await EditContextAsync(context);
            var remove = new Button { Content = "Remove" };
            remove.Click += async (_, _) => await RemoveContextAsync(contextId);
            buttons.Children.Add(edit);
            buttons.Children.Add(remove);
            card.Children.Add(buttons);

            ContextsList.Children.Add(WrapCard(card));
        }
        if (ContextsList.Children.Count == 0)
        {
            ContextsList.Children.Add(new TextBlock
            {
                Text = "No contexts declared. Contexts map a household entity's \"on\" to a meaning HAVEN can reason about.",
                Opacity = 0.72,
                TextWrapping = TextWrapping.Wrap,
            });
        }
    }

    private static Border WrapCard(StackPanel content) => new()
    {
        Style = (Style)Application.Current.Resources["HavenCardStyle"],
        Child = content,
    };

    /* Status chip (spec 13): small rounded pill, ~20% tinted background,
       solid accent text. */
    private static Border MakeChip(string text, string tintResource, string foregroundResource)
    {
        var chip = new Border
        {
            CornerRadius = new Microsoft.UI.Xaml.CornerRadius(10),
            Padding = new Microsoft.UI.Xaml.Thickness(8, 2, 8, 2),
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[tintResource],
            Child = new TextBlock
            {
                Text = text,
                FontSize = 11.5,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[foregroundResource],
            },
        };
        ToolTipService.SetToolTip(chip, text);
        return chip;
    }

    private async Task RunPeopleMutationAsync(Func<Task<JsonElement>> operation)
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
                PeopleErrorText.Text = envelope.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? "HAVEN Core refused the change."
                    : "HAVEN Core refused the change.";
                return;
            }
            PeopleErrorText.Text = "";
            await LoadPeopleAsync();
        }
        catch (Exception ex)
        {
            PeopleErrorText.Text = ex.Message;
        }
    }

    private async void OnAddPersonClicked(object sender, RoutedEventArgs args)
    {
        await EditPersonAsync(default);
    }

    private async Task EditPersonAsync(JsonElement person)
    {
        if (_client is null)
        {
            return;
        }
        var editing = person.ValueKind == JsonValueKind.Object;
        var personId = editing ? GetString(person, "person_id") ?? "" : "";
        var name = new TextBox { PlaceholderText = "Name", Text = editing ? GetString(person, "name") ?? "" : "" };
        var role = new TextBox
        {
            PlaceholderText = "Role (owner or member)",
            Text = editing ? GetString(person, "role") ?? "" : "",
        };
        // The web PATCH for a person only edits name and role; entity/room
        // presence sources are declared at add time.
        StackPanel fields;
        TextBox? entityId = null;
        TextBox? roomId = null;
        if (editing)
        {
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
        }
        else
        {
            entityId = new TextBox { PlaceholderText = "Presence entity (optional)" };
            roomId = new TextBox { PlaceholderText = "Room (required with entity)" };
            fields = new StackPanel { Spacing = 8, MinWidth = 320 };
            fields.Children.Add(name);
            fields.Children.Add(role);
            fields.Children.Add(entityId);
            fields.Children.Add(roomId);
        }
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit person" : "Add person",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(name.Text))
        {
            return;
        }
        var roleValue = string.IsNullOrWhiteSpace(role.Text) ? null : role.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdatePersonAsync(personId, name.Text.Trim(), roleValue));
        }
        else
        {
            var entityValue = string.IsNullOrWhiteSpace(entityId!.Text) ? null : entityId.Text.Trim();
            var roomValue = string.IsNullOrWhiteSpace(roomId!.Text) ? null : roomId.Text.Trim();
            await RunPeopleMutationAsync(() => _client.AddPersonAsync(name.Text.Trim(), roleValue ?? "member", entityValue, roomValue));
        }
    }

    private async Task RemovePersonAsync(string personId, string name)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = $"Remove {name}?",
            Content = new TextBlock
            {
                Text = "HAVEN will forget the declaration, including any presence sources attached to it.",
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
        await RunPeopleMutationAsync(() => _client.RemovePersonAsync(personId));
    }

    private async void OnAddContextClicked(object sender, RoutedEventArgs args)
    {
        await EditContextAsync(default);
    }

    private async Task EditContextAsync(JsonElement context)
    {
        if (_client is null)
        {
            return;
        }
        var editing = context.ValueKind == JsonValueKind.Object;
        var contextId = editing ? GetString(context, "context_id") ?? "" : "";
        var label = new TextBox { PlaceholderText = "Label", Text = editing ? GetString(context, "label") ?? "" : "" };
        var entity = new TextBox
        {
            PlaceholderText = "Entity (e.g. input_boolean.working_late)",
            Text = editing ? GetString(context, "entity_id") ?? "" : "",
        };
        var fields = new StackPanel { Spacing = 8, MinWidth = 320 };
        fields.Children.Add(label);
        fields.Children.Add(entity);
        var dialog = new ContentDialog
        {
            Title = editing ? "Edit context" : "Add context",
            Content = fields,
            PrimaryButtonText = editing ? "Save" : "Add",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(label.Text))
        {
            return;
        }
        var entityValue = string.IsNullOrWhiteSpace(entity.Text) ? null : entity.Text.Trim();
        if (editing)
        {
            await RunPeopleMutationAsync(() => _client.UpdateContextAsync(contextId, label.Text.Trim(), entityValue));
        }
        else
        {
            await RunPeopleMutationAsync(() => _client.AddContextAsync(label.Text.Trim(), entityValue ?? ""));
        }
    }

    private async Task RemoveContextAsync(string contextId)
    {
        if (_client is null)
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Remove this context?",
            Content = new TextBlock
            {
                Text = "HAVEN will stop reasoning about this context until it is declared again.",
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
        await RunPeopleMutationAsync(() => _client.RemoveContextAsync(contextId));
    }

}
