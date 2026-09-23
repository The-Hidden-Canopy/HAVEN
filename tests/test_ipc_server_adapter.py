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
            audit = server.claims.list_audit(scope_id=household_id)
            assert [event.action.value for event in audit] == ["correct", "mark_stale", "forget"]
            assert {event.actor_id for event in audit} == {"gerron"}
        finally:
            server.server_close()


def test_native_setup_household_authoring_round_trips_through_the_same_service():
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            dispatcher = server.build_ipc_dispatcher()

            added = dispatcher(
                request_message(
                    "req-add-person",
                    "setup.household.people.add",
                    {"name": "Gerron Smith", "role": "owner"},
                )
            )
            assert added["ok"] is True
            assert added["result"]["ok"] is True
            person_id = next(
                person["person_id"]
                for person in added["result"]["setup"]["household"]["people"]
                if person["name"] == "Gerron Smith"
            )

            removed = dispatcher(
                request_message("req-remove-person", "setup.household.people.remove", {"person_id": person_id})
            )
            assert removed["ok"] is True
            assert removed["result"]["ok"] is True
            assert person_id not in {
                person["person_id"] for person in removed["result"]["setup"]["household"]["people"]
            }

            added_context = dispatcher(
                request_message(
                    "req-add-context",
                    "setup.household.contexts.add",
                    {"label": "Away", "entity_id": "input_boolean.away"},
                )
            )
            assert added_context["ok"] is True
            assert added_context["result"]["ok"] is True
            context_id = next(
                context["context_id"]
                for context in added_context["result"]["setup"]["household"]["contexts"]
                if context["label"] == "Away"
            )

            removed_context = dispatcher(
                request_message(
                    "req-remove-context", "setup.household.contexts.remove", {"context_id": context_id}
                )
            )
            assert removed_context["ok"] is True
            assert removed_context["result"]["ok"] is True
        finally:
            server.server_close()


def test_native_setup_preferences_and_lifecycle_delegate_to_setup_service():
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            dispatcher = server.build_ipc_dispatcher()

            preferences = dispatcher(
                request_message(
                    "req-preferences", "setup.preferences", {"voice": False, "intelligence": True}
                )
            )
            assert preferences["ok"] is True
            assert preferences["result"]["ok"] is True

            packages = dispatcher(request_message("req-packages", "setup.providers.packages", {}))
            assert packages["ok"] is True
            assert packages["result"]["ok"] is True
            assert "providers" in packages["result"]

            owner = dispatcher(
                request_message(
                    "req-owner",
                    "setup.household.people.add",
                    {"name": "Gerron Smith", "role": "owner"},
                )
            )
            assert owner["ok"] is True
            assert owner["result"]["ok"] is True

            completed = dispatcher(request_message("req-complete", "setup.complete", {}))
            assert completed["ok"] is True
            assert completed["result"]["ok"] is True

            reopened = dispatcher(request_message("req-reopen", "setup.reopen", {}))
            assert reopened["ok"] is True
            assert reopened["result"]["ok"] is True
        finally:
            server.server_close()
