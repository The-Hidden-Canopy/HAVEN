"""`OntologyStore`: persistence, scope isolation, provenance, edge traversal,
restart survival."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.knowledge import ClaimState
from haven.ontology import OntologyAssertion, OntologyStore
from haven.ontology import predicates

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _assertion(
    assertion_id="a1",
    *,
    subject="resource:proposal-v7",
    predicate=predicates.BELONGS_TO,
    object="project:nasa-livei",
    scope_id="project:haven",
    state=ClaimState.OBSERVED,
    source_ref="filesystem:proposal-v7.docx",
) -> OntologyAssertion:
    return OntologyAssertion(
        assertion_id=assertion_id,
        subject=subject,
        predicate=predicate,
        object=object,
        scope_id=scope_id,
        state=state,
        created_at=NOW,
        source_ref=source_ref,
    )


def test_save_and_get_round_trips_every_field():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion())
        assert store.get("a1") == _assertion()


def test_get_missing_assertion_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        assert store.get("nope") is None


def test_provenance_round_trips_source_ref_and_confidence():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(
            OntologyAssertion(
                assertion_id="a1",
                subject="resource:proposal-v7",
                predicate=predicates.BELONGS_TO,
                object="project:nasa-livei",
                scope_id="project:haven",
                state=ClaimState.REPORTED,
                created_at=NOW,
                source_ref="conversation:call-1",
                confidence=0.7,
            )
        )
        loaded = store.get("a1")
    assert loaded.source_ref == "conversation:call-1"
    assert loaded.confidence == 0.7
    assert loaded.state == ClaimState.REPORTED


def test_edges_from_finds_outgoing_edges_by_subject():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", subject="resource:proposal-v7", predicate=predicates.BELONGS_TO, object="project:nasa-livei"))
        store.save(_assertion("a2", subject="resource:proposal-v7", predicate=predicates.CREATED_BY, object="person:gerron"))
        store.save(_assertion("a3", subject="resource:other-doc", predicate=predicates.BELONGS_TO, object="project:nasa-livei"))

        edges = store.edges_from("resource:proposal-v7")
    assert {e.assertion_id for e in edges} == {"a1", "a2"}


def test_edges_to_finds_incoming_edges_by_object():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", subject="resource:proposal-v7", object="project:nasa-livei"))
        store.save(_assertion("a2", subject="repo:haven", predicate=predicates.BELONGS_TO, object="project:nasa-livei"))
        store.save(_assertion("a3", subject="resource:unrelated-doc", object="project:other"))

        edges = store.edges_to("project:nasa-livei")
    assert {e.assertion_id for e in edges} == {"a1", "a2"}


def test_edges_from_respects_a_scope_filter():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", subject="resource:x", object="project:visible", scope_id="project:haven"))
        store.save(_assertion("a2", subject="resource:x", object="project:secret", scope_id="secret-project"))

        edges = store.edges_from("resource:x", scope_ids=("project:haven",))
    assert [e.assertion_id for e in edges] == ["a1"]


def test_edges_to_respects_a_scope_filter():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", subject="resource:visible", object="project:x", scope_id="project:haven"))
        store.save(_assertion("a2", subject="resource:secret", object="project:x", scope_id="secret-project"))

        edges = store.edges_to("project:x", scope_ids=("project:haven",))
    assert [e.assertion_id for e in edges] == ["a1"]


def test_edges_from_with_no_scope_filter_is_unrestricted():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", subject="resource:x", object="project:a", scope_id="project:haven"))
        store.save(_assertion("a2", subject="resource:x", object="project:b", scope_id="secret-project"))

        edges = store.edges_from("resource:x")
    assert {e.assertion_id for e in edges} == {"a1", "a2"}


def test_scope_isolation_never_leaks_across_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion("a1", scope_id="project:haven"))
        store.save(_assertion("a2", scope_id="project:other"))

        assert [a.assertion_id for a in store.list_by_scope("project:haven")] == ["a1"]
        assert [a.assertion_id for a in store.list_by_scope("project:other")] == ["a2"]
        assert store.list_by_scope("project:nonexistent") == ()


def test_saving_the_same_id_again_upserts_not_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        store = OntologyStore(Path(tmp) / "ontology.db")
        store.save(_assertion(state=ClaimState.REPORTED))
        store.save(_assertion(state=ClaimState.CORROBORATED))
        assert store.get("a1").state == ClaimState.CORROBORATED
        assert len(store.list_by_scope("project:haven")) == 1


def test_persists_across_a_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ontology.db"
        first = OntologyStore(db_path)
        first.save(_assertion())

        second = OntologyStore(db_path)
        assert second.get("a1") == _assertion()
