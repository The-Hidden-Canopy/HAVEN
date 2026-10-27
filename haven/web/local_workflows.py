"""Host composition for decision memory, bundled features and local products.

Request data can select a visible scope, but never a principal or authority
callback. Durable domain receipts remain the source of truth if publication
to the existing history projection is interrupted: inspect state before retry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from haven.core.correlation import current, new_id
from haven.core.domain import DomainEvent, EventType, RoleTier
from haven.decisions import DecisionService, DecisionStore
from haven.decisions.api import DecisionAPI
from haven.decisions.models import DecisionContext
from haven.extensions import FeatureContext, FeatureLifecycle, FeatureLifecycleError, bundled_feature_releases
from haven.marketplace import ProductError, create_local_fixture_manager
from haven.web.marketplace_lifecycle_api import product_action


class LocalWorkflows:
    def __init__(self, host, data_dir: Path):
        self.host = host
        self._closed = False
        self.decisions = DecisionService(
            store=DecisionStore(data_dir / "decisions.db"), identity=host.identity,
            resources=host.resources, has_declared_owner=lambda: host.director.has_declared_owner,
            clock=host._director_clock,
        )
        self.decision_api = DecisionAPI(decisions=self.decisions, default_scope=lambda: host.identity.personal_scope_id)
        try:
            self.features = FeatureLifecycle(
                data_dir / "features.json", releases=bundled_feature_releases(), authorize=self.authorize,
                service_operations={"decisions.context": self.decision_context},
            )
        except (FeatureLifecycleError, OSError):
            self.features = None
        try:
            self.products = create_local_fixture_manager(
                data_dir / "marketplace-products",
                authorize=lambda principal_id, operation: self.authorize(
                    principal_id, host.identity.personal_scope_id, f"products.{operation}"),
            )
        except (ProductError, OSError):
            self.products = None

    def authorize(self, principal_id, scope_id, operation):
        if self._closed:
            return False
        read = {"features.list", "features.status", "decisions.context", "products.list"}
        mutations = {f"features.{name}" for name in ("install", "enable", "disable", "run", "update", "rollback", "uninstall")}
        mutations |= {f"products.{name}" for name in ("download", "install", "update", "rollback", "enable", "disable", "launch", "uninstall", "cancel")}
        if operation not in read | mutations:
            return False
        principal = self.host.identity.current_principal()
        if principal.actor_id != principal_id:
            return False
        memberships = self.host.identity.memberships()
        member = next((item for item in memberships if item.actor_id == principal_id
                       and item.scope.scope_id == scope_id and item.scope.status == "active"), None)
        if member is None:
            return False
        return operation in read or (
            self.host.director.has_declared_owner and principal.role_tier is RoleTier.OWNER and member.role == "owner"
        )

    def decision_context(self, *, principal_id, scope_id, **params):
        return self.decisions.context(scope_id, principal_id=principal_id, **params)

    def agent_context(self):
        identity = self.host.identity
        return DecisionContext.from_dict(self.decisions.context(
            identity.personal_scope_id, principal_id=identity.current_principal().actor_id))

    def _context(self, params):
        return FeatureContext(self.host.identity.current_principal().actor_id,
                              params.get("scope_id", self.host.identity.personal_scope_id))

    def record(self, operation, params):
        """Publish a bounded receipt through the existing scoped history owner."""
        now = (self.host._director_clock or (lambda: datetime.now(timezone.utc)))()
        scope_id = self.host.identity.personal_scope_id if operation.startswith("products.") else params.get("scope_id", self.host.identity.personal_scope_id)
        payload = {"operation": operation, "scope_id": scope_id}
        for key in ("feature_id", "product_id", "decision_id", "version"):
            value = params.get(key)
            if isinstance(value, str) and len(value) <= 200:
                payload[key] = value
        self.host.director.store.append_domain_event(DomainEvent(
            event_id=f"local-lifecycle:{uuid4().hex}", household_id=self.host.director.household_id,
            event_type=EventType.LOCAL_LIFECYCLE_CHANGED,
            actor_id=self.host.identity.current_principal().actor_id, occurred_at=now,
            payload=tuple(payload.items()), correlation_id=current() or new_id(), source="haven.local-workflows",
        ))
        self.host.director._publish_state()
        self.host._emit_event("memory.changed" if operation.startswith("decisions.") else "extensions.changed")

    def _with_receipt(self, operation, params, result):
        if result.get("ok"):
            try:
                self.record(operation, params)
            except Exception:
                # The domain has already committed. Do not imply rollback
                # or invite a blind retry when its history projection fails.
                return {"ok": False, "error": "outcome_uncertain", "message": "The change completed, but history publication failed. Inspect current state before retrying."}
        return result

    def feature_action(self, params):
        if self.features is None or self._closed:
            return {"ok": False, "error": "feature_lifecycle_unavailable"}
        try:
            context = self._context(params)
            action, feature_id = params.get("action"), params.get("feature_id")
            if action == "stage":
                result = self.features.stage(feature_id, params.get("version"), context=context)
            elif action == "run":
                arguments = params.get("parameters", {})
                if not isinstance(arguments, dict):
                    raise ValueError("feature parameters must be an object")
                result = self.features.run(feature_id, params.get("command"), context=context, params=arguments)
            elif action in {"install", "enable", "disable", "update", "rollback", "uninstall"}:
                result = getattr(self.features, action)(feature_id, context=context)
            else:
                raise ValueError("unknown feature action")
            if result.get("ok") and action != "run":
                return self._with_receipt(f"features.{action}", params, result)
            return result
        except FeatureLifecycleError as exc:
            return {"ok": False, "error": exc.code, "message": str(exc)}
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "error": "request_invalid", "message": "invalid feature request"}
        except OSError:
            return {"ok": False, "error": "outcome_uncertain", "message": "Inspect feature state before retrying this operation."}

    def products_action(self, params):
        if self.products is None or self._closed:
            return {"ok": False, "error": "product_lifecycle_unavailable"}
        result = product_action(self.products, params.get("action"), params,
                                principal_id=self.host.identity.current_principal().actor_id)
        return self._with_receipt(f"products.{params.get('action')}", params, result)

    def feature_catalog(self, params):
        if self.features is None or self._closed:
            return {"ok": False, "error": "feature_lifecycle_unavailable"}
        try:
            return {"ok": True, **self.features.catalog(self._context(params))}
        except (FeatureLifecycleError, ValueError):
            return {"ok": False, "error": "scope_denied"}

    def products_view(self, _params):
        identity = self.host.identity
        if not self.authorize(identity.current_principal().actor_id, identity.personal_scope_id, "products.list"):
            return {"ok": False, "error": "scope_denied"}
        if self.products is None or self._closed:
            return {"ok": False, "error": "product_lifecycle_unavailable"}
        try:
            return self.products.view()
        except (ProductError, OSError):
            return {"ok": False, "error": "product_lifecycle_unavailable"}

    def handlers(self):
        handlers = {}
        for name, handler in self.decision_api.handlers().items():
            def call(params, name=name, handler=handler):
                if self._closed:
                    return {"ok": False, "error": "service_replaced"}
                result = handler(params)
                if result.get("ok") and name not in {"decisions.snapshot", "decisions.context"}:
                    receipt = dict(params)
                    if "decision" in result:
                        receipt["decision_id"] = result["decision"]["decision_id"]
                    return self._with_receipt(name, receipt, result)
                return result
            handlers[name] = call
        handlers.update({"features.list": self.feature_catalog, "features.action": self.feature_action,
                         "marketplace.products": self.products_view, "marketplace.product": self.products_action})
        return handlers

    def close(self):
        self._closed = True
        if self.features is not None:
            self.features.close()
