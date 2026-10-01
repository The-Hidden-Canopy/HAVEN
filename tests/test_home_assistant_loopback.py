"""Real loopback acceptance for the Home Assistant REST adapter.

The provider's external account and network remain deployment gates. This
test nevertheless exercises the production urllib transport against a real
local HTTP server, so request routing, bearer authentication, state parsing,
and service payloads are not proved only by mocking ``urlopen``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from haven.core.domain import DeviceCommand
from haven.integrations.home_assistant import LiveHomeAssistantAdapter


NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


class _HomeAssistantLoopbackHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    requests: list[tuple[str, str | None, dict]] = []
    states = [
        {
            "entity_id": "light.proof_room",
            "state": "on",
            "attributes": {"brightness": 128},
            "last_changed": "2026-10-01T14:59:00+00:00",
        }
    ]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write_json(self, status: int, body: object) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/api/states":
            self._write_json(404, {"error": "not found"})
            return
        self._write_json(200, self.states)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.append((self.path, self.headers.get("Authorization"), body))
        self._write_json(200, [])


def test_live_adapter_round_trips_states_and_service_over_real_loopback_http() -> None:
    _HomeAssistantLoopbackHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HomeAssistantLoopbackHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = LiveHomeAssistantAdapter(
            base_url=f"http://127.0.0.1:{server.server_port}",
            access_token="loopback-token",
        )

        assert adapter.fetch_states() == tuple(_HomeAssistantLoopbackHandler.states)
        result = adapter.execute(
            DeviceCommand(
                request_id="loopback-request",
                target_device_id="light.proof_room",
                service="light.turn_off",
                parameters=(("transition", 2),),
                requested_at=NOW,
            )
        )

        assert result.success is True
        assert result.detail == "http_200"
        assert _HomeAssistantLoopbackHandler.requests == [
            (
                "/api/services/light/turn_off",
                "Bearer loopback-token",
                {"entity_id": "light.proof_room", "transition": 2},
            )
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
