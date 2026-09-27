"""RelationshipService: the graph's service façade over projector, correlator,
and policy -- plus the persistence of human decisions (rejected candidates
never re-surface)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

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
        self._lock = threading.Lock()
        self._rejected = self._load_rejected()
        self._auto_admitted: set[str] = set()

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
            if verdict == "auto" and candidate.candidate_id not in self._auto_admitted:
                self._policy.auto_admit(candidate)
                self._auto_admitted.add(candidate.candidate_id)
                continue  # admitted edges surface through relationships.for instead
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

    def admit(self, *, candidate_id_value: str | None, visible_scopes: tuple[str, ...]) -> dict:
        candidate = self._find_candidate(candidate_id_value, visible_scopes)
        if candidate is None:
            return {"ok": False, "error": f"unknown candidate: {candidate_id_value}"}
        if candidate.scope_id not in visible_scopes:
            return {"ok": False, "error": "the candidate's scope is not visible"}
        return self._policy.admit(candidate)

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
