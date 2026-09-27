"""DiscoveryService: everyday discovery over real transports, governed enrollment.

Providers are `FixtureDiscoveryProvider`s here -- the point of this file is
the service's own aggregation/enrollment logic, not any one transport (SSDP
has its own `test_wifi_ssdp.py`; enrollment's own rules have `test_discovery.py`).
"""

from datetime import datetime, timezone
from unittest.mock import patch

from haven.discovery import DiscoveredDevice, FixtureDiscoveryProvider
from haven.web.demo import DemoDirector
from haven.web.discovery_service import DiscoveryService, default_discovery_providers

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def _candidate(candidate_id: str, **overrides) -> DiscoveredDevice:
    defaults = dict(
        candidate_id=candidate_id,
        provider_id="fixture",
        discovered_at=NOW,
        source="fixture.scan",
        suggested_device_type="light",
        suggested_room="office",
    )
    defaults.update(overrides)
    return DiscoveredDevice(**defaults)


class _RaisingProvider:
    def discover(self):
        raise RuntimeError("transport unavailable")


def _service(*providers, director=None) -> DiscoveryService:
    return DiscoveryService(director=director or DemoDirector(clock=lambda: NOW), providers=providers)


def test_scan_aggregates_candidates_across_providers():
    service = _service(
        FixtureDiscoveryProvider((_candidate("a"),)),
        FixtureDiscoveryProvider((_candidate("b"),)),
    )

    body = service.scan()

    assert body["ok"] is True
    ids = {row["candidate_id"] for row in body["candidates"]}
    assert ids == {"a", "b"}


def test_scan_deduplicates_by_candidate_id_last_provider_wins():
    service = _service(
        FixtureDiscoveryProvider((_candidate("a", suggested_room="office"),)),
        FixtureDiscoveryProvider((_candidate("a", suggested_room="bedroom"),)),
    )

    body = service.scan()

    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["suggested_room"] == "bedroom"


def test_a_failing_transport_never_blanks_the_others():
    service = _service(_RaisingProvider(), FixtureDiscoveryProvider((_candidate("a"),)))

    body = service.scan()

    assert body["ok"] is True
    assert [row["candidate_id"] for row in body["candidates"]] == ["a"]


def test_candidates_returns_the_last_scan_without_rescanning():
    provider = FixtureDiscoveryProvider((_candidate("a"),))
    service = _service(provider)
    service.scan()

    # A provider that would now return something different if re-invoked;
    # candidates() must not call discover() again.
    provider._candidates = (_candidate("b"),)

    body = service.candidates()

    assert [row["candidate_id"] for row in body["candidates"]] == ["a"]


def test_view_marks_supported_from_the_shared_capability_presets():
    service = _service(
        FixtureDiscoveryProvider(
            (
                _candidate("a", suggested_device_type="light"),
                _candidate("b", suggested_device_type="unknown-vendor-blob"),
            )
        )
    )

    body = service.scan()
    by_id = {row["candidate_id"]: row for row in body["candidates"]}

    assert by_id["a"]["supported"] is True
    assert by_id["b"]["supported"] is False


def test_enroll_requires_a_declared_owner():
    director = DemoDirector(clock=lambda: NOW)
    director.has_declared_owner = False
    service = _service(FixtureDiscoveryProvider((_candidate("a"),)), director=director)
    service.scan()

    body = service.enroll("a", device_type="light")

    assert body["ok"] is False
    assert "declare a household owner" in body["error"]


def test_enroll_rejects_an_unknown_candidate():
    service = _service(FixtureDiscoveryProvider(()))

    body = service.enroll("nope", device_type="light")

    assert body["ok"] is False
    assert "unknown candidate" in body["error"]


def test_enroll_rejects_an_unsupported_device_type():
    service = _service(FixtureDiscoveryProvider((_candidate("a"),)))
    service.scan()

    body = service.enroll("a", device_type="unknown-vendor-blob")

    assert body["ok"] is False
    assert "unsupported device_type" in body["error"]


def test_enroll_registers_a_manifest_and_marks_the_candidate_enrolled():
    service = _service(FixtureDiscoveryProvider((_candidate("a", suggested_room="office"),)))
    service.scan()

    body = service.enroll("a", device_type="light")

    assert body["ok"] is True
    row = next(r for r in body["candidates"] if r["candidate_id"] == "a")
    assert row["enrolled"] is True
    manifest = service._director.registry.get("a")
    assert manifest.device_type == "light"
    assert manifest.room == "office"


def test_enroll_rejects_a_candidate_already_in_the_shared_registry():
    service = _service(FixtureDiscoveryProvider((_candidate("a"),)))
    service.scan()
    first = service.enroll("a", device_type="light")
    assert first["ok"] is True

    second = service.enroll("a", device_type="light")

    assert second["ok"] is False
    assert "already enrolled" in second["error"]


def test_enroll_lets_the_household_override_the_suggested_room():
    service = _service(FixtureDiscoveryProvider((_candidate("a", suggested_room="office"),)))
    service.scan()

    service.enroll("a", device_type="light", room="bedroom")

    assert service._director.registry.get("a").room == "bedroom"


def test_default_discovery_providers_always_includes_ssdp_and_mdns():
    from haven.integrations.wifi.mdns import MdnsDiscoveryProvider
    from haven.integrations.wifi.ssdp import SsdpDiscoveryProvider

    with patch("haven.integrations.bluetooth.CtypesBluetoothLibrary.load", side_effect=FileNotFoundError()):
        providers = default_discovery_providers()

    assert any(isinstance(provider, SsdpDiscoveryProvider) for provider in providers)
    assert any(isinstance(provider, MdnsDiscoveryProvider) for provider in providers)


def test_default_discovery_providers_degrades_gracefully_with_no_bluetooth_library():
    with patch("haven.integrations.bluetooth.CtypesBluetoothLibrary.load", side_effect=FileNotFoundError()):
        providers = default_discovery_providers()

    assert len(providers) == 2  # SSDP + mDNS only -- one fewer transport, not a crash


def test_default_discovery_providers_includes_bluetooth_when_the_library_loads():
    from haven.integrations.bluetooth import BluetoothProvider

    with patch("haven.integrations.bluetooth.CtypesBluetoothLibrary.load", return_value=object()):
        providers = default_discovery_providers()

    assert any(isinstance(provider, BluetoothProvider) for provider in providers)


def test_candidates_view_surfaces_cross_transport_correlation():
    ssdp_like = _candidate("wifi-ssdp:uuid-1", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.50")
    mdns_like = _candidate("wifi-mdns:tv", provider_id="wifi-mdns", source="wifi.mdns", source_ip="192.168.1.50")
    unrelated = _candidate("wifi-ssdp:uuid-2", provider_id="wifi-ssdp", source="wifi.ssdp", source_ip="192.168.1.60")

    service = _service(FixtureDiscoveryProvider((ssdp_like, mdns_like, unrelated)))
    result = service.scan()

    by_id = {row["candidate_id"]: row for row in result["candidates"]}
    assert by_id[ssdp_like.candidate_id]["correlated_with"] == (mdns_like.candidate_id,)
    assert by_id[mdns_like.candidate_id]["correlated_with"] == (ssdp_like.candidate_id,)
    assert by_id[unrelated.candidate_id]["correlated_with"] == ()
