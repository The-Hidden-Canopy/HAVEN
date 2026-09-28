"""RelationshipService: the graph's service façade over projector, correlator,
and policy -- plus the persistence of human decisions (rejected candidates
never re-surface)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from haven.core.correlation import current as current_correlation, new_id as new_correlation_id
from haven.core.domain import EventType, Principal, RoleTier, Transition, TransitionKind
from haven.core.store import HavenStore, RelationshipAdmission
from haven.errors import InvalidTransition, ScopeViolation, StateConflict
from haven.graph.candidates import CandidateRelationship, Correlator, candidate_id
from haven.graph.deterministic import RelationshipProjector
from haven.graph.model_proposer import ModelRelationshipProposer
from haven.graph.policy import RelationshipAdmissionPolicy
from haven.ontology.store import OntologyStore


class RelationshipService:
    def __init__(
        self,
        *,
        projector: RelationshipProjector,
        correlator: Correlator,
        policy: RelationshipAdmissionPolicy,
        ontology: OntologyStore,
        state_path: str | Path,
        model_proposers: tuple[ModelRelationshipProposer, ...] = (),
        transition_store: HavenStore | None = None,
        principal: Principal | None = None,
        transition_store_provider: Callable[[], HavenStore] | None = None,
        principal_provider: Callable[[], Principal] | None = None,
        on_admitted: Callable[[], None] | None = None,
    ) -> None:
        self._projector = projector
        self._correlator = correlator
        self._policy = policy
        self._ontology = ontology
        self._state_path = Path(state_path)
        # Optional, additional candidate sources (native product-
        # consolidation plan, P2: a model proposer feeding the same
        # admission boundary the deterministic correlator already does).
        # Empty by default -- every existing caller that only ever passed
        # `correlator=` keeps behaving exactly as before.
        self._model_proposers = model_proposers
        self._transition_store = transition_store
        self._principal = principal
        self._transition_store_provider = transition_store_provider
        self._principal_provider = principal_provider
        self._on_admitted = on_admitted
        self._lock = threading.Lock()
        self._rejected = self._load_rejected()

    def _fresh_candidates(
        self, *, visible_scopes: tuple[str, ...], exclude_pairs: frozenset[str] | None = None
    ) -> tuple[CandidateRelationship, ...]:
        exclude = self._excluded_pairs() if exclude_pairs is None else exclude_pairs
        merged: dict[str, CandidateRelationship] = {
            candidate.candidate_id: candidate
            for candidate in self._correlator.candidates(visible_scopes=visible_scopes, exclude_pairs=exclude)
        }
        for proposer in self._model_proposers:
            for candidate in proposer.candidates(visible_scopes=visible_scopes, exclude_pairs=exclude):
                merged.setdefault(candidate.candidate_id, candidate)
        return tuple(sorted(merged.values(), key=lambda item: (-item.confidence, item.candidate_id)))

    def _load_rejected(self) -> set[str]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if not isinstance(data, dict):
            return set()
        return {str(item) for item in data.get("rejected", [])}

    def _save_rejected(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"rejected": sorted(self._rejected)}, indent=2), encoding="utf-8"
        )

    # -- reads -----------------------------------------------------------------

    def candidates(self, *, visible_scopes: tuple[str, ...]) -> dict:
        self._projector.project_all(visible_scopes=visible_scopes)
        fresh = self._fresh_candidates(visible_scopes=visible_scopes)
        rows = []
        for candidate in fresh:
            if candidate.candidate_id in self._rejected:
                continue
            verdict = self._policy.classify(candidate)
            row = candidate.wire()
            row["verdict"] = verdict
            rows.append(row)
        return {"ok": True, "candidates": rows}

    def edges_for(self, resource_id: str, *, visible_scopes: tuple[str, ...]) -> dict:
        self._projector.recompute_region(resource_id, visible_scopes=visible_scopes)
        outgoing = [
            {"assertion_id": edge.assertion_id, "predicate": edge.predicate, "other": edge.object,
             "direction": "from", "provenance": edge.source_ref, "confidence": edge.confidence}
            for edge in self._ontology.edges_from(resource_id, scope_ids=visible_scopes)
        ]
        incoming = [
            {"assertion_id": edge.assertion_id, "predicate": edge.predicate, "other": edge.subject,
             "direction": "to", "provenance": edge.source_ref, "confidence": edge.confidence}
            for edge in self._ontology.edges_to(resource_id, scope_ids=visible_scopes)
        ]
        return {"ok": True, "edges": outgoing + incoming}

    def _excluded_pairs(self) -> frozenset[str]:
        return frozenset(self._rejected)

    # -- decisions ---------------------------------------------------------------

    def admit(
        self,
        *,
        candidate_id_value: str | None,
        visible_scopes: tuple[str, ...],
        justification: str | None,
    ) -> dict:
        if not isinstance(justification, str) or not justification.strip():
            return {"ok": False, "error": "relationship admission requires a non-empty justification"}
        transition_store = (
            self._transition_store_provider() if self._transition_store_provider is not None else self._transition_store
        )
        principal = self._principal_provider() if self._principal_provider is not None else self._principal
        if transition_store is None or principal is None:
            return {"ok": False, "error": "relationship admission governance is not configured"}
        if principal.role_tier != RoleTier.OWNER:
            return {"ok": False, "error": "only an owner can admit a household relationship"}
        if principal.household_id != transition_store.household_id:
            return {"ok": False, "error": "relationship admission household scope does not match"}
        candidate = self._find_candidate(candidate_id_value, visible_scopes)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate: {candidate_id_value}"}
        if candidate.scope_id not in visible_scopes:
            return {"ok": False, "error": "the candidate's scope is not visible"}
        assertion = self._policy.build_assertion(candidate)
        with self._lock:
            if any(
                event.event_type is EventType.RELATIONSHIP_ADMITTED
                and dict(event.payload).get("assertion_id") == assertion.assertion_id
                for event in transition_store.events
            ):
                return {"ok": False, "error": f"relationship assertion already admitted: {assertion.assertion_id}"}
            prior_assertion = self._ontology.get(assertion.assertion_id)
            self._ontology.save(assertion)
            try:
                event = transition_store.execute_transition(
                    Transition(
                        kind=TransitionKind.ADMIT_RELATIONSHIP,
                        household_id=principal.household_id,
                        actor_id=principal.actor_id,
                        payload=RelationshipAdmission(
                            candidate_id=candidate.candidate_id,
                            assertion_id=assertion.assertion_id,
                            scope_id=candidate.scope_id,
                            predicate=candidate.predicate,
                            admitted_by=principal.actor_id,
                            admitted_by_role=principal.role_tier,
                            justification=justification.strip(),
                        ),
                        correlation_id=current_correlation() or new_correlation_id(),
                    ),
                    now=assertion.created_at,
                )
            except (InvalidTransition, ScopeViolation, StateConflict, TypeError, ValueError) as exc:
                if prior_assertion is None:
                    self._ontology.remove(assertion.assertion_id)
                return {"ok": False, "error": str(exc)}
            if self._on_admitted is not None:
                self._on_admitted()
        return {"ok": True, "assertion_id": assertion.assertion_id, "event_id": event.event_id}

    def reject(self, *, candidate_id_value: str | None, visible_scopes: tuple[str, ...]) -> dict:
        candidate = self._find_candidate(candidate_id_value, visible_scopes)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate: {candidate_id_value}"}
        with self._lock:
            self._rejected.add(candidate.candidate_id)
            self._save_rejected()
        return {"ok": True, "rejected": candidate.candidate_id}

    def _find_candidate(
        self, candidate_id_value: str | None, visible_scopes: tuple[str, ...]
    ) -> CandidateRelationship | None:
        if not isinstance(candidate_id_value, str) or not candidate_id_value.strip():
            return None
        wanted = candidate_id_value.strip()
        for candidate in self._fresh_candidates(visible_scopes=visible_scopes, exclude_pairs=frozenset()):
            if candidate.candidate_id == wanted:
                return candidate
        return None


__all__ = ["RelationshipService"]
