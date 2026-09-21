"""The native adapter delegates to current application services."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.ipc import request_message
from haven.knowledge import Claim, ClaimProvenance, ClaimState
from haven.resources import ResourceRecord
from haven.web.server import make_server

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_native_search_is_bounded_to_the_authenticated_household_scope():
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            household_id = server.director.household_id
            server.resources.save(
                ResourceRecord(
                    resource_id="file:visible",
                    resource_type="file",
                    scope_id=household_id,
                    provider_id="local_computer",
                    title="visible notes",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
            server.resources.save(
                ResourceRecord(
                    resource_id="file:secret",
                    resource_type="file",
                    scope_id="secret-project",
                    provider_id="local_computer",
                    title="visible secret notes",
                    locator=None,
                    capabilities=(),
                    observed_at=NOW,
                )
            )
            dispatcher = server.build_ipc_dispatcher()

            visible = dispatcher(request_message("req-visible", "search.query", {"text": "notes"}))
            blocked = dispatcher(
                request_message(
                    "req-secret",
                    "search.query",
                    {"text": "notes", "scope_ids": ["secret-project"]},
                )
            )

            assert visible["ok"] is True
            assert [hit["resource_id"] for hit in visible["result"]["hits"]] == ["file:visible"]
            assert blocked["ok"] is False
            assert "authenticated household scope" in blocked["error"]
        finally:
            server.server_close()


def test_native_memory_detail_returns_provenance_and_rejects_other_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            household_id = server.director.household_id
            server.claims.save(
                Claim(
                    claim_id="claim:visible",
                    scope_id=household_id,
                    proposition="The proposal concerns lunar site preparation.",
                    state=ClaimState.REPORTED,
                    source_refs=("file:proposal",),
                    evidence_refs=("file:proposal#paragraph:4",),
                    created_at=NOW,
                    confidence=0.9,
                    provenance=ClaimProvenance.DOCUMENT_STATED,
                )
            )
            server.claims.save(
                Claim(
                    claim_id="claim:secret",
                    scope_id="secret-project",
                    proposition="Hidden claim.",
                    state=ClaimState.REPORTED,
                    source_refs=(),
                    evidence_refs=(),
                    created_at=NOW,
                    provenance=ClaimProvenance.USER_REPORTED,
                )
            )
            dispatcher = server.build_ipc_dispatcher()

            visible = dispatcher(
                request_message("req-visible-claim", "knowledge.claim", {"claim_id": "claim:visible"})
            )
            blocked = dispatcher(
                request_message("req-secret-claim", "knowledge.claim", {"claim_id": "claim:secret"})
            )

            assert visible["ok"] is True
            detail = visible["result"]["claim"]
            assert detail["proposition"].startswith("The proposal concerns")
            assert detail["sources"][0]["ref"] == "file:proposal"
            assert detail["evidence_refs"] == ["file:proposal#paragraph:4"]
            assert blocked["ok"] is False
            assert "unknown claim" in blocked["error"]
        finally:
            server.server_close()


def test_native_memory_mutations_are_owner_bound_and_preserve_correction_lineage():
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            household_id = server.director.household_id
            original = Claim(
                claim_id="claim:original",
                scope_id=household_id,
                proposition="The launch date is October 1.",
                state=ClaimState.REPORTED,
                source_refs=("file:proposal",),
                evidence_refs=("file:proposal#paragraph:4",),
                created_at=NOW,
                confidence=0.9,
                provenance=ClaimProvenance.DOCUMENT_STATED,
            )
            server.claims.save(original)

            dispatcher = server.build_ipc_dispatcher()
            denied = dispatcher(
                request_message(
                    "req-no-owner",
                    "knowledge.claim.correct",
                    {
                        "claim_id": original.claim_id,
                        "proposition": "The launch date is October 2.",
                        "actor": "foreign-user",
                    },
                )
            )
            assert denied["ok"] is False
            assert "owner" in denied["error"]

            declared = server.setup.declare_person(name="Gerron", role="owner")
            assert declared["ok"] is True
            server.claims.save(original)
            dispatcher = server.build_ipc_dispatcher()

            corrected = dispatcher(
                request_message(
                    "req-correct",
                    "knowledge.claim.correct",
                    {
                        "claim_id": original.claim_id,
                        "proposition": "The launch date is October 2.",
                        "actor": "foreign-user",
                    },
                )
            )
            assert corrected["ok"] is True
            replacement = corrected["result"]["claim"]
            assert replacement["source_refs"] == ["file:proposal"]
            assert replacement["evidence_refs"] == ["claim:original", "user:gerron"]
            assert replacement["supersedes"] == ["claim:original"]
            assert server.claims.get(original.claim_id).state is ClaimState.STALE

            stale = dispatcher(
                request_message(
                    "req-stale",
                    "knowledge.claim.stale",
                    {"claim_id": replacement["claim_id"]},
                )
            )
            assert stale["ok"] is True
            assert stale["result"]["changed"] is True

            forgotten = dispatcher(
                request_message(
                    "req-forget",
                    "knowledge.claim.forget",
                    {"claim_id": replacement["claim_id"]},
                )
            )
            assert forgotten["ok"] is True
            assert server.claims.get(replacement["claim_id"]).state is ClaimState.STALE
        finally:
            server.server_close()
