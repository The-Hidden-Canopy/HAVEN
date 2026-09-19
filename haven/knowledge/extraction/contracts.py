"""Contracts between resource providers and the knowledge pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from haven.core.time import require_aware_utc
from haven.resources.models import ResourceRecord

from ..candidates import CandidateClaim


@dataclass(frozen=True)
class ExtractedContent:
    resource_id: str
    text: str
    extracted_at: datetime
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource_id, str) or not self.resource_id.strip():
            raise ValueError("resource_id must be a non-empty string")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        object.__setattr__(self, "extracted_at", require_aware_utc(self.extracted_at, name="extracted_at"))


class ContentReader(Protocol):
    def __call__(self, resource: ResourceRecord) -> str | None:
        """Read one already-authorized resource, or return unavailable."""


class ClaimExtractor(Protocol):
    def extract(
        self, resource: ResourceRecord, content: ExtractedContent
    ) -> tuple[CandidateClaim, ...]:
        """Propose claims without writing or choosing a belief state."""


__all__ = ["ClaimExtractor", "ContentReader", "ExtractedContent"]
