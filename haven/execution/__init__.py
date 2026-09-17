"""Provider-neutral execution routing: DeviceManifest.provider_id -> adapter.

Haven's model side is pluggable (`haven.providers`), and the device
description side is pluggable (`haven.devices`) -- capability, control
class, and routed service are all independent of vendor. This package
closes the third side: which adapter actually executes an authorized
`DeviceCommand` is chosen by a device's declared `provider_id`, not
hardcoded to one integration. `HomeAssistantAdapter` is now just one
`ExecutionAdapter` among however many a deployment registers -- Matter, an
IR blaster, an ONVIF camera, a vendor plugin all satisfy the same shape.
"""

from .registry import ExecutionAdapter, ExecutionProviderRegistry, UnknownExecutionProvider

__all__ = ["ExecutionAdapter", "ExecutionProviderRegistry", "UnknownExecutionProvider"]
