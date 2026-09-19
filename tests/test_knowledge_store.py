"""`ClaimStore`: persistence, scope isolation, provenance, contradictions,
stale state, restart survival."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from haven.knowledge import Claim, ClaimState, ClaimStore, is_stale

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _claim(
    claim_id="claim-1",
    scope_id="project:haven",
    *,
    state=ClaimState.REPORTED,
    source_refs=("doc:solicitation",),
    evidence_refs=("evidence:1",),
    valid_until=None,
    supersedes=(),
    contradicts=(),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        scope_id=scope_id,
        proposition="deadline is Sept 28",
        state=state,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
        created_at=NOW,
        valid_until=valid_until,
        supersedes=supersedes,
        contradicts=contradicts,
    )


def test_save_and_get_round_trips_every_field():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim())
        assert store.get("claim-1") == _claim()


def test_get_missing_claim_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        assert store.get("nope") is None


def test_scope_isolation_never_leaks_across_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim("claim-a", "project:haven"))
        store.save(_claim("claim-b", "project:other"))

        assert [c.claim_id for c in store.list_by_scope("project:haven")] == ["claim-a"]
        assert [c.claim_id for c in store.list_by_scope("project:other")] == ["claim-b"]
        assert store.list_by_scope("project:nonexistent") == ()


def test_provenance_round_trips_source_and_evidence_refs():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim(source_refs=("doc:solicitation", "conversation:call-1"), evidence_refs=("evidence:1",)))
        loaded = store.get("claim-1")
    assert loaded.source_refs == ("doc:solicitation", "conversation:call-1")
    assert loaded.evidence_refs == ("evidence:1",)


def test_contradictions_of_resolves_the_actual_conflicting_claims():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim("claim-sept28", source_refs=("doc:solicitation",), contradicts=("claim-oct1",)))
        store.save(
            Claim(
                claim_id="claim-oct1",
                scope_id="project:haven",
                proposition="deadline is Oct 1",
                state=ClaimState.REPORTED,
                source_refs=("conversation:call-1",),
                evidence_refs=(),
                created_at=NOW,
            )
        )

        conflicts = store.contradictions_of("claim-sept28")
        assert [c.claim_id for c in conflicts] == ["claim-oct1"]
        # And the disagreement is symmetric information, not a chosen winner
        # -- neither claim's own state changes just because it was named.
        assert store.get("claim-oct1").state == ClaimState.REPORTED
        assert store.get("claim-sept28").state == ClaimState.REPORTED


def test_contradictions_of_a_missing_reference_degrades_to_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim(contradicts=("claim-never-saved",)))
        assert store.contradictions_of("claim-1") == ()
        assert store.contradictions_of("claim-does-not-exist") == ()


def test_supersedes_of_resolves_the_prior_claim():
    with tempfile.TemporaryDirectory() as tmp:
        store = ClaimStore(Path(tmp) / "claims.db")
        store.save(_claim("claim-v2", supersedes=("claim-v1",)))
        store.save(_claim("claim-v1"))
        assert [c.claim_id for c in store.supersedes_of("claim-v2")] == ["claim-v1"]


def test_is_stale_true_for_an_explicit_stale_state():
    claim = _claim(state=ClaimState.STALE)
    assert is_stale(claim, now=NOW) is True


def test_is_stale_true_once_valid_until_has_passed():
    claim = _claim(valid_until=NOW + timedelta(hours=1))
    assert is_stale(claim, now=NOW) is False
    assert is_stale(claim, now=NOW + timedelta(hours=2)) is True


def test_is_stale_false_with_no_expiry_and_a_fresh_state():
    claim = _claim(state=ClaimState.CORROBORATED)
    assert is_stale(claim, now=NOW + timedelta(days=365)) is False


def test_persists_across_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "claims.db"
        first = ClaimStore(db_path)
        first.save(_claim())

        second = ClaimStore(db_path)
        assert second.get("claim-1") == _claim()
