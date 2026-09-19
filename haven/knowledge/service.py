"""The resource -> content -> candidate -> admission knowledge pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from haven.resources.models import ResourceRecord
from haven.resources.store import ResourceStore

from .admission import AdmissionResult, AdmissionStatus, ClaimAdmissionService
from .claims import is_stale
from .extraction import ClaimExtractor, ContentReader, DocumentStatementExtractor, extract_text_content
from .store import ClaimStore


@dataclass(frozen=True)
class KnowledgeIngestResult:
    resource_id: str
    skipped_unchanged: bool = False
    admitted: int = 0
    duplicates: int = 0
    suppressed: int = 0
    rejected: int = 0


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

    @property
    def claims(self) -> ClaimStore:
        return self._claims

    @property
    def resources(self) -> ResourceStore:
        return self._resources

    def ingest_resource(
        self, resource: ResourceRecord, *, reader: ContentReader
    ) -> KnowledgeIngestResult:
        """Extract only a new/changed resource, before its new row is saved."""

        previous = self._resources.get(resource.resource_id)
        if previous is not None and not previous.stale and previous.content_hash == resource.content_hash:
            return KnowledgeIngestResult(resource.resource_id, skipped_unchanged=True)

        if previous is not None:
            # A changed source invalidates claims that relied only on its old
            # contents. The new extraction below can admit fresh claims.
            self._claims.mark_stale_by_source((resource.resource_id,))

        content = extract_text_content(resource, reader=reader, now=self._clock())
        if content is None:
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

    def list_claims(self, *, scope_id: str | None = None, include_stale: bool = False):
        claims = self._claims.list_all() if scope_id is None else self._claims.list_by_scope(scope_id)
        if include_stale:
            return claims
        now = self._clock()
        return tuple(claim for claim in claims if not is_stale(claim, now=now))

    def correct_claim(self, claim_id: str, *, proposition: str, actor: str) -> AdmissionResult:
        claim = self._claims.get(claim_id)
        if claim is None:
            return AdmissionResult(AdmissionStatus.REJECTED, reason="unknown claim")
        return self._admission.correct(claim, proposition=proposition, actor=actor, now=self._clock())

    def mark_claim_stale(self, claim_id: str) -> bool:
        return self._claims.mark_stale(claim_id)

    def forget_claim(self, claim, *, forgotten_by: str) -> None:
        self._claims.forget(claim, forgotten_at=self._clock(), forgotten_by=forgotten_by)


__all__ = ["KnowledgeIngestResult", "KnowledgeService"]
