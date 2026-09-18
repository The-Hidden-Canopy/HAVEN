"""Live smoke: automations survive a restart in real mode."""
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
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
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


def boot(root, port):
    clock = {"now": NOW}
    server, director = make_server(port, clock=lambda: clock["now"], data_dir=root)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, director


def main():
    ha = ThreadingHTTPServer(("127.0.0.1", 0), StubHA)
    threading.Thread(target=ha.serve_forever, daemon=True).start()
    ha_port = ha.server_address[1]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
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
            "provider_base_url": f"http://127.0.0.1:{ha_port}",
            "provider_token_file": "ha_token.txt",
            "voice_enabled": True, "intelligence_enabled": True}))
        (root / "ha_token.txt").write_text("stub-token")

        # first boot: propose an automation through chat (the scripted floor
        # drafts its example phrase; it stays proposed because "don't blast"
        # is honestly unresolved — persistence covers every status)
        s1, d1 = boot(root, 8131)
        try:
            post("/api/chat", 8131,
                 {"text": "when i'm working late, don't blast the bedroom lights when i walk in."})
            st = get("/api/state", 8131)
            autos = st.get("automations", [])
            print("1. after chat, automations:", [(a.get("summary"), a.get("status")) for a in autos])
            assert autos, "no automation proposed"
            rules_file = root / "rules.json"
            print("2. rules.json exists:", rules_file.exists())
        finally:
            s1.shutdown()
            s1.server_close()

        # second boot: same data dir, automation must be there
        s2, d2 = boot(root, 8132)
        try:
            st = get("/api/state", 8132)
            autos = st.get("automations", [])
            sched = st.get("scheduler", [])
            print("3. after restart, automations:", [a.get("summary") or a for a in autos])
            print("4. scheduler entries:", [(r.get("summary"), r.get("enabled")) for r in sched])
            assert autos, "automation lost across restart!"
            print("SMOKE OK — automations survive restart in real mode")
        finally:
            s2.shutdown()
            s2.server_close()
            ha.shutdown()


if __name__ == "__main__":
    main()
