"""mDNS/DNS-SD discovery: real UDP multicast + real (hand-decoded) DNS wire
format, tested without any real network.

`parse_dns_response()`/`build_ptr_query()` are pure and tested directly with
hand-built DNS packets (including a compressed name, the case that actually
breaks naive decoders). `MdnsDiscoveryProvider.discover()` is the only
method that touches a socket, mocked the same way `test_wifi_ssdp.py` mocks
`socket.socket`.
"""

from __future__ import annotations

import socket
import struct
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from haven.discovery import DiscoveredDevice
from haven.integrations.wifi.mdns import (
    MDNS_MULTICAST_ADDR,
    MDNS_PORT,
    MdnsDiscoveryProvider,
    WIFI_MDNS_PROVIDER_ID,
    _decode_name,
    _encode_name,
    build_ptr_query,
    parse_dns_response,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _response_header(*, ancount: int) -> bytes:
    # id=0, flags=0x8400 (QR=1 response, AA=1 authoritative -- typical mDNS),
    # qdcount=0 (a real reply usually echoes the question too, but the
    # parser must not require it), ancount as given, ns/ar=0.
    return struct.pack(">HHHHHH", 0, 0x8400, 0, ancount, 0, 0)


def _ptr_answer(name: str, target: str) -> bytes:
    rdata = _encode_name(target)
    return (
        _encode_name(name)
        + struct.pack(">HHIH", 12, 1, 120, len(rdata))  # type=PTR, class=IN, ttl=120
        + rdata
    )


def test_encode_decode_name_round_trips():
    encoded = _encode_name("_airplay._tcp.local")
    name, end = _decode_name(encoded, 0)
    assert name == "_airplay._tcp.local"
    assert end == len(encoded)


def test_decode_name_follows_a_compression_pointer():
    # A packet where the second name is a pointer back to the first --
    # exactly what real mDNS responses do constantly (RFC 1035 §4.1.4).
    first = _encode_name("_airplay._tcp.local")
    pointer = struct.pack(">H", 0xC000 | 0)  # point at offset 0
    message = first + pointer
    name, end = _decode_name(message, len(first))
    assert name == "_airplay._tcp.local"
    assert end == len(first) + 2  # right after the 2-byte pointer, not the target


def test_build_ptr_query_shape():
    query = build_ptr_query("_airplay._tcp.local")
    _id, flags, qdcount, ancount, nscount, arcount = struct.unpack(">HHHHHH", query[:12])
    assert (flags, qdcount, ancount, nscount, arcount) == (0, 1, 0, 0, 0)
    name, offset = _decode_name(query, 12)
    assert name == "_airplay._tcp.local"
    qtype, qclass = struct.unpack(">HH", query[offset : offset + 4])
    assert (qtype, qclass) == (12, 1)


def test_parse_dns_response_decodes_a_ptr_answer():
    message = _response_header(ancount=1) + _ptr_answer(
        "_airplay._tcp.local", "Living Room._airplay._tcp.local"
    )
    candidates = parse_dns_response(message, source_ip="192.168.1.50", now=NOW)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, DiscoveredDevice)
    assert candidate.provider_id == WIFI_MDNS_PROVIDER_ID
    assert candidate.source == "wifi.mdns"
    assert candidate.suggested_device_type == "airplay"
    assert "Living Room" in candidate.candidate_id


def test_parse_dns_response_decodes_a_compressed_ptr_target():
    # The PTR rdata points back at the question name via compression --
    # a real, common shape for self-referential-looking mDNS answers.
    header = _response_header(ancount=1)
    name = _encode_name("_hap._tcp.local")
    pointer_to_name = struct.pack(">H", 0xC000 | len(header))
    answer = name + struct.pack(">HHIH", 12, 1, 120, 2) + pointer_to_name
    message = header + answer

    candidates = parse_dns_response(message, source_ip="192.168.1.51", now=NOW)
    assert len(candidates) == 1
    assert candidates[0].candidate_id.endswith("_hap._tcp.local")


def test_parse_dns_response_ignores_non_ptr_answers():
    header = _response_header(ancount=1)
    name = _encode_name("device.local")
    a_record_rdata = socket.inet_aton("192.168.1.60")
    answer = name + struct.pack(">HHIH", 1, 1, 120, len(a_record_rdata)) + a_record_rdata  # type=A
    message = header + answer

    assert parse_dns_response(message, source_ip="192.168.1.60", now=NOW) == ()


def test_parse_dns_response_ignores_queries_not_responses():
    query = build_ptr_query("_airplay._tcp.local")
    assert parse_dns_response(query, source_ip="192.168.1.1", now=NOW) == ()


def test_parse_dns_response_returns_empty_on_malformed_input():
    assert parse_dns_response(b"\x00\x01", source_ip="192.168.1.1", now=NOW) == ()
    assert parse_dns_response(b"", source_ip="192.168.1.1", now=NOW) == ()


def test_provider_requires_a_positive_timeout():
    with pytest.raises(ValueError):
        MdnsDiscoveryProvider(timeout=0)


def test_provider_requires_at_least_one_service_type():
    with pytest.raises(ValueError):
        MdnsDiscoveryProvider(service_types=())


def test_discover_queries_every_configured_service_type_and_dedupes():
    provider = MdnsDiscoveryProvider(timeout=1.0, service_types=("_airplay._tcp.local", "_hap._tcp.local"))
    fake_sock = MagicMock()
    message = _response_header(ancount=1) + _ptr_answer(
        "_airplay._tcp.local", "Kitchen._airplay._tcp.local"
    )
    responses = [(message, ("192.168.1.50", MDNS_PORT)), (message, ("192.168.1.50", MDNS_PORT))]

    def recvfrom(_bufsize):
        if responses:
            return responses.pop(0)
        raise socket.timeout()

    fake_sock.recvfrom.side_effect = recvfrom

    with patch("haven.integrations.wifi.mdns.socket.socket", return_value=fake_sock):
        found = provider.discover()

    assert len(found) == 1  # deduplicated by candidate_id
    assert fake_sock.sendto.call_count == 2  # one query per configured service type
    fake_sock.bind.assert_called_once_with(("", MDNS_PORT))
    fake_sock.setsockopt.assert_any_call(
        socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNS_MULTICAST_ADDR) + socket.inet_aton("0.0.0.0")
    )
    fake_sock.close.assert_called_once()


def test_discover_returns_empty_when_nothing_responds():
    provider = MdnsDiscoveryProvider(timeout=1.0)
    fake_sock = MagicMock()
    fake_sock.recvfrom.side_effect = socket.timeout()

    with patch("haven.integrations.wifi.mdns.socket.socket", return_value=fake_sock):
        found = provider.discover()

    assert found == ()
