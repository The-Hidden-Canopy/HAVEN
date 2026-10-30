using System.Globalization;
using System.Text.Json;

namespace Haven.Desktop;

/// <summary>Pure, UI-free projections from raw Latch API records
/// (System.Text.Json only, no WinUI) so the real wire shapes are unit-test
/// without a WinUI host. Shapes pinned here come from Latch's own stores:
///
/// - commitments: economics.{amount_minor,currency,cadence},
///   temporal.{renewal_at,cancel_before}, confidence.overall, facts object,
///   evidence array of ids (core/projection/projection_store.cpp).
/// - access grants: grantee/resource objects, stale bool + stale_reason,
///   source_connector/source_object_id/updated_at (core/access/access_store.cpp).
/// - scheduler proposals: nested target objects (access_grant_id /
///   commitment_id), evidence ARRAY of {source, description, evidence_id,
///   acquired_at, freshness} (core/scheduler/scheduler.cpp).
/// - action plans: status proposed → authorized → needs_verification →
///   succeeded/failed; transport success is never verification
///   (core/actions/action_engine.cpp).
///
/// Everything returned is presentation-shaped text; no instants are computed
/// or reformatted beyond minor-unit money display.</summary>
public enum LatchStageState
{
    Pending,
    Current,
    Done,
    Failed,
}

public enum LatchActionState
{
    Proposed,
    Authorized,
    Executed,
    AwaitingVerification,
    Verified,
    Failed,
    Unknown,
}

public sealed record LatchStage(string Name, LatchStageState State);

public sealed record LatchActionPresentation(string Label, bool Complete, LatchActionState State);

public sealed record LatchLedgerRow(
    string Id,
    string? Provider,
    string? Cost,
    string? RenewalAt,
    string? CancelBefore,
    string Confidence,
    IReadOnlyList<string> EvidenceIds);

public sealed record LatchAccessRow(
    string Id,
    string Grantee,
    string Resource,
    bool Stale,
    string? StaleReason,
    string? SourceConnector,
    string? SourceObjectId,
    string? UpdatedAt);

public sealed record LatchEvidenceRef(
    string? Source,
    string? Description,
    string? EvidenceId,
    string? AcquiredAt,
    string? Freshness);

public sealed record LatchProposalRow(
    string Id,
    string? ScheduleId,
    string? Kind,
    string? Status,
    string? CreatedAt,
    string? Reason,
    IReadOnlyList<string> TargetIds,
    IReadOnlyList<LatchEvidenceRef> Evidence);

public static class LatchRecordProjection
{
    // -- envelope unwrapping ----------------------------------------------------

    /// <summary>Latch list endpoints answer either a bare array or an object
    /// wrapping it (<c>{"items": [...]}</c> for query endpoints, or a named
    /// array like <c>commitments</c>/<c>proposals</c>); accept all of them.</summary>
    public static IReadOnlyList<JsonElement> Items(JsonElement root)
    {
        if (root.ValueKind == JsonValueKind.Array)
        {
            return root.EnumerateArray().ToList();
        }
        if (root.ValueKind != JsonValueKind.Object)
        {
            return Array.Empty<JsonElement>();
        }
        if (root.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
        {
            return items.EnumerateArray().ToList();
        }
        foreach (var key in new[] { "commitments", "access", "grants", "proposals", "plans", "actions" })
        {
            if (root.TryGetProperty(key, out var named) && named.ValueKind == JsonValueKind.Array)
            {
                return named.EnumerateArray().ToList();
            }
        }
        return Array.Empty<JsonElement>();
    }

    // -- commitments ------------------------------------------------------------

    public static LatchLedgerRow LedgerRow(JsonElement commitment)
    {
        var factsEvidence = new List<string>();
        if (commitment.TryGetProperty("facts", out var facts) && facts.ValueKind == JsonValueKind.Object)
        {
            foreach (var fact in facts.EnumerateObject())
            {
                if (fact.Value.ValueKind == JsonValueKind.Object
                    && TryString(fact.Value, "evidence_id") is { } factEvidence)
                {
                    factsEvidence.Add(factEvidence);
                }
            }
        }
        var evidence = EvidenceIds(commitment);
        // Facts can cite the same evidence record under several predicates;
        // merge without duplicating.
        var merged = evidence.Count > 0
            ? evidence
            : factsEvidence.Distinct(StringComparer.Ordinal).ToList();
        return new LatchLedgerRow(
            Id: TryString(commitment, "id") ?? "",
            Provider: TryString(commitment, "provider") ?? TryString(commitment, "subject"),
            Cost: FormatCost(commitment),
            // Latch-computed instants, passed through verbatim.
            RenewalAt: commitment.TryGetProperty("temporal", out var temporal) ? TryString(temporal, "renewal_at") : null,
            CancelBefore: commitment.TryGetProperty("temporal", out var temporal2) ? TryString(temporal2, "cancel_before") : null,
            Confidence: FormatConfidence(commitment),
            EvidenceIds: merged);
    }

    public static string? FormatCost(JsonElement commitment)
    {
        if (commitment.TryGetProperty("economics", out var economics)
            && economics.ValueKind == JsonValueKind.Object)
        {
            if (!economics.TryGetProperty("amount_minor", out var amount) || amount.ValueKind != JsonValueKind.Number)
            {
                return null;
            }
            var currency = TryString(economics, "currency") ?? "";
            var cadence = TryString(economics, "cadence");
            var value = (amount.GetDecimal() / 100m).ToString("0.00", CultureInfo.InvariantCulture);
            var cost = $"{value} {currency}".Trim();
            return cadence is not null ? $"{cost} · {cadence}" : cost;
        }
        // Older/flat shapes and honest fallbacks.
        return TryString(commitment, "cost") ?? TryString(commitment, "price");
    }

    public static string FormatConfidence(JsonElement commitment)
    {
        var raw = commitment.TryGetProperty("confidence", out var confidence)
            && confidence.ValueKind == JsonValueKind.Object
                ? TryString(confidence, "overall")
                : TryString(commitment, "confidence_state");
        return raw is null ? "Unknown" : NormalizeState(raw);
    }

    // Latch emits confidence states in both cases ('SUPPORTED' from the
    // compiler, 'supported' from the read model); display one form.
    private static string NormalizeState(string value)
    {
        var text = value.Replace('_', ' ').Trim();
        if (text.Length > 1 && text == text.ToUpperInvariant())
        {
            text = text.ToLowerInvariant();
        }
        return Sentence(text);
    }

    /// <summary>Evidence references on a record: an array of bare ids
    /// (commitments) or of objects carrying <c>evidence_id</c> (scheduler
    /// proposals). Missing means "no evidence recorded" - never invent any.</summary>
    public static IReadOnlyList<string> EvidenceIds(JsonElement record)
    {
        var ids = new List<string>();
        if (!record.TryGetProperty("evidence", out var evidence))
        {
            return ids;
        }
        if (evidence.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in evidence.EnumerateArray())
            {
                var id = entry.ValueKind == JsonValueKind.String
                    ? entry.GetString()
                    : entry.ValueKind == JsonValueKind.Object ? TryString(entry, "evidence_id") : null;
                if (!string.IsNullOrWhiteSpace(id))
                {
                    ids.Add(id!);
                }
            }
        }
        else if (evidence.ValueKind == JsonValueKind.String)
        {
            ids.Add(evidence.GetString()!);
        }
        return ids;
    }

    // -- access grants ----------------------------------------------------------

    public static LatchAccessRow AccessRow(JsonElement grant)
    {
        var stale = grant.TryGetProperty("stale", out var staleValue)
            && staleValue.ValueKind == JsonValueKind.True;
        return new LatchAccessRow(
            Id: TryString(grant, "id") ?? TryString(grant, "grant_id") ?? "",
            Grantee: grant.TryGetProperty("grantee", out var grantee) ? EntityLabel(grantee) ?? "unknown grantee" : "unknown grantee",
            Resource: grant.TryGetProperty("resource", out var resource) ? EntityLabel(resource) ?? "unknown resource" : "unknown resource",
            Stale: stale,
            StaleReason: stale ? TryString(grant, "stale_reason") : null,
            SourceConnector: TryString(grant, "source_connector"),
            SourceObjectId: TryString(grant, "source_object_id"),
            UpdatedAt: TryString(grant, "updated_at"));
    }

    /// <summary>Grantee/resource are JSON entities; render their declared
    /// label fields, not the raw object.</summary>
    public static string? EntityLabel(JsonElement entity)
    {
        if (entity.ValueKind == JsonValueKind.String)
        {
            var text = entity.GetString();
            return string.IsNullOrWhiteSpace(text) ? null : text;
        }
        if (entity.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        foreach (var key in new[] { "display_name", "name", "label", "email", "address", "id" })
        {
            if (TryString(entity, key) is { Length: > 0 } value)
            {
                return value;
            }
        }
        return null;
    }

    // -- scheduler proposals ----------------------------------------------------

    public static LatchProposalRow ProposalRow(JsonElement proposal)
    {
        var targets = new List<string>();
        if (proposal.TryGetProperty("target", out var target))
        {
            CollectTargetIds(target, targets);
        }
        foreach (var key in new[] { "target_id", "commitment_id", "access_grant_id" })
        {
            if (TryString(proposal, key) is { } id && !targets.Contains(id))
            {
                targets.Add(id);
            }
        }
        var evidence = new List<LatchEvidenceRef>();
        if (proposal.TryGetProperty("evidence", out var evidenceArray) && evidenceArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in evidenceArray.EnumerateArray())
            {
                if (entry.ValueKind != JsonValueKind.Object)
                {
                    continue;
                }
                evidence.Add(new LatchEvidenceRef(
                    Source: TryString(entry, "source"),
                    Description: TryString(entry, "description"),
                    EvidenceId: TryString(entry, "evidence_id"),
                    AcquiredAt: TryString(entry, "acquired_at"),
                    Freshness: TryString(entry, "freshness")));
            }
        }
        return new LatchProposalRow(
            Id: TryString(proposal, "id") ?? TryString(proposal, "proposal_id") ?? "",
            ScheduleId: TryString(proposal, "schedule_id"),
            Kind: TryString(proposal, "kind"),
            Status: TryString(proposal, "status"),
            CreatedAt: TryString(proposal, "created_at"),
            Reason: TryString(proposal, "reason"),
            TargetIds: targets,
            Evidence: evidence);
    }

    private static void CollectTargetIds(JsonElement target, List<string> ids)
    {
        if (target.ValueKind == JsonValueKind.String)
        {
            if (!string.IsNullOrWhiteSpace(target.GetString()))
            {
                ids.Add(target.GetString()!);
            }
            return;
        }
        if (target.ValueKind != JsonValueKind.Object)
        {
            return;
        }
        foreach (var key in new[] { "access_grant_id", "commitment_id", "id", "target_id" })
        {
            if (TryString(target, key) is { } id && !ids.Contains(id))
            {
                ids.Add(id);
            }
        }
    }

    // -- action plans -----------------------------------------------------------

    /// <summary>Map Latch's plan status to a display label. States stay
    /// distinct; only <c>succeeded</c>/<c>verified</c> is complete and
    /// <c>needs_verification</c> (transport success) is explicitly not.</summary>
    public static LatchActionPresentation ActionPresentation(string? raw) => (raw ?? "") switch
    {
        "proposed" => new("Proposed", false, LatchActionState.Proposed),
        "authorized" => new("Authorized", false, LatchActionState.Authorized),
        "executed" => new("Executed", false, LatchActionState.Executed),
        "needs_verification" => new("Executed — awaiting verification", false, LatchActionState.AwaitingVerification),
        "succeeded" or "verified" => new("Verified", true, LatchActionState.Verified),
        "failed" => new("Failed", false, LatchActionState.Failed),
        _ => new(string.IsNullOrEmpty(raw) ? "Unknown" : Sentence(raw), false, LatchActionState.Unknown),
    };

    /// <summary>The four lifecycle stages as a progress display. Derived
    /// purely from Latch's status: needs_verification parks on the Executed
    /// stage with Verified still pending - it is never shown as complete.</summary>
    public static IReadOnlyList<LatchStage> ActionStages(string? status) => status switch
    {
        "proposed" => Stage(LatchStageState.Current),
        "authorized" => Stage(done: 1, current: 1),
        "executed" or "needs_verification" => Stage(done: 2, current: 2),
        "succeeded" or "verified" => Stage(done: 4),
        "failed" => new[]
        {
            new LatchStage("Proposed", LatchStageState.Done),
            new LatchStage("Authorized", LatchStageState.Done),
            new LatchStage("Executed", LatchStageState.Done),
            new LatchStage("Verified", LatchStageState.Failed),
        },
        _ => Stage(LatchStageState.Current),
    };

    private static IReadOnlyList<LatchStage> Stage(LatchStageState first) =>
        new[]
        {
            new LatchStage("Proposed", first),
            new LatchStage("Authorized", LatchStageState.Pending),
            new LatchStage("Executed", LatchStageState.Pending),
            new LatchStage("Verified", LatchStageState.Pending),
        };

    private static IReadOnlyList<LatchStage> Stage(int done, int current = -1) =>
        new[]
        {
            new LatchStage("Proposed", StageAt(0, done, current)),
            new LatchStage("Authorized", StageAt(1, done, current)),
            new LatchStage("Executed", StageAt(2, done, current)),
            new LatchStage("Verified", StageAt(3, done, current)),
        };

    private static LatchStageState StageAt(int index, int done, int current)
    {
        if (index < done && index != current)
        {
            return LatchStageState.Done;
        }
        return index == current ? LatchStageState.Current : LatchStageState.Pending;
    }

    // -- shared -----------------------------------------------------------------

    public static string? TryString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    public static string Sentence(string value) =>
        value.Length == 0 ? value : char.ToUpperInvariant(value[0]) + value[1..];
}
