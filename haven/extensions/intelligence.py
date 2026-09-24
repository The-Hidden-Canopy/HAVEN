"""The intelligence-service boundary (spec page 41).

An intelligence service receives ONLY the bounded context HAVEN constructs
(visible scopes, capped, titles/propositions -- never locators, bodies, or
out-of-scope records) and returns one of: an answer, an action proposal, a
mutation proposal, a rule draft, or a clarification request. Proposals
re-enter HAVEN through the existing governed proposal paths; the boundary
itself never touches a store. A malicious or buggy service can waste a
question -- it cannot mutate a thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from haven.intelligence.intents import (
    ActionProposal,
    ClarificationRequest,
    MutationProposal,
)

_CONTEXT_RESOURCE_CAP = 25
_CONTEXT_CLAIM_CAP = 25


@dataclass(frozen=True)
class BoundedContext:
    """Everything a service may see for one request."""

    text: str
    focus: str | None
    scope_ids: tuple[str, ...]
    resources: tuple[dict[str, Any], ...] = ()
    claims: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a non-empty string")
        if not isinstance(self.scope_ids, tuple):
            object.__setattr__(self, "scope_ids", tuple(self.scope_ids))

    def wire(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "focus": self.focus,
            "scope_ids": list(self.scope_ids),
            "resources": [dict(item) for item in self.resources],
            "claims": [dict(item) for item in self.claims],
        }


@dataclass(frozen=True)
class IntelligenceResponse:
    """One service answer: an answer string or a proposal of record."""

    kind: str  # "answer" | "action_proposal" | "mutation_proposal" | "clarification"
    text: str = ""
    intent: Any = None

    def __post_init__(self) -> None:
        allowed = {"answer", "action_proposal", "mutation_proposal", "clarification"}
        if self.kind not in allowed:
            raise ValueError(f"response kind must be one of {sorted(allowed)}")


class IntelligenceService(Protocol):
    def answer(self, context: BoundedContext) -> IntelligenceResponse: ...


class IntelligenceBoundary:
    """Constructs the bounded context and routes responses -- stores never.

    The composition root supplies the stores; the service sees only what
    `build_context` hands it. Responses are validated and returned as an
    envelope the caller routes through the governed proposal paths.
    """

    def __init__(self, *, resources, claims, identity) -> None:
        self._resources = resources
        self._claims = claims
        self._identity = identity

    def build_context(self, *, text: str, focus: str | None = None) -> BoundedContext:
        visible = self._identity.visible_scope_ids()
        resource_rows = []
        seen_scopes = set()
        for scope_id in visible:
            if scope_id in seen_scopes:
                continue
            seen_scopes.add(scope_id)
            for record in self._resources.list_by_scope(scope_id):
                if record.stale or len(resource_rows) >= _CONTEXT_RESOURCE_CAP:
                    continue
                # Titles and types only: no locators, no metadata, no bodies.
                resource_rows.append(
                    {"resource_id": record.resource_id, "resource_type": record.resource_type, "title": record.title}
                )
        claim_rows = []
        for claim in self._claims.list_all():
            if claim.scope_id not in visible or len(claim_rows) >= _CONTEXT_CLAIM_CAP:
                continue
            claim_rows.append(
                {
                    "claim_id": claim.claim_id,
                    "proposition": claim.proposition,
                    "state": claim.state.value,
                }
            )
        return BoundedContext(
            text=text.strip(),
            focus=focus,
            scope_ids=visible,
            resources=tuple(resource_rows),
            claims=tuple(claim_rows),
        )

    def submit(self, service: IntelligenceService, *, text: str, focus: str | None = None) -> dict:
        """Run one request. The envelope is routed by the caller; stores
        are never written here, whatever the service returns."""

        context = self.build_context(text=text, focus=focus)
        response = service.answer(context)
        if not isinstance(response, IntelligenceResponse):
            return {"ok": False, "error": "the service returned an invalid response envelope"}
        if response.kind == "answer":
            return {"ok": True, "kind": "answer", "text": response.text, "context": context.wire()}
        if response.intent is None:
            return {"ok": False, "error": f"a {response.kind} response must carry a proposal intent"}
        expected = {
            "action_proposal": ActionProposal,
            "mutation_proposal": MutationProposal,
            "clarification": ClarificationRequest,
        }[response.kind]
        if not isinstance(response.intent, expected):
            return {
                "ok": False,
                "error": f"a {response.kind} response must carry a {expected.__name__}",
            }
        # Proposals leave as proposals. The caller routes them through the
        # governed paths (`_run_proposal`, authoring review); nothing here
        # executes or persists.
        return {
            "ok": True,
            "kind": response.kind,
            "intent": response.intent,
            "text": response.text,
            "context": context.wire(),
        }


class EchoIntelligenceService:
    """Diagnostic stub: proves the seam and shows exactly what the bounded
    context contained. Can be told to answer as a proposal for tests."""

    def __init__(self, *, respond_as: str = "answer") -> None:
        self.respond_as = respond_as
        self.seen_contexts: list[BoundedContext] = []

    def answer(self, context: BoundedContext) -> IntelligenceResponse:
        self.seen_contexts.append(context)
        summary = (
            f"I saw {len(context.resources)} resource(s) and {len(context.claims)} claim(s) "
            f"across {len(context.scope_ids)} scope(s)."
        )
        if self.respond_as == "answer":
            return IntelligenceResponse(kind="answer", text=summary)
        return IntelligenceResponse(kind=self.respond_as, text=summary, intent=None)


__all__ = [
    "BoundedContext",
    "EchoIntelligenceService",
    "IntelligenceBoundary",
    "IntelligenceResponse",
    "IntelligenceService",
]
