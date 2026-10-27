"""Keep application-server defaults inside each test's disposable directory."""

import pytest
import ipaddress
import socket


def assert_fault(payload, code, *, error=None):
    """Assert the versioned negative-path contract, including its stable code."""
    assert payload["ok"] is False
    fault = payload["fault"]
    assert fault["schema_version"] == 1
    assert fault["code"] == code
    assert fault["correlation_id"]
    assert fault["message"] and fault["recovery_action"]
    assert fault["probable_causes"][0]["confidence"] == "possible"
    if error is not None:
        assert payload["error"] == error


@pytest.fixture(autouse=True)
def isolated_application_defaults(tmp_path, monkeypatch):
    # Several older server fixtures supplied neither data_dir nor models_root.
    # They must never read or mutate the developer's live installation.
    from haven.web import server, setup_service

    # Isolate every user-home fallback, including child processes and native
    # settings. Explicit server roots below remain separate for assertions.
    test_home = tmp_path / "application-home"
    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("USERPROFILE", str(test_home))
    monkeypatch.setenv("APPDATA", str(test_home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(test_home / "AppData" / "Local"))
    monkeypatch.delenv("HAVEN_DESKTOP_DATA_DIR", raising=False)
    monkeypatch.delenv("HAVEN_DATA_DIR", raising=False)
    monkeypatch.delenv("HAVEN_MODELS_ROOT", raising=False)
    monkeypatch.setattr(server, "default_data_dir", lambda: tmp_path / "default-data")
    monkeypatch.setattr(server, "default_models_root", lambda: tmp_path / "default-models")
    monkeypatch.setattr(setup_service, "default_data_dir", lambda: tmp_path / "default-data")


@pytest.fixture(autouse=True)
def loopback_network_only(monkeypatch):
    """Synthetic verification must not contact real integrations or DNS."""
    def check(address):
        if not isinstance(address, tuple):
            return  # Unix-domain transports, where supported.
        host = address[0]
        if host in {"", "localhost"}:
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise PermissionError("Tests permit loopback networking only")

    for method in ("connect", "connect_ex", "bind"):
        original = getattr(socket.socket, method)
        def guarded(self, address, *args, _original=original, **kwargs):
            check(address)
            return _original(self, address, *args, **kwargs)
        monkeypatch.setattr(socket.socket, method, guarded)
    original_sendto = socket.socket.sendto
    def sendto(self, data, *args):
        check(args[-1])
        return original_sendto(self, data, *args)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    original_resolve = socket.getaddrinfo
    def resolve(host, port, *args, **kwargs):
        check((host, port))
        return original_resolve(host, port, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
