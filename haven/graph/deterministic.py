"""Deterministic relationship projections (spec page 21).

Every edge HAVEN can justify from records it already owns -- no model, no
guessing. Ids are deterministic (hash of subject|predicate|object) so
re-projection is idempotent upsert, and each assertion carries provenance
(the "why" is a provenance query: read the assertion's source_ref).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from haven.knowledge.claims import ClaimState
from haven.ontology.assertions import OntologyAssertion
from haven.ontology.predicates import (
    ASSIGNED_TO,
    BELONGS_TO,
    CONTAINS,
    DERIVED_FROM,
    LOCATED_IN,
    SUPERSEDES,
)
from haven.ontology.store import OntologyStore
from haven.resources.store import ResourceStore

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731

_SOURCE = "haven.graph.deterministic"

# Suffixes worth correlating (content files); binaries never correlate.
_CORRELATABLE_TYPES = frozenset({"file", "document", "project", "task", "calendar_event", "email_message"})


def assertion_id(subject: str, predicate: str, object_: str) -> str:
    digest = hashlib.sha256(f"{subject}|{predicate}|{object_}".encode("utf-8")).hexdigest()[:16]
    return f"assert:{digest}"


class RelationshipProjector:
    """Writes the deterministic edge set into the OntologyStore.

    `recompute_region(resource_id)` does the bounded BuildThread wake-set:
    only the directly connected region (the resource, its 1-hop neighbors,
    and edges derived from them) is re-derived -- never the whole life.
    """

    def __init__(
        self,
        *,
        resources: ResourceStore,
        ontology: OntologyStore,
        tasks_store=None,
        projects_store=None,
        claims_store=None,
        rooms_provider=None,
        clock=_DEFAULT_CLOCK,
    ) -> None:
        self._resources = resources
        self._ontology = ontology
        self._tasks = tasks_store
        self._projects = projects_store
        self._claims = claims_store
        self._rooms = rooms_provider
        self._clock = clock

    # -- full pass (idempotent) ---------------------------------------------------

    def project_all(self, *, visible_scopes: tuple[str, ...]) -> int:
        written = 0
        seen: set[str] = set()
        for scope_id in visible_scopes:
            for record in self._resources.list_by_scope(scope_id):
                if record.resource_id in seen or record.stale:
                    continue
                seen.add(record.resource_id)
                written += self._project_record(record)
        written += self._project_task_edges(visible_scopes)
        written += self._project_claim_edges(visible_scopes)
        written += self._project_room_edges()
        return written

    def recompute_region(self, resource_id: str, *, visible_scopes: tuple[str, ...]) -> int:
        """Bounded wake-set around one changed resource (spec: BuildThread)."""

        written = 0
        record = self._resources.get(resource_id)
        if record is not None and not record.stale:
            written += self._project_record(record)
        # 1-hop expansion: re-derive the edges of direct neighbors only.
        for edge in self._ontology.edges_from(resource_id, scope_ids=visible_scopes):
            written += self._project_subject(edge.object, visible_scopes)
        for edge in self._ontology.edges_to(resource_id, scope_ids=visible_scopes):
            written += self._project_subject(edge.subject, visible_scopes)
        return written

    def _project_subject(self, subject: str, visible_scopes: tuple[str, ...]) -> int:
        record = self._resources.get(subject)
        if record is None or record.stale:
            return 0
        if record.scope_id not in visible_scopes:
            return 0
        return self._project_record(record)

    # -- per-record rules ------------------------------------------------------------

    def _project_record(self, record) -> int:
        written = 0
        if record.resource_type == "file" and record.locator:
            from pathlib import Path

            path = Path(record.locator)
            parent = f"file:{path.parent.as_posix()}"
            written += self._assert(record.scope_id, record.resource_id, BELONGS_TO, parent)
            written += self._assert(record.scope_id, parent, CONTAINS, record.resource_id)
            written += self._project_git_root(record, path)
        elif record.resource_type == "window":
            process = dict(record.metadata).get("process", "")
            if process:
                written += self._assert(
                    record.scope_id, record.resource_id, BELONGS_TO, f"application:{process}"
                )
        elif record.resource_type == "browser_tab":
            browser = dict(record.metadata).get("browser", "")
            if browser:
                written += self._assert(
                    record.scope_id,
                    record.resource_id,
                    BELONGS_TO,
                    f"browser-session:{browser}",
                )
        return written

    def _project_git_root(self, record, path) -> int:
        current = path.parent
        for _ in range(32):  # bounded walk upward
            if (current / ".git").exists():
                return self._assert(
                    record.scope_id,
                    record.resource_id,
                    BELONGS_TO,
                    f"repository:{current.as_posix()}",
                )
            if current.parent == current:
                return 0
            current = current.parent
        return 0

    def _project_task_edges(self, visible_scopes: tuple[str, ...]) -> int:
        if self._tasks is None:
            return 0
        written = 0
        for scope_id in visible_scopes:
            for task in self._tasks.list_by_scope(scope_id):
                task_node = f"task:{task.task_id}"
                if task.project_id is not None:
                    written += self._assert(
                        scope_id, f"project:{task.project_id}", CONTAINS, task_node
                    )
                    written += self._assert(
                        scope_id, task_node, BELONGS_TO, f"project:{task.project_id}"
                    )
                if task.assignee_person_id is not None:
                    written += self._assert(
                        scope_id, task_node, ASSIGNED_TO, f"person:{task.assignee_person_id}"
                    )
        return written

    def _project_claim_edges(self, visible_scopes: tuple[str, ...]) -> int:
        if self._claims is None:
            return 0
        written = 0
        for claim in self._claims.list_all():
            if claim.scope_id not in visible_scopes:
                continue
            for ref in claim.source_refs:
                if not ref.startswith(("device:", "user:")):
                    written += self._assert(
                        claim.scope_id, f"claim:{claim.claim_id}", DERIVED_FROM, ref
                    )
            for superseded in claim.supersedes:
                written += self._assert(
                    claim.scope_id, f"claim:{claim.claim_id}", SUPERSEDES, superseded
                )
        return written

    def _project_room_edges(self) -> int:
        if self._rooms is None:
            return 0
        written = 0
        for room in self._rooms():
            for device in room.get("devices", ()):
                written += self._assert(
                    room["scope_id"],
                    f"device:{device}",
                    LOCATED_IN,
                    f"room:{room['room_id']}",
                )
        return written

    def _assert(self, scope_id: str, subject: str, predicate: str, object_: str) -> int:
        existing = self._ontology.get(assertion_id(subject, predicate, object_))
        if existing is not None:
            return 0
        self._ontology.save(
            OntologyAssertion(
                assertion_id=assertion_id(subject, predicate, object_),
                subject=subject,
                predicate=predicate,
                object=object_,
                scope_id=scope_id,
                state=ClaimState.REPORTED,
                created_at=self._clock(),
                source_ref=_SOURCE,
            )
        )
        return 1


__all__ = ["RelationshipProjector", "assertion_id"]
