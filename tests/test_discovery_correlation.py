"""Candidate correlation: grouping DiscoveredDevice candidates that share a
source_ip, so a native Discover view can notice "SSDP and mDNS just
described the same physical device" (native product-consolidation plan,
P0 "Discovery")."""

from __future__ import annotations

from datetime import datetime, timezone

from haven.discovery import DiscoveredDevice, correlate

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _device(candidate_id: str, *, provider_id: str, source: str, source_ip: str | None) -> DiscoveredDevice:
    return DiscoveredDevice(
        candidate_id=candidate_id,
        provider_id=provider_id,
        discovered_at=NOW,
        source=source,
        source_ip=source_ip,
    )


def test_candidates_sharing_an_ip_land_in_one_group():
    ssdp = _device("wifi-ssdp:uuid-1", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.50")
    mdns = _device("wifi-mdns:tv._airplay", provider_id="wifi-mdns", source="wifi.mdns", source_ip="192.168.1.50")

    groups = correlate((ssdp, mdns))

    assert groups[ssdp.candidate_id] is groups[mdns.candidate_id]
    assert groups[ssdp.candidate_id].is_correlated
    assert set(groups[ssdp.candidate_id].candidate_ids) == {ssdp.candidate_id, mdns.candidate_id}


def test_candidates_with_different_ips_stay_in_separate_groups():
    a = _device("wifi-ssdp:a", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.50")
    b = _device("wifi-ssdp:b", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.51")

    groups = correlate((a, b))

    assert groups[a.candidate_id] is not groups[b.candidate_id]
    assert not groups[a.candidate_id].is_correlated
    assert not groups[b.candidate_id].is_correlated


def test_a_candidate_with_no_source_ip_gets_its_own_singleton_group():
    bluetooth = _device("bt:aa:bb:cc", provider_id="bluetooth", source="bluetooth", source_ip=None)

    groups = correlate((bluetooth,))

    group = groups[bluetooth.candidate_id]
    assert group.candidate_ids == (bluetooth.candidate_id,)
    assert not group.is_correlated


def test_a_shared_ip_never_merges_with_an_ip_less_candidate():
    ssdp = _device("wifi-ssdp:a", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.50")
    bluetooth = _device("bt:aa:bb:cc", provider_id="bluetooth", source="bluetooth", source_ip=None)

    groups = correlate((ssdp, bluetooth))

    assert groups[ssdp.candidate_id] is not groups[bluetooth.candidate_id]


def test_empty_scan_correlates_to_nothing():
    assert correlate(()) == {}
