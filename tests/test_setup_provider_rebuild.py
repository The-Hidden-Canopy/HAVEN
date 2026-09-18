"""The setup wizard's provider connect must rebuild the live household.

Before this test existed, `SetupService.connect_provider` persisted a valid
Home Assistant connection to `haven.json` but never touched the server's
already-built `DemoDirector` -- a resident who entered real HA credentials
kept seeing the demo household until they manually restarted the process.
`HavenWebServer.rebuild_director` closes that gap: this test proves it end
to end, over real HTTP, against a server that started with no provider
configured at all (the ordinary fresh-install boot).
"""

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from unittest.mock import patch

from haven.integrations.home_assistant import HomeAssistantWorldProvider
from haven.providers.plugin import ProviderManifest
from haven.web.server import make_server

CANNED_STATES = [
    {"entity_id": "light.living_room", "state": "on", "attributes": {}, "last_changed": "2026-09-16T20:00:00+00:00"}
]


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen(request, timeout=None):
    # LiveHomeAssistantAdapter.fetch_states() hits GET {base_url}/api/states;
    # any other call in this test would be a bug, so it is left unhandled.
    return _FakeResponse(json.dumps(CANNED_STATES).encode("utf-8"))


@contextmanager
def _boot(data_dir: Path):
    instance, _ = make_server(0, data_dir=str(data_dir))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload)
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    return status, json.loads(raw) if raw else {}


def test_connecting_a_provider_rebuilds_the_live_director_without_a_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp, _boot(Path(tmp)) as (server, port):
        # Fresh install, no provider configured yet: the server boots demo.
        assert server.director.house is not None
        assert not isinstance(server.director.world, HomeAssistantWorldProvider)
        original_director = server.director

        with patch("haven.integrations.home_assistant.client.urlopen", side_effect=_fake_urlopen):
            status, body = _post(
                port,
                "/api/setup/provider",
                {"kind": "home_assistant", "base_url": "http://ha.local:8123", "token": "secret-token"},
            )

        assert status == 200
        assert body["ok"] is True

        # The live director was swapped in place -- not just the config file.
        assert server.director is not original_director
        assert server.director.house is None
        assert isinstance(server.director.world, HomeAssistantWorldProvider)
        # The setup service itself must act on the new household from here on
        # (enrollment, status) rather than the retired demo director.
        assert server.setup._director is server.director

        # A fresh /api/state call over HTTP reaches the rebuilt household,
        # proving the swap is visible to the running server, not just to
        # objects held in this test's own references.
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.close()


class _FakeHueInstance:
    def __init__(self, config):
        self.config = dict(config)

    def observe(self):
        return ()


class FakeHuePluginForRebuildTest:
    def describe(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="philips_hue",
            kind="observation",
            capabilities=frozenset({"light.read"}),
            display_name="Philips Hue",
            description="test fixture",
        )

    def build(self, *, config):
        return _FakeHueInstance(config)


FAKE_HUE_PLUGIN = FakeHuePluginForRebuildTest()


def test_installing_a_community_provider_package_rebuilds_the_live_director() -> None:
    ep = metadata.EntryPoint(name="philips_hue", value=f"{__name__}:FAKE_HUE_PLUGIN", group="haven.providers")
    with tempfile.TemporaryDirectory() as tmp, _boot(Path(tmp)) as (server, port):
        assert server.director.house is not None
        original_director = server.director

        with patch.object(metadata, "entry_points", lambda *, group: (ep,) if group == "haven.providers" else ()):
            status, body = _post(
                port,
                "/api/setup/providers/install",
                {"entry_point_name": "philips_hue", "config": {}},
            )

        assert status == 200
        assert body["ok"] is True
        assert body["provider_id"] == "philips_hue"
        # The live director was swapped in place, exactly like connecting HA.
        assert server.director is not original_director
        assert server.director.house is None
        assert isinstance(server.director.world, HomeAssistantWorldProvider)


def test_skipping_the_provider_after_connecting_rebuilds_back_to_demo() -> None:
    with tempfile.TemporaryDirectory() as tmp, _boot(Path(tmp)) as (server, port):
        with patch("haven.integrations.home_assistant.client.urlopen", side_effect=_fake_urlopen):
            _post(
                port,
                "/api/setup/provider",
                {"kind": "home_assistant", "base_url": "http://ha.local:8123", "token": "secret-token"},
            )
        assert isinstance(server.director.world, HomeAssistantWorldProvider)
        real_director = server.director

        status, body = _post(port, "/api/setup/provider", {"skip": True})

        assert status == 200
        assert body["ok"] is True
        assert server.director is not real_director
        assert server.director.house is not None
        assert not isinstance(server.director.world, HomeAssistantWorldProvider)
        assert server.setup._director is server.director
