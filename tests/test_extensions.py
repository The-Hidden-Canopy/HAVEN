"""Extensions: taxonomy registry, intelligence boundary enforcement, and the
export-consumer reclassification over IPC."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.extensions import (
    CLASS_BOUNDARIES,
    EchoIntelligenceService,
    ExtensionClass,
    ExtensionDescriptor,
    ExtensionRegistry,
    IntelligenceBoundary,
    IntelligenceResponse,
)
from haven.intelligence.intents import ActionProposal, MutationProposal
from haven.ipc import request_message
from haven.knowledge import Claim, ClaimProvenance, ClaimState
from haven.knowledge.store import ClaimStore
from haven.resources import ResourceRecord
from haven.resources.store import ResourceStore
from haven.scopes.store import ScopeStore
from haven.identity import provision_identity
from haven.web.server import make_server

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def boundary_stack():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        claims = ClaimStore(Path(tmp) / "claims.db")
        scopes = ScopeStore(Path(tmp) / "scopes.db")
        identity, _ = provision_identity(
            data_dir=Path(tmp), household_id="household-shot", scope_store=scopes, clock=lambda: NOW
        )
        visible = identity.visible_scope_ids()
        resources.save(
            ResourceRecord(
                resource_id="file:visible.txt", resource_type="file", scope_id=identity.personal_scope_id,
                provider_id="local_filesystem", title="visible notes",
                locator="E:/secret-path/visible.txt",  # locator must NOT reach a service
                capabilities=(), observed_at=NOW,
            )
        )
        resources.save(
            ResourceRecord(
                resource_id="file:secret.txt", resource_type="file", scope_id="scope:secret",
                provider_id="local_filesystem", title="secret notes",
                locator="E:/secret-path/secret.txt", capabilities=(), observed_at=NOW,
            )
        )
        claims.save(
            Claim(
                claim_id="claim:visible", scope_id=identity.personal_scope_id,
                proposition="The launch is October 1.", state=ClaimState.REPORTED,
                source_refs=(), evidence_refs=(), created_at=NOW,
                provenance=ClaimProvenance.USER_REPORTED,
            )
        )
        claims.save(
            Claim(
                claim_id="claim:secret", scope_id="scope:secret", proposition="hidden.",
                state=ClaimState.REPORTED, source_refs=(), evidence_refs=(), created_at=NOW,
                provenance=ClaimProvenance.USER_REPORTED,
            )
        )
        boundary = IntelligenceBoundary(resources=resources, claims=claims, identity=identity)
        yield boundary, resources, claims, identity, visible


def test_taxonomy_registry_and_fail_closed_class() -> None:
    registry = ExtensionRegistry()
    registry.register(
        ExtensionDescriptor(
            extension_id="intelligence.local-echo",
            display_name="Local echo",
            extension_class=ExtensionClass.INTELLIGENCE,
            source="builtin",
        )
    )
    assert [item.extension_id for item in registry.list_by_class(ExtensionClass.INTELLIGENCE)] == [
        "intelligence.local-echo"
    ]
    assert registry.list_by_class(ExtensionClass.FEATURE) == ()
    assert registry.all()[0].wire()["access_boundary"] == CLASS_BOUNDARIES[
        ExtensionClass.INTELLIGENCE
    ]["access_boundary"]
    with pytest.raises(ValueError):
        ExtensionDescriptor(
            extension_id="x.y", display_name="x", extension_class="not-a-class", source="t"
        )
    with pytest.raises(ValueError):
        registry.list_by_class("not-a-class")


def test_bounded_context_never_exposes_out_of_scope_or_locators(boundary_stack) -> None:
    boundary, _resources, _claims, identity, visible = boundary_stack
    service = EchoIntelligenceService()
    result = boundary.submit(service, text="What do you know?")
    assert result["ok"] is True
    context = service.seen_contexts[0]
    assert set(context.scope_ids) == set(visible)
    ids = {row["resource_id"] for row in context.resources}
    assert "file:visible.txt" in ids
    assert "file:secret.txt" not in ids
    claim_ids = {row["claim_id"] for row in context.claims}
    assert "claim:visible" in claim_ids
    assert "claim:secret" not in claim_ids
    # Locators never cross: titles and types only.
    serialized = str(context.wire())
    assert "E:/secret-path" not in serialized


def test_malicious_service_cannot_mutate_through_the_boundary(boundary_stack) -> None:
    boundary, resources, claims, _identity, _visible = boundary_stack

    class MaliciousService:
        def answer(self, context):
            return IntelligenceResponse(
                kind="mutation_proposal",
                text="delete everything",
                # A buggy service "wants" the memory gone. It gets to PROPOSE
                # that -- nothing more.
                intent=MutationProposal(
                    entity_kind="person",
                    operation="delete",
                    attributes=(("name", "Gerron"),),
                    target_id="gerron",
                    source_text="delete everything",
                ),
            )

    before_claims = len(claims.list_all())
    result = boundary.submit(MaliciousService(), text="delete everything")
    assert result["ok"] is True
    assert result["kind"] == "mutation_proposal"
    # The boundary wrote nothing: the proposal must re-enter through the
    # governed path, not around it.
    assert len(claims.list_all()) == before_claims
    assert resources.get("file:visible.txt") is not None


def test_proposal_routed_through_governed_path_still_requires_authority() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            server.setup.declare_person(name="Gerron Smith", role="owner")
            before = len(director.state()["pending"])

            class GuardedService:
                def answer(self, context):
                    return IntelligenceResponse(
                        kind="action_proposal",
                        text="close the garage",
                        intent=ActionProposal(
                            action_kind=__import__("haven.core.domain", fromlist=["ActionKind"]).ActionKind.CLOSE_GARAGE,
                            target_device_id="garage_door",
                            target_selector=None,
                            parameters=(),
                            justification="buggy service asked",
                            source_text="close the garage",
                        ),
                    )

            result = server.intelligence_boundary.submit(GuardedService(), text="close the garage")
            assert result["kind"] == "action_proposal"
            # Boundary alone: nothing pending, garage still open.
            assert len(director.state()["pending"]) == before
            assert director.state()["rooms"][3]["devices"][0]["is_on"] is True  # garage door open

            # Routed through the governed path, the guarded action PENDS.
            director._run_proposal(result["intent"])
            after = director.state()["pending"]
            assert len(after) == before + 1
            assert after[-1]["title"]
        finally:
            server.server_close()


def test_invalid_service_responses_are_rejected(boundary_stack) -> None:
    boundary, *_ = boundary_stack

    class GarbageService:
        def answer(self, context):
            return {"kind": "mutation_proposal"}  # not an IntelligenceResponse

    assert boundary.submit(GarbageService(), text="x")["ok"] is False

    class WrongIntentService:
        def answer(self, context):
            return IntelligenceResponse(kind="action_proposal", text="x", intent="not-an-intent")

    result = boundary.submit(WrongIntentService(), text="x")
    assert result["ok"] is False
    assert "ActionProposal" in result["error"]


def test_extensions_ipc_reclassifies_plugins_and_enables_via_registry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        server, _ = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW)
        try:
            dispatcher = server.build_ipc_dispatcher()
            result = dispatcher(request_message("r", "extensions.list", {}))["result"]
            classes = {item["class"]: item["extensions"] for item in result["classes"]}
            assert set(classes) == {"provider", "intelligence", "feature", "export_consumer"}
            # Feature modules: honest empty state.
            assert classes["feature"] == []
            # Intelligence: the diagnostic service with boundary labels.
            (echo,) = classes["intelligence"]
            assert echo["extension_id"] == "intelligence.local-echo"
            assert "bounded context" in echo["access_boundary"]
            assert "never authority-bearing" in echo["authority_boundary"]
            # Export consumers carry the plugin registry's data boundary.
            enabled = dispatcher(
                request_message(
                    "e",
                    "extensions.export_consumers.set_enabled",
                    {"plugin_id": "ghost-plugin", "enabled": True},
                )
            )
            assert enabled["ok"] is False  # fail closed through the registry

            # The bounded echo proves over IPC exactly what a service sees.
            echoed = dispatcher(
                request_message("i", "intelligence.echo", {"text": "hello haven"})
            )["result"]
            assert echoed["answer"].startswith("I saw")
            assert echoed["context"]["scope_ids"]
        finally:
            server.server_close()
