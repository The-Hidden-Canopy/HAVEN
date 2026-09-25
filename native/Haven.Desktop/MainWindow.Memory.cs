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
    private async Task LoadMemoryAsync()
    {
        if (_client is null)
        {
            return;
        }
        try
        {
            var result = await _client.GetKnowledgeClaimsAsync();
            MemoryClaims.Items.Clear();
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            foreach (var claim in result.GetProperty("claims").EnumerateArray())
            {
                var claimId = claim.GetProperty("claim_id").GetString();
                var proposition = claim.GetProperty("proposition").GetString() ?? "";
                var state = claim.GetProperty("state").GetString() ?? "unknown";
                var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
                    ? confidenceValue.GetDouble().ToString("P0")
                    : "—";
                var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
                    ? provenanceValue.GetString()
                    : "unknown";
                var summary = new StackPanel { Spacing = 3 };
                summary.Children.Add(new TextBlock
                {
                    Text = proposition,
                    TextWrapping = TextWrapping.Wrap,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                });
                summary.Children.Add(new TextBlock
                {
                    Text = $"{Sentence(state)} · {confidence} · {provenance}",
                    Opacity = 0.72,
                });
                MemoryClaims.Items.Add(new ListViewItem { Tag = claimId, Content = summary });
            }
            if (MemoryClaims.Items.Count == 0)
            {
                MemoryClaims.Items.Add(new TextBlock
                {
                    Text = "HAVEN has not admitted any memories yet.",
                    Opacity = 0.72,
                });
                MemoryDetailText.Text = "Give HAVEN access to a text document, or tell it something directly, to begin building explainable memory.";
            }
            else
            {
                MemoryDetailText.Text = "Select a memory to inspect its evidence.";
                MemoryActionStatus.Text = "";
            }
        }
        catch (Exception ex)
        {
            MemoryClaims.Items.Clear();
            MemoryClaims.Items.Add(new TextBlock { Text = ex.Message, TextWrapping = TextWrapping.Wrap });
            MemoryDetailText.Text = "HAVEN could not load memory evidence.";
            MemoryActionStatus.Text = "";
        }
    }

    private async void OnMemorySelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        if (_client is null || MemoryClaims.SelectedItem is not ListViewItem item || item.Tag is not string claimId)
        {
            _selectedClaimId = null;
            SetMemoryActionButtons(false);
            return;
        }
        _selectedClaimId = claimId;
        SetMemoryActionButtons(true);
        try
        {
            var result = await _client.GetKnowledgeClaimAsync(claimId);
            MemoryDetailText.Text = FormatClaimDetail(result.GetProperty("claim"));
        }
        catch (Exception ex)
        {
            MemoryDetailText.Text = ex.Message;
        }
    }

    private void SetMemoryActionButtons(bool enabled)
    {
        MemoryCorrectButton.IsEnabled = enabled;
        MemoryStaleButton.IsEnabled = enabled;
        MemoryForgetButton.IsEnabled = enabled;
    }

    private async void OnMemoryCorrectClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var input = new TextBox
        {
            PlaceholderText = "What should HAVEN remember instead?",
            TextWrapping = TextWrapping.Wrap,
            AcceptsReturn = true,
            MinHeight = 90,
        };
        var dialog = new ContentDialog
        {
            Title = "Correct this memory",
            Content = input,
            PrimaryButtonText = "Save correction",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary || string.IsNullOrWhiteSpace(input.Text))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.CorrectKnowledgeClaimAsync(_selectedClaimId, input.Text.Trim()),
            "Correction saved.");
    }

    private async void OnMemoryStaleClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.MarkKnowledgeClaimStaleAsync(_selectedClaimId),
            "Memory marked stale.");
    }

    private async void OnMemoryForgetClicked(object sender, RoutedEventArgs args)
    {
        if (_client is null || string.IsNullOrWhiteSpace(_selectedClaimId))
        {
            return;
        }
        var dialog = new ContentDialog
        {
            Title = "Forget this memory?",
            Content = new TextBlock
            {
                Text = "HAVEN will keep the history as stale and suppress the same candidate from being re-admitted.",
                TextWrapping = TextWrapping.Wrap,
            },
            PrimaryButtonText = "Forget",
            CloseButtonText = "Cancel",
            XamlRoot = RootGrid().XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunMemoryMutationAsync(
            () => _client.ForgetKnowledgeClaimAsync(_selectedClaimId),
            "Memory forgotten.");
    }

    private async Task RunMemoryMutationAsync(Func<Task<JsonElement>> operation, string successMessage)
    {
        SetMemoryActionButtons(false);
        try
        {
            await operation();
            MemoryActionStatus.Text = successMessage;
            await LoadMemoryAsync();
        }
        catch (Exception ex)
        {
            MemoryActionStatus.Text = ex.Message;
            SetMemoryActionButtons(!string.IsNullOrWhiteSpace(_selectedClaimId));
        }
    }

    private static string FormatClaimDetail(JsonElement claim)
    {
        var text = new StringBuilder();
        text.AppendLine(claim.GetProperty("proposition").GetString() ?? "");
        text.AppendLine();
        text.AppendLine($"STATE\n{claim.GetProperty("state").GetString()}");
        text.AppendLine();
        var provenance = claim.TryGetProperty("provenance", out var provenanceValue)
            ? provenanceValue.GetString()
            : "unknown";
        var confidence = claim.TryGetProperty("confidence", out var confidenceValue)
            ? confidenceValue.GetDouble().ToString("P0")
            : "—";
        text.AppendLine($"PROVENANCE\n{provenance}");
        text.AppendLine();
        text.AppendLine($"CONFIDENCE\n{confidence}");
        text.AppendLine();
        text.AppendLine("SOURCE");
        if (claim.TryGetProperty("sources", out var sources) && sources.GetArrayLength() > 0)
        {
            foreach (var source in sources.EnumerateArray())
            {
                var reference = source.GetProperty("ref").GetString() ?? "unknown source";
                var resource = source.TryGetProperty("resource", out var resourceValue)
                    && resourceValue.ValueKind == JsonValueKind.Object
                    ? resourceValue
                    : default;
                var title = resource.ValueKind == JsonValueKind.Object
                    && resource.TryGetProperty("title", out var titleValue)
                    ? titleValue.GetString()
                    : null;
                text.AppendLine($"• {title ?? reference}");
                if (title is not null && resource.TryGetProperty("locator", out var locatorValue))
                {
                    text.AppendLine($"  {locatorValue.GetString() ?? reference}");
                }
            }
        }
        else
        {
            text.AppendLine("None recorded");
        }
        AppendClaimGroup(text, "EVIDENCE", claim, "evidence_refs");
        AppendClaimGroup(text, "SUPERSEDES", claim, "supersedes");
        AppendClaimGroup(text, "CONTRADICTIONS", claim, "contradicts");
        AppendAuditHistory(text, claim);
        return text.ToString().TrimEnd();
    }

    private static void AppendClaimGroup(StringBuilder text, string label, JsonElement claim, string property)
    {
        text.AppendLine();
        text.AppendLine(label);
        if (!claim.TryGetProperty(property, out var values) || values.GetArrayLength() == 0)
        {
            text.AppendLine("None recorded");
            return;
        }
        foreach (var value in values.EnumerateArray())
        {
            text.AppendLine($"• {value.GetString()}");
        }
    }

    private static void AppendAuditHistory(StringBuilder text, JsonElement claim)
    {
        text.AppendLine();
        text.AppendLine("AUDIT HISTORY");
        if (!claim.TryGetProperty("audit", out var events) || events.GetArrayLength() == 0)
        {
            text.AppendLine("No user changes recorded");
            return;
        }
        foreach (var audit in events.EnumerateArray())
        {
            var action = audit.GetProperty("action").GetString() ?? "unknown";
            var actor = audit.GetProperty("actor_id").GetString() ?? "unknown actor";
            var at = audit.GetProperty("occurred_at").GetString() ?? "unknown time";
            text.AppendLine($"• {action} by {actor} at {at}");
        }
    }

}
