"""Phone-facing product integration contracts, separate from export consumers.

A trusted host injects the Latch reference adapter or Harbor metadata transport
and an explicit per-product authorizer. No integration is auto-discovered,
installed, enabled, or granted authority by this module.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Protocol

from haven.core.time import require_aware_utc
from haven.external_agents.domain import ExternalRequest, ExternalScope
from haven.external_agents.errors import ExternalDenied
from haven.external_agents.transport import hash_credential

VERSION = "haven-product-ui-1"
MAX_ITEMS = 100
PRODUCT_IDS = ("latch", "harbor")


def scalars(value, fields):
    if not isinstance(value, Mapping): return {}
    result = {}
    for key in fields:
        item = value.get(key)
        if isinstance(item, str): result[key] = item[:512]
        elif isinstance(item, (int, float, bool)) or item is None: result[key] = item
    return result


def rows(value, fields):
    items = value.get("items", []) if isinstance(value, Mapping) else value
    if not isinstance(items, (tuple, list)): raise ValueError("invalid item collection")
    return [scalars(item, fields) for item in items[:MAX_ITEMS] if isinstance(item, Mapping)]


def evidence_rows(value):
    if not isinstance(value, (tuple, list)): return []
    return [scalars(e, ("id", "evidence_id", "source", "source_kind", "source_connector", "acquired_at", "fresh", "freshness", "mime_type")) for e in value[:MAX_ITEMS] if isinstance(e, Mapping)]


class ProductAdapter(Protocol):
    def describe(self) -> dict: ...
    def read(self, section: str, identifier: str | None) -> dict: ...
    def propose(self, operation: str, identifier: str) -> dict: ...


class LatchAdapter:
    """Uses Latch's existing read/propose-only HavenAdapter, never its ledger."""
    def __init__(self, reference=None):
        self.reference = reference
        self._lock = threading.RLock()
    def describe(self):
        r = self.reference
        caps = []
        for name, method in (("commitments", "list_interventions"), ("access", "list_access"),
                             ("reviews", "list_review_proposals"), ("plans", "list_action_plans"),
                             ("evidence", "get_evidence"), ("commitment_detail", "get_commitment"),
                             ("propose_cancellation", "propose_cancellation")):
            if r is not None and callable(getattr(r, method, None)): caps.append(name)
        return {"id":"latch", "version":VERSION, "availability":"configured" if r is not None else "unavailable", "capabilities":caps}
    def read(self, section, identifier=None):
        with self._lock:
            return self._read(section, identifier)
    def _read(self, section, identifier=None):
        if section not in self.describe()["capabilities"]: raise ValueError("unsupported or unavailable")
        r = self.reference
        if section == "commitments":
            return {"items":rows(r.list_interventions(), ("commitment_id", "kind", "provider", "next_event_at", "message"))}
        if section == "access":
            raw = r.list_access(); items = raw.get("items") if isinstance(raw, Mapping) else None
            if not isinstance(items, list): raise ValueError("invalid access response")
            out = []
            for item in items[:MAX_ITEMS]:
                if not isinstance(item, Mapping): continue
                row=scalars(item,("id", "access_grant_id", "last_used_at", "granted_at", "source_connector", "stale", "status"))
                row["grantee"]=scalars(item.get("grantee"),("id", "type")); row["resource"]=scalars(item.get("resource"),("id", "type")); out.append(row)
            return {"items":out}
        if section == "reviews":
            raw=r.list_review_proposals(); items=raw.get("items") if isinstance(raw, Mapping) else None
            if not isinstance(items, list): raise ValueError("invalid review response")
            out=[]
            for item in items[:MAX_ITEMS]:
                if not isinstance(item, Mapping): continue
                row=scalars(item,("id", "proposal_id", "kind", "status", "created_at", "summary")); row["target"]=scalars(item.get("target"),("commitment_id", "access_grant_id")); row["evidence"]=evidence_rows(item.get("evidence")); out.append(row)
            return {"items":out}
        if section == "plans": return {"items":rows(r.list_action_plans(), ("plan_id", "commitment_id", "status", "operation", "created_at"))}
        if section == "evidence":
            return {"item":scalars(r.get_evidence(_identifier(identifier), include_content=False), ("id", "source_kind", "source_connector", "acquired_at", "mime_type", "content_hash"))}
        if section == "commitment_detail":
            item=r.get_commitment(_identifier(identifier)); result=scalars(item,("id", "commitment_id"))
            facts = item.get("facts", {}) if isinstance(item, Mapping) else {}
            result["facts"]={key:scalars(facts.get(key), ("value", "confidence", "state")) for key in ("kind", "provider", "next_event_at") if isinstance(facts.get(key), Mapping)} if isinstance(facts, Mapping) else {}
            return {"item":result}
        raise ValueError("unsupported")
    def propose(self, operation, identifier):
        with self._lock:
            return self._propose(operation, identifier)
    def _propose(self, operation, identifier):
        if operation != "propose_cancellation" or operation not in self.describe()["capabilities"]: raise ValueError("unsupported or unavailable")
        # Latch's reference adapter supplies its stable per-commitment idempotency key.
        result=self.reference.propose_cancellation(_identifier(identifier))
        return {"item":scalars(result,("plan_id", "status", "commitment_id", "operation")), "handoff":"Authorization, execution and verification remain in Latch."}


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value)>128: raise ValueError("invalid identifier")
    return value.strip()


class HarborAdapter:
    """Metadata-only, host-injected transport; never unlocks or verifies."""
    def __init__(self, metadata_reader: Callable[[], dict] | None = None):
        self.reader=metadata_reader
        self._lock=threading.RLock()
    def describe(self):
        return {"id":"harbor", "version":VERSION, "availability":"configured" if self.reader else "unavailable", "capabilities":["metadata"] if self.reader else []}
    def read(self, section, identifier=None):
        with self._lock:
            return self._read(section, identifier)
    def _read(self, section, identifier=None):
        if section != "metadata" or self.reader is None: raise ValueError("unsupported or unavailable")
        value=self.reader()
        if not isinstance(value, Mapping) or value.get("state") not in {"unlocked", "locked", "unavailable"}: raise ValueError("invalid Harbor state")
        if value["state"] != "unlocked": return {"state":value["state"], "snapshot":{}}
        snap=value.get("snapshot")
        if not isinstance(snap, Mapping) or not {"objects", "requests", "app_grants", "history"} <= set(snap): raise ValueError("invalid Harbor snapshot")
        return {"state":"unlocked", "snapshot":{
            "objects":rows(snap.get("objects", []),("object_id", "object_type", "display_label", "created_at", "updated_at")),
            "requests":rows(snap.get("requests", []),("request_id", "caller_app_id", "purpose", "operation", "decision", "created_at")),
            "app_grants":rows(snap.get("app_grants", []),("app_id", "display_name", "publisher", "trust_level", "status", "network_posture")),
            "history":rows(snap.get("history", []),("event_id", "request_id", "operation", "decision", "created_at")),
        }}
    def propose(self, operation, identifier): raise ValueError("Harbor action and verification are unavailable")


@dataclass
class ProductHost:
    """Composition root: a separate, mandatory per-product authorization gate."""
    store: object
    gateway: object
    adapters: Mapping[str, ProductAdapter]
    authorize: Callable
    clock: Callable

    def call(self, credential, subject, tool, request_id, arguments):
        if tool not in {"haven.product.describe", "haven.product.read", "haven.product.propose"}: raise ValueError("unknown product tool")
        if not isinstance(arguments, dict) or set(arguments) - {"id", "version", "section", "item_id"}: raise ValueError("invalid product arguments")
        product=arguments.get("id")
        if product not in PRODUCT_IDS or arguments.get("version") != VERSION: raise ValueError("unsupported product or version")
        connection=self.store.connection_for_credential(hash_credential(credential))
        if connection is None: raise PermissionError("connection unavailable")
        scope=ExternalScope.ACTIONS_REQUEST if tool == "haven.product.propose" else ExternalScope.WORLD_READ
        admitted=self.gateway.admit(ExternalRequest(connection_id=connection.connection_id, tool=tool, required_scope=scope,
            external_request_id=request_id, subject=subject, client_metadata={"transport":"android-https-v1"}))
        # Unbound subjects never see product data, even if world.read is granted.
        operation=arguments.get("section", "describe") if tool != "haven.product.describe" else "describe"
        if admitted.binding is None or admitted.principal.household_id != connection.household_id or self.authorize(admitted, product, operation) is not True: raise PermissionError("product access not granted")
        adapter=self.adapters.get(product)
        if adapter is None: adapter=LatchAdapter() if product == "latch" else HarborAdapter()
        if type(adapter) is not (LatchAdapter if product == "latch" else HarborAdapter): raise ValueError("unrecognized adapter")
        descriptor=adapter.describe()
        if descriptor.get("id") != product or descriptor.get("version") != VERSION: raise ValueError("incompatible adapter")
        if tool == "haven.product.describe": return {"ok":True, "descriptor":descriptor}
        if descriptor.get("availability") != "configured" or operation not in descriptor.get("capabilities", []): raise ValueError("unsupported or unavailable")
        identifier=arguments.get("item_id")
        if tool == "haven.product.read": return {"ok":True, "descriptor":descriptor, "data":adapter.read(operation, identifier)}
        # Independent request ledger prevents duplicate side effects from the
        # same request id. Latch's own adapter also uses its stable plan key.
        digest=hashlib.sha256(json.dumps({"tool":tool,"arguments":arguments,"subject":subject}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        claim, prior=self.store.claim_request(household_id=connection.household_id, connection_id=connection.connection_id,
            external_request_id=request_id, request_hash=digest, created_at=require_aware_utc(self.clock(), name="clock"))
        if claim == "complete": return prior
        if claim != "claimed": raise RuntimeError("request is already in progress")
        try:
            result={"ok":True, "descriptor":descriptor, "data":adapter.propose(operation, _identifier(identifier))}
            self.store.complete_request(household_id=connection.household_id, connection_id=connection.connection_id,
                external_request_id=request_id, request_hash=digest, result=result)
            return result
        except Exception as exc:
            raise RuntimeError("proposal outcome uncertain") from exc
