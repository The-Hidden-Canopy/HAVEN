"""`CredentialStore`: a durable, DPAPI-encrypted secret store with SQLite
metadata, and the one place a provider secret is allowed to live on disk
(native product-consolidation plan, P1: "credentials are a subsystem, not
provider config fields").

Same SQLite idiom as the other stores in this repo (`ActionLedgerStore`,
`ResourceStore`) -- one file, a fresh connection per call -- except the
secret itself is Windows-DPAPI ciphertext (`haven.credentials.dpapi`), not
plaintext, and "enumerate" (`list_metadata`) never touches the ciphertext
column at all, let alone decrypts it. `get_secret()` is the one method on
this whole store that can ever produce secret material -- it does not
return a `CredentialMetadata`, so a caller that only ever calls
`list_metadata()`/`create()`/`rotate()`/`revoke()` structurally cannot leak
one by accident.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from ..core.time import require_aware_utc
from . import dpapi

_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    credential_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    account_label TEXT NOT NULL,
    kind TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    expires_at TEXT,
    revoked INTEGER NOT NULL,
    secret_ciphertext BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS credentials_provider ON credentials(provider);
"""

_DEFAULT_CLOCK = lambda: datetime.now(timezone.utc)  # noqa: E731


class CredentialKind(str, Enum):
    """Distinguishes "who/what this secret authenticates as" (plan §5.1:
    "distinguish user credentials from machine/device keys and from model
    endpoint secrets") -- never a statement about the provider itself."""

    USER = "user"
    DEVICE = "device"
    MODEL_ENDPOINT = "model_endpoint"


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class CredentialMetadata:
    """Provenance only -- provider, account label, granted scopes, created/
    last-use/expiry, revocation status (plan §5.1). No secret field exists
    on this type; that is deliberate, not an oversight."""

    credential_id: str
    provider: str
    account_label: str
    kind: CredentialKind
    scopes: tuple[str, ...]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked: bool

    def __post_init__(self) -> None:
        for field_name in ("credential_id", "provider", "account_label"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), name=field_name))
        if not isinstance(self.kind, CredentialKind):
            raise ValueError("kind must be a CredentialKind")
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at, name="created_at"))
        if self.last_used_at is not None:
            object.__setattr__(self, "last_used_at", require_aware_utc(self.last_used_at, name="last_used_at"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at, name="expires_at"))


def _row_to_metadata(row: sqlite3.Row) -> CredentialMetadata:
    return CredentialMetadata(
        credential_id=row["credential_id"],
        provider=row["provider"],
        account_label=row["account_label"],
        kind=CredentialKind(row["kind"]),
        scopes=tuple(row["scopes"].split(",")) if row["scopes"] else (),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_used_at=datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        revoked=bool(row["revoked"]),
    )


class UnknownCredentialError(KeyError):
    pass


class CredentialStore:
    def __init__(
        self,
        path: str | Path,
        *,
        protect: Callable[[bytes], bytes] = dpapi.protect,
        unprotect: Callable[[bytes], bytes] = dpapi.unprotect,
        clock: Callable[[], datetime] = _DEFAULT_CLOCK,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._protect = protect
        self._unprotect = unprotect
        self._clock = clock
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def create(
        self,
        *,
        credential_id: str,
        provider: str,
        account_label: str,
        kind: CredentialKind,
        secret: str,
        scopes: tuple[str, ...] = (),
        expires_at: datetime | None = None,
    ) -> CredentialMetadata:
        if not isinstance(secret, str) or not secret:
            raise ValueError("secret must be a non-empty string")
        now = self._clock()
        metadata = CredentialMetadata(
            credential_id=credential_id,
            provider=provider,
            account_label=account_label,
            kind=kind,
            scopes=scopes,
            created_at=now,
            last_used_at=None,
            expires_at=expires_at,
            revoked=False,
        )
        ciphertext = self._protect(secret.encode("utf-8"))
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO credentials "
                    "(credential_id, provider, account_label, kind, scopes, created_at, last_used_at, "
                    " expires_at, revoked, secret_ciphertext) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        metadata.credential_id,
                        metadata.provider,
                        metadata.account_label,
                        metadata.kind.value,
                        ",".join(metadata.scopes),
                        metadata.created_at.isoformat(),
                        None,
                        metadata.expires_at.isoformat() if metadata.expires_at else None,
                        0,
                        ciphertext,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return metadata

    def get_secret(self, credential_id: str) -> str:
        """The only method on this store that can ever return secret
        material. Touches `last_used_at` on every successful read, since a
        credential nobody has used in months is exactly the kind of thing
        a household should be able to notice and revoke."""

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
                ).fetchone()
                if row is None:
                    raise UnknownCredentialError(credential_id)
                if row["revoked"]:
                    raise UnknownCredentialError(f"{credential_id} has been revoked")
                secret = self._unprotect(row["secret_ciphertext"]).decode("utf-8")
                conn.execute(
                    "UPDATE credentials SET last_used_at = ? WHERE credential_id = ?",
                    (self._clock().isoformat(), credential_id),
                )
                conn.commit()
            finally:
                conn.close()
        return secret

    def rotate(self, credential_id: str, *, new_secret: str) -> CredentialMetadata:
        if not isinstance(new_secret, str) or not new_secret:
            raise ValueError("new_secret must be a non-empty string")
        ciphertext = self._protect(new_secret.encode("utf-8"))
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
                ).fetchone()
                if row is None:
                    raise UnknownCredentialError(credential_id)
                conn.execute(
                    "UPDATE credentials SET secret_ciphertext = ? WHERE credential_id = ?",
                    (ciphertext, credential_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
                ).fetchone()
            finally:
                conn.close()
        return _row_to_metadata(row)

    def revoke(self, credential_id: str) -> CredentialMetadata:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
                ).fetchone()
                if row is None:
                    raise UnknownCredentialError(credential_id)
                conn.execute("UPDATE credentials SET revoked = 1 WHERE credential_id = ?", (credential_id,))
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
                ).fetchone()
            finally:
                conn.close()
        return _row_to_metadata(row)

    def list_metadata(self, *, provider: str | None = None, include_revoked: bool = True) -> tuple[CredentialMetadata, ...]:
        """Never touches `secret_ciphertext`'s decrypted value -- this is the
        "enumerate never returns secret material" method (plan §5.1)."""

        conn = self._connect()
        try:
            if provider is not None:
                rows = conn.execute("SELECT * FROM credentials WHERE provider = ?", (provider,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM credentials").fetchall()
        finally:
            conn.close()
        metadata = tuple(_row_to_metadata(row) for row in rows)
        if not include_revoked:
            metadata = tuple(item for item in metadata if not item.revoked)
        return metadata


__all__ = ["CredentialKind", "CredentialMetadata", "CredentialStore", "UnknownCredentialError"]
