"""CredentialStore: DPAPI-encrypted secrets, SQLite metadata, enumerate
never returns secret material (native product-consolidation plan, P1
"Credentials").

Most tests inject a reversible fake protect/unprotect pair so they run
deterministically regardless of the Windows user profile running them;
`test_real_dpapi_round_trip` proves the actual production mechanism
(`haven.credentials.dpapi`) works end to end, the same "prove it against
the real mechanism" bar `test_bluetooth_fixture_backend.py` sets on the
Bluetooth side.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from haven.credentials import CredentialKind, CredentialStore, UnknownCredentialError
from haven.credentials import dpapi

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _fake_protect(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


def _fake_unprotect(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


def _store(tmp_path: Path, *, clock=lambda: NOW) -> CredentialStore:
    return CredentialStore(tmp_path / "credentials.db", protect=_fake_protect, unprotect=_fake_unprotect, clock=clock)


def test_create_and_get_secret_round_trips(tmp_path):
    store = _store(tmp_path)
    metadata = store.create(
        credential_id="cred-1",
        provider="home_assistant",
        account_label="Home Assistant (primary)",
        kind=CredentialKind.USER,
        secret="super-secret-token",
        scopes=("read", "control"),
    )
    assert metadata.revoked is False
    assert metadata.created_at == NOW
    assert store.get_secret("cred-1") == "super-secret-token"


def test_list_metadata_never_carries_the_secret(tmp_path):
    store = _store(tmp_path)
    store.create(
        credential_id="cred-1",
        provider="home_assistant",
        account_label="HA",
        kind=CredentialKind.USER,
        secret="super-secret-token",
    )
    listed = store.list_metadata()
    assert len(listed) == 1
    dumped = repr(listed[0]) + str(vars(listed[0]))
    assert "super-secret-token" not in dumped
    assert not hasattr(listed[0], "secret")


def test_get_secret_touches_last_used_at(tmp_path):
    clock = {"now": NOW}
    store = _store(tmp_path, clock=lambda: clock["now"])
    store.create(
        credential_id="cred-1", provider="p", account_label="a", kind=CredentialKind.USER, secret="s"
    )
    assert store.list_metadata()[0].last_used_at is None

    clock["now"] = NOW + timedelta(minutes=5)
    store.get_secret("cred-1")
    assert store.list_metadata()[0].last_used_at == NOW + timedelta(minutes=5)


def test_rotate_replaces_the_secret_without_changing_metadata_identity(tmp_path):
    store = _store(tmp_path)
    store.create(credential_id="cred-1", provider="p", account_label="a", kind=CredentialKind.USER, secret="old")
    store.rotate("cred-1", new_secret="new")
    assert store.get_secret("cred-1") == "new"
    assert store.list_metadata()[0].credential_id == "cred-1"


def test_revoke_makes_the_secret_unreachable_but_keeps_the_metadata(tmp_path):
    store = _store(tmp_path)
    store.create(credential_id="cred-1", provider="p", account_label="a", kind=CredentialKind.USER, secret="s")
    store.revoke("cred-1")

    assert store.list_metadata()[0].revoked is True
    with pytest.raises(UnknownCredentialError):
        store.get_secret("cred-1")


def test_get_secret_on_unknown_credential_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(UnknownCredentialError):
        store.get_secret("nope")


def test_list_metadata_filters_by_provider(tmp_path):
    store = _store(tmp_path)
    store.create(credential_id="cred-1", provider="home_assistant", account_label="a", kind=CredentialKind.USER, secret="s")
    store.create(credential_id="cred-2", provider="model_endpoint", account_label="b", kind=CredentialKind.MODEL_ENDPOINT, secret="s2")

    assert {m.credential_id for m in store.list_metadata(provider="home_assistant")} == {"cred-1"}
    assert {m.credential_id for m in store.list_metadata()} == {"cred-1", "cred-2"}


def test_list_metadata_can_exclude_revoked(tmp_path):
    store = _store(tmp_path)
    store.create(credential_id="cred-1", provider="p", account_label="a", kind=CredentialKind.USER, secret="s")
    store.create(credential_id="cred-2", provider="p", account_label="b", kind=CredentialKind.USER, secret="s2")
    store.revoke("cred-1")

    assert {m.credential_id for m in store.list_metadata(include_revoked=False)} == {"cred-2"}


def test_secrets_are_encrypted_at_rest_not_plaintext(tmp_path):
    store = _store(tmp_path)
    store.create(
        credential_id="cred-1", provider="p", account_label="a", kind=CredentialKind.USER, secret="super-secret-token"
    )
    raw = (tmp_path / "credentials.db").read_bytes()
    assert b"super-secret-token" not in raw


@pytest.mark.skipif(dpapi.platform.system() != "Windows", reason="Windows DPAPI is only available on Windows")
def test_real_dpapi_round_trip(tmp_path):
    """No fake protect/unprotect here -- the real Windows DPAPI mechanism,
    proving the production path (not just the store's own SQL/plumbing)."""

    store = CredentialStore(tmp_path / "credentials.db", clock=lambda: NOW)
    store.create(
        credential_id="cred-1",
        provider="home_assistant",
        account_label="HA",
        kind=CredentialKind.USER,
        secret="a-real-dpapi-protected-secret",
    )
    raw = (tmp_path / "credentials.db").read_bytes()
    assert b"a-real-dpapi-protected-secret" not in raw
    assert store.get_secret("cred-1") == "a-real-dpapi-protected-secret"
