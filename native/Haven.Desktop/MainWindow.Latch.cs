using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Windows.System;

namespace Haven.Desktop;

public sealed partial class MainWindow
{
    // -- Latch plugin (HAVEN<->Latch): read + propose only -----------------------
    //
    // Authority boundary: this page renders Latch API records and can propose
    // a cancellation plan. It has no authorize/execute/verify path at all -
    // authorization is handed off to Latch's own desktop action flow via the
    // ?plan_id= deep link (GetAuthorizationHandoffUri). Plan states stay
    // distinct end to end and transport success (needs_verification) is never
    // displayed as complete.
    //
    // Data comes from Services/LatchClient.cs (a thin System.Net.Http JSON
    // adapter over latchd loopback HTTP - no Latch DB/core access, no
    // authorization methods) and is shaped by Services/LatchRecordProjection.cs
    // against Latch's real wire shapes (economics/temporal/confidence.overall
    // on commitments, grantee/resource objects + stale_reason on access,
    // nested targets + evidence arrays on scheduler proposals). Reads return
    // cloned JsonElements, so the snapshot below is safe to hold.

    private LatchClient? _latchClient;
    private bool _latchActivationHooked;
    private bool _latchAcceleratorsAdded;
    private readonly LatchRefreshCoordinator _latchRefresh = new();  // refresh-race tokens
    private int _latchDetailsSequence;      // details-selection race token
    private bool _latchPlanInFlight;        // repeated-click guard for PlanCancellationAsync
    private string _latchTab = "ledger";
    private DateTimeOffset? _latchLastGoodAt;
    private readonly Dictionary<string, JsonElement> _latchRecords = new();
    // Evidence id -> the record Latch returned for it (immutable, so the
    // cache survives refreshes); ids Latch could not serve.
    private readonly Dictionary<string, JsonElement> _latchEvidence = new();
    private readonly HashSet<string> _latchEvidenceUnavailable = new();

    private LatchClient? EnsureLatchClient()
    {
        if (_latchClient is not null)
        {
            return _latchClient;
        }
        try
        {
            // CreateDefault resolves HAVEN_LATCH_BASE_URL (default
            // http://127.0.0.1:8787) and HAVEN_LATCH_DEMO=1, and rejects a
            // non-loopback override at startup with ArgumentException.
            _latchClient = LatchClient.CreateDefault();
        }
        catch (ArgumentException ex)
        {
            LatchErrorText.Text = $"Latch configuration problem: {ex.Message}";
            return null;
        }
        LatchBaseUrlText.Text = _latchClient.BaseUri.ToString();
        // IsDemoMode only labels synthetic demo data; the rows still come
        // from Latch (real daemon or fixture-backed), never from here.
        LatchDemoChip.Visibility = _latchClient.IsDemoMode ? Visibility.Visible : Visibility.Collapsed;
        HookLatchActivation();
        AddLatchAccelerators();
        // The window's own Closed handler lives in MainWindow.xaml.cs; this
        // partial adds its own subscription so the adapter still disposes.
        Closed += (_, _) => _latchClient?.Dispose();
        return _latchClient;
    }

    private async Task LoadLatchAsync()
    {
        if (EnsureLatchClient() is null)
        {
            return;
        }
        await RefreshLatchAsync();
    }

    private async void OnLatchRefreshClicked(object sender, RoutedEventArgs args)
    {
        await RefreshLatchAsync();
    }

    private void OnLatchHelpClicked(object sender, RoutedEventArgs args)
    {
        LatchHelpCard.Visibility = LatchHelpCard.Visibility == Visibility.Visible
            ? Visibility.Collapsed
            : Visibility.Visible;
    }

    private async Task RefreshLatchAsync(bool silent = false)
    {
        var client = _latchClient;
        if (client is null)
        {
            return;
        }
        // Every refresh claims a fresh coordinator token; any response that
        // arrives after a newer refresh started is discarded untouched, so a
        // slow stale response can never overwrite newer data.
        var token = _latchRefresh.BeginRefresh();
        LatchErrorText.Text = "";
        if (!silent)
        {
            LatchLoadingPanel.Visibility = Visibility.Visible;
            LatchDisconnectedCard.Visibility = Visibility.Collapsed;
        }
        try
        {
            if (!await client.IsAvailableAsync())
            {
                if (!_latchRefresh.IsCurrent(token))
                {
                    return;
                }
                ShowLatchDisconnected(client);
                return;
            }
            var commitments = await client.GetCommitmentsAsync();
            // The review screen lists what actually needs review: only the
            // grants Latch itself evaluates as stale.
            var access = await client.GetAccessAsync(staleOnly: true);
            var proposals = await client.GetReviewProposalsAsync();
            var actions = await client.GetActionPlansAsync();
            if (!_latchRefresh.IsCurrent(token))
            {
                return;
            }
            AdoptLatchSnapshot(commitments, access, proposals, actions);
            _latchLastGoodAt = DateTimeOffset.Now;
            LatchStaleBanner.Visibility = Visibility.Collapsed;
            LatchDisconnectedCard.Visibility = Visibility.Collapsed;
            RenderLatch();
            _ = EnrichLedgerEvidenceAsync(token);
        }
        catch (Exception ex)
        {
            if (!_latchRefresh.IsCurrent(token))
            {
                return;
            }
            ShowLatchRefreshError(ex);
        }
        finally
        {
            if (_latchRefresh.IsCurrent(token))
            {
                LatchLoadingPanel.Visibility = Visibility.Collapsed;
            }
        }
    }

    private void ShowLatchDisconnected(LatchClient client)
    {
        LatchDisconnectedText.Text =
            $"HAVEN can't reach Latch at {client.BaseUri}. Start Latch on this machine from the Latch "
            + "repository: ./build/bin/latchd --socket /tmp/latchd.sock --db ~/.latch/latch.db "
            + "--http-port 8787 --web-root apps/desktop (on Windows, build\\bin\\latchd.exe with equivalent "
            + "paths). The --web-root flag is what serves Latch's own desktop UI - the only place actions "
            + "get authorized. You can point HAVEN at another loopback address with HAVEN_LATCH_BASE_URL.";
        LatchDisconnectedCard.Visibility = Visibility.Visible;
        if (_latchRecords.Count > 0 && _latchLastGoodAt is { } lastGood)
        {
            // Disconnected with last-good data: keep it visible, say so.
            LatchStaleText.Text = $"Disconnected - showing last successful data from {lastGood:HH:mm:ss}.";
            LatchStaleBanner.Visibility = Visibility.Visible;
            RenderLatch();
        }
        else
        {
            LatchList.Items.Clear();
            LatchEmptyText.Visibility = Visibility.Collapsed;
        }
        LatchStatusText.Text = "Disconnected from Latch.";
    }

    private void ShowLatchRefreshError(Exception ex)
    {
        LatchErrorText.Text = $"Could not refresh Latch data: {ex.Message}";
        if (_latchRecords.Count > 0 && _latchLastGoodAt is { } lastGood)
        {
            // Last-good rule: a failed refresh never blanks good data; it is
            // labeled stale instead.
            LatchStaleText.Text = $"Showing last successful data from {lastGood:HH:mm:ss} - this refresh failed: {ex.Message}";
            LatchStaleBanner.Visibility = Visibility.Visible;
            RenderLatch();
        }
        else
        {
            LatchEmptyText.Text = "Latch data is unavailable right now.";
            LatchEmptyText.Visibility = Visibility.Visible;
        }
    }

    private void AdoptLatchSnapshot(
        JsonElement commitments, JsonElement access, JsonElement proposals, JsonElement actions)
    {
        _latchRecords.Clear();
        foreach (var commitment in LatchRecordProjection.Items(commitments))
        {
            var id = GetString(commitment, "id");
            if (id is not null)
            {
                _latchRecords["com:" + id] = commitment;
            }
        }
        foreach (var grant in LatchRecordProjection.Items(access))
        {
            var id = GetString(grant, "id") ?? GetString(grant, "grant_id");
            if (id is not null)
            {
                _latchRecords["acc:" + id] = grant;
            }
        }
        foreach (var proposal in LatchRecordProjection.Items(proposals))
        {
            var id = GetString(proposal, "id") ?? GetString(proposal, "proposal_id")
                ?? $"{GetString(proposal, "schedule_id") ?? "schedule"}:{LatchRecordProjection.TryString(proposal, "target_key") ?? "target"}";
            _latchRecords["prop:" + id] = proposal;
        }
        foreach (var action in LatchRecordProjection.Items(actions))
        {
            var id = GetString(action, "plan_id") ?? GetString(action, "id");
            if (id is not null)
            {
                _latchRecords["act:" + id] = action;
            }
        }
    }

    // -- evidence metadata (source observation column) ---------------------------

    // Best-effort, non-blocking: pull each visible commitment's backing
    // evidence records (metadata only) so the ledger's "source observation"
    // column shows what Latch actually observed (source_connector ·
    // acquired_at). Rows say "unavailable" honestly when Latch can't serve
    // one; nothing is fabricated. Render is re-done only while the refresh
    // token that scheduled the work is still current.
    private async Task EnrichLedgerEvidenceAsync(long token)
    {
        var client = _latchClient;
        if (client is null)
        {
            return;
        }
        var ids = _latchRecords
            .Where(pair => pair.Key.StartsWith("com:", StringComparison.Ordinal))
            .SelectMany(pair => LatchRecordProjection.LedgerRow(pair.Value).EvidenceIds)
            .Where(id => !_latchEvidence.ContainsKey(id) && !_latchEvidenceUnavailable.Contains(id))
            .Take(25)
            .ToList();
        foreach (var id in ids)
        {
            try
            {
                var evidence = await client.GetEvidenceAsync(id, includeContent: false);
                _latchEvidence[id] = evidence;
            }
            catch (Exception)
            {
                _latchEvidenceUnavailable.Add(id);
            }
            if (!_latchRefresh.IsCurrent(token))
            {
                return;
            }
        }
        if (ids.Count > 0 && _latchRefresh.IsCurrent(token) && _currentTag == "latch")
        {
            var selected = LatchList.SelectedItem is ListViewItem item ? item.Tag as string : null;
            RenderLatch();
            RestoreLatchSelection(selected);
        }
    }

    private string LatchSourceObservation(LatchLedgerRow row)
    {
        foreach (var id in row.EvidenceIds)
        {
            if (_latchEvidence.TryGetValue(id, out var evidence))
            {
                var connector = GetString(evidence, "source_connector") ?? "unknown connector";
                var acquiredAt = GetString(evidence, "acquired_at");
                return acquiredAt is not null ? $"{connector} · {acquiredAt}" : connector;
            }
        }
        if (row.EvidenceIds.Count == 0)
        {
            return "—";
        }
        return row.EvidenceIds.All(_latchEvidenceUnavailable.Contains)
            ? "unavailable"
            : "loading…";
    }

    // -- tabs -------------------------------------------------------------------

    private void OnLatchTabClicked(object sender, RoutedEventArgs args)
    {
        if (sender is not Button button || button.Tag is not string tab)
        {
            return;
        }
        SelectLatchTab(tab);
    }

    private void SelectLatchTab(string tab)
    {
        _latchTab = tab;
        StyleLatchTabs();
        RenderLatch();
        var selected = LatchList.SelectedItem is ListViewItem item ? item.Tag as string : null;
        if (selected is not null)
        {
            _ = RenderLatchDetailsAsync(selected);
        }
    }

    private void StyleLatchTabs()
    {
        foreach (var button in LatchTabs.Children.OfType<Button>())
        {
            var selected = button.Tag?.ToString() == _latchTab;
            button.Foreground = (Brush)Application.Current.Resources[
                selected ? "HavenAccentBrush" : "HavenMutedTextBrush"];
            button.BorderBrush = (Brush)Application.Current.Resources[
                selected ? "HavenAccentBrush" : "HavenStrokeBrush"];
        }
    }

    // -- rendering --------------------------------------------------------------

    private void RenderLatch()
    {
        StyleLatchTabs();
        LatchList.Items.Clear();
        LatchErrorText.Text = "";
        LatchColumnHeader.Visibility = _latchTab == "ledger" ? Visibility.Visible : Visibility.Collapsed;

        var prefix = _latchTab switch
        {
            "ledger" => "com:",
            "access" => "acc:",
            _ => "prop:",
        };
        var keys = _latchRecords.Keys
            .Where(key => key.StartsWith(prefix, StringComparison.Ordinal))
            .Concat(_latchTab == "proposals"
                ? _latchRecords.Keys.Where(key => key.StartsWith("act:", StringComparison.Ordinal))
                : Enumerable.Empty<string>())
            .ToList();

        foreach (var key in keys)
        {
            var record = _latchRecords[key];
            var row = _latchTab switch
            {
                "ledger" => BuildLedgerRow(record),
                "access" => BuildAccessRow(record),
                _ => key.StartsWith("act:", StringComparison.Ordinal)
                    ? BuildActionRow(record)
                    : BuildProposalRow(record),
            };
            if (row is not null)
            {
                LatchList.Items.Add(new ListViewItem { Tag = key, Content = row });
            }
        }

        LatchEmptyText.Text = _latchTab switch
        {
            "ledger" => "No commitments recorded in Latch yet.",
            "access" => "No access currently needs review.",
            _ => "No review proposals or action plans from Latch yet.",
        };
        LatchEmptyText.Visibility = keys.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        var updated = _latchLastGoodAt is { } at ? $"Updated {at:HH:mm:ss}" : "Not refreshed yet";
        LatchStatusText.Text = $"{updated} · {_latchRecords.Count(k => k.Key.StartsWith("com:", StringComparison.Ordinal))} ledger · "
            + $"{_latchRecords.Count(k => k.Key.StartsWith("acc:", StringComparison.Ordinal))} access · "
            + $"{_latchRecords.Count(k => k.Key.StartsWith("prop:", StringComparison.Ordinal))} proposals · "
            + $"{_latchRecords.Count(k => k.Key.StartsWith("act:", StringComparison.Ordinal))} action plans";
    }

    private void RestoreLatchSelection(string? key)
    {
        if (key is null)
        {
            return;
        }
        foreach (var item in LatchList.Items.OfType<ListViewItem>())
        {
            if (item.Tag as string == key)
            {
                LatchList.SelectedItem = item;
                return;
            }
        }
    }

    private UIElement? BuildLedgerRow(JsonElement commitment)
    {
        var row = LatchRecordProjection.LedgerRow(commitment);
        if (row.Id.Length == 0)
        {
            return null;
        }
        var grid = new Grid { ColumnSpacing = 10, Padding = new Thickness(10, 4, 10, 4) };
        foreach (var width in new[] { "150", "96", "106", "106", "*", "104" })
        {
            grid.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = width == "*"
                    ? new GridLength(1, GridUnitType.Star)
                    : new GridLength(double.Parse(width, System.Globalization.CultureInfo.InvariantCulture)),
            });
        }
        AddCell(grid, 0, row.Provider ?? row.Id, semiBold: true);
        AddCell(grid, 1, row.Cost);
        // Renewal and cancel-by instants are Latch-computed; passed through verbatim.
        AddCell(grid, 2, row.RenewalAt);
        AddCell(grid, 3, row.CancelBefore);
        AddCell(grid, 4, LatchSourceObservation(row));
        AddCell(grid, 5, row.Confidence);
        return grid;
    }

    private UIElement? BuildAccessRow(JsonElement grant)
    {
        var row = LatchRecordProjection.AccessRow(grant);
        if (row.Id.Length == 0)
        {
            return null;
        }
        var panel = new StackPanel { Spacing = 2 };
        var headline = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        headline.Children.Add(new TextBlock
        {
            Text = $"{row.Grantee} → {row.Resource}",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        if (row.Stale)
        {
            headline.Children.Add(LatchChip("Needs review", "HavenWarningTintBrush", "HavenWarningBrush"));
        }
        if (ProposalTargets(row.Id))
        {
            headline.Children.Add(LatchChip("Review proposed", "HavenVioletTintBrush", "HavenVioletBrush"));
        }
        panel.Children.Add(headline);
        var meta = new List<string>();
        if (row.StaleReason is not null)
        {
            meta.Add(row.StaleReason);
        }
        if (row.SourceConnector is not null)
        {
            meta.Add(row.SourceObjectId is not null
                ? $"{row.SourceConnector} · {row.SourceObjectId}"
                : row.SourceConnector);
        }
        if (row.UpdatedAt is not null)
        {
            meta.Add($"observed {row.UpdatedAt}");
        }
        panel.Children.Add(new TextBlock
        {
            Text = meta.Count > 0 ? string.Join(" · ", meta) : "No staleness metadata reported by Latch.",
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
        });
        return panel;
    }

    private bool ProposalTargets(string id) => _latchRecords.Any(pair =>
        pair.Key.StartsWith("prop:", StringComparison.Ordinal)
        && LatchRecordProjection.ProposalRow(pair.Value).TargetIds.Contains(id));

    private UIElement? BuildProposalRow(JsonElement proposal)
    {
        var row = LatchRecordProjection.ProposalRow(proposal);
        if (row.Id.Length == 0)
        {
            return null;
        }
        var panel = new StackPanel { Spacing = 2 };
        var headline = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        headline.Children.Add(new TextBlock
        {
            Text = Sentence(row.Kind?.Replace('_', ' ') ?? "review"),
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
        });
        if (row.Status is not null)
        {
            headline.Children.Add(LatchChip(Sentence(row.Status), "HavenAccentTintBrush", "HavenAccentBrush"));
        }
        panel.Children.Add(headline);
        var meta = new List<string>();
        if (row.ScheduleId is not null)
        {
            meta.Add($"schedule {ShortId(row.ScheduleId)}");
        }
        foreach (var target in row.TargetIds.Take(3))
        {
            meta.Add($"target {ShortId(target)}");
        }
        if (row.Evidence.Count > 0)
        {
            meta.Add($"{row.Evidence.Count} evidence");
        }
        if (row.CreatedAt is not null)
        {
            meta.Add(row.CreatedAt);
        }
        panel.Children.Add(new TextBlock
        {
            Text = string.Join(" · ", meta),
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
        });
        return panel;
    }

    private UIElement? BuildActionRow(JsonElement action)
    {
        var presentation = LatchRecordProjection.ActionPresentation(GetString(action, "status"));
        var planId = GetString(action, "plan_id") ?? GetString(action, "id") ?? "";
        var panel = new StackPanel { Spacing = 2 };
        var headline = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        headline.Children.Add(new TextBlock
        {
            Text = GetString(action, "operation") ?? "action plan",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
        });
        headline.Children.Add(LatchStatusChip(presentation));
        panel.Children.Add(headline);
        var meta = new List<string> { ShortId(planId) };
        if (GetString(action, "commitment_id") is { } commitmentId)
        {
            meta.Add($"commitment {ShortId(commitmentId)}");
        }
        if (GetString(action, "authority_class") is { } authority)
        {
            meta.Add(Sentence(authority.Replace('_', ' ')));
        }
        if (!presentation.Complete && presentation.State != LatchActionState.Failed)
        {
            meta.Add(presentation.State == LatchActionState.AwaitingVerification
                ? "transport succeeded; verification pending - not complete"
                : "not complete");
        }
        panel.Children.Add(new TextBlock
        {
            Text = string.Join(" · ", meta),
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
        });
        return panel;
    }

    private void AddCell(Grid row, int column, string? text, bool semiBold = false)
    {
        var cell = new TextBlock
        {
            Text = string.IsNullOrWhiteSpace(text) ? "—" : text,
            Style = HavenStyle("HavenMetadataTextStyle"),
            FontWeight = semiBold ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal,
            TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center,
        };
        row.Children.Add(cell);
        Grid.SetColumn(cell, column);
    }

    // -- details ----------------------------------------------------------------

    private void OnLatchSelectionChanged(object sender, SelectionChangedEventArgs args)
    {
        var key = LatchList.SelectedItem is ListViewItem item ? item.Tag as string : null;
        _ = RenderLatchDetailsAsync(key);
    }

    // Base render is synchronous from the snapshot; original evidence then
    // loads per selection under a race token so fast re-selections never
    // cross-paint.
    private async Task RenderLatchDetailsAsync(string? key)
    {
        var token = Interlocked.Increment(ref _latchDetailsSequence);
        RenderLatchDetailsBase(key);
        if (key is null || _latchClient is null)
        {
            return;
        }
        // Commitments: re-render from the full record Latch serves for a
        // single commitment (richer than the list projection). A failure
        // keeps the snapshot render - the evidence pass below still runs.
        if (key.StartsWith("com:", StringComparison.Ordinal))
        {
            try
            {
                var full = await _latchClient.GetCommitmentAsync(key["com:".Length..]);
                if (token != _latchDetailsSequence
                    || LatchList.SelectedItem is not ListViewItem current
                    || current.Tag as string != key)
                {
                    return;
                }
                _latchRecords[key] = full;
                RenderLatchDetailsBase(key);
            }
            catch (Exception)
            {
                if (token != _latchDetailsSequence)
                {
                    return;
                }
            }
        }
        var evidenceIds = key switch
        {
            var k when k.StartsWith("com:", StringComparison.Ordinal) =>
                LatchRecordProjection.LedgerRow(_latchRecords[k]).EvidenceIds.ToList(),
            var k when k.StartsWith("prop:", StringComparison.Ordinal) =>
                LatchRecordProjection.ProposalRow(_latchRecords[k]).Evidence
                    .Select(reference => reference.EvidenceId)
                    .Where(id => id is not null)
                    .Cast<string>()
                    .ToList(),
            _ => new List<string>(),
        };
        if (evidenceIds.Count == 0)
        {
            return;
        }
        var loaded = new List<(string Id, JsonElement? Record, string? Error)>();
        foreach (var id in evidenceIds.Take(10))
        {
            try
            {
                loaded.Add((id, await _latchClient.GetEvidenceAsync(id, includeContent: true), null));
            }
            catch (Exception ex)
            {
                loaded.Add((id, null, ex.Message));
            }
            if (token != _latchDetailsSequence)
            {
                return;
            }
        }
        if (token != _latchDetailsSequence || LatchList.SelectedItem is not ListViewItem item || item.Tag as string != key)
        {
            return;
        }
        AppendLatchEvidenceDetails(loaded);
    }

    private void RenderLatchDetailsBase(string? key)
    {
        LatchDetails.Children.Clear();
        if (key is null || !_latchRecords.TryGetValue(key, out var record))
        {
            LatchDetails.Children.Add(new TextBlock
            {
                Text = "Select a record to see its evidence, observed dates, reasoning, impact and actions.",
                TextWrapping = TextWrapping.Wrap,
            });
            return;
        }
        if (key.StartsWith("com:", StringComparison.Ordinal))
        {
            RenderCommitmentDetails(record);
        }
        else if (key.StartsWith("acc:", StringComparison.Ordinal))
        {
            RenderAccessDetails(record);
        }
        else if (key.StartsWith("prop:", StringComparison.Ordinal))
        {
            RenderProposalDetails(record);
        }
        else
        {
            RenderActionDetails(record);
        }
    }

    private void RenderCommitmentDetails(JsonElement commitment)
    {
        var row = LatchRecordProjection.LedgerRow(commitment);
        AddLatchHeading("Commitment", row.Id);
        AddLatchField("Provider", row.Provider);
        AddLatchField("Subject / account", Combine(GetString(commitment, "subject"), GetString(commitment, "account")));
        AddLatchField("Cost", row.Cost);
        AddLatchField("Renewal (from Latch)", row.RenewalAt);
        AddLatchField("Cancel by (from Latch)", row.CancelBefore);
        AddLatchField("Lifecycle", GetString(commitment, "lifecycle") is { } life ? Sentence(life) : null);
        AddLatchField("Confidence", row.Confidence);
        AddLatchField("Last observed", GetString(commitment, "updated_at"));
        AddLatchFacts(commitment);

        // Actual action plans Latch holds for this commitment, each with its
        // distinct state - and the handoff into Latch's own authorization flow.
        var plans = _latchRecords
            .Where(pair => pair.Key.StartsWith("act:", StringComparison.Ordinal)
                && string.Equals(GetString(pair.Value, "commitment_id"), row.Id, StringComparison.Ordinal))
            .Select(pair => (Id: GetString(pair.Value, "plan_id") ?? GetString(pair.Value, "id") ?? "plan",
                Status: LatchRecordProjection.ActionPresentation(GetString(pair.Value, "status"))))
            .ToList();
        if (plans.Count > 0)
        {
            AddLatchSubheading("Action plans");
            foreach (var plan in plans)
            {
                var line = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                line.Children.Add(new TextBlock
                {
                    Text = ShortId(plan.Id),
                    VerticalAlignment = VerticalAlignment.Center,
                });
                line.Children.Add(LatchStatusChip(plan.Status));
                LatchDetails.Children.Add(line);
            }
        }
        AddLatchSubheading("Actions");
        var propose = new Button
        {
            Content = "Propose cancellation",
            Tag = row.Id,
            Style = HavenStyle("HavenPrimaryButtonStyle"),
            IsEnabled = !_latchPlanInFlight && _latchClient is not null,
        };
        AutomationProperties.SetName(propose, "Propose cancellation plan");
        propose.Click += OnLatchProposeCancellationClicked;
        LatchDetails.Children.Add(propose);
        LatchDetails.Children.Add(new TextBlock
        {
            Text = "Proposes an idempotent cancellation plan in Latch. HAVEN cannot authorize it: use "
                + "“Authorize in Latch…” on the proposed plan to continue in Latch's own desktop action flow.",
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
    }

    private void RenderAccessDetails(JsonElement grant)
    {
        var row = LatchRecordProjection.AccessRow(grant);
        AddLatchHeading("Access grant", row.Id);
        AddLatchField("Grantee", row.Grantee);
        AddLatchField("Resource", row.Resource);
        AddLatchField("Stale", row.Stale ? "yes" : "no");
        AddLatchField("Staleness reason", row.StaleReason);
        AddLatchField("Granted", GetString(grant, "granted_at"));
        AddLatchField("Last used", GetString(grant, "last_used_at"));
        AddLatchField("Last reviewed", GetString(grant, "last_reviewed_at"));
        AddLatchField("Parent commitment", GetString(grant, "parent_commitment"));
        AddLatchVerbatim(LatchDetails, grant,
            "grantor", "grantee", "resource", "capabilities", "stale", "stale_reason");

        // Connector-observation-only grants carry no backing evidence record;
        // say exactly that and show the observation itself - never invent
        // evidence, never block on a fetch that cannot exist.
        AddLatchSubheading("Original evidence");
        LatchDetails.Children.Add(new TextBlock
        {
            Text = "No backing evidence record - this grant is a connector observation.",
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
        AddLatchField("Observed by connector", row.SourceConnector);
        AddLatchField("Source object", row.SourceObjectId);
        AddLatchField("Observed at", row.UpdatedAt);

        var related = _latchRecords
            .Where(pair => pair.Key.StartsWith("prop:", StringComparison.Ordinal)
                && LatchRecordProjection.ProposalRow(pair.Value).TargetIds.Contains(row.Id))
            .Select(pair => LatchRecordProjection.ProposalRow(pair.Value))
            .ToList();
        if (related.Count > 0)
        {
            AddLatchSubheading("Review proposals for this grant");
            foreach (var proposal in related)
            {
                LatchDetails.Children.Add(new TextBlock
                {
                    Text = $"{Sentence(proposal.Kind?.Replace('_', ' ') ?? "review")} · "
                        + $"{Sentence(proposal.Status ?? "proposed")} · {proposal.CreatedAt ?? ""}",
                    Style = HavenStyle("HavenMetadataTextStyle"),
                    TextWrapping = TextWrapping.Wrap,
                });
            }
        }
    }

    private void RenderProposalDetails(JsonElement proposal)
    {
        var row = LatchRecordProjection.ProposalRow(proposal);
        AddLatchHeading("Scheduler review proposal", row.Id);
        AddLatchField("Schedule", row.ScheduleId);
        AddLatchField("Kind", row.Kind is { } kind ? Sentence(kind.Replace('_', ' ')) : null);
        AddLatchField("Status", row.Status is { } status ? Sentence(status) : null);
        AddLatchField("Created", row.CreatedAt);
        AddLatchField("Reasoning", row.Reason);
        if (row.TargetIds.Count > 0)
        {
            AddLatchSubheading("Targets");
            foreach (var target in row.TargetIds)
            {
                LatchDetails.Children.Add(new TextBlock
                {
                    Text = target,
                    Style = HavenStyle("HavenMonoTextStyle"),
                    TextWrapping = TextWrapping.Wrap,
                });
            }
        }
        AddLatchVerbatim(LatchDetails, proposal,
            "id", "proposal_id", "schedule_id", "kind", "status", "created_at", "reason", "target", "evidence");
        // Evidence array entries are rendered from the projection above; the
        // backing records (where they carry evidence_id) load asynchronously
        // and are appended by AppendLatchEvidenceDetails.
    }

    private void RenderActionDetails(JsonElement action)
    {
        var status = LatchRecordProjection.ActionPresentation(GetString(action, "status"));
        var planId = GetString(action, "plan_id") ?? GetString(action, "id") ?? "";
        AddLatchHeading("Action plan", planId);

        // Lifecycle stages stay visually distinct: transport success parks on
        // "Executed" with "Verified" still pending - never shown as complete.
        var stageRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        foreach (var stage in LatchRecordProjection.ActionStages(GetString(action, "status")))
        {
            stageRow.Children.Add(LatchStageChip(stage));
        }
        LatchDetails.Children.Add(stageRow);

        var chipRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        chipRow.Children.Add(LatchStatusChip(status));
        chipRow.Children.Add(new TextBlock
        {
            Text = status.Complete
                ? "Latch verified the outcome; this plan is complete."
                : status.State == LatchActionState.AwaitingVerification
                    ? "The provider call reported success, but transport success is not verification - "
                        + "Latch still has to confirm the outcome. Not complete."
                    : status.State == LatchActionState.Failed
                        ? "Latch's verification found the intended change did not take effect."
                        : "Nothing has happened in the provider yet.",
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
            VerticalAlignment = VerticalAlignment.Center,
        });
        LatchDetails.Children.Add(chipRow);

        AddLatchField("Operation", GetString(action, "operation"));
        AddLatchField("Commitment", GetString(action, "commitment_id"));
        AddLatchField("Authority class", GetString(action, "authority_class") is { } authority ? Sentence(authority.Replace('_', ' ')) : null);
        AddLatchField("Proposed", GetString(action, "created_at"));
        AddLatchField("Last observed", GetString(action, "updated_at"));
        if (action.TryGetProperty("impact", out var impact) && impact.ValueKind == JsonValueKind.Object)
        {
            AddLatchSubheading("Impact (computed by Latch)");
            AddLatchVerbatim(LatchDetails, impact);
        }
        if (action.TryGetProperty("receipt", out var receipt) && receipt.ValueKind == JsonValueKind.Object)
        {
            AddLatchSubheading("Receipt");
            AddLatchVerbatim(LatchDetails, receipt);
        }

        // Authorization handoff: opens Latch's real desktop action flow, and
        // only when that UI is actually served (IsHandoffAvailableAsync) -
        // otherwise the button is disabled with an honest explanation.
        AddLatchSubheading("Actions");
        var handoff = new Button
        {
            Content = status.State == LatchActionState.Proposed ? "Authorize in Latch…" : "Open in Latch…",
            Tag = planId,
            Style = HavenStyle("HavenPrimaryButtonStyle"),
        };
        AutomationProperties.SetName(handoff, "Open authorization handoff in Latch");
        handoff.Click += OnLatchHandoffClicked;
        LatchDetails.Children.Add(handoff);
        LatchDetails.Children.Add(new TextBlock
        {
            Text = "Continues in Latch's own desktop action flow (authorize → execute → verify). "
                + "HAVEN never authorizes, executes or verifies anything itself.",
            Style = HavenStyle("HavenMetadataTextStyle"),
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
    }

    // Original evidence records Latch served for the selected commitment or
    // proposal: connector, acquisition time, content hash and a bounded
    // content preview. Unavailable records say so with Latch's error.
    private void AppendLatchEvidenceDetails(IReadOnlyList<(string Id, JsonElement? Record, string? Error)> loaded)
    {
        AddLatchSubheading("Original evidence");
        foreach (var (id, record, error) in loaded)
        {
            if (record is null)
            {
                LatchDetails.Children.Add(new TextBlock
                {
                    Text = $"{ShortId(id)}: unavailable ({error ?? "no response"})",
                    Style = HavenStyle("HavenMetadataTextStyle"),
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = (Brush)Application.Current.Resources["HavenWarningBrush"],
                });
                continue;
            }
            var evidence = record.Value;
            var line = GetString(evidence, "source_connector") ?? "unknown connector";
            if (GetString(evidence, "acquired_at") is { } acquiredAt)
            {
                line += $" · {acquiredAt}";
            }
            LatchDetails.Children.Add(new TextBlock
            {
                Text = line,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextWrapping = TextWrapping.Wrap,
            });
            AddLatchField("Evidence id", GetString(evidence, "id") ?? id);
            AddLatchField("Source kind", GetString(evidence, "source_kind"));
            AddLatchField("Content hash", GetString(evidence, "content_hash"));
            AddLatchField("MIME type", GetString(evidence, "mime_type"));
            if (GetString(evidence, "content") is { Length: > 0 } content)
            {
                const int previewLimit = 1200;
                var preview = content.Length > previewLimit ? content[..previewLimit] + "… (truncated)" : content;
                AddLatchField("Content", preview);
            }
        }
    }

    // -- actions (propose + handoff; the whole authority boundary) --------------

    private async void OnLatchProposeCancellationClicked(object sender, RoutedEventArgs args)
    {
        if (sender is not Button button || button.Tag is not string commitmentId || _latchClient is null)
        {
            return;
        }
        if (_latchPlanInFlight)
        {
            // Repeated-click guard: one proposal in flight at a time; the
            // plan itself is idempotent server-side, double-submitting the
            // UI is still noise.
            return;
        }
        _latchPlanInFlight = true;
        button.IsEnabled = false;
        LatchStatusText.Text = "Proposing cancellation plan…";
        try
        {
            var plan = await _latchClient.PlanCancellationAsync(commitmentId);
            var planId = GetString(plan, "plan_id") ?? GetString(plan, "id");
            // actions.plan is idempotent: the result may be a plan that
            // already advanced (Authorized/Executed/…). Say what Latch says,
            // never assume "Proposed".
            var status = LatchRecordProjection.ActionPresentation(GetString(plan, "status"));
            LatchStatusText.Text = planId is null
                ? $"Latch accepted the cancellation proposal ({status.Label})."
                : $"Plan {planId} is {status.Label}."
                    + (status.Complete
                        ? " Latch already verified this plan."
                        : status.State == LatchActionState.Failed
                            ? " Latch's verification found it did not take effect."
                            : " Nothing further happens until it advances in Latch's own action flow.");
            await RefreshLatchAsync(silent: true);
        }
        catch (Exception ex)
        {
            LatchErrorText.Text = $"Could not propose cancellation: {ex.Message}";
        }
        finally
        {
            _latchPlanInFlight = false;
            button.IsEnabled = true;
        }
    }

    private async void OnLatchHandoffClicked(object sender, RoutedEventArgs args)
    {
        if (sender is not Button button || button.Tag is not string planId || _latchClient is null)
        {
            return;
        }
        button.IsEnabled = false;
        try
        {
            // Probe Latch's own UI, not just its API: the deep link 404s
            // unless latchd runs with --web-root, so an API-only daemon must
            // disable the handoff with an honest message instead of opening
            // a dead link.
            if (!await _latchClient.IsHandoffAvailableAsync())
            {
                button.Content = "Latch UI unavailable";
                LatchStatusText.Text =
                    $"Latch's own desktop UI is not served at {_latchClient.BaseUri}. "
                    + "Start latchd with --web-root apps/desktop (plus --http-port 8787) so the "
                    + "authorization flow is served, then try again.";
                return;
            }
            var handoffUri = _latchClient.GetAuthorizationHandoffUri(planId);
            if (handoffUri is null)
            {
                button.Content = "Latch UI unavailable";
                LatchStatusText.Text = "Latch did not provide an authorization handoff link for this plan.";
                return;
            }
            // The deep link lands in Latch's real desktop action flow; status
            // is re-read on window reactivation (HookLatchActivation).
            await Launcher.LaunchUriAsync(handoffUri);
            button.Content = "Open in Latch…";
            button.IsEnabled = true;
        }
        catch (Exception ex)
        {
            LatchErrorText.Text = $"Could not open Latch: {ex.Message}";
            button.Content = "Open in Latch…";
            button.IsEnabled = true;
        }
    }

    // -- window reactivation: re-read current action status ---------------------

    private void HookLatchActivation()
    {
        if (_latchActivationHooked)
        {
            return;
        }
        _latchActivationHooked = true;
        Activated += (_, args) =>
        {
            // Returning to the window re-reads Latch: plans move
            // Proposed → Authorized → Executed → Verified in Latch's own
            // flow, and the page must reflect what Latch says now.
            if (args.WindowActivationState == WindowActivationState.Deactivated
                || _currentTag != "latch"
                || _latchClient is null)
            {
                return;
            }
            _ = RefreshLatchAsync(silent: true);
        };
    }

    // -- keyboard ---------------------------------------------------------------

    private void AddLatchAccelerators()
    {
        if (_latchAcceleratorsAdded)
        {
            return;
        }
        _latchAcceleratorsAdded = true;
        void Add(VirtualKey key, VirtualKeyModifiers modifiers, Action act)
        {
            var accelerator = new KeyboardAccelerator { Key = key, Modifiers = modifiers };
            accelerator.Invoked += (_, _) => act();
            LatchPanel.KeyboardAccelerators.Add(accelerator);
        }
        Add(VirtualKey.Number1, VirtualKeyModifiers.Control, () => SelectLatchTab("ledger"));
        Add(VirtualKey.Number2, VirtualKeyModifiers.Control, () => SelectLatchTab("access"));
        Add(VirtualKey.Number3, VirtualKeyModifiers.Control, () => SelectLatchTab("proposals"));
        Add(VirtualKey.F5, VirtualKeyModifiers.None, () => _ = RefreshLatchAsync());
    }

    // -- shared detail helpers --------------------------------------------------

    private void AddLatchHeading(string type, string id)
    {
        LatchDetails.Children.Add(new TextBlock
        {
            Text = type,
            Style = HavenStyle("HavenSectionTextStyle"),
        });
        LatchDetails.Children.Add(new TextBlock
        {
            Text = id,
            Style = HavenStyle("HavenMonoTextStyle"),
            TextWrapping = TextWrapping.Wrap,
        });
    }

    private void AddLatchSubheading(string title)
    {
        LatchDetails.Children.Add(new TextBlock
        {
            Text = title,
            Style = HavenStyle("HavenMetadataTextStyle"),
        });
    }

    private void AddLatchField(string label, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }
        AddLatchSubheading(label);
        LatchDetails.Children.Add(new TextBlock
        {
            Text = value,
            TextWrapping = TextWrapping.Wrap,
            Opacity = 0.85,
        });
    }

    private void AddLatchFacts(JsonElement record)
    {
        if (!record.TryGetProperty("facts", out var facts))
        {
            return;
        }
        if (facts.ValueKind == JsonValueKind.String)
        {
            try
            {
                using var parsed = JsonDocument.Parse(facts.GetString() ?? "{}");
                facts = parsed.RootElement.Clone();
            }
            catch (JsonException)
            {
                return;
            }
        }
        if (facts.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        AddLatchSubheading("Observed facts");
        AddLatchVerbatim(LatchDetails, facts);
    }

    // Verbatim projection of a Latch record: what Latch said, field by field.
    private static void AddLatchVerbatim(StackPanel host, JsonElement record, params string[] skip)
    {
        var skipSet = new HashSet<string>(skip, StringComparer.OrdinalIgnoreCase);
        foreach (var property in record.EnumerateObject())
        {
            if (skipSet.Contains(property.Name))
            {
                continue;
            }
            var value = property.Value.ValueKind switch
            {
                JsonValueKind.String => property.Value.GetString(),
                JsonValueKind.Number => property.Value.ToString(),
                JsonValueKind.True => "true",
                JsonValueKind.False => "false",
                JsonValueKind.Null or JsonValueKind.Undefined => null,
                _ => JsonSerializer.Serialize(property.Value),
            };
            if (!string.IsNullOrWhiteSpace(value))
            {
                host.Children.Add(new TextBlock
                {
                    Text = $"{property.Name}: {value}",
                    Style = HavenStyle("HavenMetadataTextStyle"),
                    TextWrapping = TextWrapping.Wrap,
                    Opacity = 0.85,
                });
            }
        }
    }

    // -- chips ------------------------------------------------------------------

    private static Border LatchStatusChip(LatchActionPresentation status)
    {
        var (tint, brush) = status.State switch
        {
            LatchActionState.Proposed => ("HavenAccentTintBrush", "HavenAccentBrush"),
            LatchActionState.Authorized => ("HavenVioletTintBrush", "HavenVioletBrush"),
            LatchActionState.Executed => ("HavenAccentTintBrush", "HavenAccentBrush"),
            LatchActionState.AwaitingVerification => ("HavenWarningTintBrush", "HavenWarningBrush"),
            LatchActionState.Verified => ("HavenSuccessTintBrush", "HavenSuccessBrush"),
            LatchActionState.Failed => ("HavenDangerTintBrush", "HavenDangerBrush"),
            _ => ("HavenSurface2Brush", "HavenMutedTextBrush"),
        };
        return LatchChip(status.Label, tint, brush);
    }

    private static Border LatchStageChip(LatchStage stage) => stage.State switch
    {
        LatchStageState.Done => LatchChip(stage.Name, "HavenSuccessTintBrush", "HavenSuccessBrush"),
        LatchStageState.Current => LatchChip(stage.Name, "HavenWarningTintBrush", "HavenWarningBrush"),
        LatchStageState.Failed => LatchChip(stage.Name, "HavenDangerTintBrush", "HavenDangerBrush"),
        _ => LatchChip(stage.Name, "HavenSurface2Brush", "HavenMutedTextBrush"),
    };

    private static Border LatchChip(string text, string tintBrushKey, string brushKey)
    {
        var resources = Application.Current.Resources;
        return new Border
        {
            CornerRadius = new CornerRadius(10, 10, 10, 10),
            Padding = new Thickness(8, 2, 8, 2),
            Background = (Brush)resources[tintBrushKey],
            Child = new TextBlock
            {
                Text = text,
                FontSize = 11.5,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                Foreground = (Brush)resources[brushKey],
            },
        };
    }

    // -- small helpers ----------------------------------------------------------

    private static string? Combine(string? first, string? second) =>
        first is null ? second : second is null ? first : $"{first} · {second}";

    private static string ShortId(string id) => id.Length <= 16 ? id : id[..13] + "…";

    private static Style HavenStyle(string key) => (Style)Application.Current.Resources[key];
}
