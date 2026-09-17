"""In-memory stub HTTP server for model-manager URL tests.

Serves a fixed mapping of path -> bytes on 127.0.0.1 with an ephemeral
port, mirroring the ThreadingHTTPServer pattern in tests/test_web_server.py.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from haven.models.manifest import SCHEMA_VERSION


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output clean
        pass

    def do_GET(self) -> None:
        body = self.server.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubServer:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.files = dict(files)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def make_file_server(
    model_id: str = "stub-model",
    *,
    kind: str = "intelligence",
    capabilities=("chat",),
    backend: str = "fake",
    architecture: str = "stub",
    languages=("en",),
    license_name: str = "Apache-2.0",
    contents: dict[str, bytes] | None = None,
    subdir: str = "/models/stub-model",
    sha256_override: dict[str, str] | None = None,
    manifest_overrides: dict | None = None,
) -> tuple[StubServer, str, dict, dict]:
    """Serve a manifest plus its files; returns (server, manifest_url, manifest_dict, sha256)."""

    files = contents or {"weights.bin": b"stub weights bytes", "config.json": b'{"layers": 1}'}
    sha256 = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if sha256_override:
        sha256.update(sha256_override)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "id": model_id,
        "version": "1.0.0",
        "kind": kind,
        "capabilities": list(capabilities),
        "architecture": architecture,
        "backend": backend,
        "files": {"weights": "weights.bin", "config": "config.json"},
        "sha256": sha256,
        "languages": list(languages),
        "hardware": {"cpu": True},
        "license": license_name,
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    served = {f"{subdir}/haven-model.json": json.dumps(manifest).encode("utf-8")}
    served.update({f"{subdir}/{name}": data for name, data in files.items()})
    server = StubServer(served)
    return server, server.url(f"{subdir}/haven-model.json"), manifest, sha256


def make_manifest_dict(
    model_id: str = "local-model",
    *,
    kind: str = "intelligence",
    capabilities=("chat",),
    backend: str = "fake",
    architecture: str = "stub",
    languages=("en",),
    license_name: str = "Apache-2.0",
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": model_id,
        "version": "1.0.0",
        "kind": kind,
        "capabilities": list(capabilities),
        "architecture": architecture,
        "backend": backend,
        "files": {"weights": "weights.bin"},
        "sha256": {"weights.bin": hashlib.sha256(b"local weights").hexdigest()},
        "languages": list(languages),
        "hardware": {"cpu": True},
        "license": license_name,
    }
