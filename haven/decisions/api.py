"""Shared service handlers for HAVEN's existing IPC/HTTP adapters."""

from .models import DocumentImpact, SourceRevision, text


class DecisionAPI:
    def __init__(self, *, decisions, default_scope):
        self.decisions = decisions
        self.default_scope = default_scope

    @staticmethod
    def _fields(params, required, optional=()):
        if not isinstance(params, dict):
            raise ValueError("decision parameters must be an object")
        unknown = set(params) - set(required) - set(optional) - {"scope_id"}
        if unknown:
            raise ValueError("unsupported decision parameters")
        if set(required) - set(params):
            raise ValueError("missing required decision parameters")

    def _scope(self, params):
        return text(params.get("scope_id", self.default_scope()), "scope_id")

    @staticmethod
    def _content(data):
        data = dict(data)
        if "evidence" in data:
            data["evidence"] = tuple(SourceRevision(**item) for item in data["evidence"])
        if "affected_documents" in data:
            data["affected_documents"] = tuple(
                DocumentImpact(**{**item, "source": SourceRevision(**item["source"])})
                for item in data["affected_documents"])
        return data

    def handlers(self):
        methods = {
            "decisions.snapshot": self.snapshot,
            "decisions.context": self.context,
            "decisions.propose": self.propose,
            "decisions.revise": self.revise,
            "decisions.review": self.review,
            "decisions.reconcile_document": self.reconcile_document,
            "decisions.erase": self.erase,
        }
        def envelope(method):
            def call(params):
                try:
                    return {"ok": True, **method(params)}
                except (ValueError, PermissionError, TypeError, KeyError) as exc:
                    return {"ok": False, "error": str(exc)}
            return call
        return {name: envelope(method) for name, method in methods.items()}

    def snapshot(self, params):
        self._fields(params, ())
        return {"snapshot": self.decisions.snapshot(self._scope(params)).to_dict()}

    def context(self, params):
        self._fields(params, (), ("limit", "decision_id"))
        return {"context": self.decisions.context(self._scope(params), limit=params.get("limit", 8),
                                                  decision_id=params.get("decision_id"))}

    def propose(self, params):
        required = ("expected_generation", "problem", "options", "rationale")
        optional = ("owner_id", "observations", "evidence", "constraints", "dependencies",
                    "affected_documents", "reconsideration_triggers")
        self._fields(params, required, optional)
        data = self._content({key: value for key, value in params.items() if key != "scope_id"})
        return {"decision": self.decisions.propose(self._scope(params), **data).to_dict()}

    def revise(self, params):
        self._fields(params, ("decision_id", "expected_generation", "expected_revision", "rationale"), ("changes",))
        if not isinstance(params.get("changes", {}), dict):
            raise ValueError("decision changes must be an object")
        changes = self._content(params.get("changes", {}))
        result = self.decisions.revise(self._scope(params), params["decision_id"],
                                      expected_generation=params["expected_generation"],
                                      expected_revision=params["expected_revision"], rationale=params["rationale"], **changes)
        return {"snapshot": result.to_dict()}

    def review(self, params):
        self._fields(params, ("decision_id", "expected_generation", "expected_revision", "status", "rationale", "review_ref"),
                     ("selected_option_id", "replacement_id"))
        result = self.decisions.review(self._scope(params), **{key: value for key, value in params.items() if key != "scope_id"})
        return {"snapshot": result.to_dict()}

    def reconcile_document(self, params):
        self._fields(params, ("decision_id", "expected_generation", "expected_revision", "resource_id", "observed_revision", "receipt_ref", "rationale"))
        result = self.decisions.reconcile_document(self._scope(params), **{key: value for key, value in params.items() if key != "scope_id"})
        return {"snapshot": result.to_dict()}

    def erase(self, params):
        self._fields(params, ("expected_generation", "justification"), ("decision_id",))
        result = self.decisions.erase(self._scope(params), **{key: value for key, value in params.items() if key != "scope_id"})
        return {"snapshot": result.to_dict()}
