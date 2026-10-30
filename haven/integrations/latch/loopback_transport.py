"""Loopback-only HTTP transport for a HAVEN-to-Latch host adapter.

This module owns no Latch state or action authority.  It can be integrated
into a host adapter without depending on an agent or a particular UI.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class LoopbackAddressError(ValueError):
    """The configured daemon URL is not an unambiguous loopback origin."""


class LoopbackUnavailableError(ConnectionError):
    """The local daemon could not be reached."""


def validate_loopback_origin(url: str) -> str:
    """Accept an HTTP(S) loopback origin, without credentials or URL suffixes."""
    try:
        if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url) or "?" in url or "#" in url:
            raise ValueError
        parts = urlsplit(url)
        host = parts.hostname
        # Reading port also validates malformed and out-of-range ports.
        port = parts.port
        if not host or parts.scheme not in ("http", "https") or parts.netloc.endswith(":"):
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError
        if host.lower() != "localhost":
            address = ipaddress.ip_address(host)
            if ("%" in host or not address.is_loopback or
                    (address.version == 6 and address != ipaddress.IPv6Address("::1"))):
                raise ValueError
        # Explicit port 0 cannot identify a listening daemon.
        if port == 0:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise LoopbackAddressError("Latch URL must be a loopback HTTP(S) origin") from exc
    return url.rstrip("/")


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method: str, url: str, body: bytes | None, timeout: float):
    """Make one local request, returning non-2xx responses without redirects.

    The caller constructs only fixed Latch paths. Proxies and redirect
    following are disabled, so the request cannot silently leave the origin.
    """
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    validate_loopback_origin(origin)
    if parts.fragment:
        raise LoopbackAddressError("fragments are not valid Latch request paths")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    outgoing = Request(url, data=body, method=method, headers=headers)
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    try:
        with opener.open(outgoing, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            raw = b""
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, raw
    except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
        raise LoopbackUnavailableError("no answer from local Latch daemon") from exc
