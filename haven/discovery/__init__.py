"""Discovery produces a candidate, not authority.

A `DiscoveredDevice` is what a scan (BLE, mDNS, SSDP, a vendor's own
registry) can know on its own: that something is out there, roughly what
kind of thing it looks like, and how strongly. It is never enough to control
anything -- a scan cannot know a device's risk tier or which service call
turns it off, and this package has no path that skips a household deciding
those. `enroll_device()` is the only way a `DiscoveredDevice` becomes a
`DeviceManifest`, and it requires an approving actor and a justification,
the same way `HavenRuntime.approve_rule()` does for a rule.
"""

from .enrollment import enroll_device
from .models import DiscoveredDevice
from .provider import DiscoveryProvider, FixtureDiscoveryProvider

__all__ = ["DiscoveredDevice", "DiscoveryProvider", "FixtureDiscoveryProvider", "enroll_device"]
