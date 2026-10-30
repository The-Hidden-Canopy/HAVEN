"""Narrow HTTPS adapter for the existing external-agent transport.

The embedding host provisions credential-hash -> subject mappings out of band.
Neither the client nor request body can choose a subject or a HAVEN principal.
This module does not start a listener or provision credentials by itself.
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from haven.external_agents.errors import ExternalDenied
from haven.session_bridge.gateway import SessionUnavailable
from haven.session_bridge.contract import Conflict

MAX_BODY = 65536
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Only parameter-free services for which the phone can make a complete request.
# A listed service is an option to request, never a grant to execute.
PHONE_SERVICES = frozenset({"light.turn_on", "light.turn_off", "switch.turn_on", "switch.turn_off", "fan.turn_on", "fan.turn_off", "cover.open", "cover.close"})

def phone_services(registry, device_id):
    if not isinstance(device_id, str) or not registry.is_registered(device_id):
        return []
    return sorted({cap.service for cap in registry.get(device_id).capabilities
                   if cap.writable and cap.service in PHONE_SERVICES})

SESSION_TOOLS = frozenset({"haven.session.capabilities", "haven.session.sessions", "haven.session.snapshot", "haven.session.replay", "haven.session.memory", "haven.session.command_status", "haven.session.submit", "haven.session.cancel", "haven.session.decide"})

FEATURE_TOOLS = frozenset({"haven.feature.call", "haven.feature.catalog"})

PRODUCT_TOOLS = frozenset({"haven.product.describe", "haven.product.read", "haven.product.propose"})

TOOLS = frozenset({"haven.world.get", "haven.rooms.list", "haven.action.request", "haven.action.confirm", "haven.action.deny"})


def pending_belongs_to_connection(store, director, credential, request_id):
    """A phone may resolve only a pending action it requested on this connection."""
    if not isinstance(request_id, str) or not request_id:
        return False
    connection = store.connection_for_credential(hashlib.sha256(credential.encode("utf-8")).hexdigest())
    if connection is None or not connection.active:
        return False
    return any(p.request_id == request_id and p.external_connection_id == connection.connection_id
               for p in director.pending_requests)


def dispatch(bridge, credential: str, subjects: Mapping[str, str | None], body: object, services=None, product_host=None, pending_checker=None, session_host=None, feature_host=None):
    """Return status and JSON envelope; let TransportBridge own admission and policy."""
    if not isinstance(credential, str) or not credential or len(credential) > 4096:
        return 401, {"ok": False, "code": "external.connection_unknown"}
    fingerprint = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    if hasattr(subjects, "resolve"):
        try:
            subject = subjects.resolve(fingerprint)
        except Exception:
            return 503, {"ok": False, "code": "bridge_unavailable"}
        if subject is None:
            return 401, {"ok": False, "code": "external.connection_unknown"}
    else:
        if fingerprint not in subjects:
            return 401, {"ok": False, "code": "external.connection_unknown"}
        subject = subjects[fingerprint]
    if not isinstance(body, dict) or set(body) != {"tool", "request_id", "arguments"}:
        return 400, {"ok": False, "code": "tool.invalid_arguments"}
    tool, request_id, args = body["tool"], body["request_id"], body["arguments"]
    if not isinstance(tool, str) or tool not in TOOLS | PRODUCT_TOOLS | SESSION_TOOLS | FEATURE_TOOLS or not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id) or not isinstance(args, dict):
        return 400, {"ok": False, "code": "tool.invalid_arguments"}
    try:
        if tool in FEATURE_TOOLS:
            if feature_host is None: return 503, {"ok":False,"code":"feature_host_unavailable"}
            return 200, feature_host.call(credential,subject,tool,request_id,args)
        if tool in SESSION_TOOLS:
            if session_host is None: return 503, {"ok": False, "code": "session_provider_unavailable"}
            return 200, session_host.call(credential, subject, tool, request_id, args)
        if tool in PRODUCT_TOOLS:
            if product_host is None: return 503, {"ok": False, "code": "product_host_unavailable"}
            return 200, product_host.call(credential, subject, tool, request_id, args)
        if tool in {"haven.action.confirm", "haven.action.deny"}:
            # Check before forwarding; a matching principal alone is insufficient
            # when the same person uses more than one external connection.
            if pending_checker is None or pending_checker(credential, args.get("request_id")) is not True:
                return 403, {"ok": False, "code": "pending_not_available"}
        result = bridge.call_tool(credential=credential, tool=tool,
            external_request_id=request_id, arguments=args, subject=subject,
            client_metadata={"transport": "android-https-v1"})
        if tool == "haven.world.get" and result.get("ok") and services is not None:
            # Enrich only device IDs already admitted by the world.read projection.
            for room in result.get("state", {}).get("rooms", []):
                for device in room.get("devices", []):
                    device["phone_services"] = [s for s in services(device.get("id")) if s in PHONE_SERVICES]
        elif tool.startswith("haven.action."):
            # Existing action responses can include the full local UI state, which
            # is not a scoped external projection. Never export it via this bridge.
            result = {key: value for key, value in result.items() if key in
                      {"ok", "status", "request_id", "expires_at", "receipt_id", "reason", "error", "code"}}
        return 200, result
    except ExternalDenied as exc:
        if tool in SESSION_TOOLS:
            return 403, {"ok": False, "code": "access_denied"}
        return 403, {"ok": False, "code": exc.code, "error": exc.message}
    except SessionUnavailable:
        return 503, {"ok": False, "code": "session_provider_unavailable"}
    except Conflict:
        return 409, {"ok": False, "code": "stale_approval" if tool == "haven.session.decide" else "not_executed"}
    except PermissionError:
        return 403, {"ok": False, "code": "access_denied" if tool in SESSION_TOOLS | FEATURE_TOOLS else "product_access_denied"}
    except ValueError:
        return 400, {"ok": False, "code": "invalid_or_unsupported"}
    except Exception:
        # A command may have executed before a failure. Never tell a caller
        # it is safe to retry with a new request id.
        return 500, {"ok": False, "code": "outcome_uncertain"}


class BridgeServer(ThreadingHTTPServer):
    """TLS listener with bounded worker concurrency and handshake timeout."""
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, bridge, subjects, tls_context: ssl.SSLContext, services=None, max_connections=16, product_host=None, pending_checker=None, session_host=None, feature_host=None):
        if not isinstance(tls_context, ssl.SSLContext) or tls_context.protocol != ssl.PROTOCOL_TLS_SERVER:
            raise ValueError("a server TLS context is required")
        if not isinstance(max_connections, int) or isinstance(max_connections, bool) or not 1 <= max_connections <= 128:
            raise ValueError("max_connections must be between 1 and 128")
        tls_context.minimum_version = max(tls_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.bridge = bridge
        self.subjects = subjects if hasattr(subjects, "resolve") else dict(subjects)
        self.services = services
        self.product_host = product_host
        self.pending_checker = pending_checker
        self.session_host = session_host
        self.feature_host = feature_host
        self._tls_context = tls_context
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        secure = None
        try:
            # Handshake is performed inside a bounded worker, never on the
            # accept thread; an idle peer cannot block all new connections.
            request.settimeout(5)
            secure = self._tls_context.wrap_socket(request, server_side=True)
            self.finish_request(secure, client_address)
        except Exception:
            pass  # close without exposing request contents or TLS diagnostics
        finally:
            try:
                self.shutdown_request(secure if secure is not None else request)
            finally:
                self._slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass  # no tokens, bodies or query strings in access logs

    def do_POST(self):
        self.close_connection = True
        if self.path != "/v1/tool":
            return self.reply(404, {"ok": False, "code": "not_found"})
        lengths = self.headers.get_all("Content-Length", [])
        auths = self.headers.get_all("Authorization", [])
        types = self.headers.get_all("Content-Type", [])
        if (self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1 or len(auths) > 1 or len(types) != 1
                or self.headers.get_content_type() != "application/json" or not re.fullmatch(r"[0-9]{1,6}", lengths[0])):
            return self.reply(400, {"ok": False, "code": "invalid_request"})
        try:
            length = int(lengths[0])
            if length < 1 or length > MAX_BODY:
                raise ValueError()
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError()
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            return self.reply(400, {"ok": False, "code": "invalid_request"})
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        status, result = dispatch(self.server.bridge, token, self.server.subjects, body, self.server.services, self.server.product_host, self.server.pending_checker, self.server.session_host, self.server.feature_host)
        self.reply(status, result)

    def reply(self, status, result):
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
