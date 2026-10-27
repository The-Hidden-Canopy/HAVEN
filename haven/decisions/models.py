"""Versioned project decisions: concise rationale and evidence, never grants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 2
STATUSES = frozenset({"proposed", "accepted", "challenged", "superseded"})


def text(value: object, name: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")
    return value.strip()


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("recorded_at must be timezone-aware")
    return value


@dataclass(frozen=True)
class SourceRevision:
    resource_id: str
    scope_id: str
    revision: str

    def __post_init__(self) -> None:
        for name in ("resource_id", "scope_id", "revision"):
            object.__setattr__(self, name, text(getattr(self, name), name))


@dataclass(frozen=True)
class DocumentImpact:
    source: SourceRevision
    change: str
    state: str = "pending"
    receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceRevision):
            raise ValueError("document source must be a SourceRevision")
        object.__setattr__(self, "change", text(self.change, "document change"))
        if self.state not in {"pending", "reconciled"}:
            raise ValueError("unknown document state")
        if self.state == "reconciled" and not self.receipt_ref:
            raise ValueError("reconciled document requires a receipt reference")
        if self.receipt_ref is not None:
            text(self.receipt_ref, "receipt_ref")


@dataclass(frozen=True)
class DecisionRevision:
    revision: int
    status: str
    problem: str
    options: tuple[tuple[str, str], ...]
    owner_id: str
    recorded_at: str
    recorded_by: str
    rationale: str
    selected_option_id: str | None = None
    observations: tuple[str, ...] = ()
    evidence: tuple[SourceRevision, ...] = ()
    constraints: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    affected_documents: tuple[DocumentImpact, ...] = ()
    reconsideration_triggers: tuple[str, ...] = ()
    review_ref: str | None = None
    replacement_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        if self.status not in STATUSES:
            raise ValueError("unknown decision status")
        for name in ("problem", "owner_id", "recorded_by", "rationale"):
            object.__setattr__(self, name, text(getattr(self, name), name, 1600))
        timestamp(self.recorded_at)
        options = tuple((text(key, "option id", 100), text(value, "option")) for key, value in self.options)
        if not options or len(options) > 20 or len({key for key, _ in options}) != len(options):
            raise ValueError("options require 1-20 unique ids")
        object.__setattr__(self, "options", options)
        if self.selected_option_id is not None and self.selected_option_id not in dict(options):
            raise ValueError("selected direction must name an option")
        if self.status in {"accepted", "superseded"} and self.selected_option_id is None:
            raise ValueError("accepted decisions require a selected direction")
        for name in ("observations", "constraints", "dependencies", "reconsideration_triggers"):
            values = tuple(text(item, name) for item in getattr(self, name))
            if len(values) > 40 or len(set(values)) != len(values):
                raise ValueError(f"{name} must contain at most 40 unique values")
            object.__setattr__(self, name, values)
        for name, kind in (("evidence", SourceRevision), ("affected_documents", DocumentImpact)):
            values = tuple(getattr(self, name))
            if len(values) > 40 or any(not isinstance(item, kind) for item in values):
                raise ValueError(f"invalid {name}")
            object.__setattr__(self, name, values)
        if len({item.resource_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence resource")
        if len({item.source.resource_id for item in self.affected_documents}) != len(self.affected_documents):
            raise ValueError("duplicate affected document")
        for name in ("review_ref", "replacement_id"):
            if getattr(self, name) is not None:
                text(getattr(self, name), name)
        if self.status == "superseded" and self.replacement_id is None:
            raise ValueError("superseded decision requires a replacement")
        if self.status != "superseded" and self.replacement_id is not None:
            raise ValueError("only superseded decisions name replacements")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "DecisionRevision":
        data = dict(value)
        data["evidence"] = tuple(SourceRevision(**item) for item in data.get("evidence", ()))
        data["affected_documents"] = tuple(
            DocumentImpact(**{**item, "source": SourceRevision(**item["source"])})
            for item in data.get("affected_documents", ())
        )
        return cls(**data)


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    scope_id: str
    revisions: tuple[DecisionRevision, ...]

    def __post_init__(self) -> None:
        text(self.decision_id, "decision_id", 200)
        text(self.scope_id, "scope_id", 200)
        object.__setattr__(self, "revisions", tuple(self.revisions))
        if not self.revisions or len(self.revisions) > 200 or any(not isinstance(item, DecisionRevision) for item in self.revisions):
            raise ValueError("decision requires revision history")
        if [item.revision for item in self.revisions] != list(range(1, len(self.revisions) + 1)):
            raise ValueError("decision revisions must be contiguous")
        for revision in self.revisions:
            refs = (*revision.evidence, *(item.source for item in revision.affected_documents))
            if any(ref.scope_id != self.scope_id for ref in refs):
                raise ValueError("cross-scope source reference")

    @property
    def current(self) -> DecisionRevision:
        return self.revisions[-1]

    def to_dict(self) -> dict:
        return {"decision_id": self.decision_id, "scope_id": self.scope_id,
                "revisions": [item.to_dict() for item in self.revisions]}

    @classmethod
    def from_dict(cls, value: dict) -> "DecisionRecord":
        return cls(value["decision_id"], value["scope_id"],
                   tuple(DecisionRevision.from_dict(item) for item in value["revisions"]))


@dataclass(frozen=True)
class DecisionSnapshot:
    scope_id: str
    generation: int = 0
    decisions: tuple[DecisionRecord, ...] = field(default_factory=tuple)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        text(self.scope_id, "scope_id")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("invalid snapshot generation")
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported decision schema version")
        object.__setattr__(self, "decisions", tuple(self.decisions))
        records = {record.decision_id: record for record in self.decisions}
        if len(records) != len(self.decisions) or len(records) > 1000:
            raise ValueError("duplicate decisions or scope decision limit exceeded")
        for record in self.decisions:
            if record.scope_id != self.scope_id:
                raise ValueError("cross-scope decision")
            for revision in record.revisions:
                for ref in (*revision.dependencies, *((revision.replacement_id,) if revision.replacement_id else ())):
                    if ref == record.decision_id or ref not in records:
                        raise ValueError("unknown, self or cross-scope decision reference")
        # Historical replacement/dependency links remain valid and acyclic.
        for attribute in ("replacement_id", "dependencies"):
            visiting, visited = set(), set()
            def visit(key):
                if key in visiting:
                    raise ValueError(f"cyclic decision {attribute}")
                if len(visiting) >= 64:
                    raise ValueError("decision reference depth exceeds 64")
                if key in visited:
                    return
                visiting.add(key)
                for revision in records[key].revisions:
                    value = getattr(revision, attribute)
                    for target in ((value,) if isinstance(value, str) else value or ()):
                        visit(target)
                visiting.remove(key)
                visited.add(key)
            for key in records:
                visit(key)

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "scope_id": self.scope_id,
                "generation": self.generation, "decisions": [item.to_dict() for item in self.decisions]}

    @classmethod
    def from_dict(cls, value: dict) -> "DecisionSnapshot":
        data = dict(value)
        version = data.get("schema_version")
        if type(version) is not int:
            raise ValueError("unsupported decision schema version")
        if version == 1:
            # v1 had a single current revision. Preserve it as review material;
            # historical metadata cannot establish a fresh acceptance.
            migrated = []
            for item in data["decisions"]:
                current = {**item["current"], "revision": 1, "status": "proposed",
                           "review_ref": None, "replacement_id": None}
                migrated.append({"decision_id": item["decision_id"], "scope_id": data["scope_id"],
                                 "revisions": [current]})
            data.update(schema_version=SCHEMA_VERSION, decisions=migrated)
        elif version != SCHEMA_VERSION:
            raise ValueError("unsupported decision schema version")
        return cls(scope_id=data["scope_id"], generation=data["generation"],
                   decisions=tuple(DecisionRecord.from_dict(item) for item in data["decisions"]))


@dataclass(frozen=True)
class DecisionContext:
    """Canonical bounded JSON as read-only provider evidence, not live objects."""

    canonical_json: str

    def __post_init__(self):
        import json
        if not isinstance(self.canonical_json, str) or len(self.canonical_json.encode("utf-8")) > 16_000:
            raise ValueError("decision context exceeds byte budget")
        data = json.loads(self.canonical_json)
        if set(data) != {"schema_version", "scope_id", "generation", "snapshot_revision", "decisions"}:
            raise ValueError("invalid decision context fields")
        if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION or type(data["generation"]) is not int or data["generation"] < 0:
            raise ValueError("invalid decision context version")
        text(data["scope_id"], "scope_id")
        digest = data["snapshot_revision"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid decision snapshot revision")
        if not isinstance(data["decisions"], list) or len(data["decisions"]) > 12:
            raise ValueError("invalid bounded decision projection")
        for item in data["decisions"]:
            if set(item) != {"decision_id", "revision", "problem", "direction", "rationale", "constraints", "source_revisions"}:
                raise ValueError("invalid decision projection fields")
            text(item["decision_id"], "decision_id", 200)
            if type(item["revision"]) is not int or item["revision"] < 1:
                raise ValueError("invalid projected decision revision")
            for name, maximum in (("problem", 320), ("direction", 240), ("rationale", 640)):
                text(item[name], name, maximum)
            if not isinstance(item["constraints"], list) or len(item["constraints"]) > 4:
                raise ValueError("invalid context constraints")
            for constraint in item["constraints"]:
                text(constraint, "constraint")
            if not isinstance(item["source_revisions"], list) or len(item["source_revisions"]) > 80:
                raise ValueError("invalid context source revisions")
            for source in item["source_revisions"]:
                ref = SourceRevision(**source)
                if ref.scope_id != data["scope_id"]:
                    raise ValueError("cross-scope context source")

    def to_dict(self):
        import json
        return json.loads(self.canonical_json)

    @classmethod
    def from_dict(cls, value):
        import json
        return cls(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
