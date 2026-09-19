"""Small, deterministic content-to-candidate extraction contracts."""

from .contracts import ClaimExtractor, ContentReader, ExtractedContent
from .content import TEXT_SUFFIXES, extract_text_content
from .deterministic import DocumentStatementExtractor

__all__ = [
    "ClaimExtractor",
    "ContentReader",
    "DocumentStatementExtractor",
    "ExtractedContent",
    "TEXT_SUFFIXES",
    "extract_text_content",
]
