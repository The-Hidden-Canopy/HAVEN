"""Host composition for decision memory, bundled features and local products.

Request data can select a visible scope, but never a principal or authority
callback. Durable domain receipts remain the source of truth if publication
to the existing history projection is interrupted: inspect state before retry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import traceback

from haven.core.correlation import current, new_id
from haven.core.domain import DomainEvent, EventType, RoleTier
from haven.decisions import DecisionService, DecisionStore
from haven.decisions.api import DecisionAPI
from haven.decisions.models import DecisionContext
from haven.extensions import FeatureContext, FeatureLifecycle, FeatureLifecycleError, bundled_feature_releases
from haven.faults import public_failure
from haven.marketplace import ProductError, create_local_fixture_manager
from haven.web.marketplace_lifecycle_api import product_action


class LocalWorkflows:
    def __init__(self, host, data_dir: Path):
        self.host = host
        self._closed = False
        from haven.session_bridge.durable import DurableSessionBinding
        from haven.session_bridge.local import LocalSessionRoutes
        self.sessions = DurableSessionBinding(data_dir / "sessions.db",
            member=lambda principal,scope: self.authorize(principal,scope,"features.list"))
        self.session_routes = LocalSessionRoutes(host,self.sessions)
        from haven.android_bridge.feature_host import FeatureGateway
        self.feature_gateway = FeatureGateway(workflows=self,path=data_dir / "paired_features.db")
        self._host_bindings = None
        factory = getattr(host,"_local_host_factory",None)
        if factory is not None:
            self._host_bindings = factory(self,data_dir)
        self.decisions = DecisionService(
            store=DecisionStore(data_dir / "decisions.db"), identity=host.identity,
            resources=host.resources, has_declared_owner=lambda: host.director.has_declared_owner,
            clock=host._director_clock,
        )
        self.decision_api = DecisionAPI(decisions=self.decisions, default_scope=lambda: host.identity.personal_scope_id)
        self.ghost_teacher_bindings = None
        self._owns_ghost_teacher_bindings = False
        try:
            releases = bundled_feature_releases()
            operations = {"decisions.context": self.decision_context}
            self._traceglass_routes = None
            broker = getattr(host, "traceglass_broker", None)
            self._traceglass_broker = broker
            if broker is not None:
                # This import is only enabled by the host-reviewed private integration.
                from haven.extensions.lifecycle import BundledFeatureRelease
                from traceglass.haven import TraceGlassFeature, OPERATIONS
                from traceglass.haven.service import TraceGlassServices, FeatureServiceError
                from traceglass.haven.routes import TraceGlassHostRoutes
                service = TraceGlassServices(broker)
                self._traceglass_routes = TraceGlassHostRoutes(broker=broker, context=self._context, authorize=self.authorize)
                def safe(operation):
                    def call(**params):
                        try:
                            return operation(**params)
                        except FeatureServiceError as exc:
                            raise FeatureLifecycleError(exc.code, "TraceGlass operation unavailable") from exc
                    return call
                releases += (BundledFeatureRelease("traceglass", "0.1.0", "TraceGlass", TraceGlassFeature,
                            OPERATIONS, ("inspect", "verify_package", "export_package", "capture")),)
                operations.update({OPERATIONS[0]: safe(service.inspect), OPERATIONS[1]: safe(service.verify_package),
                                   OPERATIONS[2]: safe(service.export_package), OPERATIONS[3]: safe(service.capture)})
            # Only a host-reviewed factory may attach private local bindings.
            # Importable packages alone never grant activation or data access.
            bindings = getattr(host, "ghost_teacher_bindings", None)
            if bindings is not None:
                from ghost_teacher.haven_host_patch import ghost_teacher_release, ghost_teacher_operations
                self.ghost_teacher_bindings = bindings
                releases += (ghost_teacher_release(),)
                operations.update(ghost_teacher_operations(bindings))
            self.features = FeatureLifecycle(
                data_dir / "features.json", releases=releases, authorize=self.authorize,
                service_operations=operations,
            )
        except (FeatureLifecycleError, OSError, ImportError) as exc:
            self.features = None
            self._failure("feature_lifecycle_unavailable", "features.install", exc, "Feature lifecycle initialization failed.")
        try:
            self.products = create_local_fixture_manager(
                data_dir / "marketplace-products",
                authorize=lambda principal_id, operation: self.authorize(
                    principal_id, host.identity.personal_scope_id, f"products.{operation}"),
            )
        except (ProductError, OSError) as exc:
            self.products = None
            self._failure("product_lifecycle_unavailable", "marketplace.install", exc, "Product lifecycle initialization failed.")

    def _failure(self, error, operation, exception, message):
        # Mint the safe envelope here so original exceptions reach the private
        # journal without becoming fields a downstream transport could expose.
        return public_failure(
            {"ok": False, "error": error, "message": message,
             "_private_evidence": {"type": type(exception).__name__, "message": str(exception),
                                   "errno": getattr(exception, "errno", None), "winerror": getattr(exception, "winerror", None),
                                   "traceback": "".join(traceback.format_exception(exception))}}, operation,
            journal=getattr(self.host, "faults", None), exception=exception,
            correlation_id=current() or new_id(),
        )

    def authorize(self, principal_id, scope_id, operation):
        if self._closed:
            return False
        read = {"features.list", "features.status", "decisions.context", "products.list"}
        mutations = {f"features.{name}" for name in ("install", "enable", "disable", "run", "update", "rollback", "uninstall")}
        mutations |= {f"products.{name}" for name in ("download", "install", "update", "rollback", "enable", "disable", "launch", "uninstall", "cancel")}
        # Keep feature invocation and all TraceGlass operations owner-only.
        mutations |= {"traceglass.inspect", "traceglass.verify_package", "traceglass.export_package", "traceglass.capture"}
        mutations.add("ghost_teacher.command")
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

    def paired_session_gateway(self):
        """Inject into the existing paired TLS host; never starts a listener."""
        host=self.host
        class CurrentSessionGateway:
            def call(self,*args):return host.local_workflows._bound_session_gateway().call(*args)
        return CurrentSessionGateway()

    def _bound_session_gateway(self):
        from haven.session_bridge.gateway import SessionGateway
        from haven.session_bridge.contract import AuthContext
        def authorize(admitted, permission, session):
            auth = AuthContext(admitted.principal.household_id, admitted.principal.actor_id,
                               frozenset({permission}))
            return self.sessions.allowed(auth,permission,session)
        return SessionGateway(store=self.host._external_agents_store,
            gateway=self.host.external_agent_gateway, authorize=authorize, provider=self.sessions)

    def paired_feature_gateway(self):
        host=self.host
        class CurrentFeatureGateway:
            def call(self,*args):
                return host.local_workflows.feature_gateway.call(*args)
        return CurrentFeatureGateway()

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
            except Exception as exc:
                # The domain has already committed. Do not imply rollback
                # or invite a blind retry when its history projection fails.
                return self._failure("outcome_uncertain", operation, exc,
                                     "The change completed, but history publication failed. Inspect current state before retrying.")
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
        except OSError as exc:
            return self._failure("outcome_uncertain", "features." + str(params.get("action")), exc,
                                 "Inspect feature state before retrying this operation.")

    def products_action(self, params):
        if self.products is None or self._closed:
            return {"ok": False, "error": "product_lifecycle_unavailable"}
        result = product_action(self.products, params.get("action"), params,
                                principal_id=self.host.identity.current_principal().actor_id,
                                on_failure=lambda error, message, exc: self._failure(
                                    error, "marketplace." + str(params.get("action")), exc, message))
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
        if self._traceglass_routes is not None and not self._closed:
            handlers.update({"traceglass.entitlement": self._traceglass_routes.entitlement,
                             "traceglass.picker": self._traceglass_routes.picker})
        handlers.update({"sessions.call": self.session_routes.call,
                         "features.list": self.feature_catalog, "features.action": self.feature_action,
                         "marketplace.products": self.products_view, "marketplace.product": self.products_action})
        return handlers

    def close(self):
        self._closed = True
        self.sessions.close()
        if self.features is not None:
            self.features.close()
        if self._traceglass_broker is not None:
            self._traceglass_broker.clear()
        if self._host_bindings is not None:
            self._host_bindings.close()
