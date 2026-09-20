"""Execution contracts for both devices and general providers.

Haven's model side is pluggable (`haven.providers`), and the device
description side is pluggable (`haven.devices`) -- capability, control
class, and routed service are all independent of vendor. This package
closes the third side for the home/device vertical: which adapter actually
executes an authorized `DeviceCommand` is chosen by a device's declared
`provider_id`, not hardcoded to one integration. `HomeAssistantAdapter` is
now just one `ExecutionAdapter` among however many a deployment registers --
Matter, an IR blaster, an ONVIF camera, a vendor plugin all satisfy the same
shape. General computer/life providers additionally use
`ProviderCommand`/`ProviderResult`, so a filesystem or browser capability
does not have to masquerade as a device.
"""

from .commands import ProviderCommand, ProviderResult
from .registry import (
    ExecutionAdapter,
    ExecutionProviderRegistry,
    ProviderExecutionAdapter,
    UnknownExecutionProvider,
)

__all__ = [
    "ExecutionAdapter",
    "ExecutionProviderRegistry",
    "ProviderCommand",
    "ProviderExecutionAdapter",
    "ProviderResult",
    "UnknownExecutionProvider",
]
