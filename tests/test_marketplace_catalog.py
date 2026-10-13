"""Adversarial tests for the separate HAVEN Marketplace discovery contract."""

from __future__ import annotations

import base64
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

cryptography = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from haven.marketplace import MarketplaceCatalogClient
from haven.marketplace import catalog_client
from haven.marketplace.cache import MarketplaceCache, MarketplaceCacheError
from haven.web.marketplace_api import marketplace_item_row

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _base_catalog(*, issued: datetime = NOW, expires: datetime | None = None) -> dict:
    return {
        "schema_version": catalog_client.SCHEMA_VERSION,
        "catalog_version": "test-1",
        "publisher": "The Hidden Canopy LLC",
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (expires or (issued + timedelta(days=30))).isoformat().replace("+00:00", "Z"),
        "items": [
            {
                "product_id": "cartographer",
                "display_name": "Cartographer",
                "kind": "developer_tool",
                "release_state": "early_access",
                "categories": ["creative", "developer_tools"],
                "summary": "A geometry authoring environment.",
                "execution": "local",
                "source": {"visibility": "public", "url": "https://example.test/cartographer"},
                "distribution": [{"kind": "source", "platforms": ["windows-x86_64"]}],
            },
            {
                "product_id": "ask-ida",
                "display_name": "Ask IDA",
                "kind": "app",
                "release_state": "coming_soon",
                "categories": ["intelligence"],
            },
        ],
    }


def _sign(payload: dict, private_key: Ed25519PrivateKey) -> dict:
    signed = copy.deepcopy(payload)
    signed["signature"] = {
        "algorithm": "ed25519",
        "key_id": "marketplace-test-key",
        "value": base64.b64encode(
            private_key.sign(catalog_client._canonical_bytes(payload))
        ).decode("ascii"),
    }
    return signed


@pytest.fixture
def keypair():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    return private_key, base64.b64encode(public_key).decode("ascii")


def test_signed_catalog_is_verified_and_preserves_product_states(keypair):
    private_key, public_b64 = keypair
    snapshot = catalog_client.parse_and_verify_catalog(
        _sign(_base_catalog(), private_key),
        trusted_keys={"marketplace-test-key": public_b64},
        now=NOW,
    )
    assert snapshot.signature_verified is True
    assert [item.product_id for item in snapshot.items] == ["cartographer", "ask-ida"]
    assert snapshot.items[0].release_state.value == "early_access"
    assert snapshot.items[1].release_state.value == "coming_soon"


def test_remote_catalog_requires_a_signature():
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(_base_catalog(), now=NOW)
    assert exc.value.code == "signature_missing"


def test_catalog_rejects_a_signature_from_an_unknown_key(keypair):
    private_key, _public_b64 = keypair
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(_base_catalog(), private_key),
            trusted_keys={},
            now=NOW,
        )
    assert exc.value.code == "signature_untrusted"


def test_tampering_after_signature_is_rejected(keypair):
    private_key, public_b64 = keypair
    signed = _sign(_base_catalog(), private_key)
    signed["items"][0]["release_state"] = "available"
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            signed,
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "signature_invalid"


def test_naive_catalog_timestamps_fail_closed(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog()
    payload["issued_at"] = "2026-10-04T12:00:00"
    signed = _sign(payload, private_key)
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            signed,
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "schema_invalid"


def test_catalog_with_non_chronological_lifetime_is_rejected(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog(
        issued=NOW,
        expires=NOW - timedelta(minutes=1),
    )
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(payload, private_key),
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW - timedelta(days=1),
        )
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source", "haven_integration", "relationships", "distribution"])
def test_malformed_known_fields_fail_closed(keypair, field):
    private_key, public_b64 = keypair
    payload = _base_catalog()
    payload["items"][0][field] = "not-an-object-or-array"
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(payload, private_key),
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "schema_invalid"


def test_duplicate_product_ids_are_rejected(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog()
    payload["items"][1]["product_id"] = payload["items"][0]["product_id"]
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(payload, private_key),
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "schema_invalid"


def test_integration_status_cannot_coerce_untrusted_text_to_true(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog()
    payload["items"][0]["haven_integration"] = {"available": "false"}
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(payload, private_key),
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "schema_invalid"


def test_arbitrary_install_fields_are_not_exposed_by_the_wire_projection(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog()
    payload["items"][0]["install_command"] = "powershell -c Remove-Item *"
    snapshot = catalog_client.parse_and_verify_catalog(
        _sign(payload, private_key),
        trusted_keys={"marketplace-test-key": public_b64},
        now=NOW,
    )
    row = marketplace_item_row(snapshot.items[0])
    assert "install_command" not in row
    assert row["install_available"] is False


def test_expired_catalog_is_rejected_from_network(keypair):
    private_key, public_b64 = keypair
    payload = _base_catalog(expires=NOW - timedelta(minutes=1))
    with pytest.raises(catalog_client.CatalogFetchError) as exc:
        catalog_client.parse_and_verify_catalog(
            _sign(payload, private_key),
            trusted_keys={"marketplace-test-key": public_b64},
            now=NOW,
        )
    assert exc.value.code == "expired"


def test_unsigned_bundled_preview_is_explicitly_unverified(tmp_path):
    bundled = tmp_path / "starter.json"
    bundled.write_text(json.dumps(_base_catalog()), encoding="utf-8")
    client = MarketplaceCatalogClient(
        tmp_path / "cache.json",
        bundled_catalog_path=bundled,
        clock=lambda: NOW,
    )
    view = client.view()
    assert view.source == "bundled_preview"
    assert view.signature_verified is False
    assert view.stale is False
    assert client.list_items(release_state="coming_soon")[0].product_id == "ask-ida"


def test_verified_catalog_survives_restart_from_last_known_good_cache(tmp_path, keypair, monkeypatch):
    private_key, public_b64 = keypair
    signed = _sign(_base_catalog(), private_key)
    monkeypatch.setattr(catalog_client, "_fetch_bytes", lambda _url: json.dumps(signed).encode("utf-8"))
    cache_path = tmp_path / "cache.json"
    client = MarketplaceCatalogClient(
        cache_path,
        catalog_url="https://catalog.example.test/marketplace.json",
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    assert client.refresh().source == "network"

    restarted = MarketplaceCatalogClient(
        cache_path,
        catalog_url=None,
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    view = restarted.view()
    assert view.source == "cache"
    assert view.signature_verified is True
    assert len(view.items) == 2


def test_concurrent_refreshes_leave_a_valid_last_known_good_cache(tmp_path, keypair, monkeypatch):
    private_key, public_b64 = keypair
    signed = _sign(_base_catalog(), private_key)
    monkeypatch.setattr(catalog_client, "_fetch_bytes", lambda _url: json.dumps(signed).encode("utf-8"))
    cache_path = tmp_path / "cache.json"
    client = MarketplaceCatalogClient(
        cache_path,
        catalog_url="https://catalog.example.test/marketplace.json",
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        views = list(pool.map(lambda _index: client.refresh(), range(8)))
    assert all(view.catalog_error is None for view in views)
    restarted = MarketplaceCatalogClient(
        cache_path,
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    assert restarted.view().source == "cache"
    assert restarted.view().signature_verified is True


def test_refresh_rejects_an_older_signed_catalog_as_a_replay(tmp_path, keypair, monkeypatch):
    private_key, public_b64 = keypair
    newer = _base_catalog(issued=NOW)
    newer["catalog_version"] = "newer"
    older = _base_catalog(issued=NOW - timedelta(days=1))
    older["catalog_version"] = "older"
    payloads = iter((newer, older))
    monkeypatch.setattr(
        catalog_client,
        "_fetch_bytes",
        lambda _url: json.dumps(_sign(next(payloads), private_key)).encode("utf-8"),
    )
    client = MarketplaceCatalogClient(
        tmp_path / "cache.json",
        catalog_url="https://catalog.example.test/marketplace.json",
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    assert client.refresh().catalog_version == "newer"
    view = client.refresh()
    assert view.catalog_error == "replay"
    assert view.catalog_version == "newer"


def test_failed_refresh_keeps_expired_cache_and_marks_it_stale(tmp_path, keypair, monkeypatch):
    private_key, public_b64 = keypair
    signed = _sign(_base_catalog(), private_key)
    monkeypatch.setattr(catalog_client, "_fetch_bytes", lambda _url: json.dumps(signed).encode("utf-8"))
    cache_path = tmp_path / "cache.json"
    client = MarketplaceCatalogClient(
        cache_path,
        catalog_url="https://catalog.example.test/marketplace.json",
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW,
    )
    client.refresh()
    later = MarketplaceCatalogClient(
        cache_path,
        catalog_url="https://catalog.example.test/marketplace.json",
        trusted_keys={"marketplace-test-key": public_b64},
        clock=lambda: NOW + timedelta(days=31),
    )
    monkeypatch.setattr(
        catalog_client,
        "_fetch_bytes",
        lambda _url: (_ for _ in ()).throw(catalog_client.CatalogFetchError("down", code="unreachable")),
    )
    view = later.refresh()
    assert view.catalog_error == "unreachable"
    assert view.stale is True
    assert len(view.items) == 2


def test_corrupt_cache_is_not_treated_as_an_empty_catalog(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(MarketplaceCacheError):
        MarketplaceCache(path).load()
