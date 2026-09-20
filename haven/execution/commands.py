"""Provider-neutral execution values.

The original home/device vertical uses :class:`DeviceCommand` and
:class:`DeviceResult`.  Those values remain the compatibility contract for
lights, locks, climate, and other device integrations.  Computer and future
life providers use these open-vocabulary values instead: a provider receives
an already-authorized capability request and returns what the provider
actually accepted, without pretending that every resource is a device.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from haven.core.time import require_aware_utc


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _normalize_parameters(
    parameters: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    name: str,
) -> tuple[tuple[str, Any], ...]:
    items = tuple(parameters.items()) if isinstance(parameters, Mapping) else tuple(parameters)
    normalized: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for key, value in items:
        key = _require_text(key, name=f"{name} name")
        if key in seen:
            raise ValueError(f"duplicate {name}: {key}")
        seen.add(key)
        normalized.append((key, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


@dataclass(frozen=True)
class ProviderCommand:
    """One already-authorized capability request for any provider.

    ``target_resource_id`` is optional for actions that create a new
    resource.  The open ``capability`` string is intentional: providers can
    add capabilities without expanding the closed home-device ``ActionKind``
    enum, while the authority layer still classifies every action explicitly.
    """

    request_id: str
    provider_id: str
    capability: str
    target_resource_id: str | None
    parameters: tuple[tuple[str, Any], ...]
    requested_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("request_id", "provider_id", "capability"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if self.target_resource_id is not None:
            object.__setattr__(
                self,
                "target_resource_id",
                _require_text(self.target_resource_id, name="target_resource_id"),
            )
        object.__setattr__(
            self,
            "parameters",
            _normalize_parameters(self.parameters, name="parameter"),
        )
        object.__setattr__(self, "requested_at", require_aware_utc(self.requested_at, name="requested_at"))


@dataclass(frozen=True)
class ProviderResult:
    """What a provider accepted and what it can honestly report afterward.

    ``success`` means the provider accepted/performed the requested
    operation.  It does not grant authority and does not claim more than the
    provider's own verification supports.  Optional metadata is deliberately
    small and structured so callers can retain operation-specific facts
    without changing this shared result shape.
    """

    success: bool
    detail: str
    observed_at: datetime
    source: str
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("provider result success must be a boolean")
        object.__setattr__(self, "detail", _require_text(self.detail, name="provider result detail"))
        object.__setattr__(self, "observed_at", require_aware_utc(self.observed_at, name="result observed_at"))
        object.__setattr__(self, "source", _require_text(self.source, name="provider result source"))
        object.__setattr__(
            self,
            "metadata",
            _normalize_parameters(self.metadata, name="metadata"),
        )


__all__ = ["ProviderCommand", "ProviderResult"]
