"""The resource -> content -> candidate -> admission knowledge pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence
from uuid import uuid4

from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

from .admission import AdmissionResult, AdmissionStatus, ClaimAdmissionService
from .audit import KnowledgeAuditAction, KnowledgeAuditEvent
from .claims import is_stale
from .extraction import (
    ClaimExtractor,
    ContentReader,
    DocumentStatementExtractor,
    extract_document_content,
)
from .store import ClaimStore


@dataclass(frozen=True)
class KnowledgeIngestResult:
    resource_id: str
    skipped_unchanged: bool = False
    admitted: int = 0
    duplicates: int = 0
    suppressed: int = 0
    rejected: int = 0
    # Explicit capability state for format adapters (NOMAD honesty): an
    # absent optional dependency or unreadable document is reported here,
    # never silently skipped.
    unavailable: tuple[str, ...] = ()


def _no_bytes(resource: ResourceRecord) -> bytes | None:
    """Default binary reader: binary formats simply report unavailable.

    Providers that support binary reads (the filesystem provider) supply
    their own boundary-checked reader; callers that only have a text reader
    degrade honestly instead of guessing an encoding.
    """

    return None


class KnowledgeService:
    """Own the only runtime path that turns observed resources into claims."""

    def __init__(
        self,
        *,
        resources: ResourceStore,
        claims: ClaimStore,
        admission: ClaimAdmissionService | None = None,
        extractors: Sequence[ClaimExtractor] | None = None,
        clock=None,
    ) -> None:
        self._resources = resources
        self._claims = claims
        self._admission = admission or ClaimAdmissionService(claims)
        self._extractors = tuple(extractors or (DocumentStatementExtractor(),))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mutation_listener = None

    def set_mutation_listener(self, listener) -> None:
        self._mutation_listener = listener

    def _emit_claim(self, claim) -> None:
        listener = self._mutation_listener
        if listener is not None and claim is not None:
            listener("claim", claim)

    @property
    def claims(self) -> ClaimStore:
        return self._claims

    @property
    def resources(self) -> ResourceStore:
        return self._resources

    def ingest_resource(
        self, resource: ResourceRecord, *, reader: ContentReader, bytes_reader=None
    ) -> KnowledgeIngestResult:
        """Extract only a new/changed resource, before its new row is saved."""

        previous = self._resources.get(resource.resource_id)
        if previous is not None and not previous.stale and previous.content_hash == resource.content_hash:
            return KnowledgeIngestResult(resource.resource_id, skipped_unchanged=True)

        if previous is not None:
            # A changed source invalidates claims that relied only on its old
            # contents. The new extraction below can admit fresh claims.
            self._claims.mark_stale_by_source((resource.resource_id,))

        document = extract_document_content(
            resource, reader=reader, bytes_reader=bytes_reader or _no_bytes, now=self._clock()
        )
        if document is None:
            return KnowledgeIngestResult(resource.resource_id)
        if not document.extraction.available:
            return KnowledgeIngestResult(
                resource.resource_id,
                unavailable=(f"{document.extraction.format}: {document.extraction.detail}",),
            )
        content = document.content
        if content is None or not content.text.strip():
            return KnowledgeIngestResult(resource.resource_id)

        results: list[AdmissionResult] = []
        for extractor in self._extractors:
            results.extend(extractor.extract(resource, content))
        counts = {status: 0 for status in AdmissionStatus}
        for candidate in results:
            counts[self._admission.admit(candidate).status] += 1
        return KnowledgeIngestResult(
            resource_id=resource.resource_id,
            admitted=counts[AdmissionStatus.ADMITTED],
            duplicates=counts[AdmissionStatus.DUPLICATE],
            suppressed=counts[AdmissionStatus.SUPPRESSED],
            rejected=counts[AdmissionStatus.REJECTED],
        )

    def reconcile_stale_sources(self, *, provider_id: str, scope_id: str) -> int:
        """Stale claims whose only source resources are stale."""

        stale_ids = {
            record.resource_id
            for record in self._resources.list_by_scope(scope_id)
            if record.provider_id == provider_id and record.stale
        }
        return self._claims.mark_stale_by_source(stale_ids)

    def revoke_locator_prefix(self, prefix: str) -> tuple[int, int]:
        """Revoke resource visibility and stale claims derived from it."""

        normalized = prefix.rstrip("/\\")
        source_ids = {
            record.resource_id
            for record in self._resources.list_all()
            if record.locator is not None
            and (
                record.locator == normalized
                or (
                    record.locator.startswith(normalized)
                    and record.locator[len(normalized) : len(normalized) + 1] in ("/", "\\")
                )
            )
            and not record.stale
        }
        resources_marked = self._resources.mark_stale_by_locator_prefix(normalized)
        claims_marked = self._claims.mark_stale_by_source(source_ids)
        return resources_marked, claims_marked

    def list_claims(
        self,
        *,
        scope_id: str | None = None,
        scope_ids: tuple[str, ...] | None = None,
        include_stale: bool = False,
    ):
        if scope_ids is not None:
            claims = tuple(
                claim
                for scope in scope_ids
                for claim in self._claims.list_by_scope(scope)
            )
        else:
            claims = self._claims.list_all() if scope_id is None else self._claims.list_by_scope(scope_id)
        if include_stale:
            return claims
        now = self._clock()
        return tuple(claim for claim in claims if not is_stale(claim, now=now))

    def correct_claim(self, claim_id: str, *, proposition: str, actor: str) -> AdmissionResult:
        claim = self._claims.get(claim_id)
        if claim is None:
            return AdmissionResult(AdmissionStatus.REJECTED, reason="unknown claim")
        actor = self._require_actor(actor)
        now = self._clock()
        result = self._admission.correct(
            claim,
            proposition=proposition,
            actor=actor,
            now=now,
            audit_factory=lambda replacement: self._audit_event(
                scope_id=claim.scope_id,
                claim_id=replacement.claim_id,
                action=KnowledgeAuditAction.CORRECT,
                actor_id=actor,
                occurred_at=now,
                details=(
                    ("prior_claim_id", claim.claim_id),
                    ("replacement_claim_id", replacement.claim_id),
                ),
            ),
        )
        if result.claim is not None:
            self._emit_claim(result.claim)
        return result

    def mark_claim_stale(self, claim_id: str, *, actor: str) -> bool:
        actor = self._require_actor(actor)
        claim = self._claims.get(claim_id)
        if claim is None:
            return False
        changed = self._claims.mark_stale_with_audit(
            claim_id,
            self._audit_event(
                scope_id=claim.scope_id,
                claim_id=claim.claim_id,
                action=KnowledgeAuditAction.MARK_STALE,
                actor_id=actor,
                occurred_at=self._clock(),
            ),
        )
        if changed:
            self._emit_claim(self._claims.get(claim_id))
        return changed

    def forget_claim(self, claim, *, forgotten_by: str) -> None:
        forgotten_by = self._require_actor(forgotten_by)
        now = self._clock()
        self._claims.forget_with_audit(
            claim,
            forgotten_at=now,
            forgotten_by=forgotten_by,
            event=self._audit_event(
                scope_id=claim.scope_id,
                claim_id=claim.claim_id,
                action=KnowledgeAuditAction.FORGET,
                actor_id=forgotten_by,
                occurred_at=now,
            ),
        )
        self._emit_claim(self._claims.get(claim.claim_id))

    @staticmethod
    def _require_actor(actor: str) -> str:
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("a knowledge mutation actor is required")
        return actor.strip()

    def _audit_event(
        self,
        *,
        scope_id: str,
        claim_id: str,
        action: KnowledgeAuditAction,
        actor_id: str,
        occurred_at: datetime,
        details: tuple[tuple[str, object], ...] = (),
    ) -> KnowledgeAuditEvent:
        return KnowledgeAuditEvent(
            event_id=f"knowledge-audit:{uuid4().hex}",
            scope_id=scope_id,
            claim_id=claim_id,
            action=action,
            actor_id=actor_id,
            occurred_at=occurred_at,
            details=details,
        )


__all__ = ["KnowledgeIngestResult", "KnowledgeService"]
