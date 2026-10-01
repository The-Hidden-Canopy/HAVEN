"""Provider credential metadata becomes honest Needs You attention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from haven.attention.sources import ProviderSource
from haven.credentials import CredentialKind, CredentialMetadata


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _metadata(
    credential_id: str,
    *,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> CredentialMetadata:
    return CredentialMetadata(
        credential_id=credential_id,
        provider="imap_smtp",
        account_label="household mailbox",
        kind=CredentialKind.USER,
        scopes=("mail.read", "mail.send"),
        created_at=NOW - timedelta(days=10),
        last_used_at=None,
        expires_at=expires_at,
        revoked=revoked,
    )


class _MetadataOnlyCredentials:
    def __init__(self, metadata):
        self._metadata = tuple(metadata)

    def list_metadata(self, *, include_revoked=True):
        assert include_revoked is True
        return self._metadata

    def get_secret(self, _credential_id):  # pragma: no cover - should never run
        raise AssertionError("ProviderSource must never read credential secrets")


def _source(metadata):
    return ProviderSource(
        credentials=_MetadataOnlyCredentials(metadata),
        identity=SimpleNamespace(personal_scope_id="scope:personal"),
    )


def test_provider_source_surfaces_expiring_and_revoked_metadata_without_secrets():
    items = _source(
        (
            _metadata("cred-expiring", expires_at=NOW + timedelta(days=2)),
            _metadata("cred-revoked", revoked=True),
            _metadata("cred-healthy", expires_at=NOW + timedelta(days=30)),
        )
    ).collect(now=NOW, visible_scope_ids=("scope:personal",))

    assert {item.source_ref for item in items} == {
        "credential:cred-expiring",
        "credential:cred-revoked",
    }
    revoked = next(item for item in items if item.source_ref.endswith("cred-revoked"))
    assert revoked.severity.value == "high"
    assert revoked.route.page == "settings"
    assert "credential" in revoked.evidence_refs[0]
    assert "secret" not in str(revoked.to_dict()).lower()


def test_provider_source_marks_expired_credentials_and_ignores_unbounded_ones():
    items = _source(
        (
            _metadata("cred-expired", expires_at=NOW - timedelta(minutes=1)),
            _metadata("cred-unbounded"),
        )
    ).collect(now=NOW, visible_scope_ids=("scope:personal",))

    assert [item.source_ref for item in items] == ["credential:cred-expired"]
    assert items[0].expires_at == NOW - timedelta(minutes=1)
