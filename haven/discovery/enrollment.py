"""The only path from a DiscoveredDevice to a controllable DeviceManifest.

A scan cannot know a device's risk tier or which service call turns it off
-- a household member supplies `capabilities` explicitly, the same way an
owner supplies a justification to approve a rule. `enroll_device()` is a
pure function, not a stored workflow: Haven does not need a persisted
"pending enrollment" state machine for this decision to be deliberate, only
a function signature that cannot be satisfied without one.
"""

from __future__ import annotations

from haven.devices import CapabilityDescriptor, DeviceManifest

from .models import DiscoveredDevice


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def enroll_device(
    candidate: DiscoveredDevice,
    *,
    device_type: str,
    capabilities: tuple[CapabilityDescriptor, ...],
    approved_by: str,
    justification: str,
    room: str | None = None,
    semantic_role: str | None = None,
) -> DeviceManifest:
    """Turn a discovery candidate into a registerable DeviceManifest.

    `device_type` and `capabilities` are supplied by the caller, not taken
    from `candidate.suggested_device_type` -- a suggestion is not a
    decision. `approved_by` and `justification` are required and must be
    non-empty; this function has no path that produces a `DeviceManifest`
    without both, which is what makes "discovery produces a candidate, not
    authority" an enforced property of this module rather than a comment.
    """

    _require_text(approved_by, name="approved_by")
    _require_text(justification, name="justification")
    return DeviceManifest(
        device_id=candidate.candidate_id,
        device_type=device_type,
        provider_id=candidate.provider_id,
        capabilities=capabilities,
        room=room if room is not None else candidate.suggested_room,
        semantic_role=semantic_role,
    )


__all__ = ["enroll_device"]
