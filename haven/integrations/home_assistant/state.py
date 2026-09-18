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

from haven.core.domain import CoverState, DeviceState, EvidenceStatus, LockState
from haven.devices import DeviceRegistry


_OBSERVED_SOURCE = "home_assistant.rest"

# HA's on/off domains: a boolean is the entire physical question, so `is_on`
# stays the right (and only) typed field for these.
_BOOLEAN_DOMAINS = frozenset({"light", "switch", "fan"})

_COVER_STATES = {member.value for member in CoverState}
_LOCK_STATES = {member.value for member in LockState}


def _parse_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entity_domain(entity_id: str) -> str:
    domain, sep, _ = entity_id.partition(".")
    return domain if sep else ""


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _domain_fields(domain: str, raw_state: str, attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Map one domain's raw state/attributes onto DeviceState's typed fields.

    Each domain gets its own real vocabulary instead of being forced through
    `is_on`: a cover's `open`/`closed`/`opening`/`closing` are real states in
    their own right (not a fuzzy on/off), and a value this function doesn't
    recognize for a domain is left as `None` on that domain's typed field --
    `raw_state` on the returned `DeviceState` still preserves exactly what
    the provider reported, so nothing this mapping doesn't understand is
    lost, only left untyped.
    """

    if domain in _BOOLEAN_DOMAINS:
        is_on = {"on": True, "off": False}.get(raw_state)
        brightness = attributes.get("brightness")
        brightness_pct = (
            round(brightness / 255 * 100) if is_on and isinstance(brightness, (int, float)) else None
        )
        return {"is_on": is_on, "brightness_pct": brightness_pct}
    if domain == "cover":
        cover_state = raw_state if raw_state in _COVER_STATES else None
        # Preserved for anything that still reads the boolean convention
        # (e.g. the demo's own garage door): open is "on", closed is "off",
        # and the transitional states are honestly neither -- they stay
        # `None` on `is_on` rather than guessing, because `cover_state`
        # already carries the real answer.
        is_on = {"open": True, "closed": False}.get(raw_state)
        return {"is_on": is_on, "cover_state": CoverState(cover_state) if cover_state else None}
    if domain == "lock":
        lock_state = raw_state if raw_state in _LOCK_STATES else None
        return {"lock_state": LockState(lock_state) if lock_state else None}
    if domain == "climate":
        return {
            "climate_mode": raw_state or None,
            "current_temperature": _as_float(attributes.get("current_temperature")),
            "target_temperature": _as_float(attributes.get("temperature")),
        }
    if domain == "camera":
        return {"camera_available": True}
    return {}


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

    Every entity is mapped through `_domain_fields` by its HA domain (the
    prefix before the dot in its entity_id) rather than one on/off
    interpretation for everything: a light or switch keeps the boolean
    `is_on`/`brightness_pct` shape, but a cover, lock, climate entity, or
    camera gets its own typed fields instead of being squeezed through
    `{"on": True, "off": False}`, which is how "open" used to become `None`.
    `raw_state` on every mapped `DeviceState` is always the provider's
    literal state string, whether or not this mapping recognized it.
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
        raw_state = raw_state if isinstance(raw_state, str) else None
        domain = _entity_domain(entity_id)

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
                    raw_state=raw_state,
                    camera_available=False if domain == "camera" else None,
                )
            )
            continue

        fields = _domain_fields(domain, raw_state or "", attributes)
        mapped.append(
            DeviceState(
                device_id=entity_id,
                kind=manifest.device_type,
                room_id=manifest.room or "unknown",
                is_on=fields.get("is_on"),
                brightness_pct=fields.get("brightness_pct"),
                observed_at=observed_at,
                source=_OBSERVED_SOURCE,
                status=EvidenceStatus.OBSERVED,
                raw_state=raw_state,
                cover_state=fields.get("cover_state"),
                lock_state=fields.get("lock_state"),
                climate_mode=fields.get("climate_mode"),
                current_temperature=fields.get("current_temperature"),
                target_temperature=fields.get("target_temperature"),
                camera_available=fields.get("camera_available"),
            )
        )
    return tuple(mapped)


__all__ = ["device_states_from_ha"]
