#!/usr/bin/env python3
"""HAVEN browser native-messaging host (EXPERIMENTAL reference scaffold).

Speaks the Chrome native-messaging framing protocol on stdin/stdout. Tab
observations are forwarded to HAVEN through an embedded in-process
BrowserHub when the Core runs in the same machine context; the loopback
alternative (a local pipe per installation) is the same seam with a
transport attached. Nothing here runs unless a deployer installs the host
manifest and the extension.

Privacy boundary: this host only ever forwards tab identity/title/url/
activity. It never requests page bodies, cookies, or incognito data.
"""

from __future__ import annotations

import json
import struct
import sys


def _read_message():
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    (length,) = struct.unpack("<I", raw_length)
    body = sys.stdin.buffer.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(body)))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main() -> int:
    # When embedded, `bridge` is injected by the composition root; the
    # standalone scaffold acknowledges commands so the extension can tell
    # the host is alive.
    bridge = globals().get("bridge")
    while True:
        message = _read_message()
        if message is None:
            return 0
        if bridge is not None:
            bridge(message)
        _write_message({"ok": True, "received": message.get("type", "unknown")})


if __name__ == "__main__":
    raise SystemExit(main())
