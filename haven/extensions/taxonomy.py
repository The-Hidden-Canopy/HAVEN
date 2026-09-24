"""The extension taxonomy (spec page 41).

Four classes, each with a declared run-location and access/authority
boundary:

* Provider -- runs as a local process/companion; observes and executes only
  by declared capability; authority stays in HAVEN.
* Intelligence Service -- a local model/service or an explicit remote
  endpoint; receives a bounded context HAVEN constructs and returns
  proposals or answers; NEVER authority-bearing mutations.
* Feature Module -- a native/domain extension with an explicit contract;
  must call application services, never stores directly.
* Export Consumer -- runs outside HAVEN entirely; reads only explicitly
  exported, redacted artifacts through the signed-catalog boundary.

The registry is descriptor-only: describing what is installed and what each
class may do. Fail closed -- an unknown class is refused, never coerced.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExtensionClass(str, Enum):
    PROVIDER = "provider"
    INTELLIGENCE = "intelligence"
    FEATURE = "feature"
    EXPORT_CONSUMER = "export_consumer"


# Declared boundaries per class (spec page 41). These ride along with every
# descriptor so every surface that lists an extension also states its limits.
CLASS_BOUNDARIES: dict[ExtensionClass, dict[str, str]] = {
    ExtensionClass.PROVIDER: {
        "run_location": "local process or companion device",
        "access_boundary": "observation/execution by declared capability only",
        "authority_boundary": "authority stays in HAVEN; provider proposes, never decides",
    },
    ExtensionClass.INTELLIGENCE: {
        "run_location": "local model/service or explicit remote endpoint",
        "access_boundary": "receives only the bounded context HAVEN constructs",
        "authority_boundary": "answers and proposals only; never authority-bearing mutations",
    },
    ExtensionClass.FEATURE: {
        "run_location": "inside HAVEN (native/domain extension)",
        "access_boundary": "explicit contract; application services only, never stores directly",
        "authority_boundary": "same governance as first-party features",
    },
    ExtensionClass.EXPORT_CONSUMER: {
        "run_location": "outside HAVEN (external process)",
        "access_boundary": "read-only, explicitly exported redacted artifacts",
        "authority_boundary": "no live state, no credentials, no execution",
    },
}


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ExtensionDescriptor:
    """One installed or available extension, described -- not granted."""

    extension_id: str
    display_name: str
    extension_class: ExtensionClass
    source: str  # "builtin" or the module/entry point that provides it
    detail: str = ""
    # Optional class-instance state (e.g. an export consumer's enabled flag).
    state: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "extension_id", _require_text(self.extension_id, name="extension_id"))
        object.__setattr__(self, "display_name", _require_text(self.display_name, name="display_name"))
        if not isinstance(self.extension_class, ExtensionClass):
            # Fail closed: an unknown class is refused, never coerced.
            raise ValueError(f"unknown extension class: {self.extension_class!r}")
        object.__setattr__(self, "source", _require_text(self.source, name="source"))
        if not isinstance(self.state, tuple):
            object.__setattr__(self, "state", tuple(self.state))

    def wire(self) -> dict[str, Any]:
        boundaries = CLASS_BOUNDARIES[self.extension_class]
        return {
            "extension_id": self.extension_id,
            "display_name": self.display_name,
            "class": self.extension_class.value,
            "run_location": boundaries["run_location"],
            "access_boundary": boundaries["access_boundary"],
            "authority_boundary": boundaries["authority_boundary"],
            "source": self.source,
            "detail": self.detail,
            "state": dict(self.state),
        }


class ExtensionRegistry:
    """Descriptor registry: what exists, per class. Grants live elsewhere."""

    def __init__(self) -> None:
        self._descriptors: dict[str, ExtensionDescriptor] = {}

    def register(self, descriptor: ExtensionDescriptor) -> None:
        if not isinstance(descriptor, ExtensionDescriptor):
            raise ValueError("descriptor must be an ExtensionDescriptor")
        self._descriptors[descriptor.extension_id] = descriptor

    def list_by_class(self, extension_class: ExtensionClass) -> tuple[ExtensionDescriptor, ...]:
        if not isinstance(extension_class, ExtensionClass):
            raise ValueError(f"unknown extension class: {extension_class!r}")
        return tuple(
            sorted(
                (item for item in self._descriptors.values() if item.extension_class is extension_class),
                key=lambda item: item.extension_id,
            )
        )

    def all(self) -> tuple[ExtensionDescriptor, ...]:
        return tuple(sorted(self._descriptors.values(), key=lambda item: item.extension_id))


__all__ = [
    "CLASS_BOUNDARIES",
    "ExtensionClass",
    "ExtensionDescriptor",
    "ExtensionRegistry",
]
