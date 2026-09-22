"""Catalog fetch/verify: every failure mode is a named CatalogFetchError.code.

These tests use a fresh, ephemeral Ed25519 keypair (never the real pinned
Hub key) and monkeypatch `PINNED_CATALOG_PUBLIC_KEY_B64` to match it, the
same way the Hub's own JS tests generate an ephemeral key rather than
depending on production key material.
"""

from __future__ import annotations

import base64
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from haven.plugins import catalog_client
from haven.plugins.catalog_client import CatalogFetchError, parse_and_verify_catalog

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _base_catalog(*, issued=None, expires=None) -> dict:
    issued = issued or NOW
    expires = expires or (NOW + timedelta(days=30))
    return {
        "schema_version": catalog_client.SCHEMA_VERSION,
        "catalog_version": "test-1",
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "boundary_policy": {
            "authority_path": "never_exposed",
            "data_scope": "hub_exported_receipts_only",
            "household_pii": "never_included",
        },
        "plugins": [
            {
                "plugin_id": "traceglass",
                "display_name": "TraceGlass",
                "publisher": "The Hidden Canopy LLC",
                "capability": "decision_chain_reconstruction",
                "status": "catalog_only",
                "data_boundary": "exported_receipts_only",
                "description": "Reconstructs decision chains from exported receipts.",
            },
            {
                "plugin_id": "ghost-teacher",
                "display_name": "Ghost Teacher",
                "publisher": "The Hidden Canopy LLC",
                "capability": "adaptive_curriculum_evaluation",
                "status": "catalog_only",
                "data_boundary": "exported_receipts_only",
                "description": "Uses exported evaluation signals to propose training curriculum.",
            },
        ],
    }


def _sign(payload: dict, private_key: Ed25519PrivateKey) -> dict:
    message = catalog_client._canonical_bytes(payload)
    signature = private_key.sign(message)
    signed = copy.deepcopy(payload)
    signed["signature"] = {
        "algorithm": "ed25519",
        "key_id": catalog_client.CATALOG_KEY_ID,
        "value": base64.b64encode(signature).decode("ascii"),
    }
    return signed


@pytest.fixture
def keypair():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    raw_public = public_key.public_bytes_raw()
    return private_key, base64.b64encode(raw_public).decode("ascii")


@pytest.fixture(autouse=True)
def pinned_key(monkeypatch, keypair):
    _, public_b64 = keypair
    monkeypatch.setattr(catalog_client, "PINNED_CATALOG_PUBLIC_KEY_B64", public_b64)


def test_verifies_a_genuinely_signed_catalog(keypair):
    private_key, _ = keypair
    signed = _sign(_base_catalog(), private_key)
    snapshot = parse_and_verify_catalog(json.dumps(signed).encode("utf-8"))
    ids = sorted(p.plugin_id for p in snapshot.plugins)
    assert ids == ["ghost-teacher", "traceglass"]
    assert snapshot.catalog_version == "test-1"


def test_rejects_a_catalog_signed_by_the_wrong_key(keypair):
    other_key = Ed25519PrivateKey.generate()
    signed = _sign(_base_catalog(), other_key)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "signature_invalid"


def test_rejects_any_tampering_after_signing(keypair):
    private_key, _ = keypair
    signed = _sign(_base_catalog(), private_key)
    signed["plugins"][0]["display_name"] = "Not TraceGlass"
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "signature_invalid"


def test_rejects_missing_signature():
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(_base_catalog())
    assert exc.value.code == "signature_missing"


def test_rejects_wrong_key_id(keypair):
    private_key, _ = keypair
    signed = _sign(_base_catalog(), private_key)
    signed["signature"]["key_id"] = "some-other-key"
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "signature_invalid"


def test_rejects_invalid_json_payload():
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(b"not json at all")
    assert exc.value.code == "invalid_payload"


def test_rejects_a_non_object_payload():
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(json.dumps([1, 2, 3]).encode("utf-8"))
    assert exc.value.code == "invalid_payload"


def test_rejects_expired_catalog(keypair):
    private_key, _ = keypair
    stale = _base_catalog(issued=NOW - timedelta(days=60), expires=NOW - timedelta(days=1))
    signed = _sign(stale, private_key)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "expired"


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("authority_path", "exposed_on_request"),
        ("data_scope", "raw_household_evidence"),
        ("household_pii", "included_when_consented"),
    ],
)
def test_haven_independently_rejects_a_weakened_boundary_policy_even_if_signed(keypair, key, bad_value):
    """A valid signature only proves the Hub's key produced these bytes.

    HAVEN's own re-check must still reject a payload that names an unsafe
    boundary, so a compromised or misconfigured Hub cannot widen the
    boundary just because it can produce a validly-signed catalog.
    """

    private_key, _ = keypair
    catalog = _base_catalog()
    catalog["boundary_policy"][key] = bad_value
    signed = _sign(catalog, private_key)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "schema_invalid"


def test_rejects_an_unrecognized_schema_version(keypair):
    private_key, _ = keypair
    catalog = _base_catalog()
    catalog["schema_version"] = "haven-plugin-catalog.v2"
    signed = _sign(catalog, private_key)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "schema_invalid"


def test_rejects_an_entry_with_an_unknown_capability(keypair):
    private_key, _ = keypair
    catalog = _base_catalog()
    catalog["plugins"][0]["capability"] = "arbitrary_capability"
    signed = _sign(catalog, private_key)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "schema_invalid"


def test_verification_unavailable_when_cryptography_is_not_importable(monkeypatch, keypair):
    private_key, _ = keypair
    signed = _sign(_base_catalog(), private_key)

    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("simulated: cryptography not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(CatalogFetchError) as exc:
        parse_and_verify_catalog(signed)
    assert exc.value.code == "verification_unavailable"


def test_canonical_bytes_match_the_hub_javascript_implementation():
    """Locked byte-for-byte against a real Node canonicalBytes() output.

    Captured once from `api/haven-plugins/index.js` in the Hub repo for a
    small fixture object; this is the one test that would catch a future
    accidental drift between the two canonicalizers (key sort order,
    separator whitespace, or Unicode escaping) even without cross-repo CI.
    """

    fixture = {"b": 1, "a": {"z": "é", "y": 2}, "c": [3, 2, 1]}
    assert catalog_client._canonical_bytes(fixture) == (
        b'{"a":{"y":2,"z":"\xc3\xa9"},"b":1,"c":[3,2,1]}'
    )
