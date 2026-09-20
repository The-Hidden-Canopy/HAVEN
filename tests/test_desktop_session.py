"""The desktop shell's loopback API is bound to one launch session."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from haven.web.server import make_server


@contextmanager
def _boot(data_dir: Path, *, token: str, on_activate=None):
    instance, _ = make_server(
        0,
        data_dir=data_dir,
        demo=True,
        session_token="session-secret",
        bootstrap_token=token,
        activation_token="activation-secret",
        on_activate=on_activate,
    )
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


def _post_json(port: int, path: str, payload: dict):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
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

            status, _, body = _request(port, "GET", "/api/host/capabilities")
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

            status, _, _ = _request(
                port,
                "GET",
                "/__desktop_bootstrap?session=launch-token",
            )
            assert status == 404

            status, _, body = _request(
                port,
                "GET",
                "/api/state",
                cookie="haven_session=launch-token",
            )
            assert status == 401


def test_desktop_bootstrap_rejects_a_wrong_token():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            status, _, _ = _request(port, "GET", "/__desktop_bootstrap?session=wrong")
            assert status == 404


def test_desktop_bootstrap_cookie_secret_is_not_the_url_nonce():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            status, headers, _ = _request(
                port,
                "GET",
                "/__desktop_bootstrap?session=launch-token",
            )
            assert status == 303
            set_cookie = dict(headers)["Set-Cookie"]
            assert "haven_session=session-secret" in set_cookie
            assert "haven_session=launch-token" not in set_cookie


def test_desktop_bootstrap_nonce_can_only_create_one_session_under_concurrency():
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(Path(tmp) / "data", token="launch-token") as (_, port):
            def bootstrap(_attempt: int) -> int:
                return _request(
                    port,
                    "GET",
                    "/__desktop_bootstrap?session=launch-token",
                )[0]

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(bootstrap, (1, 2)))

            assert sorted(statuses) == [303, 404]


def test_desktop_activation_requires_its_private_control_token():
    activations = []
    with tempfile.TemporaryDirectory() as tmp:
        with _boot(
            Path(tmp) / "data",
            token="launch-token",
            on_activate=lambda: activations.append(True) or True,
        ) as (_, port):
            status, _, _ = _post_json(port, "/__desktop_activate", {"token": "wrong"})
            assert status == 404
            assert activations == []

            status, _, body = _post_json(
                port,
                "/__desktop_activate",
                {"token": "activation-secret"},
            )
            assert status == 200
            assert json.loads(body) == {"ok": True, "activated": True}
            assert activations == [True]


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
