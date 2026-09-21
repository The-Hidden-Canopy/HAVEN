"""Small, deterministic JSON framing for the native HAVEN IPC boundary.

The wire format is a four-byte little-endian length followed by one UTF-8 JSON
object.  Messages are typed so a native client cannot accidentally treat an
event as a response or a response as an executable request.

This module contains no authority decisions and performs no state mutation.
It is only the transport contract; application dispatch remains on the Core
side and must continue to call the existing governed services.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any

IPC_PROTOCOL_VERSION = "haven-ipc-1"
MAX_FRAME_BYTES = 1 << 20
_HEADER = struct.Struct("<I")
_KINDS = frozenset({"request", "response", "event"})


class IpcProtocolError(ValueError):
    """Raised when a peer sends an invalid or oversized IPC message."""


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IpcProtocolError(f"{name} must be a non-empty string")
    return value.strip()


def _require_object(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IpcProtocolError(f"{name} must be a JSON object")
    return dict(value)


def _validate_message(message: Mapping[str, Any]) -> dict[str, Any]:
    payload = _require_object(message, name="message")
    version = payload.get("version")
    if version != IPC_PROTOCOL_VERSION:
        raise IpcProtocolError(f"unsupported IPC protocol version: {version!r}")
    kind = payload.get("kind")
    if kind not in _KINDS:
        raise IpcProtocolError(f"unsupported IPC message kind: {kind!r}")
    if kind in {"request", "response"}:
        _require_text(payload.get("request_id"), name="request_id")
    if kind == "request":
        _require_text(payload.get("method"), name="method")
        params = payload.get("params", {})
        if not isinstance(params, Mapping):
            raise IpcProtocolError("request params must be a JSON object")
        payload["params"] = dict(params)
    elif kind == "response":
        if not isinstance(payload.get("ok"), bool):
            raise IpcProtocolError("response 'ok' must be a boolean")
        if payload["ok"] and "error" in payload:
            raise IpcProtocolError("successful responses cannot contain an error")
        if not payload["ok"]:
            _require_text(payload.get("error"), name="error")
    else:
        _require_text(payload.get("event"), name="event")
        if "payload" in payload and not isinstance(payload["payload"], Mapping):
            raise IpcProtocolError("event payload must be a JSON object")
    return payload


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """Encode one validated message with its length prefix."""

    payload = _validate_message({"version": IPC_PROTOCOL_VERSION, **dict(message)})
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IpcProtocolError(f"message is not JSON serializable: {exc}") from exc
    if len(body) > MAX_FRAME_BYTES:
        raise IpcProtocolError(f"IPC frame exceeds {MAX_FRAME_BYTES} bytes")
    return _HEADER.pack(len(body)) + body


def decode_frame(body: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode the body of one frame, excluding its four-byte length header."""

    raw = bytes(body)
    if len(raw) > MAX_FRAME_BYTES:
        raise IpcProtocolError(f"IPC frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IpcProtocolError("IPC frame is not valid UTF-8 JSON") from exc
    return _validate_message(message)


def request_message(request_id: str, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a request envelope for a native client."""

    return _validate_message(
        {
            "version": IPC_PROTOCOL_VERSION,
            "kind": "request",
            "request_id": request_id,
            "method": method,
            "params": dict(params or {}),
        }
    )


def response_message(
    request_id: str,
    *,
    ok: bool,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a response envelope, preserving request correlation."""

    message: dict[str, Any] = {
        "version": IPC_PROTOCOL_VERSION,
        "kind": "response",
        "request_id": request_id,
        "ok": ok,
    }
    if ok:
        message["result"] = result
    else:
        message["error"] = error or "HAVEN IPC request failed"
    return _validate_message(message)


def event_message(event: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a server-to-client event envelope.

    Event delivery is a separate host concern.  Keeping its envelope in the
    same versioned contract prevents native and debug clients from inventing
    incompatible state-change shapes.
    """

    return _validate_message(
        {
            "version": IPC_PROTOCOL_VERSION,
            "kind": "event",
            "event": event,
            "payload": dict(payload or {}),
        }
    )


__all__ = [
    "IPC_PROTOCOL_VERSION",
    "MAX_FRAME_BYTES",
    "IpcProtocolError",
    "decode_frame",
    "encode_frame",
    "event_message",
    "request_message",
    "response_message",
]
