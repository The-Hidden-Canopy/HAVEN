"""Local owner-controlled Android pairing; no network endpoint or default issuer.

Host adapters must authenticate the local owner before providing `current_owner`,
and deliver the short comparison code over a separate trusted channel. Request
and approval records are deliberately in memory and expire on host restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from typing import Callable

from haven.core.domain import Principal, RoleTier
from haven.external_agents.domain import ExternalProvider, ExternalScope
from haven.external_agents.transport import hash_credential

PAIRING_TTL = timedelta(minutes=5)
MAX_BINDING_TTL = timedelta(days=90)
ALLOWED_SCOPES = frozenset({ExternalScope.WORLD_READ, ExternalScope.ACTIONS_REQUEST})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def subject_for(connection_id: str) -> str:
    return hashlib.sha256(("haven.android.subject.v1:" + connection_id).encode()).hexdigest()


def proof_digest(verifier: str) -> str:
    if not isinstance(verifier, str) or not _VERIFIER.fullmatch(verifier):
        raise ValueError("invalid pairing proof")
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


class PairingRegistry:
    """Durable activation gate, separate from HAVEN's connection/binding store.

    This contains connection identifiers and status only, never credentials.
    A connection is usable by this bridge only after activation is committed.
    """
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS android_pairings (connection_id TEXT PRIMARY KEY, active INTEGER NOT NULL CHECK(active IN (0,1)))")
    def activate(self, connection_id):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO android_pairings(connection_id,active) VALUES (?,1)", (connection_id,))
    def disable(self, connection_id):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE android_pairings SET active=0 WHERE connection_id=?", (connection_id,))
    def is_active(self, connection_id):
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT active FROM android_pairings WHERE connection_id=?", (connection_id,)).fetchone()
        return row is not None and row[0] == 1


@dataclass
class _Request:
    request_id: str
    label: str
    proof_hash: str
    expires_at: datetime
    principal_id: str | None = None
    approved_by: str | None = None
    scopes: frozenset[ExternalScope] = frozenset()
    binding_expires_at: datetime | None = None
    consumed: bool = False


class PairingProvisioningUncertain(RuntimeError):
    """Provisioning failed and compensating revocation could not be confirmed."""


class PairingCoordinator:
    """Creates temporary pairing requests and issues only after owner approval.

    `issuer` is an explicit callable returning a new unguessable token. There
    is intentionally no default issuer and no delivery/pairing HTTP route.
    A real adapter must authenticate its local owner context and protect the
    one-time delivery channel; supplying a Principal from an untrusted request
    is not authentication. Revoke/approve never accept a caller-supplied role.
    """
    def __init__(self, *, household_id, external_service, store, registry, current_owner,
                 resolve_principal, issuer=None, clock=None, max_requests=20):
        self.household_id = household_id
        self.service = external_service
        self.store = store
        self.registry = registry
        self.current_owner = current_owner
        self.resolve_principal = resolve_principal
        self.issuer = issuer
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_requests = max_requests
        self._requests = {}
        self._lock = threading.RLock()

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("clock must be timezone-aware")
        return now

    def _owner(self):
        actor = self.current_owner()
        if not isinstance(actor, Principal) or actor.household_id != self.household_id or actor.role_tier != RoleTier.OWNER:
            raise PermissionError("authenticated household owner required")
        canonical = self.resolve_principal(actor.actor_id)
        if canonical is None or canonical.household_id != self.household_id or canonical.role_tier != RoleTier.OWNER or canonical.actor_id != actor.actor_id:
            raise PermissionError("declared household owner required")
        return canonical

    def request(self, label, verifier_hash):
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError("a device label of at most 100 characters is required")
        if not isinstance(verifier_hash, str) or not _DIGEST.fullmatch(verifier_hash):
            raise ValueError("a SHA-256 proof digest is required")
        with self._lock:
            now = self._now()
            self._requests = {k: v for k, v in self._requests.items() if v.expires_at > now and not v.consumed}
            if len(self._requests) >= self.max_requests:
                raise RuntimeError("pairing request limit reached")
            req = _Request(secrets.token_urlsafe(24), label.strip(), verifier_hash, now + PAIRING_TTL)
            self._requests[req.request_id] = req
            return {"request_id": req.request_id, "label": req.label, "comparison_code": verifier_hash[:12], "expires_at": req.expires_at.isoformat()}

    def approve(self, request_id, comparison_code, principal_id, scopes, binding_expires_at):
        with self._lock:
            owner = self._owner()
            now = self._now()
            req = self._requests.get(request_id)
            if req is None or req.consumed or req.expires_at <= now or req.principal_id is not None:
                raise ValueError("pairing request unavailable")
            if not isinstance(comparison_code, str) or not hmac.compare_digest(comparison_code, req.proof_hash[:12]):
                raise ValueError("comparison code does not match")
            principal = self.resolve_principal(principal_id)
            if principal is None or principal.household_id != self.household_id:
                raise ValueError("unknown household principal")
            scopes = frozenset(scopes)
            if not scopes or not scopes <= ALLOWED_SCOPES or any(not isinstance(x, ExternalScope) for x in scopes):
                raise ValueError("unsupported or empty phone scopes")
            if not isinstance(binding_expires_at, datetime) or binding_expires_at.utcoffset() is None or not now < binding_expires_at <= now + MAX_BINDING_TTL:
                raise ValueError("binding expiry is outside the allowed window")
            req.principal_id = principal.actor_id
            req.approved_by = owner.actor_id
            req.scopes = scopes
            req.binding_expires_at = binding_expires_at
            return {"approved": True, "request_id": req.request_id, "approved_by": owner.actor_id, "scopes": sorted(x.value for x in scopes), "expires_at": binding_expires_at.isoformat()}

    def redeem(self, request_id, verifier):
        """Return a one-time token. Never call over an unauthenticated channel."""
        digest = proof_digest(verifier)
        with self._lock:
            now = self._now()
            req = self._requests.get(request_id)
            if req is None or req.consumed or req.expires_at <= now or req.principal_id is None or req.binding_expires_at <= now or not hmac.compare_digest(digest, req.proof_hash):
                raise PermissionError("pairing request unavailable")
            if self.issuer is None:
                raise RuntimeError("credential issuer is not configured")
            # Recheck owner and bound principal at issuance, because either may
            # have been removed while the approval was waiting for redemption.
            owner = self.resolve_principal(req.approved_by)
            if owner is None or owner.household_id != self.household_id or owner.role_tier != RoleTier.OWNER:
                raise PermissionError("approving owner is no longer authorized")
            principal = self.resolve_principal(req.principal_id)
            if principal is None or principal.household_id != self.household_id:
                raise PermissionError("bound principal is unavailable")
            req.consumed = True
            token = self.issuer()
            if not isinstance(token, str) or not _VERIFIER.fullmatch(token):
                raise ValueError("issuer returned an invalid credential")
            connection = None
            try:
                connection = self.service.create_connection(provider=ExternalProvider.MCP_CLIENT,
                    display_name=req.label, created_by=owner.actor_id, credential_hash=hash_credential(token),
                    unbound_scopes=frozenset(), enabled=False)
                subject = subject_for(connection.connection_id)
                binding = self.service.upsert_binding(connection_id=connection.connection_id, subject_key=subject,
                    subject_label=req.label, principal_id=principal.actor_id, scopes=req.scopes,
                    created_by=owner.actor_id, expires_at=req.binding_expires_at)
                self.service.set_connection_enabled(connection.connection_id, True, actor=owner.actor_id)
                self.registry.activate(connection.connection_id)
                return {"credential": token, "connection_id": connection.connection_id, "binding_id": binding.binding_id,
                        "expires_at": req.binding_expires_at.isoformat()}
            except Exception:
                if connection is not None:
                    gate_closed = revoked = False
                    try:
                        self.registry.disable(connection.connection_id)
                        gate_closed = True
                    except Exception: pass
                    try:
                        self.service.revoke_connection(connection.connection_id, actor=owner.actor_id)
                        revoked = True
                    except Exception: pass
                    if not gate_closed and not revoked:
                        raise PairingProvisioningUncertain("provisioning failed and neither gate closure nor revocation could be confirmed")
                raise

    def revoke(self, connection_id):
        with self._lock:
            owner = self._owner()
            connection = self.store.get_connection(connection_id)
            if connection is None or connection.household_id != self.household_id or connection.provider != ExternalProvider.MCP_CLIENT:
                raise ValueError("unknown Android connection")
            if self.store.active_binding(connection_id, subject_for(connection_id)) is None:
                raise ValueError("unknown Android connection")
            # Close the phone gate first. If the existing service then fails,
            # access remains blocked here; callers must inspect HAVEN's record.
            self.registry.disable(connection_id)
            return self.service.revoke_connection(connection_id, actor=owner.actor_id)


class PairedSubjects:
    """Resolve only active, unexpired Android bindings from the existing store."""
    def __init__(self, store, household_id, registry, clock=None):
        self.store, self.household_id, self.registry = store, household_id, registry
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(self, fingerprint):
        connection = self.store.connection_for_credential(fingerprint)
        if connection is None or connection.household_id != self.household_id or connection.provider != ExternalProvider.MCP_CLIENT or not connection.active:
            return None
        if not self.registry.is_active(connection.connection_id):
            return None
        subject = subject_for(connection.connection_id)
        binding = self.store.active_binding(connection.connection_id, subject)
        if binding is None or not binding.is_valid_at(self.clock()):
            return None
        return subject
