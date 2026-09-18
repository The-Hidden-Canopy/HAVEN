"""A WorldProvider over HomeAssistantObserver that never raises into UI flows.

Doctrine: evidence that could not be fetched does not exist, and an unobserved
household renders empty rather than crashing the UI. A failed observe returns
the last good snapshot when one exists; before the first successful fetch it
returns a valid empty WorldSnapshot for the household (captured at ``now``,
valid for one TTL window, no presence/contexts/devices). Consumers downstream
-- chat grounding, authority evidence, state rendering -- see "nothing
observed yet", which the authority engine already treats as fail-closed.

Keeping the last good snapshot for UI continuity ("last known: garage closed
2m ago") is deliberate; returning it as if THIS poll had succeeded is not.
Every entry the last good snapshot carries is still stamped
``EvidenceStatus.OBSERVED`` from whenever it really was fetched, so serving
it verbatim after a failed refresh would let a household's evidence look
fresh (and authorizable) for as long as the snapshot's original TTL window
happens to still cover ``now`` -- even though the very poll that just ran
found nothing. ``_as_fallback`` demotes every such entry to
``EvidenceStatus.FALLBACK`` before it is returned, so
``WorldSnapshot.evidence_problem`` (which only ever treats OBSERVED as
fresh) fails closed on it exactly as it already does for genuinely stale
evidence, while the retained values, ``observed_at``, and ``source`` still
let the UI say how old the last real reading was. An entry that was already
``UNAVAILABLE`` at capture time keeps that more specific status rather than
being overwritten to the vaguer FALLBACK.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from haven.core.domain import ContextState, DeviceState, EvidenceStatus, PresenceState, WorldSnapshot

from .observer import DEFAULT_SNAPSHOT_TTL, HomeAssistantObserver

_Evidence = ContextState | DeviceState | PresenceState


def _demote_to_fallback(item: _Evidence) -> _Evidence:
    if item.status == EvidenceStatus.UNAVAILABLE:
        return item
    return replace(item, status=EvidenceStatus.FALLBACK)


def _as_fallback(snapshot: WorldSnapshot) -> WorldSnapshot:
    return replace(
        snapshot,
        devices=tuple(_demote_to_fallback(item) for item in snapshot.devices),
        presence=tuple(_demote_to_fallback(item) for item in snapshot.presence),
        contexts=tuple(_demote_to_fallback(item) for item in snapshot.contexts),
    )


class HomeAssistantWorldProvider:
    """Caches the observer's last good snapshot and degrades to it, then empty."""

    def __init__(
        self,
        *,
        observer: HomeAssistantObserver,
        empty_household_id: str,
        snapshot_ttl: timedelta = DEFAULT_SNAPSHOT_TTL,
    ) -> None:
        if snapshot_ttl <= timedelta(0):
            raise ValueError("snapshot_ttl must be positive")
        if not isinstance(empty_household_id, str) or not empty_household_id.strip():
            raise ValueError("empty_household_id must be a non-empty string")
        self._observer = observer
        self._household_id = empty_household_id.strip()
        self._snapshot_ttl = snapshot_ttl
        self._last_good: WorldSnapshot | None = None

    def observe(self, now: datetime) -> WorldSnapshot:
        try:
            snapshot = self._observer.observe(now=now)
        except Exception:
            if self._last_good is not None:
                return _as_fallback(self._last_good)
            return WorldSnapshot(
                snapshot_id=f"empty-{uuid4().hex}",
                household_id=self._household_id,
                captured_at=now,
                valid_until=now + self._snapshot_ttl,
            )
        self._last_good = snapshot
        return snapshot


__all__ = ["HomeAssistantWorldProvider"]
