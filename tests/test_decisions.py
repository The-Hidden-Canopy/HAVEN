"""Decision memory through actual HAVEN stores, identity and process restarts."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from haven.decisions import DecisionConflict, DecisionContext, DecisionRecord, DecisionService, DecisionSnapshot, DecisionStore, DocumentImpact, SourceRevision
from haven.decisions.attention import DecisionSource
from haven.decisions.api import DecisionAPI
from haven.identity.local import provision_identity
from haven.intelligence.gateway import AgentContext
from haven.integrations.computer.filesystem import FilesystemProvider
from haven.resources import ResourceRecord, ResourceStore
from haven.scopes.store import ScopeStore
from haven.scopes.models import Membership, ScopeRef
from haven.core.domain import RoleTier
from haven.identity.contracts import ScopeMembership

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    scopes = ScopeStore(tmp_path / "scopes.db")
    identity, _ = provision_identity(data_dir=tmp_path, household_id="home", scope_store=scopes, clock=lambda: NOW)
    # These review fixtures represent the declared owner of this scope.
    # Provisioning deliberately grants only member standing in a household.
    scopes.add_membership(Membership(identity.principal_id, "home", role="owner", capabilities=("write",), valid_from=NOW))
    resources = ResourceStore(tmp_path / "resources.db")
    resources.save(ResourceRecord("source:code", "repository", "home", "local", "Code", None, (), NOW, "rev:1"))
    resources.save(ResourceRecord("source:doc", "document", "home", "local", "Existing guide", None, (), NOW, "doc:1"))
    store = DecisionStore(tmp_path / "decisions.db")
    consent = [True]
    service = DecisionService(store=store, identity=identity, resources=resources,
                              has_declared_owner=lambda: consent[0], clock=lambda: NOW)
    return service, scopes, resources, consent


def propose(service, **changes):
    data = dict(problem="How should the project retain choices?", options=(("json", "Versioned JSON"), ("chat", "Recent chat")),
                rationale="Retain a concise rationale across restarts.",
                evidence=(SourceRevision("source:code", "home", "rev:1"),),
                constraints=("Current scope and consent remain authoritative.",),
                reconsideration_triggers=("Revisit when the source implementation changes.",))
    data.update(changes)
    return service.propose("home", expected_generation=service.snapshot("home").generation, **data)


def accept(service, record, **changes):
    data = dict(status="accepted", selected_option_id="json", rationale="The explicit owner chose bounded persisted state.", review_ref="review:human")
    data.update(changes)
    return service.review("home", record.decision_id, expected_generation=service.snapshot("home").generation,
                          expected_revision=service.snapshot("home").decisions[-1].current.revision, **data)


def test_proposal_acceptance_and_real_restart(env, tmp_path):
    service, _, _, _ = env
    record = propose(service)
    assert service.context("home")["decisions"] == []
    accept(service, record)
    restored = DecisionService(store=DecisionStore(tmp_path / "decisions.db"), identity=service.identity,
                               resources=service.resources, has_declared_owner=lambda: True, clock=lambda: NOW)
    assert restored.context("home")["decisions"][0]["decision_id"] == record.decision_id
    code = "from haven.decisions import DecisionStore; import json,sys; print(json.dumps(DecisionStore(sys.argv[1]).load('home').to_dict()))"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "decisions.db")], capture_output=True, text=True, check=True)
    snapshot = json.loads(result.stdout)
    assert snapshot["decisions"][0]["revisions"][-1]["status"] == "accepted"
    assert len(snapshot["decisions"][0]["revisions"]) == 2


def test_revision_preserves_accepted_and_challenged_history(env):
    service, _, _, _ = env
    record = propose(service)
    accept(service, record)
    service.review("home", record.decision_id, expected_generation=2, expected_revision=2,
                   status="challenged", rationale="Source assumptions need review.", review_ref="review:challenge")
    service.revise("home", record.decision_id, expected_generation=3, expected_revision=3,
                   rationale="Revised scope of the question.", problem="How should the project retain reviewed choices?")
    assert [item.status for item in service.snapshot("home").decisions[0].revisions] == ["proposed", "accepted", "challenged", "proposed"]
    assert service.context("home")["decisions"] == []


def test_stale_sources_and_observation_expiry_are_not_active(env):
    service, _, resources, _ = env
    record = propose(service)
    accept(service, record)
    original = resources.get("source:code")
    for changed in (replace(original, content_hash="rev:2"), replace(original, stale=True),
                    replace(original, observed_at=NOW - timedelta(days=8)), replace(original, scope_id="secret")):
        resources.save(changed)
        assert service.context("home")["decisions"] == []
        assert service.review_items("home")


def test_acceptance_rejects_stale_or_cross_scope_evidence(env):
    service, _, resources, _ = env
    record = propose(service)
    resources.save(replace(resources.get("source:code"), content_hash="rev:2"))
    with pytest.raises(DecisionConflict):
        accept(service, record)
    with pytest.raises(ValueError, match="cross-scope"):
        propose(service, evidence=(SourceRevision("source:code", "secret", "rev:2"),))
    assert service.snapshot("home").generation == 1


def test_documents_require_real_changed_provider_observation(env):
    service, _, resources, _ = env
    record = propose(service, affected_documents=(DocumentImpact(SourceRevision("source:doc", "home", "doc:1"), "Revise the existing guide's persistence section."),))
    accept(service, record)
    assert service.context("home")["decisions"] == []
    with pytest.raises(DecisionConflict):
        service.reconcile_document("home", record.decision_id, expected_generation=2, expected_revision=2,
                                   resource_id="source:doc", observed_revision="doc:2", receipt_ref="receipt:claimed", rationale="Updated guide.")
    resources.save(replace(resources.get("source:doc"), content_hash="doc:2"))
    service.reconcile_document("home", record.decision_id, expected_generation=2, expected_revision=2,
                               resource_id="source:doc", observed_revision="doc:2", receipt_ref="receipt:observed", rationale="Revised existing guide to match the implemented register.")
    assert service.context("home")["decisions"]
    resources.save(replace(resources.get("source:doc"), content_hash="doc:3"))
    assert service.context("home")["decisions"] == []


def test_existing_document_revision_is_reobserved_by_real_filesystem_provider(env, tmp_path):
    service, _, resources, _ = env
    document = tmp_path / "guide.md"
    document.write_text("# Guide\n\nMemory is transient.\n", encoding="utf-8")
    provider = FilesystemProvider(allowed_roots=(tmp_path,), scope_id="home", clock=lambda: NOW)
    baseline = provider.observe_one(document)
    resources.save(baseline)
    record = propose(service, affected_documents=(DocumentImpact(
        SourceRevision(baseline.resource_id, "home", baseline.content_hash),
        "Revise the memory section of the existing guide."),))
    accept(service, record)
    document.write_text("# Guide\n\nReviewed decisions persist with versioned source evidence.\n", encoding="utf-8")
    observed = provider.observe_one(document)
    assert observed.content_hash != baseline.content_hash
    resources.save(observed)
    service.reconcile_document("home", record.decision_id, expected_generation=2, expected_revision=2,
                               resource_id=observed.resource_id, observed_revision=observed.content_hash,
                               receipt_ref="receipt:local-edit", rationale="Existing guide now matches the implemented persistence behavior.")
    assert service.context("home")["decisions"]
    assert document.read_text(encoding="utf-8").count("# Guide") == 1


def test_current_membership_revocation_defeats_saved_checkpoint(env):
    service, scopes, _, _ = env
    record = propose(service)
    checkpoint = service.snapshot("home").to_dict()
    scopes.add_membership(Membership(service.identity.principal_id, "home", "member", valid_until=NOW))
    with pytest.raises(PermissionError):
        service.context("home")
    with pytest.raises(PermissionError):
        service.review("home", record.decision_id, expected_generation=checkpoint["generation"], expected_revision=1,
                       status="accepted", selected_option_id="json", rationale="Old approval.", review_ref="old-checkpoint")
    assert service.store.load("home").generation == 1


def test_revocation_immediately_before_commit_rolls_back(env, monkeypatch):
    service, _, _, consent = env
    propose(service)
    original = service.store.update
    def revoke(*args, **kwargs):
        guard = kwargs["before_commit"]
        def changed():
            consent[0] = False
            guard()
        return original(*args, **{**kwargs, "before_commit": changed})
    monkeypatch.setattr(service.store, "update", revoke)
    with pytest.raises(PermissionError):
        propose(service)
    assert service.store.load("home").generation == 1


def test_identity_switch_cannot_misattribute_a_revision(env, monkeypatch):
    service, scopes, _, _ = env
    original = service.identity.current_principal()
    calls = []
    def switching():
        calls.append(True)
        return original if len(calls) == 1 else replace(original, actor_id="principal:switched")
    monkeypatch.setattr(service.identity, "current_principal", switching)
    monkeypatch.setattr(service.identity, "memberships", lambda: (ScopeMembership(
        scope=scopes.get_scope("home"), actor_id=original.actor_id if len(calls) == 1 else "principal:switched", role="member"),))
    with pytest.raises(PermissionError, match="identity changed"):
        service.propose("home", expected_generation=0, problem="Question", options=(("one", "Choice"),), rationale="Reason")
    assert service.store.load("home").generation == 0


def test_archived_scope_and_forged_principal_are_denied(env):
    service, scopes, _, _ = env
    with pytest.raises(PermissionError):
        service.context("home", principal_id="another-person")
    scopes.save_scope(ScopeRef("home", "household", "Home", status="archived"))
    with pytest.raises(PermissionError):
        service.context("home")


def test_review_uses_current_role_and_requires_explicit_reference(env, monkeypatch):
    service, _, _, _ = env
    record = propose(service)
    with pytest.raises(ValueError, match="review_ref"):
        accept(service, record, review_ref="")
    principal = service.identity.current_principal()
    monkeypatch.setattr(service.identity, "current_principal", lambda: replace(principal, role_tier=RoleTier.MEMBER))
    with pytest.raises(PermissionError, match="owner"):
        accept(service, record)
    assert service.store.load("home").generation == 1


def test_supersession_links_and_dependencies_remain_scoped_and_acyclic(env):
    service, _, _, _ = env
    first = propose(service)
    accept(service, first)
    second = propose(service)
    accept(service, second)
    service.review("home", first.decision_id, expected_generation=4, expected_revision=2,
                   status="superseded", replacement_id=second.decision_id, rationale="Replacement reflects current scope.", review_ref="review:replace")
    assert [item["decision_id"] for item in service.context("home")["decisions"]] == [second.decision_id]
    with pytest.raises(DecisionConflict, match="revived"):
        service.revise("home", first.decision_id, expected_generation=5, expected_revision=3, rationale="Restore.")
    with pytest.raises(ValueError, match="reference"):
        propose(service, dependencies=("foreign-decision",))
    payload = service.snapshot("home").to_dict()
    payload["decisions"][1]["revisions"][-1].update(status="superseded", replacement_id=first.decision_id)
    with pytest.raises(ValueError, match="cyclic"):
        DecisionSnapshot.from_dict(payload)


def test_dependency_challenge_blocks_context(env):
    service, _, _, _ = env
    first = propose(service)
    accept(service, first)
    second = propose(service, dependencies=(first.decision_id,))
    accept(service, second)
    service.review("home", first.decision_id, expected_generation=4, expected_revision=2,
                   status="challenged", rationale="Dependency needs reconsideration.", review_ref="review:challenge")
    assert not service.context("home")["decisions"]


def test_atomic_concurrent_revision_conflict(env):
    service, _, _, _ = env
    barrier = Barrier(2)
    def write(_):
        store = DecisionStore(service.store.path)
        barrier.wait()
        try:
            return store.update("home", 0, lambda snap: replace(snap, generation=1), before_commit=lambda: None).generation
        except DecisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(results, key=str) == [1, "conflict"]
    assert service.store.load("home").generation == 1


def test_stale_revision_and_source_race_cannot_commit(env, monkeypatch):
    service, _, resources, _ = env
    record = propose(service)
    with pytest.raises(DecisionConflict, match="revision"):
        service.review("home", record.decision_id, expected_generation=1, expected_revision=99,
                       status="accepted", selected_option_id="json", rationale="Stale request.", review_ref="review:stale")
    original = service.store.update
    def change_source(*args, **kwargs):
        guard = kwargs["before_commit"]
        def changed():
            resources.save(replace(resources.get("source:code"), content_hash="rev:changed-during-review"))
            guard()
        return original(*args, **{**kwargs, "before_commit": changed})
    monkeypatch.setattr(service.store, "update", change_source)
    with pytest.raises(DecisionConflict, match="evidence"):
        accept(service, record)
    assert service.store.load("home").generation == 1


def test_references_prevent_partial_history_erasure(env):
    service, _, _, _ = env
    first = propose(service)
    second = propose(service, dependencies=(first.decision_id,))
    with pytest.raises(ValueError, match="reference"):
        service.erase("home", expected_generation=2, decision_id=first.decision_id, justification="Erase referenced record.")
    assert len(service.snapshot("home").decisions) == 2
    service.erase("home", expected_generation=2, decision_id=second.decision_id, justification="Erase dependent first.")
    service.erase("home", expected_generation=3, decision_id=first.decision_id, justification="Erase remaining record.")
    assert not service.snapshot("home").decisions


def test_process_crash_during_transaction_recovers_last_complete_snapshot(env):
    service, _, _, _ = env
    propose(service)
    code = "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute(\"UPDATE decision_snapshots SET data='broken'\"); os._exit(23)"
    result = subprocess.run([sys.executable, "-c", code, str(service.store.path)])
    assert result.returncode == 23
    assert service.store.load("home").generation == 1
    assert len(service.store.load("home").decisions) == 1


def test_schema_migration_and_unknown_version_preserve_original(env):
    service, _, _, _ = env
    record = propose(service)
    payload = {"schema_version": 1, "scope_id": "home", "generation": 1,
               "decisions": [{"decision_id": record.decision_id, "current": {**record.current.to_dict(), "status": "accepted", "selected_option_id": "json"}}]}
    with sqlite3.connect(service.store.path) as conn:
        conn.execute("UPDATE decision_snapshots SET data=?", (json.dumps(payload),))
    migrated = service.store.load("home")
    assert migrated.schema_version == 2
    assert migrated.decisions[0].current.status == "proposed"
    payload["schema_version"] = 999
    raw = json.dumps(payload)
    with sqlite3.connect(service.store.path) as conn:
        conn.execute("UPDATE decision_snapshots SET data=?", (raw,))
    with pytest.raises(ValueError, match="schema"):
        service.store.load("home")
    with sqlite3.connect(service.store.path) as conn:
        assert conn.execute("SELECT data FROM decision_snapshots").fetchone()[0] == raw


def test_corrupt_json_is_not_silently_reset(env):
    service, _, _, _ = env
    propose(service)
    with sqlite3.connect(service.store.path) as conn:
        conn.execute("UPDATE decision_snapshots SET data='{' ")
    with pytest.raises(ValueError):
        service.snapshot("home")


def test_retention_deletion_keeps_generation_and_no_old_text(env):
    service, _, _, _ = env
    record = propose(service, problem="Unique private decision text to erase")
    service.erase("home", expected_generation=1, justification="Owner requested local deletion.")
    assert not service.snapshot("home").decisions
    assert service.snapshot("home").generation == 2
    assert b"Unique private decision text to erase" not in service.store.path.read_bytes()
    with pytest.raises(DecisionConflict):
        service.review("home", record.decision_id, expected_generation=1, expected_revision=1,
                       status="accepted", selected_option_id="json", rationale="Replay old checkpoint.", review_ref="checkpoint")


def test_bounded_provider_context_is_detached_and_cannot_carry_grants(env):
    service, _, _, _ = env
    for _ in range(14):
        record = propose(service)
        accept(service, record)
    payload = service.context("home", limit=12)
    assert len(payload["decisions"]) == 12
    context = AgentContext("home", service.identity.principal_id, "owner", decision_memory=DecisionContext.from_dict(payload))
    serialized = context.to_dict()
    serialized["decision_memory"]["decisions"].clear()
    assert len(context.decision_memory.to_dict()["decisions"]) == 12
    assert AgentContext.from_dict(context.to_dict()) == context
    payload["permissions"] = ["unscoped_execution"]
    with pytest.raises(ValueError, match="fields"):
        DecisionContext.from_dict(payload)
    with pytest.raises(ValueError):
        service.context("home", limit=100)


def test_active_context_bounds_source_work_for_large_snapshot(env, monkeypatch):
    service, _, resources, _ = env
    first = propose(service)
    accept(service, first)
    base = service.snapshot("home").decisions[0].current
    records = []
    for index in range(70):
        refs = []
        for source in range(5):
            resource_id = f"source:bounded:{index}:{source}"
            resources.save(ResourceRecord(resource_id, "file", "home", "local", "Source", None, (), NOW, "rev:1"))
            refs.append(SourceRevision(resource_id, "home", "rev:1"))
        records.append(DecisionRecord(f"decision:bounded:{index:02}", "home", (replace(base, revision=1, evidence=tuple(refs)),)))
    service.store.update("home", 2, lambda snap: replace(snap, generation=3, decisions=tuple(records)), before_commit=lambda: None)
    calls = []
    original = resources.get
    def counted(resource_id):
        calls.append(resource_id)
        return original(resource_id)
    monkeypatch.setattr(resources, "get", counted)
    result = service.context("home", limit=12)
    assert len(result["decisions"]) <= 12 and len(calls) <= 256
    assert len(json.dumps(result).encode()) < 16_000
    # A focused old record remains addressable outside the recent candidate window.
    focused = service.context("home", decision_id="decision:bounded:00", limit=1)
    assert focused["decisions"][0]["decision_id"] == "decision:bounded:00"


def test_needs_you_projects_review_without_approval_effect(env):
    service, _, _, _ = env
    record = propose(service)
    items = DecisionSource(decisions=service).collect(now=NOW, visible_scope_ids=("home", "hidden"))
    assert len(items) == 1 and items[0].route.entity_id == record.decision_id
    assert [item.action for item in items[0].available_actions] == ["review"]
    assert service.snapshot("home").generation == 1


def test_api_round_trip_preserves_json_and_rechecks_scope(env):
    service, scopes, _, _ = env
    handlers = DecisionAPI(decisions=service, default_scope=lambda: "home").handlers()
    response = handlers["decisions.propose"]({"expected_generation": 0, "problem": "Which memory?",
        "options": [["json", "Versioned JSON"]], "rationale": "Persist reviewed choices.",
        "evidence": [{"resource_id": "source:code", "scope_id": "home", "revision": "rev:1"}]})
    assert response["ok"]
    record = response["decision"]
    accepted = handlers["decisions.review"]({"decision_id": record["decision_id"], "expected_generation": 1,
        "expected_revision": 1, "status": "accepted", "selected_option_id": "json",
        "rationale": "Owner reviewed current sources.", "review_ref": "review:human"})
    assert accepted["ok"] and accepted["snapshot"]["generation"] == 2
    assert handlers["decisions.context"]({})["context"]["decisions"]
    scopes.add_membership(Membership(service.identity.principal_id, "home", "member", valid_until=NOW))
    assert not handlers["decisions.snapshot"]({})["ok"]
    assert not handlers["decisions.review"]({"decision_id": record["decision_id"], "expected_generation": 2,
        "expected_revision": 2, "status": "accepted", "selected_option_id": "json",
        "rationale": "Replay old review.", "review_ref": "review:human"})["ok"]


@pytest.mark.parametrize("method,params", [
    ("decisions.propose", {"expected_generation": 0, "problem": "Question", "options": [["one", "Choice"]], "rationale": "Reason", "permissions": ["execute"]}),
    ("decisions.context", {"principal_id": "spoofed"}),
    ("decisions.review", {"decision_id": "missing"}),
    ("decisions.propose", {"expected_generation": 0, "problem": "Question", "options": [["one", "Choice"]], "rationale": "Reason", "evidence": [{"resource_id": "source:code", "scope_id": "foreign", "revision": "rev:1"}]})])
def test_api_rejects_grants_spoofed_identity_and_bad_sources(env, method, params):
    service, _, _, _ = env
    handlers = DecisionAPI(decisions=service, default_scope=lambda: "home").handlers()
    assert not handlers[method](params)["ok"]
    assert service.snapshot("home").generation == 0


def test_boolean_checkpoint_numbers_are_rejected(env):
    service, _, _, _ = env
    with pytest.raises(ValueError, match="generation"):
        service.erase("home", expected_generation=False, justification="Invalid checkpoint.")
    record = propose(service)
    with pytest.raises(ValueError, match="revision"):
        service.review("home", record.decision_id, expected_generation=1, expected_revision=True,
                       status="accepted", selected_option_id="json", rationale="Invalid revision.", review_ref="review:invalid")
