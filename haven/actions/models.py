"""`ResourceActionRequest`/`ResourceActionDecision`: the authorization
contract for a resource provider's write-side actions -- e.g.
`haven.integrations.computer.FilesystemProvider.execute()`.

Deliberately not `haven.core.domain.ActionRequest`/`AuthorityDecision`, the
household device pipeline's own contract: `ActionKind` there is a closed
enum built for the device vertical (lights, garage, thermostat, ...), and a
resource provider's actions (`filesystem.create_folder`, `filesystem.move`,
whatever a future browser/calendar provider needs) are an open,
provider-owned vocabulary, the same discipline `ResourceRecord.resource_type`
and `haven.ontology`'s predicates already use rather than a closed set this
module would have to keep growing. What genuinely is generic --
`Principal`, `RoleTier`, `ConfirmationToken`, `DecisionStatus` -- is reused
as-is from `haven.core.domain` rather than redefined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from haven.core.domain import ConfirmationToken, DecisionStatus, RoleTier
from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _normalize_parameters(
    parameters: Mapping[str, Any] | Iterable[tuple[str, Any]],
) -> tuple[tuple[str, Any], ...]:
    items = tuple(parameters.items()) if isinstance(parameters, Mapping) else tuple(parameters)
    normalized = []
    seen: set[str] = set()
    for key, value in items:
        key = _require_text(key, name="parameter name")
        if key in seen:
            raise ValueError(f"duplicate parameter: {key}")
        seen.add(key)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


@dataclass(frozen=True)
class ResourceActionRequest:
    """One requested write-side action against a resource provider.

    `resource_id` is the resource this acts on (a move/copy/rename's
    source) -- `None` only for an action that creates something which does
    not exist yet, matching the same "nothing to name" reasoning
    `ResourceRecord.locator` uses. `action` is an open string rather than a
    closed enum: `ResourceAuthorityEngine` is handed a risk table keyed by
    this same string, so an unrecognized action fails closed there, not
    here.
    """

    request_id: str
    household_id: str
    requested_by: str
    provider_id: str
    action: str
    resource_id: str | None
    parameters: tuple[tuple[str, Any], ...]
    justification: str
    requested_at: datetime
    confirmation_token: ConfirmationToken | None = None

    def __post_init__(self) -> None:
        for field_name in ("request_id", "household_id", "requested_by", "provider_id", "action"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if self.resource_id is not None:
            object.__setattr__(self, "resource_id", _require_text(self.resource_id, name="resource_id"))
        if not isinstance(self.justification, str):
            raise ValueError("justification must be a string")
        object.__setattr__(self, "parameters", _normalize_parameters(self.parameters))
        object.__setattr__(self, "requested_at", require_aware_utc(self.requested_at, name="requested_at"))
        if self.confirmation_token is not None and not isinstance(self.confirmation_token, ConfirmationToken):
            raise ValueError("confirmation_token must be a ConfirmationToken")


@dataclass(frozen=True)
class ResourceActionDecision:
    """A `ResourceAuthorityEngine` verdict. `reason` is free text rather
    than a closed `DecisionCode` -- this module has far fewer failure shapes
    than the device pipeline's, and a short sentence is more useful to a
    household reading its own action ledger than a code they'd have to look
    up."""

    status: DecisionStatus
    reason: str
    required_role: RoleTier | None = None

    def __post_init__(self) -> None:
        _require_text(self.reason, name="decision reason")
        if not isinstance(self.status, DecisionStatus):
            raise ValueError("decision status must be a DecisionStatus")
        if self.required_role is not None and not isinstance(self.required_role, RoleTier):
            raise ValueError("required_role must be a RoleTier")


__all__ = ["ResourceActionDecision", "ResourceActionRequest"]
