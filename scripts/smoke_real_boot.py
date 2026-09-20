"""Live smoke: real-mode boot against a stub Home Assistant.

Proves the composition root: saved setup -> factory -> HA world/execution ->
authority -> room controls, plus HA-backed discovery in the setup scan.
"""
import json
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven.devices.manifest import CapabilityDescriptor, ControlClass, DeviceManifest
from haven.web.server import make_server

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)

# -- one mutable fake HA -----------------------------------------------
HA = {"states": [], "service_calls": []}


class StubHA(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/api/states":
            body = json.dumps(HA["states"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/api/services/"):
            n = int(self.headers.get("Content-Length", 0))
            HA["service_calls"].append((self.path, json.loads(self.rfile.read(n) or b"{}")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")
        else:
            self.send_response(404)
            self.end_headers()


def get(path, port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.load(r)


def post(path, port, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    import tempfile
    from pathlib import Path

    ha = ThreadingHTTPServer(("127.0.0.1", 0), StubHA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()
    ha_port = ha.server_address[1]

    changed = (NOW - timedelta(seconds=30)).isoformat()
    HA["states"] = [
        {"entity_id": "light.office_desk", "state": "on",
         "attributes": {"brightness": 128}, "last_changed": changed},
        {"entity_id": "cover.garage_door", "state": "open",
         "attributes": {}, "last_changed": changed},
        {"entity_id": "sensor.temp", "state": "21.5",
         "attributes": {}, "last_changed": changed},
        {"entity_id": "binary_sensor.gerron_office_occupancy", "state": "on",
         "attributes": {}, "last_changed": changed},
        {"entity_id": "input_boolean.working_late", "state": "on",
         "attributes": {}, "last_changed": changed},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = DeviceManifest(
            device_id="light.office_desk",
            device_type="light",
            provider_id="home_assistant",
            capabilities=(
                CapabilityDescriptor("power", ControlClass.LOW_RISK, writable=True, service="light.turn_off"),
            ),
            room="office",
            semantic_role="light",
        )
        (root / "enrolled_devices.json").write_text(json.dumps(
            {"version": 2, "manifests": [manifest.to_dict()]}))
        (root / "haven.json").write_text(json.dumps({
            "version": 1, "completed": True,
            "data_dir": str(root),
            "provider_kind": "home_assistant",
            "provider_base_url": f"http://127.0.0.1:{ha_port}",
            "provider_token_file": "ha_token.txt",
            "voice_enabled": True, "intelligence_enabled": True,
        }))
        (root / "ha_token.txt").write_text("stub-token")
        (root / "household.json").write_text(json.dumps({
            "version": 1,
            "people": [{"person_id": "gerron", "name": "Gerron", "sources": [
                {"entity_id": "binary_sensor.gerron_office_occupancy", "room_id": "office"}]}],
            "contexts": [{"context_id": "working_late", "label": "Working late",
                          "entity_id": "input_boolean.working_late"}],
        }))

        clock_state = {"now": NOW}
        server, director = make_server(
            8129, clock=lambda: clock_state["now"], data_dir=root)
        assert director.house is None, "expected real mode (no simulated house)"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            # 1. state renders from HA
            st = get("/api/state", 8129)
            room = next(r for r in st["rooms"] if r["id"] == "office")
            light = next(d for d in room["devices"] if d["id"] == "light.office_desk")
            print("1. world from HA: light.office_desk is_on =", light["is_on"],
                  "| source =", light.get("source"), "| rooms =", [r["id"] for r in st["rooms"]])

            # 2. room control routes authority -> HA adapter
            post("/api/devices/light.office_desk/command", 8129, {"service": "light.turn_off"})
            assert HA["service_calls"], "HA received no service call!"
            path_, payload_ = HA["service_calls"][-1]
            print("2. control -> HA:", path_, payload_.get("entity_id"))

            # 3. world re-observes after execution (stub now reports off)
            HA["states"][0]["state"] = "off"
            st = get("/api/state", 8129)
            room = next(r for r in st["rooms"] if r["id"] == "office")
            light = next(d for d in room["devices"] if d["id"] == "light.office_desk")
            print("3. re-observed after execution: is_on =", light["is_on"])

            # 4. discovery scan lists HA entities
            scan = post("/api/setup/discovery/scan", 8129, {})
            ids = [c["candidate_id"] for c in scan["candidates"]]
            print("4. scan candidates:", ids)
            assert "light.office_desk" in ids and "sensor.temp" not in ids

            # 5. declared presence reaches the UI (state re-fetched at step 3)
            people = st.get("people", [])
            ctxs = {c["id"]: c["active"] for c in st.get("contexts", [])}
            print("5. declared presence: people =", people,
                  "| working_late active =", ctxs.get("working_late"))
            assert any(p["name"] == "Gerron" and p["room"] == "office" for p in people)

            # 6. trust chain on the executed action
            acts = [a for a in st["activity"] if a.get("event_type") == "action_executed"]
            chain = get(f"/api/actions/chain?event_id={acts[-1]['event_id']}", 8129)
            c = chain["chain"]
            print("6. chain: service =", c["execution"]["service"],
                  "| success =", c["execution"]["success"],
                  "| origin =", c["request"]["origin"])
            print("SMOKE OK — real-mode boot works end to end")
        finally:
            server.shutdown()
            server.server_close()
            ha.shutdown()


if __name__ == "__main__":
    main()
