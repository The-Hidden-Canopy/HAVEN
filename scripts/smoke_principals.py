"""Live smoke: principals derive from declared people in real mode."""
import json
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile

from haven.devices.manifest import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.server import make_server

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)


class StubHA(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"[]" if self.path == "/api/states" else b""
        self.send_response(200 if self.path == "/api/states" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")


def get(path, port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.load(r)


def post(path, port, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    ha = ThreadingHTTPServer(("127.0.0.1", 0), StubHA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        changed = (NOW - timedelta(seconds=30)).isoformat()
        manifest = DeviceManifest(
            device_id="light.office_desk", device_type="light",
            provider_id="home_assistant",
            capabilities=(CapabilityDescriptor(
                "power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),),
            room="office", semantic_role="light")
        (root / "enrolled_devices.json").write_text(json.dumps(
            {"version": 2, "manifests": [manifest.to_dict()]}))
        (root / "haven.json").write_text(json.dumps({
            "version": 1, "completed": True, "data_dir": str(root),
            "provider_kind": "home_assistant",
            "provider_base_url": f"http://127.0.0.1:{ha.server_address[1]}",
            "provider_token_file": "ha_token.txt",
            "voice_enabled": True, "intelligence_enabled": True}))
        (root / "ha_token.txt").write_text("stub")
        (root / "household.json").write_text(json.dumps({
            "version": 1,
            "people": [{"person_id": "ada_lovelace", "name": "Ada Lovelace",
                        "role": "owner",
                        "sources": [{"entity_id": "binary_sensor.ada_office", "room_id": "office"}]}],
            "contexts": []}))

        server, director = make_server(8134, clock=lambda: NOW, data_dir=root)
        assert director.resident.actor_id == "ada_lovelace", director.resident.actor_id
        assert director.owner.actor_id == "ada_lovelace"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            post("/api/devices/light.office_desk/command", 8134, {"service": "light.turn_off"})
            st = get("/api/state", 8134)
            acts = [a for a in st["activity"] if a.get("event_type") == "action_executed"]
            chain = get(f"/api/actions/chain?event_id={acts[-1]['event_id']}", 8134)
            actor = chain["chain"]["request"]["actor"]
            print("acted as:", actor)
            assert actor == "ada_lovelace", f"expected ada_lovelace, got {actor}"
            print("SMOKE OK — HAVEN acts as the declared owner")
        finally:
            server.shutdown()
            server.server_close()
            ha.shutdown()


if __name__ == "__main__":
    main()
