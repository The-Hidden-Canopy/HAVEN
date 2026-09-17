"""Perception fusion: combine independent sensor readings, no vision model.

There is no real vision, thermal, IR, BLE, or WiFi provider in this repo. A
real one would implement `ObservationProvider.observe()`; this package only
combines several such readings of the same fact into one.
"""

from .fusion import SensorDisagreement, fuse_context, fuse_presence
from .observation import FixtureObservationProvider, Observation, ObservationProvider

__all__ = [
    "FixtureObservationProvider",
    "Observation",
    "ObservationProvider",
    "SensorDisagreement",
    "fuse_context",
    "fuse_presence",
]
