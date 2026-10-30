using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;

namespace Haven.Desktop.Tests;

/// <summary>Record-shape and projection tests for the native Latch plugin,
/// pinned against the real wire shapes Latch's stores emit (see
/// LatchRecordProjection's doc comment for the source files). Pure
/// System.Text.Json in, presentation records out - no WinUI, no HTTP.</summary>
public sealed class LatchRecordProjectionTests
{
    private static JsonElement Parse(string json) => JsonDocument.Parse(json).RootElement.Clone();

    private const string RealCommitment = """
    {
      "id": "com_provider_adobe_f200800ac9",
      "kind": "subscription",
      "lifecycle": "active",
      "provider": "provider_adobe",
      "subject": "Creative Cloud",
      "account": "you@example.com",
      "economics": {"amount_minor": 5999, "currency": "USD", "cadence": "monthly"},
      "temporal": {"renewal_at": "2026-10-14T00:00:00-07:00", "cancel_before": "2026-10-13T00:00:00-07:00"},
      "confidence": {"overall": "SUPPORTED"},
      "facts": {
        "kind": {"value": "subscription", "evidence_id": "ev_invoice_1"},
        "amount_minor": {"value": 5999, "evidence_id": "ev_invoice_1"},
        "renewal_at": {"value": "2026-10-14T00:00:00-07:00", "evidence_id": "ev_notice_2"}
      },
      "evidence": ["ev_invoice_1", "ev_notice_2"],
      "updated_at": "2026-09-01T09:00:02-07:00"
    }
    """;

    [Fact]
    public void LedgerRow_reads_the_real_nested_commitment_shape()
    {
        var row = LatchRecordProjection.LedgerRow(Parse(RealCommitment));

        Assert.Equal("com_provider_adobe_f200800ac9", row.Id);
        Assert.Equal("provider_adobe", row.Provider);
        Assert.Equal("59.99 USD · monthly", row.Cost);
        Assert.Equal("2026-10-14T00:00:00-07:00", row.RenewalAt);
        Assert.Equal("2026-10-13T00:00:00-07:00", row.CancelBefore);
        Assert.Equal("Supported", row.Confidence);
        Assert.Equal(new[] { "ev_invoice_1", "ev_notice_2" }, row.EvidenceIds);
    }

    [Fact]
    public void LedgerRow_falls_back_to_fact_evidence_ids_when_no_evidence_array()
    {
        var json = RealCommitment.Replace("\"evidence\": [\"ev_invoice_1\", \"ev_notice_2\"],", "");
        var row = LatchRecordProjection.LedgerRow(Parse(json));

        Assert.Equal(new[] { "ev_invoice_1", "ev_notice_2" }, row.EvidenceIds);
    }

    [Fact]
    public void LedgerRow_handles_missing_economics_and_temporal_honestly()
    {
        var row = LatchRecordProjection.LedgerRow(Parse("{\"id\":\"com_x\",\"provider\":\"p\"}"));

        Assert.Equal("com_x", row.Id);
        Assert.Null(row.Cost);
        Assert.Null(row.RenewalAt);
        Assert.Null(row.CancelBefore);
        Assert.Empty(row.EvidenceIds);
    }

    [Fact]
    public void Items_unwraps_every_list_envelope_latch_uses()
    {
        Assert.Equal(2, LatchRecordProjection.Items(Parse("[1,2]")).Count);
        Assert.Single(LatchRecordProjection.Items(Parse("{\"items\":[{\"a\":1}]}")));
        Assert.Single(LatchRecordProjection.Items(Parse("{\"commitments\":[{\"a\":1}]}")));
        Assert.Single(LatchRecordProjection.Items(Parse("{\"proposals\":[{\"a\":1}]}")));
        Assert.Empty(LatchRecordProjection.Items(Parse("{\"unrelated\":true}")));
        Assert.Empty(LatchRecordProjection.Items(Parse("42")));
    }

    private const string RealAccessGrant = """
    {
      "id": "agr_gmail_1",
      "grantor": {"id": "ent_you", "display_name": "You"},
      "grantee": {"id": "ent_gmail", "display_name": "Gmail", "kind": "app"},
      "resource": {"id": "ent_mailbox", "display_name": "you@example.com"},
      "capabilities": ["read"],
      "granted_at": "2025-01-01T00:00:00Z",
      "expires_at": null,
      "last_used_at": null,
      "last_reviewed_at": null,
      "parent_commitment": null,
      "source_connector": "gmail",
      "source_object_id": "grant:42",
      "stale_notified": false,
      "updated_at": "2026-10-20T12:00:00Z",
      "stale": true,
      "stale_reason": "no recorded use since granted 302 days ago"
    }
    """;

    [Fact]
    public void AccessRow_reads_grantee_resource_and_real_staleness()
    {
        var row = LatchRecordProjection.AccessRow(Parse(RealAccessGrant));

        Assert.Equal("agr_gmail_1", row.Id);
        Assert.Equal("Gmail", row.Grantee);
        Assert.Equal("you@example.com", row.Resource);
        Assert.True(row.Stale);
        Assert.Equal("no recorded use since granted 302 days ago", row.StaleReason);
        Assert.Equal("gmail", row.SourceConnector);
        Assert.Equal("grant:42", row.SourceObjectId);
        Assert.Equal("2026-10-20T12:00:00Z", row.UpdatedAt);
    }

    [Fact]
    public void AccessRow_on_a_fresh_grant_has_no_reason()
    {
        var json = RealAccessGrant
            .Replace("\"stale\": true", "\"stale\": false")
            .Replace("\"stale_reason\": \"no recorded use since granted 302 days ago\"", "\"stale_reason\": null");
        var row = LatchRecordProjection.AccessRow(Parse(json));

        Assert.False(row.Stale);
        Assert.Null(row.StaleReason);
    }

    private const string RealProposal = """
    {
      "id": "prop_9f2c",
      "schedule_id": "sch_stale_1",
      "occurrence_id": "occ_1",
      "kind": "stale_access_review",
      "target": {"type": "access_grant", "access_grant_id": "agr_gmail_1"},
      "target_key": "agr_gmail_1:stale:no-use",
      "reason": "grant idle beyond 180 day policy",
      "status": "proposed",
      "evidence": [
        {"source": "assertion evidence for commitment ent_gmail via connector gmail",
         "description": "last_used observation",
         "evidence_id": "ev_mail_7",
         "acquired_at": "2025-12-01T10:00:00Z",
         "age_days": 332,
         "freshness": "stale",
         "fresh": false}
      ],
      "budgets": {"proposals_created": 1},
      "idempotency_key": "sched:sch_stale_1:occ:agr_gmail_1:stale:no-use",
      "created_at": "2026-10-29T09:00:00Z"
    }
    """;

    [Fact]
    public void ProposalRow_reads_nested_target_and_evidence_array()
    {
        var row = LatchRecordProjection.ProposalRow(Parse(RealProposal));

        Assert.Equal("prop_9f2c", row.Id);
        Assert.Equal("sch_stale_1", row.ScheduleId);
        Assert.Equal("stale_access_review", row.Kind);
        Assert.Equal("proposed", row.Status);
        Assert.Equal("grant idle beyond 180 day policy", row.Reason);
        Assert.Equal(new[] { "agr_gmail_1" }, row.TargetIds);
        var evidence = Assert.Single(row.Evidence);
        Assert.Equal("ev_mail_7", evidence.EvidenceId);
        Assert.Equal("2025-12-01T10:00:00Z", evidence.AcquiredAt);
        Assert.Equal("stale", evidence.Freshness);
    }

    [Fact]
    public void ProposalRow_collects_commitment_targets_too()
    {
        var json = RealProposal.Replace(
            "\"target\": {\"type\": \"access_grant\", \"access_grant_id\": \"agr_gmail_1\"}",
            "\"target\": {\"type\": \"commitment\", \"commitment_id\": \"com_adobe\"}");
        var row = LatchRecordProjection.ProposalRow(Parse(json));

        Assert.Equal(new[] { "com_adobe" }, row.TargetIds);
    }

    [Theory]
    [InlineData("proposed", "Proposed", false, LatchActionState.Proposed)]
    [InlineData("authorized", "Authorized", false, LatchActionState.Authorized)]
    [InlineData("executed", "Executed", false, LatchActionState.Executed)]
    [InlineData("needs_verification", "Executed — awaiting verification", false, LatchActionState.AwaitingVerification)]
    [InlineData("succeeded", "Verified", true, LatchActionState.Verified)]
    [InlineData("verified", "Verified", true, LatchActionState.Verified)]
    [InlineData("failed", "Failed", false, LatchActionState.Failed)]
    [InlineData(null, "Unknown", false, LatchActionState.Unknown)]
    public void ActionPresentation_keeps_states_distinct_and_only_verified_is_complete(
        string? status, string label, bool complete, LatchActionState state)
    {
        var presentation = LatchRecordProjection.ActionPresentation(status);

        Assert.Equal(label, presentation.Label);
        Assert.Equal(complete, presentation.Complete);
        Assert.Equal(state, presentation.State);
    }

    [Fact]
    public void ActionStages_never_show_verified_complete_for_transport_success()
    {
        var awaiting = LatchRecordProjection.ActionStages("needs_verification");

        Assert.Equal(LatchStageState.Done, awaiting[0].State);
        Assert.Equal(LatchStageState.Done, awaiting[1].State);
        Assert.Equal(LatchStageState.Current, awaiting[2].State);
        Assert.Equal(LatchStageState.Pending, awaiting[3].State);

        var verified = LatchRecordProjection.ActionStages("succeeded");
        Assert.All(verified, stage => Assert.Equal(LatchStageState.Done, stage.State));

        var failed = LatchRecordProjection.ActionStages("failed");
        Assert.Equal(LatchStageState.Failed, failed[3].State);

        var proposed = LatchRecordProjection.ActionStages("proposed");
        Assert.Equal(LatchStageState.Current, proposed[0].State);
        Assert.All(proposed.Skip(1), stage => Assert.Equal(LatchStageState.Pending, stage.State));
    }
}
