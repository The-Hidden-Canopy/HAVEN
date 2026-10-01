"""Ring-shaped doorbell evidence with a simulator-first provider boundary.

The package deliberately exposes observation only. It does not discover
devices, store credentials, stream media, or issue doorbell commands. A live
Ring transport can be added behind the same ``RingEventSource`` contract
later; until then the deterministic simulator makes the evidence path
testable without pretending that a network account or camera is connected.
"""

from .events import RingEvent, RingEventKind
from .provider import RingEvidenceProvider, RingEventSource, RingSimulator

__all__ = [
    "RingEvent",
    "RingEventKind",
    "RingEvidenceProvider",
    "RingEventSource",
    "RingSimulator",
]
