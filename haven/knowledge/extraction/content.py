"""Content eligibility and provider-mediated text extraction."""

from __future__ import annotations

from datetime import datetime

from haven.resources.models import ResourceRecord

from .contracts import ContentReader, ExtractedContent

TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".py", ".json", ".csv", ".log", ".ini",
        ".cfg", ".yaml", ".yml", ".toml", ".rst", ".xml", ".html", ".css",
    }
)


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
    if resource.locator.lower().rpartition(".")[2]:
        suffix = "." + resource.locator.lower().rpartition(".")[2]
    else:
        suffix = ""
    if suffix not in TEXT_SUFFIXES:
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


__all__ = ["TEXT_SUFFIXES", "extract_text_content"]
