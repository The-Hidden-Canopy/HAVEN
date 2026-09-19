"""`HavenSearchService`: the "life search bar", proven without vector infra.

Combines several signals, none of which needs an embedding model:

- direct text match against `ResourceRecord.title`/`metadata` (a plain
  case-insensitive substring match -- this module deliberately does not
  reach for SQLite FTS5 yet: this repo's zero-dependency philosophy already
  tolerates a stdlib feature that may or may not be compiled into a given
  Python's `sqlite3`, and a substring scan over a household's own resource
  count is not a performance problem worth that risk yet);
- ontology-relationship expansion: a resource connected to a direct match
  via any `OntologyAssertion` edge (`edges_from`/`edges_to`) is surfaced
  too, at a lower score, with a `reason` naming the actual predicate --
  this is the "ontology-aware retrieval instead of keyword search" the
  life search bar is supposed to be, not a bigger regex.

Admitted knowledge is a third, source-backed signal: a proposition can match
and surface the current resource that supports it, while disputed or stale
knowledge keeps its state visible and never silently becomes authority.

The expansion pass forwards `query.scope_ids` into `edges_from`/`edges_to`
themselves, not only as a post-filter on the resource the traversal lands
on -- a relation recorded in a scope a query cannot see must not be able to
connect two resources for it, even when both of those resources happen to
also be visible. Closing the deeper "which scopes can this caller see at
all" question is still `SearchQuery`'s stated future work
(`IdentityProvider.memberships()` + authority), not this pass's.

A revoked/deleted resource (`ResourceRecord.stale`) is excluded from every
result -- direct match or expansion -- unless `query.include_stale` says
otherwise; see `ResourceStore.reconcile`/`mark_stale_by_locator_prefix`.

Recency, provenance weighting, and embeddings are exactly what a later pass
can add as more scoring inputs into the same `SearchHit` shape -- this is
architecture proof, not the ceiling.
"""

from __future__ import annotations

from datetime import datetime, timezone

from haven.knowledge.claims import ClaimState, is_stale
from haven.knowledge.store import ClaimStore
from haven.ontology.store import OntologyStore
from haven.resources.store import ResourceStore

from .query import SearchHit, SearchQuery

_TITLE_MATCH_SCORE = 1.0
_METADATA_MATCH_SCORE = 0.6
_RELATED_SCORE_FACTOR = 0.4
_KNOWLEDGE_MATCH_SCORE = 0.8


class HavenSearchService:
    def __init__(
        self,
        *,
        resources: ResourceStore,
        ontology: OntologyStore | None = None,
        claims: ClaimStore | None = None,
    ) -> None:
        self._resources = resources
        self._ontology = ontology
        self._claims = claims

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        needle = query.text.lower()
        candidates = self._candidate_resources(query)

        direct: dict[str, SearchHit] = {}
        for record in candidates:
            if not self._passes_filters(record, query):
                continue
            score, reason = self._match(record, needle)
            if score is None:
                continue
            direct[record.resource_id] = SearchHit(
                resource_id=record.resource_id, score=score, reason=reason, matched_refs=(record.resource_id,)
            )

        hits: dict[str, SearchHit] = dict(direct)
        self._add_knowledge_hits(hits, query, needle)
        if self._ontology is not None:
            for hit in tuple(direct.values()):
                for related_id, predicate, direction in self._related_resource_ids(
                    hit.resource_id, scope_ids=query.scope_ids
                ):
                    if related_id in hits:
                        continue
                    related = self._resources.get(related_id)
                    if related is None or not self._passes_filters(related, query):
                        continue
                    reason = (
                        f"related via {predicate} to {hit.resource_id}"
                        if direction == "from"
                        else f"{hit.resource_id} related via {predicate} to this"
                    )
                    hits[related_id] = SearchHit(
                        resource_id=related_id,
                        score=hit.score * _RELATED_SCORE_FACTOR,
                        reason=reason,
                        matched_refs=(hit.resource_id,),
                    )

        ranked = sorted(hits.values(), key=lambda hit: (-hit.score, hit.resource_id))
        return tuple(ranked[: query.limit])

    def _add_knowledge_hits(self, hits: dict[str, SearchHit], query: SearchQuery, needle: str) -> None:
        if self._claims is None:
            return
        claims = self._claims.list_all()
        now = datetime.now(timezone.utc)
        for claim in claims:
            if query.scope_ids and claim.scope_id not in query.scope_ids:
                continue
            if is_stale(claim, now=now) and not query.include_stale:
                continue
            if needle not in claim.proposition.lower():
                continue
            reason = (
                "matched disputed knowledge claim"
                if claim.state is ClaimState.DISPUTED
                else "matched knowledge claim"
            )
            for source_ref in claim.source_refs:
                resource = self._resources.get(source_ref)
                if resource is None or not self._passes_filters(resource, query):
                    continue
                candidate = SearchHit(
                    resource_id=resource.resource_id,
                    score=_KNOWLEDGE_MATCH_SCORE * claim.confidence,
                    reason=reason,
                    matched_refs=(claim.claim_id,),
                )
                current = hits.get(resource.resource_id)
                if current is None or candidate.score > current.score:
                    hits[resource.resource_id] = candidate

    def _candidate_resources(self, query: SearchQuery):
        if query.scope_ids:
            seen: dict[str, object] = {}
            for scope_id in query.scope_ids:
                for record in self._resources.list_by_scope(scope_id):
                    seen[record.resource_id] = record
            return tuple(seen.values())
        return self._resources.list_all()

    def _passes_filters(self, record, query: SearchQuery) -> bool:
        if record.stale and not query.include_stale:
            return False
        if query.scope_ids and record.scope_id not in query.scope_ids:
            return False
        if query.resource_types and record.resource_type not in query.resource_types:
            return False
        return True

    def _match(self, record, needle: str) -> tuple[float, str] | tuple[None, None]:
        if needle in record.title.lower():
            return _TITLE_MATCH_SCORE, "matched title"
        for key, value in record.metadata:
            if needle in str(value).lower():
                return _METADATA_MATCH_SCORE, f"matched metadata field {key!r}"
        return None, None

    def _related_resource_ids(self, resource_id: str, *, scope_ids: tuple[str, ...]):
        assert self._ontology is not None
        for edge in self._ontology.edges_from(resource_id, scope_ids=scope_ids or None):
            yield edge.object, edge.predicate, "from"
        for edge in self._ontology.edges_to(resource_id, scope_ids=scope_ids or None):
            yield edge.subject, edge.predicate, "to"


__all__ = ["HavenSearchService"]
