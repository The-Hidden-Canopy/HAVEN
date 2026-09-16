"""Device manifests: a device declares capabilities, not a brand.

A manifest says a washer has a `start` capability in control class MEDIUM
with allowed `cycle` values -- not that it is an LG washer reachable through
some vendor API. `haven.integrations` adapters are what actually know how to
turn a capability action into a protocol call; this module only describes
the shape a device exposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


class ControlClass(str, Enum):
    """How much authority a capability action needs before it can run.

    This mirrors `haven.core.domain.RiskTier` in spirit -- READ needs no
    authorization at all, GUARDED is the appliance-world analogue of
    CONFIRMATION_REQUIRED -- but it is kept as a separate enum here because
    wiring it into `AuthorityEngine.decide()` is a follow-up migration, not
    part of this descriptive layer.
    """

    READ = "read"
    LOW_RISK = "low_risk"
    MEDIUM = "medium"
    GUARDED = "guarded"


class UnknownCapability(KeyError):
    """Raised when a manifest has no capability of the requested name."""


@dataclass(frozen=True)
class CapabilityDescriptor:
    """One named thing a device can do: power, cycle, start, volume, ..."""

    name: str
    control_class: ControlClass
    readable: bool = False
    writable: bool = False
    values: tuple[str, ...] = ()
    service: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_text(self.name, name="capability name"))
        if not isinstance(self.control_class, ControlClass):
            raise ValueError("control_class must be a ControlClass")
        if not self.readable and not self.writable:
            raise ValueError(f"capability {self.name!r} must be readable, writable, or both")
        if self.writable and not (isinstance(self.service, str) and self.service.strip()):
            raise ValueError(
                f"writable capability {self.name!r} must declare the provider service it routes to"
            )
        if self.service is not None:
            object.__setattr__(self, "service", _require_text(self.service, name="capability service"))
        object.__setattr__(
            self,
            "values",
            tuple(_require_text(v, name="capability value") for v in self.values),
        )


@dataclass(frozen=True)
class DeviceManifest:
    """What one household device exposes, independent of its provider."""

    device_id: str
    device_type: str
    provider_id: str
    capabilities: tuple[CapabilityDescriptor, ...]
    telemetry: tuple[str, ...] = ()
    semantic_role: str | None = None
    room: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _require_text(self.device_id, name="device_id"))
        object.__setattr__(self, "device_type", _require_text(self.device_type, name="device_type"))
        object.__setattr__(self, "provider_id", _require_text(self.provider_id, name="provider_id"))
        if self.semantic_role is not None:
            object.__setattr__(self, "semantic_role", _require_text(self.semantic_role, name="semantic_role"))
        if self.room is not None:
            object.__setattr__(self, "room", _require_text(self.room, name="room"))
        capabilities = tuple(self.capabilities)
        names = [c.name for c in capabilities]
        if len(names) != len(set(names)):
            raise ValueError(f"device {self.device_id!r} declares duplicate capability names")
        if not all(isinstance(c, CapabilityDescriptor) for c in capabilities):
            raise ValueError("capabilities must be CapabilityDescriptor values")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(
            self,
            "telemetry",
            tuple(_require_text(t, name="telemetry field") for t in self.telemetry),
        )

    @property
    def capability_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.capabilities)

    @property
    def role(self) -> str:
        """What this device behaves as: its declared `semantic_role`, or its
        `device_type` when no role override is declared.

        This is what lets "turn off all the bedroom lights" match a smart
        plug wired to a lamp (device_type="switch", semantic_role="light")
        the same way it matches an actual light entity, without either
        caller having to know which one they're addressing.
        """

        return self.semantic_role if self.semantic_role is not None else self.device_type

    def has_capability(self, name: str, *, writable: bool | None = None, readable: bool | None = None) -> bool:
        for capability in self.capabilities:
            if capability.name != name:
                continue
            if writable is not None and capability.writable != writable:
                return False
            if readable is not None and capability.readable != readable:
                return False
            return True
        return False

    def capability(self, name: str) -> CapabilityDescriptor:
        for capability in self.capabilities:
            if capability.name == name:
                return capability
        raise UnknownCapability(f"{self.device_id} has no capability named {name!r}")


__all__ = [
    "CapabilityDescriptor",
    "ControlClass",
    "DeviceManifest",
    "UnknownCapability",
]
