"""Native discovery IPC adapter: everyday scan/enroll over injected fixture transports.

`self.discovery` is swapped for a fixture-backed `DiscoveryService` in each
test rather than left on `default_discovery_providers()`'s real SSDP/BLE
transports -- this file is about the IPC adapter's own plumbing (params
validation, event emission, response shape), not about any real transport.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.discovery import DiscoveredDevice, FixtureDiscoveryProvider
from haven.ipc import request_message
from haven.web.discovery_service import DiscoveryService
from haven.web.server import make_server

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


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


@pytest.fixture()
def server():
    with tempfile.TemporaryDirectory() as tmp:
        instance, director = make_server(0, data_dir=Path(tmp) / "data", clock=lambda: NOW, demo=True)
        try:
            yield instance, director
        finally:
            instance.server_close()


def _dispatch(server, method: str, params: dict) -> dict:
    return server.build_ipc_dispatcher()(request_message(f"req-{method}", method, params))


def _use_fixture_transport(instance, *candidates: DiscoveredDevice) -> None:
    instance.discovery = DiscoveryService(
        director=instance.director, providers=(FixtureDiscoveryProvider(candidates),)
    )


def test_discovery_scan_returns_candidates_from_the_configured_transports(server) -> None:
    instance, _ = server
    _use_fixture_transport(instance, _candidate("a"), _candidate("b", suggested_device_type="thermostat"))

    response = _dispatch(instance, "discovery.scan", {})

    assert response["ok"] is True
    ids = {row["candidate_id"] for row in response["result"]["candidates"]}
    assert ids == {"a", "b"}


def test_discovery_candidates_reflects_the_last_scan_without_rescanning(server) -> None:
    instance, _ = server
    _use_fixture_transport(instance, _candidate("a"))
    _dispatch(instance, "discovery.scan", {})

    response = _dispatch(instance, "discovery.candidates", {})

    assert response["ok"] is True
    assert [row["candidate_id"] for row in response["result"]["candidates"]] == ["a"]


def test_discovery_enroll_end_to_end_marks_the_candidate_enrolled(server) -> None:
    instance, director = server
    _use_fixture_transport(instance, _candidate("a", suggested_room="office"))
    _dispatch(instance, "discovery.scan", {})

    response = _dispatch(instance, "discovery.enroll", {"candidate_id": "a", "device_type": "light"})

    assert response["ok"] is True
    row = next(r for r in response["result"]["candidates"] if r["candidate_id"] == "a")
    assert row["enrolled"] is True
    assert director.registry.get("a").room == "office"


def test_discovery_enroll_lets_the_household_override_the_room(server) -> None:
    instance, director = server
    _use_fixture_transport(instance, _candidate("a", suggested_room="office"))
    _dispatch(instance, "discovery.scan", {})

    _dispatch(instance, "discovery.enroll", {"candidate_id": "a", "device_type": "light", "room": "bedroom"})

    assert director.registry.get("a").room == "bedroom"


def test_discovery_enroll_rejects_missing_candidate_id_at_the_adapter(server) -> None:
    instance, _ = server

    response = _dispatch(instance, "discovery.enroll", {"device_type": "light"})

    assert response["ok"] is False
    assert "candidate_id" in response["error"]


def test_discovery_enroll_rejects_missing_device_type_at_the_adapter(server) -> None:
    instance, _ = server
    _use_fixture_transport(instance, _candidate("a"))
    _dispatch(instance, "discovery.scan", {})

    response = _dispatch(instance, "discovery.enroll", {"candidate_id": "a"})

    assert response["ok"] is False
    assert "device_type" in response["error"]


def test_discovery_enroll_rejects_an_unsupported_device_type(server) -> None:
    instance, _ = server
    _use_fixture_transport(instance, _candidate("a"))
    _dispatch(instance, "discovery.scan", {})

    response = _dispatch(instance, "discovery.enroll", {"candidate_id": "a", "device_type": "unknown-vendor-blob"})

    assert response["ok"] is True  # the adapter call succeeded; the business result failed
    assert response["result"]["ok"] is False
    assert "unsupported device_type" in response["result"]["error"]


def test_discovery_enroll_emits_home_state_changed_on_success(server) -> None:
    instance, _ = server
    _use_fixture_transport(instance, _candidate("a"))
    _dispatch(instance, "discovery.scan", {})
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    _dispatch(instance, "discovery.enroll", {"candidate_id": "a", "device_type": "light"})

    assert "home.state.changed" in events


def test_discovery_enroll_emits_nothing_when_the_adapter_rejects_the_request(server) -> None:
    # Adapter-level validation failures raise before the handler returns, so
    # `_with_event` never reaches its own emit call -- unlike a *business*
    # failure (unsupported device_type), which the handler still returns as
    # an ok:false result and therefore still emits (the same framework-wide
    # behavior every other IPC mutation already has: `_with_event` only
    # distinguishes a raised exception from a returned result, not a
    # returned result's own ok flag).
    instance, _ = server
    events: list[str] = []
    instance._emit_event = lambda name, **_kwargs: events.append(name)  # noqa: SLF001

    _dispatch(instance, "discovery.enroll", {"device_type": "light"})  # missing candidate_id

    assert events == []


def test_discovery_enrollment_survives_a_real_server_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        instance, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            declared = instance.setup.declare_person(name="Owner", role="owner")
            assert declared["ok"] is True
            instance.discovery = DiscoveryService(
                director=instance.director,
                providers=(FixtureDiscoveryProvider((_candidate("a"),)),),
                persist_enrollment=instance.setup.persist_discovery_enrollment,
            )
            _dispatch(instance, "discovery.scan", {})
            enrolled = _dispatch(
                instance,
                "discovery.enroll",
                {"candidate_id": "a", "device_type": "light"},
            )
            assert enrolled["ok"] is True
            sidecar = json.loads((data_dir / "enrolled_devices.json").read_text(encoding="utf-8"))
            assert sidecar["version"] == 2
            assert sidecar["manifests"][0]["device_id"] == "a"
        finally:
            instance.server_close()

        reopened, _ = make_server(0, data_dir=data_dir, clock=lambda: NOW)
        try:
            assert reopened.director.registry.is_registered("a")
            assert reopened.director.registry.get("a").room == "office"
        finally:
            reopened.server_close()
