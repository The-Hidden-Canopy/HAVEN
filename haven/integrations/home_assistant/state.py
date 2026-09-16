"""Map Home Assistant's state list onto Haven's DeviceState evidence.

`LiveHomeAssistantAdapter.fetch_states()` returns Home Assistant's own state
dicts; this module is the pure boundary that turns them into the immutable
`DeviceState` values the authority engine already consumes. Only entities
with a registered `DeviceManifest` are mapped -- Haven observes the devices
it knows about, not the household's whole entity list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from haven.core.domain import DeviceState, EvidenceStatus
from haven.devices import DeviceRegistry


_OBSERVED_SOURCE = "home_assistant.rest"


def _parse_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def device_states_from_ha(
    states: Iterable[Mapping[str, Any]],
    registry: DeviceRegistry,
) -> tuple[DeviceState, ...]:
    """Convert /api/states entries into DeviceState values for registered devices.

    Entities with no manifest in `registry` are skipped, as are entities whose
    `last_changed` is missing or unparseable -- evidence without an
    observation time cannot participate in Haven's freshness checks, so it is
    dropped rather than guessed at. A device Home Assistant reports as
    "unavailable" maps to `EvidenceStatus.UNAVAILABLE`, which the authority
    engine already treats as unable to authorize anything.
    """

    mapped: list[DeviceState] = []
    for state in states:
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or not registry.is_registered(entity_id):
            continue
        manifest = registry.get(entity_id)
        observed_at = _parse_observed_at(state.get("last_changed"))
        if observed_at is None:
            continue

        attributes = state.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = {}

        raw_state = state.get("state")
        if raw_state == "unavailable":
            mapped.append(
                DeviceState(
                    device_id=entity_id,
                    kind=manifest.device_type,
                    room_id=manifest.room or "unknown",
                    is_on=None,
                    brightness_pct=None,
                    observed_at=observed_at,
                    source=_OBSERVED_SOURCE,
                    status=EvidenceStatus.UNAVAILABLE,
                )
            )
            continue

        is_on = {"on": True, "off": False}.get(raw_state if isinstance(raw_state, str) else "")
        brightness = attributes.get("brightness")
        brightness_pct = round(brightness / 255 * 100) if is_on and isinstance(brightness, (int, float)) else None
        mapped.append(
            DeviceState(
                device_id=entity_id,
                kind=manifest.device_type,
                room_id=manifest.room or "unknown",
                is_on=is_on,
                brightness_pct=brightness_pct,
                observed_at=observed_at,
                source=_OBSERVED_SOURCE,
                status=EvidenceStatus.OBSERVED,
            )
        )
    return tuple(mapped)


__all__ = ["device_states_from_ha"]
