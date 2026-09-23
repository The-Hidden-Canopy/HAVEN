"""Knowledge mutation audit records are append-only and durable."""

from __future__ import annotations

import tempfile
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.knowledge import (
    Claim,
    ClaimProvenance,
    ClaimState,
    KnowledgeAuditAction,
    KnowledgeAuditEvent,
    ClaimStore,
    KnowledgeService,
)
from haven.knowledge.store import claim_fingerprint
from haven.resources import ResourceStore

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _event(event_id: str = "knowledge-audit:one") -> KnowledgeAuditEvent:
    return KnowledgeAuditEvent(
        event_id=event_id,
        scope_id="household-a",
        claim_id="claim:one",
        action=KnowledgeAuditAction.CORRECT,
        actor_id="gerron",
        occurred_at=NOW,
        details=(("replacement_claim_id", "claim:two"),),
    )


def test_knowledge_audit_is_append_only_indexed_and_survives_reopen():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "claims.db"
        store = ClaimStore(path)
        store.append_audit(_event())
        store.append_audit(
            KnowledgeAuditEvent(
                event_id="knowledge-audit:two",
                scope_id="household-a",
                claim_id="claim:one",
                action=KnowledgeAuditAction.FORGET,
                actor_id="gerron",
                occurred_at=NOW,
            )
        )

        assert [event.action for event in store.list_audit(claim_id="claim:one")] == [
            KnowledgeAuditAction.CORRECT,
            KnowledgeAuditAction.FORGET,
        ]
        assert store.list_audit(scope_id="secret-project") == ()
        assert store.list_audit(claim_id="claim:one")[0].details == (
            ("replacement_claim_id", "claim:two"),
        )

        reopened = ClaimStore(path)
        assert len(reopened.list_audit(scope_id="household-a")) == 2
        with pytest.raises(sqlite3.IntegrityError):
            reopened.append_audit(_event())


def _claim() -> Claim:
    return Claim(
        claim_id="claim:one",
        scope_id="household-a",
        proposition="The launch date is October 1.",
        state=ClaimState.REPORTED,
        source_refs=("file:proposal",),
        evidence_refs=("file:proposal#paragraph:4",),
        created_at=NOW,
        confidence=0.9,
        provenance=ClaimProvenance.DOCUMENT_STATED,
    )


def test_correction_rolls_back_claim_replacement_when_audit_write_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        claims = ClaimStore(Path(tmp) / "claims.db")
        claims.save(_claim())
        service = KnowledgeService(
            resources=ResourceStore(Path(tmp) / "resources.db"),
            claims=claims,
            clock=lambda: NOW,
        )

        def fail(_conn, _event):
            raise OSError("audit database unavailable")

        monkeypatch.setattr(ClaimStore, "_append_audit_on_connection", staticmethod(fail))
        with pytest.raises(OSError, match="audit database unavailable"):
            service.correct_claim(
                "claim:one",
                proposition="The launch date is October 2.",
                actor="gerron",
            )

        assert claims.get("claim:one").state is ClaimState.REPORTED
        assert len(claims.list_all()) == 1
        assert claims.list_audit() == ()


@pytest.mark.parametrize("operation", ["stale", "forget"])
def test_stale_and_forget_roll_back_claim_when_audit_write_fails(monkeypatch, operation):
    with tempfile.TemporaryDirectory() as tmp:
        claims = ClaimStore(Path(tmp) / "claims.db")
        original = _claim()
        claims.save(original)
        service = KnowledgeService(
            resources=ResourceStore(Path(tmp) / "resources.db"),
            claims=claims,
            clock=lambda: NOW,
        )

        def fail(_conn, _event):
            raise OSError("audit database unavailable")

        monkeypatch.setattr(ClaimStore, "_append_audit_on_connection", staticmethod(fail))
        with pytest.raises(OSError, match="audit database unavailable"):
            if operation == "stale":
                service.mark_claim_stale(original.claim_id, actor="gerron")
            else:
                service.forget_claim(original, forgotten_by="gerron")

        assert claims.get(original.claim_id).state is ClaimState.REPORTED
        assert claims.is_forgotten(claim_fingerprint(original)) is False
        assert claims.list_audit() == ()
