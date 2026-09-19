"""`Claim`: a proposition HAVEN believes, with provenance and a belief state.

Contract only -- no admission policy, no store, no persistence yet. This is
deliberately not the same thing as `haven.core.domain`'s evidence types
(`PresenceState`/`ContextState`/`DeviceState`): those are the governed
home loop's own fail-closed evidence, read by `AuthorityEngine` to decide
whether an action may run right now. A `Claim` is knowledge for the "life"
substrate to search, relate, and hand off -- "the proposal deadline is
Sept 28", "Bryan is on the marketing team" -- and is never, by itself, a
license for `HavenRuntime` to act. Nothing here changes what the home loop
treats as authorizing evidence.

Two claims can disagree (`ClaimState.DISPUTED`) without HAVEN silently
picking a winner, the same discipline `haven.perception.fusion.
SensorDisagreement` already applies to conflicting sensor readings --
disagreement is a fact worth keeping, not something to resolve by fiat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class ClaimState(str, Enum):
    """How a claim came to be believed, not how confident HAVEN is in it
    (that is `confidence`, kept separate the same way `EvidenceStatus` and
    a device's `confidence` field are two different questions elsewhere in
    this repo)."""

    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    CORROBORATED = "corroborated"
    DISPUTED = "disputed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Claim:
    """One believed proposition, scoped, sourced, and possibly superseded.

    `supersedes`/`contradicts` name other claim ids rather than embedding
    them, so a claim store can resolve a chain or a conflict without this
    record needing to know the store's shape. `valid_until` is optional --
    not every claim expires ("Bryan is on the marketing team" may hold
    indefinitely); one that is set and has passed is a caller's signal to
    treat the claim as effectively `STALE` without necessarily having
    rewritten its `state` yet.
    """

    claim_id: str
    scope_id: str
    proposition: str
    state: ClaimState
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    created_at: datetime
    valid_until: datetime | None = None
    confidence: float = 1.0
    supersedes: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _require_text(self.claim_id, name="claim_id"))
        object.__setattr__(self, "scope_id", _require_text(self.scope_id, name="scope_id"))
        object.__setattr__(self, "proposition", _require_text(self.proposition, name="proposition"))
        if not isinstance(self.state, ClaimState):
            raise ValueError("state must be a ClaimState")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.valid_until is not None:
            object.__setattr__(self, "valid_until", require_aware_utc(self.valid_until, name="valid_until"))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        object.__setattr__(self, "contradicts", tuple(self.contradicts))


def is_stale(claim: Claim, *, now: datetime) -> bool:
    """Whether `claim` should be treated as stale right now.

    True either because the claim already carries `ClaimState.STALE`, or
    because `now` has passed its `valid_until` -- a caller should not have
    to re-derive this same expiry check itself, and a claim's `state` field
    is not required to be rewritten the instant it expires (nothing here
    mutates it) for a reader to know not to trust it.
    """

    now = require_aware_utc(now, name="now")
    if claim.state == ClaimState.STALE:
        return True
    return claim.valid_until is not None and now > claim.valid_until


__all__ = ["Claim", "ClaimState", "is_stale"]
