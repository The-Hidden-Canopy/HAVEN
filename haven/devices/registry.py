"""Discovery for registered device manifests.

Mirrors `haven.providers.CapabilityRegistry`: register by device_id, look up
by device_id, and filter by device_type or a required capability. Nothing
here executes a command or checks authority -- that is `AuthorityEngine`
and a `haven.integrations` adapter, downstream of this registry.
"""

from __future__ import annotations

from typing import Iterable

from haven.core.domain import DeviceSelector

from .manifest import DeviceManifest


class UnknownDevice(KeyError):
    """Raised when a device_id has not been registered."""


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceManifest] = {}

    def register(self, manifest: DeviceManifest) -> None:
        self._devices[manifest.device_id] = manifest

    def is_registered(self, device_id: str) -> bool:
        return device_id in self._devices

    def get(self, device_id: str) -> DeviceManifest:
        try:
            return self._devices[device_id]
        except KeyError:
            raise UnknownDevice(device_id) from None

    def find(
        self,
        *,
        device_type: str | None = None,
        role: str | None = None,
        room: str | None = None,
        requires_capability: str | None = None,
    ) -> tuple[str, ...]:
        """Return device_ids matching every filter given.

        `device_type` matches a manifest's literal `device_type` (e.g. a
        search for actual switches). `role` matches `DeviceManifest.role`
        instead, so a search for "light" also finds a switch wired to a lamp
        via `semantic_role="light"`. Use whichever the caller actually means.
        """

        return tuple(
            device_id
            for device_id, manifest in self._devices.items()
            if (device_type is None or manifest.device_type == device_type)
            and (role is None or manifest.role == role)
            and (room is None or manifest.room == room)
            and (requires_capability is None or manifest.has_capability(requires_capability))
        )

    def all_devices(self) -> Iterable[DeviceManifest]:
        return tuple(self._devices.values())

    def resolve(self, selector: DeviceSelector) -> tuple[str, ...]:
        """Resolve a `DeviceSelector` to the device_ids matching it now.

        This is `find()` under a selector's constraints, not a separate
        mechanism -- a selector is just a device query captured on a
        `RuleDraft` so it can be re-resolved at run time instead of once at
        proposal time.
        """

        return self.find(
            device_type=selector.device_type,
            role=selector.role,
            room=selector.room,
            requires_capability=selector.requires_capability,
        )


__all__ = ["DeviceRegistry", "UnknownDevice"]
