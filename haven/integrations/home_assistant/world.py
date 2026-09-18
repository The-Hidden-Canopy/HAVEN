"""A WorldProvider over HomeAssistantObserver that never raises into UI flows.

Doctrine: evidence that could not be fetched does not exist, and an unobserved
household renders empty rather than crashing the UI. A failed observe returns
the last good snapshot when one exists; before the first successful fetch it
returns a valid empty WorldSnapshot for the household (captured at ``now``,
valid for one TTL window, no presence/contexts/devices). Consumers downstream
-- chat grounding, authority evidence, state rendering -- see "nothing
observed yet", which the authority engine already treats as fail-closed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from haven.core.domain import WorldSnapshot

from .observer import DEFAULT_SNAPSHOT_TTL, HomeAssistantObserver


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
                return self._last_good
            return WorldSnapshot(
                snapshot_id=f"empty-{uuid4().hex}",
                household_id=self._household_id,
                captured_at=now,
                valid_until=now + self._snapshot_ttl,
            )
        self._last_good = snapshot
        return snapshot


__all__ = ["HomeAssistantWorldProvider"]
