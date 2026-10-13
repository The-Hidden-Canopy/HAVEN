"""Closed, product-level contracts for ``haven-marketplace-catalog.v1``.

Marketplace records describe products, not repositories and not permissions.
The contract intentionally has no shell command, credential, household scope,
or arbitrary executable field. Installation and integration are later,
separate workflows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse


class MarketplaceKind(str, Enum):
    APP = "app"
    DEVELOPER_TOOL = "developer_tool"
    GAME = "game"
    EXPERIENCE = "experience"
    LAB = "lab"


class MarketplaceReleaseState(str, Enum):
    AVAILABLE = "available"
    EARLY_ACCESS = "early_access"
    DEVELOPER_PREVIEW = "developer_preview"
    COMING_SOON = "coming_soon"
    DEPRECATED = "deprecated"
    SECURITY_HOLD = "security_hold"


class MarketplaceExecution(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"
    CONTENT_ONLY = "content_only"


class MarketplaceDistributionKind(str, Enum):
    INSTALLER = "installer"
    PORTABLE_ARCHIVE = "portable_archive"
    SOURCE = "source"
    PACKAGE_MANAGER = "package_manager"
    WEB = "web"
    EXTERNAL_STORE = "external_store"
    MODEL_MANIFEST = "model_manifest"


_PRODUCT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def safe_product_id(value: str) -> str:
    """Validate a product id before it can become a local lookup key."""

    if not isinstance(value, str):
        raise ValueError(f"product_id must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if not candidate or not _PRODUCT_ID_PATTERN.fullmatch(candidate):
        raise ValueError(
            "product_id must match ^[a-z0-9][a-z0-9-]{0,63}$: "
            f"{value!r}"
        )
    return candidate


def _text(value: Any, *, name: str, required: bool = True, max_length: int = 4000) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _string_tuple(value: Any, *, name: str, required: bool = False) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array of strings")
    values = []
    for entry in value:
        text = _text(entry, name=f"{name} entry", max_length=160)
        assert text is not None
        values.append(text)
    if required and not values:
        raise ValueError(f"{name} must not be empty")
    return tuple(dict.fromkeys(values))


def _url(value: Any, *, name: str) -> str:
    result = _text(value, name=name)
    assert result is not None
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an http(s) URL")
    return result


@dataclass(frozen=True)
class MarketplaceRelationship:
    relationship_type: str
    product_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "relationship_type", _text(self.relationship_type, name="relationship_type", max_length=80))
        object.__setattr__(self, "product_id", safe_product_id(self.product_id))

    @classmethod
    def from_dict(cls, value: Any) -> "MarketplaceRelationship":
        if not isinstance(value, dict):
            raise ValueError("relationship must be an object")
        return cls(
            relationship_type=value.get("type"),
            product_id=value.get("product_id"),
        )


@dataclass(frozen=True)
class MarketplaceDistribution:
    kind: MarketplaceDistributionKind
    release_id: str | None = None
    url: str | None = None
    platforms: tuple[str, ...] = ()
    sha256: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MarketplaceDistributionKind):
            object.__setattr__(self, "kind", MarketplaceDistributionKind(self.kind))
        if self.release_id is not None:
            object.__setattr__(self, "release_id", _text(self.release_id, name="release_id", max_length=160))
        if self.url is not None:
            object.__setattr__(self, "url", _url(self.url, name="distribution.url"))
        if self.sha256 is not None:
            digest = _text(self.sha256, name="sha256", max_length=128)
            assert digest is not None
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError("sha256 must be a 64-character hexadecimal digest")
            object.__setattr__(self, "sha256", digest.lower())
        if self.size_bytes is not None and (not isinstance(self.size_bytes, int) or self.size_bytes < 0):
            raise ValueError("size_bytes must be a non-negative integer")

    @classmethod
    def from_dict(cls, value: Any) -> "MarketplaceDistribution":
        if not isinstance(value, dict):
            raise ValueError("distribution entry must be an object")
        return cls(
            kind=MarketplaceDistributionKind(value.get("kind")),
            release_id=value.get("release_id"),
            url=value.get("url"),
            platforms=_string_tuple(value.get("platforms"), name="distribution.platforms"),
            sha256=value.get("sha256"),
            size_bytes=value.get("size_bytes"),
        )


@dataclass(frozen=True)
class MarketplaceItem:
    product_id: str
    display_name: str
    kind: MarketplaceKind
    release_state: MarketplaceReleaseState
    categories: tuple[str, ...]
    publisher: str
    summary: str | None = None
    description: str | None = None
    platforms: tuple[str, ...] = ()
    execution: MarketplaceExecution | None = None
    license_display: str | None = None
    source_visibility: str | None = None
    source_url: str | None = None
    haven_integration_available: bool = False
    integration_ids: tuple[str, ...] = ()
    relationships: tuple[MarketplaceRelationship, ...] = ()
    distribution: tuple[MarketplaceDistribution, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", safe_product_id(self.product_id))
        object.__setattr__(self, "display_name", _text(self.display_name, name="display_name", max_length=160))
        object.__setattr__(self, "publisher", _text(self.publisher, name="publisher", max_length=160))
        if not isinstance(self.kind, MarketplaceKind):
            object.__setattr__(self, "kind", MarketplaceKind(self.kind))
        if not isinstance(self.release_state, MarketplaceReleaseState):
            object.__setattr__(self, "release_state", MarketplaceReleaseState(self.release_state))
        if not self.categories:
            raise ValueError("categories must not be empty")
        object.__setattr__(self, "categories", _string_tuple(list(self.categories), name="categories", required=True))
        if self.summary is not None:
            object.__setattr__(self, "summary", _text(self.summary, name="summary", required=False, max_length=600))
        if self.description is not None:
            object.__setattr__(self, "description", _text(self.description, name="description", required=False, max_length=4000))
        if not isinstance(self.haven_integration_available, bool):
            raise ValueError("haven_integration_available must be a boolean")
        if self.execution is not None and not isinstance(self.execution, MarketplaceExecution):
            object.__setattr__(self, "execution", MarketplaceExecution(self.execution))
        if self.license_display is not None:
            object.__setattr__(self, "license_display", _text(self.license_display, name="license.display", required=False, max_length=160))
        if self.source_visibility is not None:
            object.__setattr__(self, "source_visibility", _text(self.source_visibility, name="source.visibility", required=False, max_length=80))
        if self.source_url is not None:
            object.__setattr__(self, "source_url", _url(self.source_url, name="source.url"))
        object.__setattr__(self, "platforms", _string_tuple(list(self.platforms), name="platforms"))
        object.__setattr__(self, "integration_ids", _string_tuple(list(self.integration_ids), name="integration_ids"))
        object.__setattr__(self, "relationships", tuple(self.relationships))
        object.__setattr__(self, "distribution", tuple(self.distribution))

    @property
    def has_distribution(self) -> bool:
        return bool(self.distribution)

    @classmethod
    def from_dict(cls, value: Any, *, default_publisher: str | None = None) -> "MarketplaceItem":
        if not isinstance(value, dict):
            raise ValueError("marketplace item must be an object")
        publisher = value.get("publisher", default_publisher)
        if isinstance(publisher, dict):
            publisher = publisher.get("display_name") or publisher.get("id")
        raw_source = value.get("source")
        if raw_source is not None and not isinstance(raw_source, dict):
            raise ValueError("source must be an object")
        source = raw_source or {}
        license_value = value.get("license")
        if isinstance(license_value, dict):
            license_display = license_value.get("display") or license_value.get("spdx")
        else:
            license_display = license_value
        raw_integration = value.get("haven_integration")
        if raw_integration is not None and not isinstance(raw_integration, dict):
            raise ValueError("haven_integration must be an object")
        integration = raw_integration or {}
        integration_available = integration.get("available", False)
        if not isinstance(integration_available, bool):
            raise ValueError("haven_integration.available must be a boolean")
        raw_relationships = value.get("relationships")
        if raw_relationships is not None and not isinstance(raw_relationships, list):
            raise ValueError("relationships must be an array")
        raw_distribution = value.get("distribution")
        if raw_distribution is not None and not isinstance(raw_distribution, list):
            raise ValueError("distribution must be an array")
        relationships = tuple(
            MarketplaceRelationship.from_dict(entry)
            for entry in (raw_relationships or [])
        )
        distribution = tuple(
            MarketplaceDistribution.from_dict(entry)
            for entry in (raw_distribution or [])
        )
        return cls(
            product_id=value.get("product_id"),
            display_name=value.get("display_name"),
            kind=MarketplaceKind(value.get("kind")),
            release_state=MarketplaceReleaseState(value.get("release_state")),
            categories=_string_tuple(value.get("categories"), name="categories", required=True),
            publisher=publisher,
            summary=value.get("summary"),
            description=value.get("description"),
            platforms=_string_tuple(value.get("platforms"), name="platforms"),
            execution=MarketplaceExecution(value["execution"]) if value.get("execution") is not None else None,
            license_display=license_display,
            source_visibility=source.get("visibility"),
            source_url=source.get("url"),
            haven_integration_available=integration_available,
            integration_ids=_string_tuple(integration.get("integration_ids"), name="integration_ids"),
            relationships=relationships,
            distribution=distribution,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "product_id": self.product_id,
            "display_name": self.display_name,
            "publisher": self.publisher,
            "kind": self.kind.value,
            "categories": list(self.categories),
            "release_state": self.release_state.value,
        }
        optional = {
            "summary": self.summary,
            "description": self.description,
            "platforms": list(self.platforms) or None,
            "execution": self.execution.value if self.execution is not None else None,
            "license": {"display": self.license_display} if self.license_display else None,
            "source": (
                {key: value for key, value in (("visibility", self.source_visibility), ("url", self.source_url)) if value}
                or None
            ),
            "haven_integration": (
                {
                    "available": self.haven_integration_available,
                    "integration_ids": list(self.integration_ids),
                }
                if self.haven_integration_available or self.integration_ids
                else None
            ),
            "relationships": [
                {"type": relationship.relationship_type, "product_id": relationship.product_id}
                for relationship in self.relationships
            ] or None,
            "distribution": [
                {
                    key: value
                    for key, value in (
                        ("kind", distribution.kind.value),
                        ("release_id", distribution.release_id),
                        ("url", distribution.url),
                        ("platforms", list(distribution.platforms) or None),
                        ("sha256", distribution.sha256),
                        ("size_bytes", distribution.size_bytes),
                    )
                    if value is not None
                }
                for distribution in self.distribution
            ] or None,
        }
        result.update({key: value for key, value in optional.items() if value is not None})
        return result


__all__ = [
    "MarketplaceDistribution",
    "MarketplaceDistributionKind",
    "MarketplaceExecution",
    "MarketplaceItem",
    "MarketplaceKind",
    "MarketplaceReleaseState",
    "MarketplaceRelationship",
    "safe_product_id",
]
