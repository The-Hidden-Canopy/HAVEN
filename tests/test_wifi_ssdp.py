"""SSDP discovery: real UPnP M-SEARCH, tested without any real network.

`parse_ssdp_response()` is pure and tested directly with fixed response
text. `SsdpDiscoveryProvider.discover()` is the only method that touches a
socket, so it is exercised here with `socket.socket` mocked out -- the same
pattern `test_home_assistant_client.py` uses for `urlopen`. Nothing that
runs `pytest` opens a socket.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from haven.discovery import DiscoveredDevice
from haven.integrations.wifi import WIFI_SSDP_PROVIDER_ID, parse_ssdp_response
from haven.integrations.wifi.ssdp import SsdpDiscoveryProvider, build_search_request

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)

_SONOS_RESPONSE = (
    "HTTP/1.1 200 OK\r\n"
    "LOCATION: http://192.168.1.42:1400/xml/device_description.xml\r\n"
    "SERVER: Linux UPnP/1.0 Sonos/60.1\r\n"
    "ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n"
    "USN: uuid:RINCON_ABC123::urn:schemas-upnp-org:device:ZonePlayer:1\r\n"
    "\r\n"
)


def test_parses_a_recognizable_ssdp_response():
    candidate = parse_ssdp_response(_SONOS_RESPONSE, source_ip="192.168.1.42", now=NOW)

    assert isinstance(candidate, DiscoveredDevice)
    assert candidate.provider_id == WIFI_SSDP_PROVIDER_ID
    assert candidate.source == "wifi.ssdp"
    assert candidate.suggested_device_type == "zoneplayer"
    assert candidate.discovered_at == NOW
    assert "RINCON_ABC123" in candidate.candidate_id


def test_response_with_no_location_or_usn_is_not_a_candidate():
    assert parse_ssdp_response("HTTP/1.1 200 OK\r\n\r\n", source_ip="192.168.1.5", now=NOW) is None


def test_unrecognized_st_still_produces_a_candidate_with_no_type_guess():
    response = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://192.168.1.7:80/desc.xml\r\n"
        "ST: upnp:rootdevice\r\n"
        "USN: uuid:some-device::upnp:rootdevice\r\n"
        "\r\n"
    )

    candidate = parse_ssdp_response(response, source_ip="192.168.1.7", now=NOW)

    assert candidate is not None
    assert candidate.suggested_device_type is None


def test_falls_back_to_location_when_usn_is_missing():
    response = "HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.9:80/desc.xml\r\nST: ssdp:all\r\n\r\n"

    candidate = parse_ssdp_response(response, source_ip="192.168.1.9", now=NOW)

    assert candidate is not None
    assert "192.168.1.9" in candidate.candidate_id or "desc.xml" in candidate.candidate_id


def test_provider_requires_a_positive_timeout():
    with pytest.raises(ValueError):
        SsdpDiscoveryProvider(timeout=0)


def test_discover_collects_and_deduplicates_responses_with_a_mocked_socket():
    provider = SsdpDiscoveryProvider(timeout=1.0)
    fake_sock = MagicMock()
    responses = [
        (_SONOS_RESPONSE.encode("utf-8"), ("192.168.1.42", 1400)),
        (_SONOS_RESPONSE.encode("utf-8"), ("192.168.1.42", 1400)),  # duplicate response
    ]

    def recvfrom(_bufsize):
        if responses:
            return responses.pop(0)
        raise __import__("socket").timeout()

    fake_sock.recvfrom.side_effect = recvfrom

    with patch("haven.integrations.wifi.ssdp.socket.socket", return_value=fake_sock):
        found = provider.discover()

    assert len(found) == 1  # deduplicated by candidate_id
    fake_sock.sendto.assert_called_once_with(build_search_request(), ("239.255.255.250", 1900))
    fake_sock.close.assert_called_once()


def test_discover_returns_empty_when_nothing_responds():
    provider = SsdpDiscoveryProvider(timeout=1.0)
    fake_sock = MagicMock()
    fake_sock.recvfrom.side_effect = __import__("socket").timeout()

    with patch("haven.integrations.wifi.ssdp.socket.socket", return_value=fake_sock):
        found = provider.discover()

    assert found == ()
