"""`HavenSearchService`: direct text match + ontology-relationship expansion."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from haven.knowledge import ClaimState
from haven.ontology import OntologyAssertion, OntologyStore, predicates
from haven.resources import ResourceRecord, ResourceStore
from haven.search import HavenSearchService, SearchQuery

UTC = timezone.utc
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _resource(
    resource_id, title, *, scope_id="project:haven", resource_type="document", metadata=(), stale=False
) -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        resource_type=resource_type,
        scope_id=scope_id,
        provider_id="local_computer",
        title=title,
        locator=None,
        capabilities=(),
        observed_at=NOW,
        metadata=metadata,
        stale=stale,
    )


def _assertion(assertion_id, subject, predicate, object_, *, scope_id="project:haven") -> OntologyAssertion:
    return OntologyAssertion(
        assertion_id=assertion_id,
        subject=subject,
        predicate=predicate,
        object=object_,
        scope_id=scope_id,
        state=ClaimState.OBSERVED,
        created_at=NOW,
    )


def _service(tmp):
    resources = ResourceStore(Path(tmp) / "resources.db")
    ontology = OntologyStore(Path(tmp) / "ontology.db")
    return HavenSearchService(resources=resources, ontology=ontology), resources, ontology


def test_no_resources_returns_no_hits():
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _ = _service(tmp)
        assert service.search(SearchQuery(text="nasa")) == ()


def test_direct_title_match():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7"))
        resources.save(_resource("doc:unrelated", "grocery list"))

        hits = service.search(SearchQuery(text="nasa"))
    assert [h.resource_id for h in hits] == ["doc:proposal-v7"]
    assert hits[0].score == 1.0
    assert hits[0].reason == "matched title"


def test_metadata_match_scores_lower_than_title_match():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:by-title", "the NASA report"))
        resources.save(_resource("doc:by-metadata", "quarterly summary", metadata=(("topic", "NASA funding"),)))

        hits = service.search(SearchQuery(text="nasa"))
    assert [h.resource_id for h in hits] == ["doc:by-title", "doc:by-metadata"]
    assert hits[0].score > hits[1].score
    assert "metadata" in hits[1].reason


def test_ontology_relationship_expansion_surfaces_related_resources():
    """The exact scenario the search bar is supposed to beat plain keyword
    search on: a repo with no "nasa" anywhere in its own title still shows
    up because it's connected to a matched resource."""

    with tempfile.TemporaryDirectory() as tmp:
        service, resources, ontology = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7"))
        resources.save(_resource("repo:haven", "haven", resource_type="repository"))
        ontology.save(_assertion("a1", "repo:haven", predicates.BELONGS_TO, "doc:proposal-v7"))

        hits = service.search(SearchQuery(text="nasa"))
    assert {h.resource_id for h in hits} == {"doc:proposal-v7", "repo:haven"}
    related = next(h for h in hits if h.resource_id == "repo:haven")
    assert related.score < 1.0
    assert "haven:belongs_to" in related.reason


def test_a_directly_matched_resource_is_never_duplicated_by_expansion():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, ontology = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7"))
        resources.save(_resource("doc:nasa-notes", "NASA notes"))
        ontology.save(_assertion("a1", "doc:nasa-notes", predicates.RELATED_TO, "doc:proposal-v7"))

        hits = service.search(SearchQuery(text="nasa"))
    ids = [h.resource_id for h in hits]
    assert ids.count("doc:nasa-notes") == 1
    # It matched directly, so it keeps its real (higher) direct-match score
    # rather than being downgraded by the relationship expansion pass.
    assert next(h for h in hits if h.resource_id == "doc:nasa-notes").score == 1.0


def test_scope_filter_excludes_other_scopes():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:a", "nasa report", scope_id="project:haven"))
        resources.save(_resource("doc:b", "nasa memo", scope_id="project:other"))

        hits = service.search(SearchQuery(text="nasa", scope_ids=("project:haven",)))
    assert [h.resource_id for h in hits] == ["doc:a"]


def test_resource_type_filter_excludes_other_types():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:a", "nasa report", resource_type="document"))
        resources.save(_resource("repo:b", "nasa repo", resource_type="repository"))

        hits = service.search(SearchQuery(text="nasa", resource_types=("document",)))
    assert [h.resource_id for h in hits] == ["doc:a"]


def test_expansion_respects_scope_and_type_filters_too():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, ontology = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7", scope_id="project:haven"))
        resources.save(_resource("repo:other-scope", "haven", scope_id="project:other"))
        ontology.save(_assertion("a1", "repo:other-scope", predicates.BELONGS_TO, "doc:proposal-v7"))

        hits = service.search(SearchQuery(text="nasa", scope_ids=("project:haven",)))
    assert [h.resource_id for h in hits] == ["doc:proposal-v7"]


def test_limit_caps_the_result_count():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        for i in range(5):
            resources.save(_resource(f"doc:{i}", f"nasa report {i}"))

        hits = service.search(SearchQuery(text="nasa", limit=2))
    assert len(hits) == 2


def test_search_works_without_an_ontology_store():
    with tempfile.TemporaryDirectory() as tmp:
        resources = ResourceStore(Path(tmp) / "resources.db")
        resources.save(_resource("doc:a", "nasa report"))
        service = HavenSearchService(resources=resources, ontology=None)

        hits = service.search(SearchQuery(text="nasa"))
    assert [h.resource_id for h in hits] == ["doc:a"]


def test_case_insensitive_match():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:a", "NASA Report"))

        hits = service.search(SearchQuery(text="nasa report"))
    assert [h.resource_id for h in hits] == ["doc:a"]


def test_stale_resources_are_excluded_from_default_search():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:current", "nasa report"))
        resources.save(_resource("doc:revoked", "nasa memo", stale=True))

        hits = service.search(SearchQuery(text="nasa"))
    assert [h.resource_id for h in hits] == ["doc:current"]


def test_stale_resources_surface_when_explicitly_requested():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, _ = _service(tmp)
        resources.save(_resource("doc:revoked", "nasa memo", stale=True))

        hits = service.search(SearchQuery(text="nasa", include_stale=True))
    assert [h.resource_id for h in hits] == ["doc:revoked"]


def test_expansion_does_not_traverse_a_relation_recorded_in_another_scope():
    """The scope-safety fix: a relation stored in one scope must not
    influence a search a caller is running scoped to a different one, even
    when the assertion happens to point at a resource that also lives in
    the visible scope."""

    with tempfile.TemporaryDirectory() as tmp:
        service, resources, ontology = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7", scope_id="project:haven"))
        resources.save(_resource("repo:haven", "haven", resource_type="repository", scope_id="project:haven"))
        ontology.save(_assertion("a1", "repo:haven", predicates.BELONGS_TO, "doc:proposal-v7", scope_id="secret-project"))

        hits = service.search(SearchQuery(text="nasa", scope_ids=("project:haven",)))
    assert [h.resource_id for h in hits] == ["doc:proposal-v7"]


def test_stale_resources_are_excluded_from_ontology_expansion_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        service, resources, ontology = _service(tmp)
        resources.save(_resource("doc:proposal-v7", "NASA LIVEI proposal v7"))
        resources.save(_resource("repo:haven", "haven", resource_type="repository", stale=True))
        ontology.save(_assertion("a1", "repo:haven", predicates.BELONGS_TO, "doc:proposal-v7"))

        hits = service.search(SearchQuery(text="nasa"))
    assert [h.resource_id for h in hits] == ["doc:proposal-v7"]
