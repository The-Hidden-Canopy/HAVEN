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
    private static IEnumerable<JsonElement> Enumerate(JsonElement array, string property = "")
    {
        var source = array;
        if (property.Length > 0)
        {
            if (array.ValueKind != JsonValueKind.Object || !array.TryGetProperty(property, out var nested))
            {
                yield break;
            }
            source = nested;
        }
        if (source.ValueKind != JsonValueKind.Array)
        {
            yield break;
        }
        foreach (var item in source.EnumerateArray())
        {
            yield return item;
        }
    }

    private static IReadOnlyList<string> GetPeople(JsonElement room) =>
        Enumerate(room, "people")
            .Select(person => person.ValueKind == JsonValueKind.String ? person.GetString() ?? "" : "")
            .Where(name => name.Length > 0)
            .ToList();

    private static string? GetString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    /* Sentence case per spec 05: "Proposed", never "PROPOSED". */
    private static string Sentence(string value) =>
        value.Length == 0 ? value : char.ToUpperInvariant(value[0]) + value[1..];

    private static string? GetString(JsonElement element, string property, string nested) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
            ? GetString(value, nested)
            : null;

    private static long GetInt(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetInt64()
            : 0;

    private static double? GetDouble(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : null;

    private async Task<string?> PromptForJustificationAsync(
        string title,
        string explanation,
        string placeholder,
        string actionLabel)
    {
        var justification = new TextBox
        {
            PlaceholderText = placeholder,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinWidth = 360,
        };
        var content = new StackPanel { Spacing = 8 };
        content.Children.Add(new TextBlock { Text = explanation, TextWrapping = TextWrapping.Wrap });
        content.Children.Add(new TextBlock { Text = "Justification" });
        content.Children.Add(justification);
        var dialog = new ContentDialog
        {
            Title = title,
            Content = content,
            PrimaryButtonText = actionLabel,
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(justification.Text))
        {
            return null;
        }
        return justification.Text.Trim();
    }

    private static StackPanel MakeEmptyState(
        string message,
        string actionLabel,
        Func<Task> action,
        bool primary = false)
    {
        var panel = new StackPanel
        {
            Spacing = 8,
            MaxWidth = 560,
        };
        panel.Children.Add(new TextBlock
        {
            Text = message,
            Opacity = 0.72,
            TextWrapping = TextWrapping.Wrap,
        });
        var actionButton = new Button
        {
            Content = actionLabel,
            Style = (Style)Application.Current.Resources[
                primary ? "HavenPrimaryButtonStyle" : "HavenSecondaryButtonStyle"],
            HorizontalAlignment = HorizontalAlignment.Left,
        };
        actionButton.Click += async (_, _) => await action();
        panel.Children.Add(actionButton);
        return panel;
    }
}
