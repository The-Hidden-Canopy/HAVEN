"""The world-source contract.

The household world is observed through this one call: `observe(now)` returns
the current ``WorldSnapshot``. Every consumer -- chat grounding, authority
evidence, scheduler ticks, state rendering -- goes through it, so the consumer
cannot tell a simulated house from a live Home Assistant. A provider may be a
``SimulatedHouse``, a polling Home Assistant observer, or anything else that
can answer "what is the world right now" without raising into UI flows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import WorldSnapshot


class WorldProvider(Protocol):
    def observe(self, now: datetime) -> WorldSnapshot:
        """Return the household world as observed at ``now``."""


__all__ = ["WorldProvider"]
