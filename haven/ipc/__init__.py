"""The versioned local IPC boundary between a native HAVEN client and Core.

The protocol is deliberately independent of HTTP, WebView, and the current
debug renderer.  Native clients may use it once a host has authenticated the
installation, while the existing web surface remains an optional compatibility
surface during the migration.
"""

from .protocol import (
    IPC_PROTOCOL_VERSION,
    MAX_FRAME_BYTES,
    IpcProtocolError,
    decode_frame,
    encode_frame,
    event_message,
    request_message,
    response_message,
)
from .dispatcher import IpcDispatcher

__all__ = [
    "IPC_PROTOCOL_VERSION",
    "MAX_FRAME_BYTES",
    "IpcProtocolError",
    "decode_frame",
    "encode_frame",
    "event_message",
    "IpcDispatcher",
    "request_message",
    "response_message",
]
