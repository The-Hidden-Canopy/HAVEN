"""First-run onboarding API: setup steps, persisted config, demo enrollment."""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haven.web.server import make_server
from haven.web.setup_config import default_data_dir

UTC = timezone.utc
FIXED_NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


@contextmanager
def _boot(data_dir: str | None = None, *, demo: bool = False):
    instance, director = make_server(0, data_dir=data_dir, clock=lambda: FIXED_NOW, demo=demo)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, director, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, body


def _post(port: int, path: str, payload: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload) if payload is not None else ""
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def test_initial_status_is_default_then_empty_data_dir_flips_source() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _get_json(port, "/api/setup")
            assert status == 200
            assert body["ok"] is True
            setup = body["setup"]
            assert setup["completed"] is False
            assert setup["data_dir"] == {"source": "default", "resolved": str(default_data_dir())}
            assert setup["provider"] == {"configured": False, "kind": None, "base_url": None}
            assert setup["discovery"] == {"candidates": [], "enrolled": []}
            assert setup["preferences"] == {"voice": False, "intelligence": False}
            assert "config_error" not in body

            # An empty body resolves to the default dir and records the choice.
            status, body = _post(port, "/api/setup/data-dir", {})
            assert status == 200
            assert body["ok"] is True
            assert body["setup"]["data_dir"]["source"] == "chosen"
            assert Path(body["setup"]["data_dir"]["resolved"]) == default_data_dir()


def test_choose_data_dir_accepts_a_directory_and_rejects_a_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "chosen"
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(port, "/api/setup/data-dir", {"path": str(target)})
            assert status == 200
            assert body["ok"] is True
            assert Path(body["setup"]["data_dir"]["resolved"]) == target.resolve()
            assert target.is_dir()
            _, body = _get_json(port, "/api/setup")
            assert body["setup"]["data_dir"]["source"] == "chosen"

            blocker = Path(tmp) / "a-file"
            blocker.write_text("occupied", encoding="utf-8")
            status, body = _post(port, "/api/setup/data-dir", {"path": str(blocker)})
            assert status == 400
            assert body["ok"] is False
            assert "file" in body["error"]


def test_provider_skip_unsupported_kind_and_unreachable_host() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(port, "/api/setup/provider", {"kind": None, "skip": True})
            assert status == 200
            assert body["ok"] is True
            assert body["setup"]["provider"] == {"configured": False, "kind": None, "base_url": None}

            status, body = _post(
                port,
                "/api/setup/provider",
                {"kind": "mqtt", "base_url": "http://broker.local", "token": "t"},
            )
            assert status == 400
            assert body["ok"] is False
            assert "unsupported provider kind" in body["error"]

            # Closed port: connection refused, reported honestly, no token.
            status, body = _post(
                port,
                "/api/setup/provider",
                {
                    "kind": "home_assistant",
                    "base_url": "http://127.0.0.1:9/",
                    "token": "secret-token",
                },
            )
            assert status == 400
            assert body["ok"] is False
            assert "connection" in body["error"]
            assert "secret-token" not in body["error"]
            _, body = _get_json(port, "/api/setup")
            assert body["setup"]["provider"] == {"configured": False, "kind": None, "base_url": None}


def test_discovery_scan_enroll_and_registry() -> None:
    # Exercises the scan -> enroll -> registry mechanics (already-enrolled,
    # unknown-candidate, unsupported-device-type checks) against the demo
    # fixture candidate set as convenient, stable test data -- explicitly a
    # demo run: a real (non-demo) fresh install shows no scan candidates
    # until a real provider or discovery source exists (see
    # `test_real_install_scan_has_no_demo_candidates` below).
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data"), demo=True) as (instance, _, port):
            status, body = _post(port, "/api/setup/discovery/scan")
            assert status == 200
            assert body["ok"] is True
            candidates = {candidate["candidate_id"]: candidate for candidate in body["candidates"]}
            assert len(candidates) == 3
            bulb = candidates["ble:bulb-a1f2"]
            assert bulb["source"] == "demo.scan.ble"
            assert bulb["suggested_device_type"] == "light"
            assert bulb["suggested_room"] == "office"
            assert bulb["signal_strength"] == -52
            assert bulb["enrolled"] is False
            assert candidates["mdns:therm-living"]["suggested_device_type"] == "thermostat"
            assert candidates["mdns:therm-living"]["suggested_room"] == "living_room"
            assert candidates["mdns:therm-living"]["signal_strength"] is None
            assert candidates["wifi:plug-heater"]["suggested_device_type"] == "switch"
            assert candidates["wifi:plug-heater"]["suggested_room"] == "bedroom"

            status, body = _post(
                port,
                "/api/setup/enroll",
                {"candidate_id": "ble:bulb-a1f2", "device_type": "light", "room": "office"},
            )
            assert status == 200
            assert body["ok"] is True
            enrolled = body["setup"]["discovery"]["enrolled"]
            assert len(enrolled) == 1
            assert enrolled[0]["candidate_id"] == "ble:bulb-a1f2"
            assert enrolled[0]["device_id"] == "ble:bulb-a1f2"
            assert enrolled[0]["device_type"] == "light"

            manifest = instance.director.registry.get("ble:bulb-a1f2")
            assert manifest.room == "office"
            assert manifest.semantic_role == "light"
            assert manifest.has_capability("power", writable=True)
            assert manifest.has_capability("brightness", writable=True)

            # A fresh scan merges the enrollment state into the candidates.
            _, body = _post(port, "/api/setup/discovery/scan")
            candidates = {candidate["candidate_id"]: candidate for candidate in body["candidates"]}
            assert candidates["ble:bulb-a1f2"]["enrolled"] is True
            assert candidates["wifi:plug-heater"]["enrolled"] is False

            status, body = _post(
                port,
                "/api/setup/enroll",
                {"candidate_id": "ble:bulb-a1f2", "device_type": "light"},
            )
            assert status == 400
            assert body["ok"] is False
            assert "already enrolled" in body["error"]

            status, body = _post(
                port,
                "/api/setup/enroll",
                {"candidate_id": "attic:sensor", "device_type": "light"},
            )
            assert status == 400
            assert body["ok"] is False
            assert "unknown candidate" in body["error"]

            status, body = _post(
                port,
                "/api/setup/enroll",
                {"candidate_id": "wifi:plug-heater", "device_type": "toaster"},
            )
            assert status == 400
            assert body["ok"] is False
            assert "unsupported device_type" in body["error"]


def test_real_install_scan_has_no_demo_candidates() -> None:
    """A real (non-demo) fresh install must never show the demo fixture
    devices as if they were real nearby hardware -- see
    `test_discovery_scan_enroll_and_registry` for the demo-mode case."""

    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(port, "/api/setup/discovery/scan")
            assert status == 200
            assert body["ok"] is True
            assert body["candidates"] == []


def test_preferences_validate_bools_and_reflect_in_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(port, "/api/setup/preferences", {"voice": True, "intelligence": False})
            assert status == 200
            assert body["ok"] is True
            assert body["setup"]["preferences"] == {"voice": True, "intelligence": False}
            _, body = _get_json(port, "/api/setup")
            assert body["setup"]["preferences"]["voice"] is True

            status, body = _post(port, "/api/setup/preferences", {"voice": "yes", "intelligence": False})
            assert status == 400
            assert body["ok"] is False


def test_declaring_a_person_forwards_the_chosen_role() -> None:
    """The household step's role picker (`personRole.value` in app.js)
    always posts a `role` -- this used to be silently dropped, so choosing
    "Owner" in the UI had no effect and a real installation could never
    satisfy `complete()`'s owner-declared requirement."""

    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(
                port, "/api/setup/household/people", {"name": "Gerron Smith", "role": "owner"}
            )
            assert status == 200
            assert body["ok"] is True
            people = body["setup"]["household"]["people"]
            assert any(p["name"] == "Gerron Smith" and p["role"] == "owner" for p in people)


def test_declaring_a_person_without_a_role_defaults_to_member() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(str(Path(tmp) / "data")) as (_, _, port):
            status, body = _post(port, "/api/setup/household/people", {"name": "Riley"})
            assert status == 200
            people = body["setup"]["household"]["people"]
            assert any(p["name"] == "Riley" and p["role"] == "member" for p in people)


def test_setup_state_persists_across_server_restarts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = str(Path(tmp) / "data")
        with _boot(data_dir) as (_, _, port):
            _post(port, "/api/setup/provider", {"kind": None, "skip": True})
            _post(port, "/api/setup/discovery/scan")
            status, body = _post(
                port,
                "/api/setup/enroll",
                {"candidate_id": "ble:bulb-a1f2", "device_type": "light", "room": "office"},
            )
            assert status == 200
            _post(port, "/api/setup/preferences", {"voice": True, "intelligence": False})
            status, body = _post(port, "/api/setup/complete")
            assert status == 200
            assert body["setup"]["completed"] is True

        with _boot(data_dir) as (_, _, port):
            status, body = _get_json(port, "/api/setup")
            assert status == 200
            assert "config_error" not in body
            setup = body["setup"]
            assert setup["completed"] is True
            assert setup["provider"] == {"configured": False, "kind": None, "base_url": None}
            assert setup["preferences"] == {"voice": True, "intelligence": False}
            enrolled = setup["discovery"]["enrolled"]
            assert len(enrolled) == 1
            assert enrolled[0]["candidate_id"] == "ble:bulb-a1f2"
            assert enrolled[0]["device_id"] == "ble:bulb-a1f2"
            assert enrolled[0]["device_type"] == "light"
            assert enrolled[0]["room"] == "office"
            # The candidate list stays scan-scoped: empty until the first scan
            # in this server process, while enrollments always show.
            assert setup["discovery"]["candidates"] == []

            status, body = _post(port, "/api/setup/reopen")
            assert status == 200
            assert body["setup"]["completed"] is False


def test_broken_config_boots_with_a_config_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "haven.json").write_bytes(b"\x00 not json \xff")
        with _boot(str(data_dir)) as (_, _, port):
            status, body = _get_json(port, "/api/setup")
            assert status == 200
            assert body["ok"] is True
            assert "config_error" in body
            assert body["setup"]["completed"] is False
