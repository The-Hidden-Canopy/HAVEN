"""Brand assets are part of the served HAVEN shell, not just source files."""

import http.client
import threading
from pathlib import Path

from haven.web.server import make_server


STATIC_ROOT = Path(__file__).parents[1] / "haven" / "web" / "static"


def _get(port: int, path: str) -> tuple[int, str | None, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    result = response.status, response.getheader("Content-Type"), body
    connection.close()
    return result


def test_shell_references_both_theme_wordmarks_and_new_mark() -> None:
    index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    manifest = (STATIC_ROOT / "manifest.webmanifest").read_text(encoding="utf-8")
    service_worker = (STATIC_ROOT / "sw.js").read_text(encoding="utf-8")

    assert '/haven-logo-light.png' in index
    assert 'srcset="/haven-logo-dark.png"' in index
    assert 'media="(prefers-color-scheme: dark)"' in index
    assert 'media="(prefers-color-scheme: light)"' in index
    assert '/icon.svg' in index
    assert '"/icon.svg"' in manifest
    assert "haven-logo-light.png" in service_worker
    assert "haven-logo-dark.png" in service_worker
    assert "haven-shell-v2" in service_worker

    for name in ("haven-logo-light.png", "haven-logo-dark.png"):
        payload = (STATIC_ROOT / name).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload) > 10_000


def test_brand_assets_are_served_with_image_content_types() -> None:
    instance, _director = make_server(0, demo=True)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        port = instance.server_address[1]
        for name in ("haven-logo-light.png", "haven-logo-dark.png"):
            status, content_type, body = _get(port, f"/{name}")
            assert status == 200
            assert content_type == "image/png"
            assert body == (STATIC_ROOT / name).read_bytes()

        status, content_type, body = _get(port, "/icon.svg")
        assert status == 200
        assert content_type == "image/svg+xml"
        assert b"linearGradient" in body
        assert b"#4fd1c5" not in body
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)
