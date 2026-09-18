"""Live smoke: diagnostics + backup/restore lifecycle."""
import json
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile

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
        (root / "haven.json").write_text(json.dumps({
            "version": 1, "completed": True, "data_dir": str(root),
            "provider_kind": "home_assistant",
            "provider_base_url": f"http://127.0.0.1:{ha.server_address[1]}",
            "provider_token_file": "ha_token.txt",
            "voice_enabled": True, "intelligence_enabled": True}))
        (root / "ha_token.txt").write_text("stub")

        server, _ = make_server(8133, clock=lambda: NOW, data_dir=root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            d = get("/api/system/diagnostics", 8133)["diagnostics"]
            print("1. diagnostics: world =", d["world"]["mode"],
                  "| provider configured =", d["provider"]["configured"],
                  "| uptime >= 0:", d["uptime_seconds"] >= 0)

            probe = post("/api/system/diagnostics/probe", 8133, {})
            print("2. probe: reachable =", probe["reachable"])

            b1 = post("/api/system/backup", 8133, {})["backup"]
            print("3. backup created:", b1["id"], "files:", b1["files"])

            post("/api/setup/preferences", 8133, {"voice": False, "intelligence": False})
            b2 = post("/api/system/backup", 8133, {})["backup"]

            bl = get("/api/system/backups", 8133)["backups"]
            print("4. backups listed:", len(bl), "newest first:", bl[0]["id"] == b2["id"])

            r = post("/api/system/backup/restore", 8133, {"id": b1["id"]})
            print("5. restore:", r["result"]["restored"],
                  "| restart_required =", r["result"]["restart_required"])
            cfg = json.loads((root / "haven.json").read_text())
            assert cfg["voice_enabled"] is True, "restore did not roll back preferences"

            try:
                post("/api/system/backup/restore", 8133, {"id": "../escape"})
                print("6. traversal NOT rejected — BUG")
            except urllib.error.HTTPError as e:
                print("6. traversal rejected:", e.code)

            post("/api/system/backup/delete", 8133, {"id": b2["id"]})
            bl = get("/api/system/backups", 8133)["backups"]
            print("7. after delete:", len(bl), "backup(s)")
            print("SMOKE OK — diagnostics + backup lifecycle work")
        finally:
            server.shutdown()
            server.server_close()
            ha.shutdown()


if __name__ == "__main__":
    main()
