"""mDNS/DNS-SD discovery: real WiFi/LAN discovery, standard library only.

mDNS (RFC 6762) + DNS-SD (RFC 6763) is how most modern LAN devices announce
themselves -- AirPlay/Chromecast targets, HomeKit accessories, printers,
Spotify Connect speakers, and any `_http._tcp` web UI. This queries a fixed
list of well-known DNS-SD service types via a real UDP multicast PTR query
to `224.0.0.251:5353` and decodes the real (binary, name-compressed) DNS
responses -- the same "second network-touching module" discipline
`haven.integrations.wifi.ssdp` already established: no third-party DNS
library, a hand-rolled wire-format encoder/decoder instead.

`build_ptr_query()`/`parse_dns_response()` are pure and are what tests
exercise; `discover()` is the only method here that touches a socket.
"""

from __future__ import annotations

import socket
import struct
import time
from datetime import datetime, timezone

from haven.discovery import DiscoveredDevice

MDNS_MULTICAST_ADDR = "224.0.0.251"
MDNS_PORT = 5353
WIFI_MDNS_PROVIDER_ID = "wifi-mdns"

_QTYPE_PTR = 12
_QCLASS_IN = 1

# A fixed, small starting set of common DNS-SD service types (plan §4.3:
# "resolve common service types") -- not a full `_services._dns-sd._udp`
# meta-query, which enumerates service *types* present on the LAN rather
# than device *instances*; querying instance-producing types directly keeps
# this provider's candidates at the same "one candidate per device" grain
# SSDP already produces.
DEFAULT_SERVICE_TYPES: tuple[str, ...] = (
    "_http._tcp.local",
    "_airplay._tcp.local",
    "_googlecast._tcp.local",
    "_spotify-connect._tcp.local",
    "_hap._tcp.local",  # HomeKit accessory protocol
    "_printer._tcp.local",
    "_ipp._tcp.local",
    "_sonos._tcp.local",
)


def _encode_name(name: str) -> bytes:
    """DNS wire-format name encoding: length-prefixed labels, no compression
    (a query never needs to compress -- there is nothing earlier in the
    packet to point back to)."""

    out = bytearray()
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii")
        if not 0 < len(encoded) <= 63:
            raise ValueError(f"invalid DNS label: {label!r}")
        out.append(len(encoded))
        out.extend(encoded)
    out.append(0)
    return bytes(out)


def _decode_name(message: bytes, offset: int) -> tuple[str, int]:
    """Decode one (possibly compressed) DNS name starting at `offset`.

    Returns `(name, offset_after_name)` -- `offset_after_name` is the byte
    position immediately following the name *in the original stream*, which
    for a compressed name is right after the 2-byte pointer, not wherever
    the pointer chain eventually terminates.
    """

    labels: list[str] = []
    original_offset = offset
    jumped = False
    visited: set[int] = set()
    while True:
        if offset >= len(message):
            raise ValueError("DNS name runs past the end of the message")
        length = message[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:  # a compression pointer
            if offset + 1 >= len(message):
                raise ValueError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            if pointer in visited:
                raise ValueError("DNS compression pointer loop")
            visited.add(pointer)
            if not jumped:
                original_offset = offset + 2
                jumped = True
            offset = pointer
            continue
        offset += 1
        labels.append(message[offset : offset + length].decode("ascii", errors="replace"))
        offset += length
    end_offset = offset if not jumped else original_offset
    return ".".join(labels), end_offset


def build_ptr_query(service_type: str) -> bytes:
    """One DNS query packet asking for PTR records under `service_type`
    (e.g. `_airplay._tcp.local`) -- id 0, since mDNS responses are matched
    by question content, not by transaction id (RFC 6762 §18.1)."""

    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    question = _encode_name(service_type) + struct.pack(">HH", _QTYPE_PTR, _QCLASS_IN)
    return header + question


def parse_dns_response(message: bytes, *, source_ip: str, now: datetime) -> tuple[DiscoveredDevice, ...]:
    """Decode every PTR answer in one mDNS response datagram into
    candidates. Non-PTR answers (A/AAAA/SRV/TXT, which a full resolve would
    also request) are skipped -- each is still just "presence", the same
    boundary `parse_ssdp_response` already draws for its own protocol.
    Malformed input produces no candidates rather than a guessed one.
    """

    try:
        _id, flags, qdcount, ancount, _nscount, _arcount = struct.unpack(">HHHHHH", message[:12])
    except struct.error:
        return ()
    if not flags & 0x8000:  # QR bit: only responses carry answers worth reading
        return ()

    offset = 12
    try:
        for _ in range(qdcount):
            _name, offset = _decode_name(message, offset)
            offset += 4  # QTYPE + QCLASS

        candidates: list[DiscoveredDevice] = []
        for _ in range(ancount):
            name, offset = _decode_name(message, offset)
            rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", message[offset : offset + 10])
            offset += 10
            rdata_start = offset
            offset += rdlength
            if rtype != _QTYPE_PTR:
                continue
            target, _ = _decode_name(message, rdata_start)
            if not target:
                continue
            service_type = name.split(".", 1)[0].lstrip("_") or None
            candidates.append(
                DiscoveredDevice(
                    candidate_id=f"{WIFI_MDNS_PROVIDER_ID}:{target}",
                    provider_id=WIFI_MDNS_PROVIDER_ID,
                    discovered_at=now,
                    source="wifi.mdns",
                    suggested_device_type=service_type,
                    source_ip=source_ip,
                )
            )
    except (struct.error, ValueError, IndexError):
        return ()
    return tuple(candidates)


class MdnsDiscoveryProvider:
    """Real mDNS/DNS-SD discovery: queries `service_types`, listens, returns
    candidates deduplicated by the DNS-SD instance name."""

    def __init__(
        self, *, timeout: float = 3.0, service_types: tuple[str, ...] = DEFAULT_SERVICE_TYPES
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not service_types:
            raise ValueError("service_types must not be empty")
        self._timeout = timeout
        self._service_types = service_types

    def discover(self) -> tuple[DiscoveredDevice, ...]:
        candidates: dict[str, DiscoveredDevice] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            # Real mDNS responders reply as multicast to 224.0.0.251:5353
            # regardless of where the query came from (RFC 6762) -- the
            # socket has to be bound to that port and joined to the group to
            # actually receive them, not just send the query.
            sock.bind(("", MDNS_PORT))
            membership = socket.inet_aton(MDNS_MULTICAST_ADDR) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.settimeout(self._timeout)
            for service_type in self._service_types:
                sock.sendto(build_ptr_query(service_type), (MDNS_MULTICAST_ADDR, MDNS_PORT))
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                for candidate in parse_dns_response(data, source_ip=addr[0], now=datetime.now(timezone.utc)):
                    candidates[candidate.candidate_id] = candidate
        finally:
            sock.close()
        return tuple(candidates.values())


__all__ = [
    "DEFAULT_SERVICE_TYPES",
    "MDNS_MULTICAST_ADDR",
    "MDNS_PORT",
    "MdnsDiscoveryProvider",
    "WIFI_MDNS_PROVIDER_ID",
    "build_ptr_query",
    "parse_dns_response",
]
