"""The desktop shell's loopback API is bound to one launch session."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from haven.web.server import make_server


@contextmanager
def _boot(data_dir: Path, *, token: str):
    instance, _ = make_server(0, data_dir=data_dir, demo=True, session_token=token)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _request(port: int, method: str, path: str, *, cookie: str | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Cookie": cookie} if cookie else {}
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    result = response.status, response.getheaders(), body
    connection.close()
    return result


def test_desktop_session_requires_cookie_and_bootstrap_sets_one():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            status, _, body = _request(port, "GET", "/api/state")
            assert status == 401
            assert json.loads(body)["error"] == "HAVEN desktop session required"

            status, headers, body = _request(
                port, "GET", "/__desktop_bootstrap?session=launch-token"
            )
            assert status == 303
            assert body == b""
            set_cookie = dict(headers)["Set-Cookie"]
            cookie = set_cookie.split(";", 1)[0]
            assert "HttpOnly" in set_cookie
            assert "SameSite=Strict" in set_cookie

            status, _, body = _request(port, "GET", "/api/state", cookie=cookie)
            assert status == 200
            assert "rooms" in json.loads(body)


def test_desktop_bootstrap_rejects_a_wrong_token():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            status, _, _ = _request(port, "GET", "/__desktop_bootstrap?session=wrong")
            assert status == 404


def test_desktop_session_requires_cookie_for_mutating_posts_too():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            status, _, body = _request(port, "POST", "/api/desktop/pick-folder")
            assert status == 401
            assert json.loads(body)["error"] == "HAVEN desktop session required"


def test_unbound_browser_mode_remains_usable_without_a_session_cookie():
    with tempfile.TemporaryDirectory() as tmp:
        instance, _ = make_server(0, data_dir=Path(tmp) / "data", demo=True)
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            status, _, body = _request(instance.server_address[1], "GET", "/api/state")
            assert status == 200
            assert "rooms" in json.loads(body)
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=5)
