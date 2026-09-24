"""Milestone C authority gates over a live server (spec page 43).

Cross-scope denial, role/validity denial, unauthorized objects invisible
including through ontology-relationship expansion, migration on boot of a
household-first data dir, and restart durability of the identity boundary.
"""

from __future__ import annotations

import http.client
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.ipc import request_message
from haven.knowledge import Claim, ClaimProvenance, ClaimState
from haven.ontology.assertions import OntologyAssertion
from haven.resources import ResourceRecord
from haven.search import SearchQuery
from haven.web.server import make_server
from haven.web.setup_config import SetupConfig, SetupConfigStore

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
SECRET = "scope:secret-project"


@pytest.fixture()
def server():
    tmp = tempfile.TemporaryDirectory()
    instance, _director = make_server(0, data_dir=Path(tmp.name) / "data", clock=lambda: NOW)
    try:
        yield instance
    finally:
        for thread in _SERVE_THREADS.pop(instance, []):
            instance.shutdown()
            thread.join(timeout=5)
        instance.server_close()
        tmp.cleanup()


_SERVE_THREADS: dict = {}


def _dispatch(instance, method: str, params: dict) -> dict:
    return instance.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _resource(resource_id: str, scope_id: str, title: str) -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        resource_type="file",
        scope_id=scope_id,
        provider_id="local_filesystem",
        title=title,
        locator=None,
        capabilities=(),
        observed_at=NOW,
    )


def test_personal_root_is_provisioned_and_household_parented(server) -> None:
    household = server.director.household_id
    personal = server.identity.personal_scope_id
    assert server.scope_store.get_scope(household).parent_scope_id == personal
    assert server.scope_store.get_scope(personal).kind == "personal"
    assert server.identity.visible_scope_ids() == tuple(sorted((personal, household)))


def test_cross_scope_queries_are_denied_over_ipc_and_web(server) -> None:
    port_server, _thread = _serve(server)
    household = server.director.household_id
    server.resources.save(_resource("file:mine", household, "my visible notes"))
    server.resources.save(_resource("file:secret", SECRET, "secret notes"))
    server.claims.save(
        Claim(
            claim_id="claim:secret",
            scope_id=SECRET,
            proposition="secret proposition",
            state=ClaimState.REPORTED,
            source_refs=(),
            evidence_refs=(),
            created_at=NOW,
            provenance=ClaimProvenance.USER_REPORTED,
        )
    )

    blocked = _dispatch(server, "search.query", {"text": "notes", "scope_ids": [SECRET]})
    assert blocked["ok"] is False
    assert "not visible" in blocked["error"]

    claims_blocked = _dispatch(server, "knowledge.claims", {"scope_ids": [SECRET]})
    assert claims_blocked["ok"] is False
    claim_blocked = _dispatch(server, "knowledge.claim", {"claim_id": "claim:secret"})
    assert claim_blocked["ok"] is False
    assert "unknown claim" in claim_blocked["error"]
    mutate_blocked = _dispatch(server, "knowledge.claim.stale", {"claim_id": "claim:secret"})
    assert mutate_blocked["ok"] is False

    status, body = _get(port_server, f"/api/search?q=notes&scope={SECRET}")
    assert status == 403
    status, body = _get(port_server, f"/api/knowledge/claims?scope={SECRET}")
    assert status == 403
    assert server.claims.get("claim:secret").state is ClaimState.REPORTED


def test_default_queries_span_membership_scopes_but_never_beyond(server) -> None:
    port_server, _thread = _serve(server)
    personal = server.identity.personal_scope_id
    household = server.director.household_id
    server.resources.save(_resource("file:personal-doc", personal, "personal diary"))
    server.resources.save(_resource("file:home-doc", household, "home inventory"))

    status, body = _get(port_server, "/api/search?q=diary")
    assert status == 200
    assert [hit["resource_id"] for hit in body["hits"]] == ["file:personal-doc"]
    status, body = _get(port_server, "/api/search?q=inventory")
    assert [hit["resource_id"] for hit in body["hits"]] == ["file:home-doc"]

    # Narrowing to one visible scope still works; mixing visible + invisible
    # fails closed.
    status, body = _get(port_server, f"/api/search?q=inventory&scope={household}")
    assert status == 200
    assert [hit["resource_id"] for hit in body["hits"]] == ["file:home-doc"]
    status, _body = _get(port_server, f"/api/search?q=inventory&scope={household}&scope={SECRET}")
    assert status == 403


def test_unauthorized_objects_stay_invisible_through_ontology_expansion(server) -> None:
    personal = server.identity.personal_scope_id
    server.resources.save(_resource("file:visible", personal, "visible notes"))
    server.resources.save(_resource("file:hidden", SECRET, "hidden annex notes"))
    server.ontology.save(
        OntologyAssertion(
            assertion_id="assert:leak",
            subject="file:visible",
            predicate="mentions",
            object="file:hidden",
            scope_id=SECRET,
            state=ClaimState.REPORTED,
            created_at=NOW,
        )
    )

    hits = server.search.search(
        SearchQuery(
            text="notes",
            scope_ids=server.identity.visible_scope_ids(),
            resource_types=(),
            limit=20,
        )
    )
    # The direct match surfaces; the relationship expansion into the secret
    # scope does not leak the hidden resource.
    assert [hit.resource_id for hit in hits] == ["file:visible"]


def test_expired_membership_removes_visibility(server) -> None:
    from haven.scopes.models import Membership

    personal = server.identity.personal_scope_id
    household = server.director.household_id
    server.resources.save(_resource("file:home-only", household, "home only document"))
    server.scope_store.add_membership(
        Membership(
            principal_id=server.identity.principal_id,
            scope_id=household,
            role="member",
            valid_from=NOW,
            valid_until=datetime(2026, 9, 20, 12, 30, tzinfo=timezone.utc),
        )
    )
    before = _dispatch(server, "search.query", {"text": "document"})
    assert [hit["resource"]["resource_id"] for hit in before["result"]["hits"]] == ["file:home-only"]

    with _time_travel(server.identity, datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)):
        after = _dispatch(server, "search.query", {"text": "document"})
        assert after["result"]["hits"] == []
        assert server.identity.visible_scope_ids() == (personal,)


class _time_travel:
    """Temporarily swap the identity provider's clock (validity windows)."""

    def __init__(self, provider, future: datetime) -> None:
        self._provider = provider
        self._future = future
        self._original = provider._clock

    def __enter__(self):
        self._provider._clock = lambda: self._future

    def __exit__(self, *exc):
        self._provider._clock = self._original
        return False


def test_household_first_boot_migrates_and_stays_restart_durable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        SetupConfigStore(data_dir / "haven.json").save(
            SetupConfig(completed=True, data_dir=str(data_dir), household_id="household-authoring")
        )
        # Pre-scope data, written before scopes existed:
        from haven.knowledge import ClaimStore
        from haven.resources.store import ResourceStore

        resources = ResourceStore(data_dir / "resources.db")
        resources.save(_resource("file:legacy-notes", "household-authoring", "legacy notes"))
        claims = ClaimStore(data_dir / "claims.db")
        claims.save(
            Claim(
                claim_id="claim:legacy",
                scope_id="household-authoring",
                proposition="legacy proposition",
                state=ClaimState.REPORTED,
                source_refs=("file:legacy-notes",),
                evidence_refs=(),
                created_at=NOW,
                provenance=ClaimProvenance.DOCUMENT_STATED,
            )
        )

        instance, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            personal = instance.identity.personal_scope_id
            assert instance.scope_migration.moved_anything is True
            assert instance.resources.get("file:legacy-notes").scope_id == personal
            assert instance.claims.get("claim:legacy").scope_id == personal
            (mapping,) = instance.scope_store.legacy_mappings()
            assert mapping["legacy_scope_id"] == "household-authoring"
            assert mapping["canonical_scope_id"] == personal
            principal_id = instance.identity.principal_id
        finally:
            instance.server_close()

        rebound, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            assert rebound.identity.principal_id == principal_id
            assert rebound.scope_migration.moved_anything is False
            assert rebound.resources.get("file:legacy-notes").scope_id == personal
            assert rebound.identity.visible_scope_ids() == tuple(
                sorted((personal, "household-authoring"))
            )
        finally:
            rebound.server_close()


# -- tiny HTTP helpers (bound-port server) ------------------------------------


def _serve(instance):
    import threading

    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    _SERVE_THREADS.setdefault(instance, []).append(thread)
    return instance.server_address[1], thread


def _get(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body
