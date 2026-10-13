"""Fetch, verify, cache, and query the signed marketplace catalog.

This module deliberately does not share the existing ``haven.plugins`` trust
contract. The plugin catalog is an Export Consumer catalog; this is the wider
product catalog. A remote catalog is never accepted without an explicitly
configured marketplace public key. A bundled draft catalog is allowed only as
an explicitly unverified preview and is never install-capable.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .cache import MarketplaceCache, MarketplaceCacheError
from .contracts import MarketplaceItem, MarketplaceReleaseState

SCHEMA_VERSION = "haven-marketplace-catalog.v1"
DEFAULT_CATALOG_URL: str | None = None
_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class CatalogFetchError(RuntimeError):
    """A named marketplace catalog failure; no failure becomes fake data."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MarketplaceCatalogSnapshot:
    catalog_version: str
    publisher: str
    issued_at: datetime | None
    expires_at: datetime | None
    items: tuple[MarketplaceItem, ...]
    signature_verified: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class MarketplaceView:
    items: tuple[MarketplaceItem, ...]
    catalog_version: str | None
    publisher: str | None
    issued_at: datetime | None
    expires_at: datetime | None
    source: str
    stale: bool
    signature_verified: bool
    catalog_error: str | None


def _canonicalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value.keys())}
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonicalize(value), separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _parse_datetime(value: Any, *, name: str, required: bool) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise CatalogFetchError(f"catalog {name} is invalid", code="schema_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogFetchError(f"catalog {name} is invalid", code="schema_invalid") from exc
    if parsed.tzinfo is None:
        raise CatalogFetchError(f"catalog {name} must be timezone-aware", code="schema_invalid")
    return parsed.astimezone(timezone.utc)


def _verify_signature(
    payload: dict[str, Any],
    signature: Any,
    trusted_keys: Mapping[str, str],
) -> None:
    if not isinstance(signature, dict):
        raise CatalogFetchError("catalog signature is invalid", code="signature_invalid")
    if signature.get("algorithm") != "ed25519":
        raise CatalogFetchError("catalog signature algorithm is not ed25519", code="signature_invalid")
    key_id = signature.get("key_id")
    if not isinstance(key_id, str) or key_id not in trusted_keys:
        raise CatalogFetchError("catalog signature key is not trusted", code="signature_untrusted")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise CatalogFetchError(
            "catalog signature verification requires the optional 'cryptography' package",
            code="verification_unavailable",
        ) from exc
    try:
        signature_bytes = base64.b64decode(signature.get("value") or "", validate=True)
        public_key_bytes = base64.b64decode(trusted_keys[key_id], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature_bytes, _canonical_bytes(payload))
    except (InvalidSignature, ValueError) as exc:
        raise CatalogFetchError(
            "catalog signature does not verify against the configured marketplace key",
            code="signature_invalid",
        ) from exc


def _publisher_name(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("display_name", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    raise CatalogFetchError("catalog publisher is missing", code="schema_invalid")


def _parse_payload(
    payload: dict[str, Any],
    *,
    signature_verified: bool,
    now: datetime,
    allow_expired: bool,
) -> MarketplaceCatalogSnapshot:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CatalogFetchError("catalog schema_version is not recognized", code="schema_invalid")
    catalog_version = payload.get("catalog_version")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        raise CatalogFetchError("catalog_version is missing", code="schema_invalid")
    publisher = _publisher_name(payload.get("publisher"))
    issued_at = _parse_datetime(
        payload.get("issued_at"), name="issued_at", required=signature_verified
    )
    expires_at = _parse_datetime(
        payload.get("expires_at"), name="expires_at", required=signature_verified
    )
    if now.tzinfo is None:
        raise CatalogFetchError("catalog verification time must be timezone-aware", code="schema_invalid")
    if expires_at is not None and expires_at <= now.astimezone(timezone.utc) and not allow_expired:
        raise CatalogFetchError("catalog has expired", code="expired")
    if issued_at is not None and expires_at is not None and expires_at <= issued_at:
        raise CatalogFetchError("catalog expires_at must be after issued_at", code="schema_invalid")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise CatalogFetchError("catalog items must be an array", code="schema_invalid")
    try:
        items = tuple(
            MarketplaceItem.from_dict(entry, default_publisher=publisher)
            for entry in raw_items
        )
    except (TypeError, ValueError) as exc:
        raise CatalogFetchError(f"catalog item is invalid: {exc}", code="schema_invalid") from exc
    ids = [item.product_id for item in items]
    if len(ids) != len(set(ids)):
        raise CatalogFetchError("catalog contains duplicate product_id values", code="schema_invalid")
    return MarketplaceCatalogSnapshot(
        catalog_version=catalog_version.strip(),
        publisher=publisher,
        issued_at=issued_at,
        expires_at=expires_at,
        items=items,
        signature_verified=signature_verified,
        payload=payload,
    )


def parse_and_verify_catalog(
    raw: bytes | bytearray | dict[str, Any],
    *,
    trusted_keys: Mapping[str, str] | None = None,
    now: datetime | None = None,
    require_signature: bool = True,
    allow_expired: bool = False,
) -> MarketplaceCatalogSnapshot:
    """Parse a signed catalog, or an explicitly opt-in unsigned preview.

    ``require_signature`` must remain true for remote data. It is false only
    for the bundled draft preview, which is visibly marked unverified by the
    resulting view and carries no install operation.
    """

    if isinstance(raw, (bytes, bytearray)):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CatalogFetchError("catalog response is not valid JSON", code="invalid_payload") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise CatalogFetchError("catalog response is not a JSON object", code="invalid_payload")
    signature = data.get("signature")
    payload = {key: value for key, value in data.items() if key != "signature"}
    if signature is None:
        if require_signature:
            raise CatalogFetchError("catalog response carries no signature", code="signature_missing")
        signature_verified = False
    else:
        _verify_signature(payload, signature, trusted_keys or {})
        signature_verified = True
    verification_time = now or datetime.now(timezone.utc)
    snapshot = _parse_payload(
        payload,
        signature_verified=signature_verified,
        now=verification_time,
        allow_expired=allow_expired,
    )
    # Keep the signed envelope intact for the cache. The normalized payload
    # used for signature verification intentionally excludes ``signature``;
    # caching that stripped form would make a valid catalog unverifiable on
    # the next offline boot.
    return replace(snapshot, payload=data)


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "haven-marketplace-client",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CatalogFetchError(f"cannot reach the marketplace catalog: {exc}", code="unreachable") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise CatalogFetchError("marketplace catalog exceeds the response limit", code="response_too_large")
    return raw


def fetch_catalog(
    url: str,
    *,
    trusted_keys: Mapping[str, str],
    now: datetime | None = None,
) -> MarketplaceCatalogSnapshot:
    if not isinstance(url, str) or not url.strip():
        raise CatalogFetchError("marketplace catalog URL is not configured", code="not_configured")
    return parse_and_verify_catalog(
        _fetch_bytes(url.strip()), trusted_keys=trusted_keys, now=now
    )


class MarketplaceCatalogClient:
    """Read-only marketplace client with verified cache and draft fallback."""

    def __init__(
        self,
        cache_path: str | Path,
        *,
        catalog_url: str | None = DEFAULT_CATALOG_URL,
        trusted_keys: Mapping[str, str] | None = None,
        bundled_catalog_path: str | Path | None = None,
        clock=None,
    ) -> None:
        self._cache = MarketplaceCache(cache_path)
        self._catalog_url = catalog_url
        self._trusted_keys = dict(trusted_keys or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._snapshot: MarketplaceCatalogSnapshot | None = None
        self._source = "none"
        self._last_error: str | None = None
        self._lock = threading.RLock()
        self._load_cache()
        if self._snapshot is None and bundled_catalog_path is not None:
            self._load_bundled_preview(Path(bundled_catalog_path))

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise CatalogFetchError("marketplace clock must return an aware datetime", code="clock_invalid")
        return value.astimezone(timezone.utc)

    def _load_cache(self) -> None:
        try:
            payload, _cached_at = self._cache.load()
            self._snapshot = parse_and_verify_catalog(
                payload,
                trusted_keys=self._trusted_keys,
                now=self._now(),
                allow_expired=True,
            )
            self._source = "cache"
        except MarketplaceCacheError:
            if self._cache.path.exists():
                self._last_error = "cache_invalid"
            return
        except CatalogFetchError as exc:
            self._last_error = exc.code

    def _load_bundled_preview(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._snapshot = parse_and_verify_catalog(
                payload,
                now=self._now(),
                require_signature=False,
            )
            self._source = "bundled_preview"
        except (OSError, json.JSONDecodeError, CatalogFetchError) as exc:
            self._last_error = getattr(exc, "code", "bundled_catalog_invalid")

    def refresh(self) -> MarketplaceView:
        with self._lock:
            try:
                candidate = fetch_catalog(
                    self._catalog_url or "",
                    trusted_keys=self._trusted_keys,
                    now=self._now(),
                )
                current = self._snapshot
                if (
                    current is not None
                    and current.signature_verified
                    and candidate.signature_verified
                    and current.issued_at is not None
                    and candidate.issued_at is not None
                    and candidate.issued_at < current.issued_at
                ):
                    raise CatalogFetchError(
                        "catalog is older than the last accepted signed catalog",
                        code="replay",
                    )
                self._snapshot = candidate
                self._cache.save(self._snapshot.payload, cached_at=self._now())
                self._source = "network"
                self._last_error = None
            except CatalogFetchError as exc:
                self._last_error = exc.code
            except MarketplaceCacheError as exc:
                # A verified network payload was obtained, but durable caching
                # failed. Keep it in memory and surface the cache failure rather
                # than pretending offline recovery is available.
                self._source = "network_uncached"
                self._last_error = "cache_write_failed"
                if self._snapshot is None:
                    self._last_error = str(exc)
            return self._view_unlocked()

    def view(self) -> MarketplaceView:
        with self._lock:
            return self._view_unlocked()

    def _view_unlocked(self) -> MarketplaceView:
        snapshot = self._snapshot
        if snapshot is None:
            return MarketplaceView(
                items=(),
                catalog_version=None,
                publisher=None,
                issued_at=None,
                expires_at=None,
                source=self._source,
                stale=False,
                signature_verified=False,
                catalog_error=self._last_error,
            )
        now = self._now()
        stale = snapshot.expires_at is not None and snapshot.expires_at <= now
        return MarketplaceView(
            items=snapshot.items,
            catalog_version=snapshot.catalog_version,
            publisher=snapshot.publisher,
            issued_at=snapshot.issued_at,
            expires_at=snapshot.expires_at,
            source=self._source,
            stale=stale,
            signature_verified=snapshot.signature_verified,
            catalog_error=self._last_error,
        )

    def list_items(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        release_state: MarketplaceReleaseState | str | None = None,
    ) -> tuple[MarketplaceItem, ...]:
        with self._lock:
            items = self._view_unlocked().items
            normalized_query = query.strip().casefold() if isinstance(query, str) else ""
            normalized_category = category.strip().casefold() if isinstance(category, str) else ""
            normalized_state = (
                release_state.value if isinstance(release_state, MarketplaceReleaseState) else release_state.strip().casefold()
                if isinstance(release_state, str)
                else ""
            )
            return tuple(
                item
                for item in items
                if (
                    not normalized_query
                    or normalized_query in item.product_id
                    or normalized_query in item.display_name.casefold()
                    or normalized_query in (item.summary or "").casefold()
                )
                and (not normalized_category or normalized_category in {value.casefold() for value in item.categories})
                and (not normalized_state or item.release_state.value == normalized_state)
            )

    def get_item(self, product_id: str) -> MarketplaceItem | None:
        with self._lock:
            return next((item for item in self._view_unlocked().items if item.product_id == product_id), None)


__all__ = [
    "CatalogFetchError",
    "DEFAULT_CATALOG_URL",
    "MarketplaceCatalogClient",
    "MarketplaceCatalogSnapshot",
    "MarketplaceView",
    "SCHEMA_VERSION",
    "fetch_catalog",
    "parse_and_verify_catalog",
    "_canonical_bytes",
]
