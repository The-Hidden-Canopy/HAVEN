"""Content eligibility and provider-mediated text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from haven.resources.models import ResourceRecord

from .contracts import ContentReader, ExtractedContent
from .formats import FormatExtraction, probe_format

TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst",
    }
)


def _suffix_of(resource: ResourceRecord) -> str:
    if resource.locator and resource.locator.lower().rpartition(".")[2]:
        return "." + resource.locator.lower().rpartition(".")[2]
    return ""


def extract_text_content(
    resource: ResourceRecord, *, reader: ContentReader, now: datetime
) -> ExtractedContent | None:
    """Read only text-like files through the provider's safety boundary.

    The pipeline does not open ``resource.locator`` itself. A provider owns
    that operation because it is the provider that knows whether a locator is
    still permitted (for example, a filesystem provider must re-check roots
    and symlinks at read time).
    """

    if resource.resource_type not in {"file", "document"} or not resource.locator:
        return None
    if _suffix_of(resource) not in TEXT_SUFFIXES:
        return None
    text = reader(resource)
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("content reader must return text or None")
    return ExtractedContent(
        resource_id=resource.resource_id,
        text=text,
        extracted_at=now,
        content_hash=resource.content_hash,
    )


@dataclass(frozen=True)
class DocumentExtraction:
    """A format adapter's outcome plus the normalized content it produced.

    ``unavailable`` carries the explicit capability state (NOMAD honesty,
    spec section 18): an absent optional dependency or an unreadable file
    is reported, never silently skipped.
    """

    extraction: FormatExtraction
    content: ExtractedContent | None


def extract_document_content(
    resource: ResourceRecord,
    *,
    reader: ContentReader,
    bytes_reader,
    now: datetime,
) -> DocumentExtraction | None:
    """Format-aware extraction behind the provider boundary.

    Text formats read through ``reader``; binary formats (DOCX, PDF) read
    through ``bytes_reader`` -- both supplied by the provider, which alone
    re-checks whether the locator is still permitted at read time.
    """

    if resource.resource_type not in {"file", "document"} or not resource.locator:
        return None
    suffix = _suffix_of(resource)
    if suffix in TEXT_SUFFIXES:
        return DocumentExtraction(
            extraction=FormatExtraction("text", (), True, "ok"),
            content=extract_text_content(resource, reader=reader, now=now),
        )
    text = reader(resource) if suffix not in (".docx", ".pdf") else None
    data = b""
    if suffix in (".docx", ".pdf"):
        data = bytes_reader(resource) or b""
    outcome = probe_format(suffix, data=data, text=text)
    if not outcome.available:
        return DocumentExtraction(extraction=outcome, content=None)
    if not outcome.statements:
        return DocumentExtraction(extraction=outcome, content=None)
    normalized = "\n".join(outcome.statements)
    return DocumentExtraction(
        extraction=outcome,
        content=ExtractedContent(
            resource_id=resource.resource_id,
            text=normalized,
            extracted_at=now,
            content_hash=resource.content_hash,
        ),
    )


__all__ = [
    "DocumentExtraction",
    "TEXT_SUFFIXES",
    "extract_document_content",
    "extract_text_content",
]
