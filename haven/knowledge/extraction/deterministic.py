"""A conservative, dependency-free document-statement extractor."""

from __future__ import annotations

import hashlib

from haven.resources.models import ResourceRecord

from ..candidates import CandidateClaim
from ..claims import ClaimProvenance
from .contracts import ClaimExtractor, ExtractedContent


class DocumentStatementExtractor:
    """Treat readable document statements as reported, never observed.

    This first extractor intentionally does not pretend to understand every
    file format. It takes non-heading, non-empty lines from text-like content,
    keeps the source line as evidence, and caps proposition size. A future
    parser or model can propose richer candidates through the same contract.
    """

    def extract(
        self, resource: ResourceRecord, content: ExtractedContent
    ) -> tuple[CandidateClaim, ...]:
        candidates: list[CandidateClaim] = []
        in_fence = False
        content_key = content.content_hash or hashlib.sha256(content.text.encode("utf-8")).hexdigest()
        for line_number, raw_line in enumerate(content.text.splitlines(), start=1):
            line = raw_line.strip()
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not line or line.startswith("#"):
                continue
            if line.startswith(("//", "/*", "*", "<!--")):
                continue
            if line.startswith(("- ", "* ", "+ ")):
                line = line[2:].strip()
            if len(line) < 8 or len(line) > 500:
                continue
            # Avoid turning obvious source-code assignments and braces into
            # household knowledge. Comments and prose remain eligible.
            if ("=" in line and not line.endswith(".")) or line in {"{", "}", "[", "]"}:
                continue
            digest = hashlib.sha256(f"{resource.resource_id}:{content_key}:{line_number}:{line}".encode()).hexdigest()[:16]
            candidates.append(
                CandidateClaim(
                    candidate_id=f"document:{digest}",
                    scope_id=resource.scope_id,
                    proposition=line,
                    source_refs=(resource.resource_id,),
                    evidence_refs=(f"{resource.resource_id}#line:{line_number}",),
                    provenance=ClaimProvenance.DOCUMENT_STATED,
                    proposed_confidence=0.9,
                    extracted_at=content.extracted_at,
                )
            )
        return tuple(candidates)


__all__ = ["DocumentStatementExtractor"]
