"""The native IPC wire contract is bounded, typed, and correlation-safe."""

from __future__ import annotations

import pytest

from haven.ipc import (
    IPC_PROTOCOL_VERSION,
    IpcDispatcher,
    IpcProtocolError,
    decode_frame,
    encode_frame,
    event_message,
    request_message,
    response_message,
)


def test_request_response_and_event_round_trip_through_length_framing():
    request = request_message("req-1", "search.query", {"text": "lunar"})
    response = response_message("req-1", ok=True, result={"hits": []})
    event = event_message("state.changed", {"revision": 4})

    for message in (request, response, event):
        frame = encode_frame(message)
        body_length = int.from_bytes(frame[:4], "little")
        assert body_length == len(frame) - 4
        assert decode_frame(frame[4:]) == message


def test_protocol_rejects_wrong_version_and_unbounded_frames():
    with pytest.raises(IpcProtocolError, match="unsupported IPC protocol version"):
        encode_frame({"version": "haven-ipc-0", "kind": "event", "event": "state.changed"})

    with pytest.raises(IpcProtocolError, match="exceeds"):
        encode_frame(
            event_message("state.changed", {"blob": "x" * (1 << 20)})
        )


def test_dispatcher_rejects_unknown_methods_without_invoking_mutation_handlers():
    called = []
    dispatcher = IpcDispatcher({"safe.read": lambda params: called.append(params) or {"ok": True}})

    response = dispatcher(request_message("req-2", "danger.write", {}))

    assert response["ok"] is False
    assert response["request_id"] == "req-2"
    assert "unknown IPC method" in response["error"]
    assert called == []


def test_dispatcher_preserves_request_id_and_returns_input_errors():
    dispatcher = IpcDispatcher({"safe.read": lambda params: params["required"]})

    response = dispatcher(request_message("req-3", "safe.read", {}))

    assert response == {
        "version": IPC_PROTOCOL_VERSION,
        "kind": "response",
        "request_id": "req-3",
        "ok": False,
        "error": "'required'",
    }
