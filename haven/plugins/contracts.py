"""The normalized plugin descriptor and the closed vocabularies around it.

A plugin is never a provider (see `docs/plugin-boundary.md`): it never runs
inside HAVEN, never implements a `haven/providers` Protocol, and never
receives live household state. Every field here mirrors the Hub's own
`api/haven-plugins/index.js` validator on purpose -- the enums below are the
closed sets the Hub enforces server-side, kept in sync deliberately rather
than re-derived, so a HAVEN installation can reject a catalog entry that
somehow claims a wider boundary even if it were signed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PluginCapability(str, Enum):
    """The closed set of things a plugin is allowed to declare it does."""

    DECISION_CHAIN_RECONSTRUCTION = "decision_chain_reconstruction"
    ADAPTIVE_CURRICULUM_EVALUATION = "adaptive_curriculum_evaluation"


class PluginStatus(str, Enum):
    CATALOG_ONLY = "catalog_only"
    SUPPORTED = "supported"
    DEPRECATED = "deprecated"


class PluginDataBoundary(str, Enum):
    """The closed set of data a plugin may ever be scoped to receive.

    Today this has exactly one member. That is not an oversight: a plugin
    reads a household's exported, redacted receipt stream through the Hub
    and nothing else. Widening this enum is a deliberate architecture
    change, not a catalog edit.
    """

    EXPORTED_RECEIPTS_ONLY = "exported_receipts_only"


class InvalidPluginIdError(ValueError):
    """Raised when a plugin id is not a safe, storage-safe identifier."""


_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")


def safe_plugin_id(value: str) -> str:
    """Validate a plugin id against the same pattern the Hub catalog enforces.

    Externally supplied ids end up in local state keys and file paths, so
    anything with separators, dot-dot segments, or characters outside the
    slug alphabet is rejected before it can reach storage.
    """

    if not isinstance(value, str):
        raise InvalidPluginIdError(f"plugin id must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate:
        raise InvalidPluginIdError("plugin id must be a non-empty string")
    if ".." in candidate or "/" in candidate or "\\" in candidate:
        raise InvalidPluginIdError(f"plugin id must not contain path separators or '..': {candidate!r}")
    if not _ID_PATTERN.fullmatch(candidate):
        raise InvalidPluginIdError(
            f"plugin id must match ^[a-z0-9][a-z0-9-]{{1,63}}$: {candidate!r}"
        )
    return candidate


def _require_text(value: str, *, name: str, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    text = value.strip()
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return text


@dataclass(frozen=True)
class PluginDescriptor:
    """The internal normalized object for one catalog entry.

    This is deliberately a thin, closed-vocabulary record: a plugin's whole
    relationship to a household is "declares a capability, is scoped to
    exported_receipts_only, and a household explicitly enabled it." There is
    no field here for credentials, endpoints, or execution scope, because a
    plugin is never handed any.
    """

    plugin_id: str
    display_name: str
    publisher: str
    capability: PluginCapability
    status: PluginStatus
    data_boundary: PluginDataBoundary
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", safe_plugin_id(self.plugin_id))
        object.__setattr__(self, "display_name", _require_text(self.display_name, name="display_name"))
        object.__setattr__(self, "publisher", _require_text(self.publisher, name="publisher"))
        object.__setattr__(self, "description", _require_text(self.description, name="description", max_length=600))
        if not isinstance(self.capability, PluginCapability):
            object.__setattr__(self, "capability", PluginCapability(self.capability))
        if not isinstance(self.status, PluginStatus):
            object.__setattr__(self, "status", PluginStatus(self.status))
        if not isinstance(self.data_boundary, PluginDataBoundary):
            object.__setattr__(self, "data_boundary", PluginDataBoundary(self.data_boundary))
        if self.data_boundary is not PluginDataBoundary.EXPORTED_RECEIPTS_ONLY:
            # Unreachable today (the enum has one member) but kept as an
            # explicit gate: if the enum ever grows, a plugin still cannot
            # silently claim a wider boundary than this constructor allows
            # without a corresponding, deliberate change here.
            raise ValueError("plugin data_boundary must be exported_receipts_only")


__all__ = [
    "InvalidPluginIdError",
    "PluginCapability",
    "PluginDataBoundary",
    "PluginDescriptor",
    "PluginStatus",
    "safe_plugin_id",
]
