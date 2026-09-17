"""SSDP (UPnP) discovery: real WiFi/LAN discovery, standard library only.

SSDP (Simple Service Discovery Protocol) is the standard UPnP discovery
protocol many WiFi/LAN devices already speak -- smart TVs, media renderers,
some smart plugs, bridges, and routers. This is Haven's second
network-touching module, after `haven.integrations.home_assistant.client`:
a real UDP multicast M-SEARCH request and a listener for responses.

An SSDP response only ever produces a `DiscoveredDevice` -- a candidate, per
`haven.discovery`'s own boundary. SSDP alone cannot tell Haven what
capabilities a device has or what HTTP calls control it; a household (or a
more specific per-vendor provider) supplies that during enrollment, through
`haven.discovery.enroll_device()`.

`parse_ssdp_response()` is pure and is what tests exercise; `discover()` is
the only method here that touches a socket.
"""

from __future__ import annotations

import re
import socket
import time
from datetime import datetime, timezone

from haven.discovery import DiscoveredDevice

SSDP_MULTICAST_ADDR = "239.255.255.250"
SSDP_PORT = 1900
WIFI_SSDP_PROVIDER_ID = "wifi-ssdp"

_SEARCH_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_MULTICAST_ADDR}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
).encode("utf-8")

# Matches the device-type segment of a standard UPnP ST/USN URN, e.g.
# "urn:schemas-upnp-org:device:MediaRenderer:1" -> "mediarenderer". This is
# a naming-convention guess, not a capability -- exactly what
# DiscoveredDevice.suggested_device_type documents itself to be.
_DEVICE_TYPE_PATTERN = re.compile(r"urn:[\w.-]+:device:([\w-]+):\d+", re.IGNORECASE)


def build_search_request() -> bytes:
    return _SEARCH_REQUEST


def _guess_device_type(st: str) -> str | None:
    match = _DEVICE_TYPE_PATTERN.search(st or "")
    return match.group(1).lower() if match else None


def parse_ssdp_response(response: str, *, source_ip: str, now: datetime) -> DiscoveredDevice | None:
    """Parse one SSDP response datagram into a DiscoveredDevice, or None.

    Returns `None` for anything that is not a recognizable SSDP response
    (no LOCATION and no USN) -- an unparseable datagram produces no
    candidate rather than a guessed one.
    """

    headers: dict[str, str] = {}
    for line in response.split("\r\n"):
        idx = line.find(":")
        if idx > 0:
            headers[line[:idx].strip().lower()] = line[idx + 1 :].strip()

    usn = headers.get("usn", "")
    location = headers.get("location", "")
    if not usn and not location:
        return None

    return DiscoveredDevice(
        candidate_id=usn or f"{WIFI_SSDP_PROVIDER_ID}:{location or source_ip}",
        provider_id=WIFI_SSDP_PROVIDER_ID,
        discovered_at=now,
        source="wifi.ssdp",
        suggested_device_type=_guess_device_type(headers.get("st", "")),
    )


class SsdpDiscoveryProvider:
    """Real SSDP discovery: sends M-SEARCH, listens, returns candidates."""

    def __init__(self, *, timeout: float = 3.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._timeout = timeout

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        candidates: dict[str, DiscoveredDevice] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(self._timeout)
            sock.sendto(build_search_request(), (SSDP_MULTICAST_ADDR, SSDP_PORT))
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                candidate = parse_ssdp_response(
                    data.decode("utf-8", errors="replace"),
                    source_ip=addr[0],
                    now=datetime.now(timezone.utc),
                )
                if candidate is not None:
                    candidates[candidate.candidate_id] = candidate
        finally:
            sock.close()
        return tuple(candidates.values())


__all__ = [
    "SSDP_MULTICAST_ADDR",
    "SSDP_PORT",
    "SsdpDiscoveryProvider",
    "WIFI_SSDP_PROVIDER_ID",
    "build_search_request",
    "parse_ssdp_response",
]
