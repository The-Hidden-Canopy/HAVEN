"""Decision lifecycle over current HAVEN identity and observed resources.

No method executes actions or changes membership. Review references and
checkpoint generations identify history; they never confer authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from haven.core.domain import RoleTier

from .models import DecisionRecord, DecisionRevision, DecisionSnapshot, DocumentImpact, SourceRevision, text
from .store import DecisionConflict, DecisionStore


class DecisionService:
    def __init__(self, *, store: DecisionStore, identity, resources,
                 has_declared_owner, clock=None, evidence_max_age=timedelta(days=7)):
        self.store = store
        self.identity = identity
        self.resources = resources
        self._has_declared_owner = has_declared_owner
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._evidence_max_age = evidence_max_age

    def _access(self, scope_id, *, write=False, owner=False, principal_id=None):
        principal = self.identity.current_principal()
        if principal_id is not None and principal_id != principal.actor_id:
            raise PermissionError("decision principal does not match current identity")
        memberships = self.identity.memberships()
        membership = next((item for item in memberships if item.scope.scope_id == scope_id
                           and item.scope.status == "active" and item.actor_id == principal.actor_id), None)
        if membership is None:
            raise PermissionError("decision scope is not visible")
        if write and (not self._has_declared_owner() or membership.role not in {"owner", "member"}):
            raise PermissionError("decision mutation requires current membership and declared owner")
        if owner and (principal.role_tier is not RoleTier.OWNER or membership.role != "owner"):
            raise PermissionError("decision review requires an owner")
        return principal

    def snapshot(self, scope_id, *, principal_id=None):
        self._access(scope_id, principal_id=principal_id)
        snapshot = self.store.load(scope_id)
        self._access(scope_id, principal_id=principal_id)
        return snapshot

    def _sources(self, revision):
        return (*revision.evidence, *(item.source for item in revision.affected_documents))

    def _source_issues(self, revision, state=None):
        issues = []
        now = self._clock()
        for ref in self._sources(revision):
            if state is None:
                record = self.resources.get(ref.resource_id)
            else:
                cache = state.setdefault("sources", {})
                if ref.resource_id not in cache:
                    if len(cache) >= state.get("source_limit", 1024):
                        issues.append("source evaluation budget requires focused review")
                        continue
                    cache[ref.resource_id] = self.resources.get(ref.resource_id)
                record = cache[ref.resource_id]
            if record is None or record.scope_id != ref.scope_id or record.stale:
                issues.append(f"source unavailable: {ref.resource_id}")
            elif record.content_hash != ref.revision:
                issues.append(f"source revision changed: {ref.resource_id}")
            elif record.observed_at > now or now - record.observed_at > self._evidence_max_age:
                issues.append(f"source observation expired: {ref.resource_id}")
        return tuple(dict.fromkeys(issues))

    def _validate_sources(self, revision, scope_id, *, fresh):
        for ref in self._sources(revision):
            record = self.resources.get(ref.resource_id)
            if ref.scope_id != scope_id or record is None or record.scope_id != scope_id:
                raise ValueError("unknown or cross-scope decision source")
        if fresh and self._source_issues(revision):
            raise DecisionConflict("decision evidence changed or expired; reconcile source revisions")

    def _write(self, scope_id, generation, transform, *, owner=False, check=None, actor_id=None):
        principal = self._access(scope_id, write=True, owner=owner)
        if actor_id is not None and principal.actor_id != actor_id:
            raise PermissionError("decision identity changed before mutation")
        def guard():
            current = self._access(scope_id, write=True, owner=owner)
            if current.actor_id != principal.actor_id:
                raise PermissionError("decision identity changed before commit")
            if check is not None:
                check()
        return self.store.update(scope_id, generation, transform, before_commit=guard)

    @staticmethod
    def _record(snapshot, decision_id, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        record = next((item for item in snapshot.decisions if item.decision_id == decision_id), None)
        if record is None:
            raise ValueError("unknown decision")
        if record.current.revision != expected_revision:
            raise DecisionConflict("decision revision changed; reload before retrying")
        if record.current.status == "superseded":
            raise DecisionConflict("superseded decisions cannot be revived")
        if len(record.revisions) >= 200:
            raise ValueError("decision revision retention limit reached; export and explicitly erase or replace")
        return record

    @staticmethod
    def _append(snapshot, record, revision):
        updated = replace(record, revisions=(*record.revisions, revision))
        return replace(snapshot, generation=snapshot.generation + 1,
                       decisions=tuple(updated if item.decision_id == record.decision_id else item
                                       for item in snapshot.decisions))

    def propose(self, scope_id, *, expected_generation, problem, options, rationale,
                owner_id=None, observations=(), evidence=(), constraints=(), dependencies=(),
                affected_documents=(), reconsideration_triggers=()):
        principal = self._access(scope_id, write=True)
        revision = DecisionRevision(
            revision=1, status="proposed", problem=problem, options=options,
            rationale=rationale, owner_id=owner_id or principal.actor_id,
            recorded_by=principal.actor_id, recorded_at=self._clock().isoformat(),
            observations=observations, evidence=evidence, constraints=constraints,
            dependencies=dependencies, affected_documents=affected_documents,
            reconsideration_triggers=reconsideration_triggers)
        self._validate_sources(revision, scope_id, fresh=False)
        decision_id = f"decision:{uuid4()}"
        record = DecisionRecord(decision_id, scope_id, (revision,))
        def transform(snapshot):
            return replace(snapshot, generation=snapshot.generation + 1,
                           decisions=(*snapshot.decisions, record))
        self._write(scope_id, expected_generation, transform,
                    check=lambda: self._validate_sources(revision, scope_id, fresh=False), actor_id=principal.actor_id)
        return record

    def revise(self, scope_id, decision_id, *, expected_generation, expected_revision, rationale, **changes):
        allowed = {"problem", "options", "owner_id", "observations", "evidence", "constraints",
                   "dependencies", "affected_documents", "reconsideration_triggers"}
        if set(changes) - allowed:
            raise ValueError("revision contains unsupported fields")
        principal = self._access(scope_id, write=True)
        def transform(snapshot):
            record = self._record(snapshot, decision_id, expected_revision)
            revision = replace(record.current, **changes, revision=expected_revision + 1, status="proposed",
                               selected_option_id=None, review_ref=None, replacement_id=None,
                               rationale=rationale, recorded_by=principal.actor_id, recorded_at=self._clock().isoformat())
            self._validate_sources(revision, scope_id, fresh=False)
            return self._append(snapshot, record, revision)
        return self._write(scope_id, expected_generation, transform, actor_id=principal.actor_id)

    def review(self, scope_id, decision_id, *, expected_generation, expected_revision,
               status, rationale, selected_option_id=None, review_ref=None, replacement_id=None):
        if status not in {"accepted", "challenged", "superseded"}:
            raise ValueError("unknown review transition")
        principal = self._access(scope_id, write=True, owner=True)
        accepted_revision = []
        def transform(snapshot):
            record = self._record(snapshot, decision_id, expected_revision)
            if status == "superseded":
                target = next((item for item in snapshot.decisions if item.decision_id == replacement_id), None)
                if target is None or self._issues(snapshot, target):
                    raise ValueError("replacement must be a current accepted decision in the same scope")
            revision = replace(record.current, revision=expected_revision + 1, status=status,
                               rationale=rationale, selected_option_id=selected_option_id or record.current.selected_option_id,
                               review_ref=text(review_ref, "review_ref"), replacement_id=replacement_id,
                               recorded_by=principal.actor_id, recorded_at=self._clock().isoformat())
            if status == "accepted":
                self._validate_sources(revision, scope_id, fresh=True)
                for dependency in revision.dependencies:
                    dep = next(item for item in snapshot.decisions if item.decision_id == dependency)
                    if self._issues(snapshot, dep):
                        raise DecisionConflict("decision dependency requires review")
                accepted_revision.append(revision)
            return self._append(snapshot, record, revision)
        return self._write(scope_id, expected_generation, transform, owner=True,
                           check=lambda: [self._validate_sources(item, scope_id, fresh=True) for item in accepted_revision],
                           actor_id=principal.actor_id)

    def reconcile_document(self, scope_id, decision_id, *, expected_generation, expected_revision,
                           resource_id, observed_revision, receipt_ref, rationale):
        """Record a real revision observed after the owning provider changed a doc.

        This method cannot write files. It confirms a provider observation;
        an approval reference alone cannot turn pending work into completion.
        """
        principal = self._access(scope_id, write=True, owner=True)
        reconciled = []
        def transform(snapshot):
            record = self._record(snapshot, decision_id, expected_revision)
            impact = next((item for item in record.current.affected_documents if item.source.resource_id == resource_id), None)
            if impact is None or impact.state != "pending" or observed_revision == impact.source.revision:
                raise ValueError("document reconciliation requires a changed pending document")
            source = SourceRevision(resource_id, scope_id, observed_revision)
            documents = tuple(DocumentImpact(source, item.change, "reconciled", text(receipt_ref, "receipt_ref"))
                              if item is impact else item for item in record.current.affected_documents)
            revision = replace(record.current, revision=expected_revision + 1, rationale=rationale,
                               affected_documents=documents,
                               evidence=tuple(source if ref.resource_id == resource_id else ref for ref in record.current.evidence),
                               recorded_at=self._clock().isoformat(), recorded_by=principal.actor_id)
            self._validate_sources(revision, scope_id, fresh=True)
            reconciled.append(revision)
            return self._append(snapshot, record, revision)
        return self._write(scope_id, expected_generation, transform, owner=True,
                           check=lambda: [self._validate_sources(item, scope_id, fresh=True) for item in reconciled],
                           actor_id=principal.actor_id)

    def _issues(self, snapshot, record, seen=frozenset(), state=None):
        state = state if state is not None else {}
        memo = state.setdefault("issues", {})
        if record.decision_id in memo:
            return memo[record.decision_id]
        visited = state.setdefault("visited", set())
        if record.decision_id not in visited and len(visited) >= state.get("decision_limit", 256):
            return ("decision evaluation budget requires focused review",)
        visited.add(record.decision_id)
        revision = record.current
        issues = list(self._source_issues(revision, state))
        if revision.status != "accepted":
            issues.append(f"decision is {revision.status}")
        if any(item.state == "pending" for item in revision.affected_documents):
            issues.append("affected documentation requires reconciliation")
        if record.decision_id in seen:
            return ("cyclic dependency",)
        if "records" not in state:
            state["records"] = {item.decision_id: item for item in snapshot.decisions}
        records = state["records"]
        for dependency in revision.dependencies:
            if self._issues(snapshot, records[dependency], seen | {record.decision_id}, state):
                issues.append(f"dependency requires review: {dependency}")
        memo[record.decision_id] = tuple(dict.fromkeys(issues))
        return memo[record.decision_id]

    def context(self, scope_id, *, principal_id=None, decision_id=None, limit=8):
        """Bounded accepted/current projection; every call rechecks membership."""
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("decision context limit must be between 1 and 12")
        snapshot = self.snapshot(scope_id, principal_id=principal_id)
        records = sorted(snapshot.decisions, key=lambda item: (item.current.recorded_at, item.decision_id), reverse=True)
        entries = []
        budget = 12_000
        state = {"decision_limit": 64, "source_limit": 256}
        candidates = [item for item in records if decision_id is None or item.decision_id == decision_id][:64]
        for record in candidates:
            if self._issues(snapshot, record, state=state):
                continue
            revision = record.current
            entry = {"decision_id": record.decision_id, "revision": revision.revision,
                     "problem": revision.problem[:320],
                     "direction": dict(revision.options)[revision.selected_option_id][:240],
                     "rationale": revision.rationale[:640], "constraints": list(revision.constraints[:4]),
                     "source_revisions": [ref.__dict__ for ref in self._sources(revision)]}
            cost = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
            if cost > budget:
                continue
            budget -= cost
            entries.append(entry)
            if len(entries) >= limit:
                break
        self._access(scope_id, principal_id=principal_id)
        canonical = json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":"))
        return {"schema_version": 2, "scope_id": scope_id, "generation": snapshot.generation,
                "snapshot_revision": hashlib.sha256(canonical.encode()).hexdigest(), "decisions": entries}

    def review_items(self, scope_id):
        snapshot = self.snapshot(scope_id)
        return tuple((record, self._issues(snapshot, record)) for record in snapshot.decisions
                     if record.current.status != "superseded" and self._issues(snapshot, record))

    def erase(self, scope_id, *, expected_generation, decision_id=None, justification):
        """Explicit local retention/deletion; keep generation to reject old checkpoints."""
        text(justification, "deletion justification")
        def transform(snapshot):
            if decision_id is not None and not any(item.decision_id == decision_id for item in snapshot.decisions):
                raise ValueError("unknown decision")
            return replace(snapshot, generation=snapshot.generation + 1,
                           decisions=tuple(item for item in snapshot.decisions if decision_id is not None
                                           and item.decision_id != decision_id))
        return self._write(scope_id, expected_generation, transform, owner=True)
