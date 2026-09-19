"""Resource observations feed deterministic extraction and admission."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.knowledge import ClaimState, ClaimStore, KnowledgeService
from haven.resources import ResourceRecord, ResourceStore

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _resource(resource_id="file:C:/Docs/proposal.md", content_hash="hash-1") -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        resource_type="file",
        scope_id="personal:gerron",
        provider_id="local_filesystem",
        title="proposal.md",
        locator="C:/Docs/proposal.md",
        capabilities=("filesystem.read",),
        observed_at=NOW,
        content_hash=content_hash,
    )


def test_new_text_resource_is_admitted_and_same_hash_rescan_dedupes():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        service = KnowledgeService(resources=resources, claims=claims, clock=lambda: NOW)
        record = _resource()
        reader = lambda resource: "The proposal concerns lunar site preparation."

        first = service.ingest_resource(record, reader=reader)
        resources.save(record)
        second = service.ingest_resource(record, reader=reader)

        assert first.admitted == 1
        assert second.skipped_unchanged is True
        assert len(claims.list_all()) == 1
        assert claims.list_all()[0].state is ClaimState.REPORTED


def test_changed_hash_stales_old_claim_and_admits_new_content():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        service = KnowledgeService(resources=resources, claims=claims, clock=lambda: NOW)
        old = _resource(content_hash="hash-old")
        new = _resource(content_hash="hash-new")

        service.ingest_resource(old, reader=lambda resource: "The launch date is October 1.")
        resources.save(old)
        service.ingest_resource(new, reader=lambda resource: "The launch date is October 2.")
        resources.save(new)

        stored = claims.list_all()
        assert len(stored) == 2
        assert sum(claim.state is ClaimState.STALE for claim in stored) == 1
        assert sum(claim.state is ClaimState.REPORTED for claim in stored) == 1


def test_deleted_resource_stales_claim_but_does_not_delete_history():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        service = KnowledgeService(resources=resources, claims=claims, clock=lambda: NOW)
        record = _resource()
        service.ingest_resource(record, reader=lambda resource: "The proposal concerns lunar site preparation.")
        resources.save(record)
        resources.reconcile(provider_id="local_filesystem", scope_id=record.scope_id, observed_ids=())
        assert service.reconcile_stale_sources(provider_id="local_filesystem", scope_id=record.scope_id) == 1

        assert claims.list_all()[0].state is ClaimState.STALE


def test_revoking_a_root_stales_both_resources_and_dependent_claims():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        service = KnowledgeService(resources=resources, claims=claims, clock=lambda: NOW)
        record = _resource()
        service.ingest_resource(record, reader=lambda resource: "A source statement.")
        resources.save(record)

        marked_resources, marked_claims = service.revoke_locator_prefix("C:/Docs")

        assert marked_resources == 1
        assert marked_claims == 1
        assert resources.get(record.resource_id).stale is True
        assert claims.list_all()[0].state is ClaimState.STALE
