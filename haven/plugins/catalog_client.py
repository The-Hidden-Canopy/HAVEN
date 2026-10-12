"""Fetch and verify the Hub's signed HAVEN plugin catalog, stdlib top-level.

`cryptography` is imported lazily inside `_verify_signature`, mirroring
`haven/models/backends/transformers_backend.py`: this module is always
importable with zero third-party dependencies, and the one place that
actually needs Ed25519 verification degrades to a named, explicit failure
state (`verification_unavailable`) instead of an ImportError or, worse,
silently trusting an unverified catalog.

Every failure mode here is a named `CatalogFetchError.code`, never a
collapsed "catalog unavailable": `unreachable` (no HTTP response),
`invalid_payload` (not JSON, or missing required shape), `schema_invalid`
(fails HAVEN's own independent boundary re-check, see `_revalidate_boundary`),
`signature_missing`, `signature_invalid`, `expired`, and
`verification_unavailable`. HAVEN never trusts the Hub's own validation
result -- it re-derives the boundary invariants from the raw payload before
any entry reaches a `PluginDescriptor`.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .contracts import PluginCapability, PluginDataBoundary, PluginDescriptor, PluginStatus

DEFAULT_CATALOG_URL = "https://thehiddencanopy.com/api/haven/plugins/catalog"
SCHEMA_VERSION = "haven-plugin-catalog.v1"
CATALOG_KEY_ID = "haven-plugin-catalog-2026-09"
# The Hub's real Ed25519 verification key (public material, not a secret),
# raw 32 bytes, base64-encoded. Pinned here the same way the Hub pins its
# own signing key's fingerprint in api/haven-plugins/verify-signing-key.js --
# a HAVEN installation trusts exactly this key for this catalog, not
# whatever key happens to be presented.
PINNED_CATALOG_PUBLIC_KEY_B64 = "1hMrXQnwbBEWr1wZ6Xt2ovimrnrOeB5MP3RL2a59L1A="
_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 512 * 1024
_REQUIRED_BOUNDARY_POLICY = {
    "authority_path": "never_exposed",
    "data_scope": "hub_exported_receipts_only",
    "household_pii": "never_included",
}
_CATALOG_KEYS = frozenset(
    {
        "schema_version",
        "catalog_version",
        "issued_at",
        "expires_at",
        "boundary_policy",
        "plugins",
        "signature",
    }
)
_PAYLOAD_KEYS = _CATALOG_KEYS - {"signature"}
_BOUNDARY_POLICY_KEYS = frozenset(_REQUIRED_BOUNDARY_POLICY)
_PLUGIN_KEYS = frozenset(
    {
        "plugin_id",
        "display_name",
        "publisher",
        "capability",
        "status",
        "data_boundary",
        "description",
    }
)
_SIGNATURE_KEYS = frozenset({"algorithm", "key_id", "value"})
_MAX_PLUGINS = 200
_PRIVATE_KEYS = frozenset(
    {
        "token",
        "secret",
        "privatekey",
        "secretkey",
        "apikey",
        "localpath",
        "checkpointpath",
        "orgid",
        "organizationid",
        "workertoken",
        "prompt",
        "rawlog",
        "rawlogs",
        "password",
        "accesstoken",
        "credential",
        "clientsecret",
        "bearertoken",
        "authtoken",
        "authorization",
        "cookie",
        "privateruntime",
        "workersubject",
        "householdid",
        "householddata",
        "evidence",
        "receipt",
    }
)


class CatalogFetchError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CatalogSnapshot:
    """A verified catalog: metadata plus the plugins HAVEN will display."""

    catalog_version: str
    issued_at: datetime
    expires_at: datetime
    plugins: tuple[PluginDescriptor, ...]


def _canonicalize(value):
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value.keys())}
    return value


def _canonical_bytes(value) -> bytes:
    return json.dumps(_canonicalize(value), separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _contains_private(value) -> bool:
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    if not isinstance(value, dict):
        return False
    for key, child in value.items():
        if isinstance(key, str):
            normalized = _normalize_key(key)
            if (
                normalized in _PRIVATE_KEYS
                or "password" in normalized
                or "accesstoken" in normalized
                or "credential" in normalized
                or ("household" in normalized and normalized != "householdpii")
            ):
                return True
        if _contains_private(child):
            return True
    return False


def _assert_exact_keys(value, allowed: frozenset[str], label: str) -> None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CatalogFetchError(f"catalog {label} must be an object", code="schema_invalid")
    if any(key not in allowed for key in value):
        raise CatalogFetchError(f"catalog {label} contains an unknown field", code="schema_invalid")


def _verify_signature(payload: dict, signature: dict) -> None:
    """Verify `signature` over `payload` (already stripped of `signature`).

    Raises CatalogFetchError with code `verification_unavailable` if the
    `cryptography` package is not importable, or `signature_invalid` if it
    is importable but the signature does not check out. Never returns
    normally unless the signature is valid.
    """

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise CatalogFetchError(
            "catalog signature verification requires the optional 'cryptography' package",
            code="verification_unavailable",
        ) from exc

    _assert_exact_keys(signature, _SIGNATURE_KEYS, "signature")
    if _contains_private(signature) or signature.get("algorithm") != "ed25519":
        raise CatalogFetchError("catalog signature algorithm is not ed25519", code="signature_invalid")
    if signature.get("key_id") != CATALOG_KEY_ID:
        raise CatalogFetchError("catalog signature key_id does not match the pinned key", code="signature_invalid")
    try:
        signature_bytes = base64.b64decode(signature.get("value") or "", validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PINNED_CATALOG_PUBLIC_KEY_B64))
        public_key.verify(signature_bytes, _canonical_bytes(payload))
    except (InvalidSignature, ValueError) as exc:
        raise CatalogFetchError("catalog signature does not verify against the pinned key", code="signature_invalid") from exc


def _parse_datetime(value, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise CatalogFetchError(f"catalog {name} is invalid", code="schema_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogFetchError(f"catalog {name} is invalid", code="schema_invalid") from exc
    if parsed.tzinfo is None:
        raise CatalogFetchError(f"catalog {name} must be timezone-aware", code="schema_invalid")
    return parsed


def _revalidate_boundary(data: dict) -> None:
    """HAVEN's own independent re-check of the boundary invariants.

    This does not trust the Hub's validator (a different codebase, a
    different language) or the signature alone (a valid signature only
    proves the Hub's signing key produced these bytes, not that the bytes
    describe a safe boundary). Both must hold before any entry becomes a
    `PluginDescriptor`.
    """

    _assert_exact_keys(data, _PAYLOAD_KEYS, "payload")
    if _contains_private(data):
        raise CatalogFetchError("catalog contains private fields", code="schema_invalid")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CatalogFetchError("catalog schema_version is not recognized", code="schema_invalid")
    if not isinstance(data.get("catalog_version"), str) or not data["catalog_version"].strip():
        raise CatalogFetchError("catalog catalog_version is invalid", code="schema_invalid")
    policy = data.get("boundary_policy")
    _assert_exact_keys(policy, _BOUNDARY_POLICY_KEYS, "boundary_policy")
    for key, required in _REQUIRED_BOUNDARY_POLICY.items():
        if policy.get(key) != required:
            raise CatalogFetchError(f"catalog boundary_policy.{key} does not match the required value", code="schema_invalid")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins or len(plugins) > _MAX_PLUGINS:
        raise CatalogFetchError("catalog plugins list is missing or empty", code="schema_invalid")
    seen: set[str] = set()
    for entry in plugins:
        _assert_exact_keys(entry, _PLUGIN_KEYS, "plugin entry")
        if _contains_private(entry):
            raise CatalogFetchError("catalog plugin entry contains private fields", code="schema_invalid")
        plugin_id = entry.get("plugin_id")
        if not isinstance(plugin_id, str) or plugin_id in seen:
            raise CatalogFetchError("catalog plugin id is invalid or duplicated", code="schema_invalid")
        seen.add(plugin_id)


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "haven-plugin-catalog-client", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise CatalogFetchError("catalog response is too large", code="response_too_large")
            return raw
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CatalogFetchError(f"cannot reach the Hub plugin catalog: {exc}", code="unreachable") from exc


def parse_and_verify_catalog(raw: bytes | dict) -> CatalogSnapshot:
    """Parse, verify, and boundary-recheck a catalog payload.

    Split from `fetch_catalog` so tests (and the CLI's offline/local-file
    mode) can exercise the whole verification path without network access.
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

    _assert_exact_keys(data, _CATALOG_KEYS, "catalog")
    if _contains_private(data):
        raise CatalogFetchError("catalog contains private fields", code="schema_invalid")
    signature = data.get("signature")
    if signature is None:
        raise CatalogFetchError("catalog response carries no signature", code="signature_missing")
    payload = {key: value for key, value in data.items() if key != "signature"}
    _verify_signature(payload, signature)
    _revalidate_boundary(payload)

    issued_at = _parse_datetime(payload.get("issued_at"), name="issued_at")
    expires_at = _parse_datetime(payload.get("expires_at"), name="expires_at")
    now = datetime.now(timezone.utc)
    if issued_at > now + timedelta(minutes=5):
        raise CatalogFetchError("catalog issued_at is in the future", code="schema_invalid")
    if expires_at <= now:
        raise CatalogFetchError("catalog has expired", code="expired")
    if expires_at <= issued_at:
        raise CatalogFetchError("catalog expires_at must be after issued_at", code="schema_invalid")

    plugins: list[PluginDescriptor] = []
    for entry in payload["plugins"]:
        if not isinstance(entry, dict):
            raise CatalogFetchError("catalog plugin entry is invalid", code="schema_invalid")
        try:
            plugins.append(
                PluginDescriptor(
                    plugin_id=entry.get("plugin_id"),
                    display_name=entry.get("display_name"),
                    publisher=entry.get("publisher"),
                    capability=PluginCapability(entry.get("capability")),
                    status=PluginStatus(entry.get("status")),
                    data_boundary=PluginDataBoundary(entry.get("data_boundary")),
                    description=entry.get("description"),
                )
            )
        except ValueError as exc:
            raise CatalogFetchError(f"catalog plugin entry is invalid: {exc}", code="schema_invalid") from exc

    return CatalogSnapshot(
        catalog_version=str(payload.get("catalog_version", "")),
        issued_at=issued_at,
        expires_at=expires_at,
        plugins=tuple(plugins),
    )


def fetch_catalog(url: str = DEFAULT_CATALOG_URL) -> CatalogSnapshot:
    """Fetch, verify, and boundary-recheck the Hub's plugin catalog.

    Raises `CatalogFetchError` for every failure mode; never returns a
    partially-trusted catalog.
    """

    return parse_and_verify_catalog(_fetch_bytes(url))


__all__ = [
    "CATALOG_KEY_ID",
    "CatalogFetchError",
    "CatalogSnapshot",
    "DEFAULT_CATALOG_URL",
    "PINNED_CATALOG_PUBLIC_KEY_B64",
    "SCHEMA_VERSION",
    "fetch_catalog",
    "parse_and_verify_catalog",
]
