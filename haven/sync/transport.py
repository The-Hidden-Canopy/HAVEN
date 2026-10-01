"""Folder transports behind the sync provider seam (spec page 42).

An export/import directory pair, stdlib only: `send` appends encoded events
to `<export_dir>/outbox.jsonl`; `fetch` reads `<import_dir>/outbox.jsonl`
and returns events past the caller's watermark. `EncryptedFolderSyncTransport`
uses AES-GCM records in a separate outbox format, so a removable folder can
carry sync without exposing event payloads at rest. Both transports keep the
same two-method seam; a folder pair keeps a single-device install fully
functional with sync simply off.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path

from .events import SyncEvent, decode_event, encode_event

_OUTBOX_NAME = "outbox.jsonl"
_ENCRYPTED_OUTBOX_NAME = "outbox.encrypted.jsonl"
_AAD = b"haven.sync.folder.v1"


def _aes_gcm_key(shared_key: bytes | str) -> bytes:
    if isinstance(shared_key, str):
        shared_key = shared_key.encode("utf-8")
    if not isinstance(shared_key, bytes) or not shared_key:
        raise ValueError("encrypted sync requires a non-empty shared key")
    # The configured shared key remains the owner-facing secret. Derive a
    # fixed-size AES key with a domain separator rather than persisting a
    # second key or placing key material in the transport folder.
    return hashlib.sha256(b"haven.sync.aes-gcm.v1\0" + shared_key).digest()


def _aes_gcm(shared_key: bytes | str):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - depends on installation extras
        raise RuntimeError(
            "encrypted sync requires the 'cryptography' package; install HAVEN with the [crypto] extra"
        ) from exc
    return AESGCM(_aes_gcm_key(shared_key))


class FolderSyncTransport:
    def __init__(self, *, export_dir: str | Path, import_dir: str | Path) -> None:
        self._export_dir = Path(export_dir)
        self._import_dir = Path(import_dir)

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    @property
    def import_dir(self) -> Path:
        return self._import_dir

    @property
    def kind(self) -> str:
        return "folder"

    def send(self, events: tuple[SyncEvent, ...]) -> int:
        """Append events to the export outbox; returns how many were written."""

        if not events:
            return 0
        self._export_dir.mkdir(parents=True, exist_ok=True)
        with (self._export_dir / _OUTBOX_NAME).open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(encode_event(event) + "\n")
        return len(events)

    def fetch(self, *, since_seq: int, limit: int = 500) -> tuple[tuple[SyncEvent, ...], int]:
        """Events from the import inbox past `since_seq`, plus the new watermark.

        The watermark here is the remote event's own `seq` -- a folder pair
        is a 1:1 relationship, so remote seq numbers are unambiguous.
        """

        inbox = self._import_dir / _OUTBOX_NAME
        if not inbox.is_file():
            return (), since_seq
        events: list[SyncEvent] = []
        watermark = since_seq
        try:
            lines = inbox.read_text(encoding="utf-8").splitlines()
        except OSError:
            return (), since_seq
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = decode_event(line)
            except (ValueError, KeyError, TypeError):
                continue  # a corrupted line must never stall the stream
            if event.seq <= since_seq:
                continue
            events.append(event)
            watermark = max(watermark, event.seq)
            if len(events) >= limit:
                break
        return tuple(events), watermark


class EncryptedFolderSyncTransport(FolderSyncTransport):
    """AES-GCM folder transport with the same send/fetch interface.

    Each line is a URL-safe base64 encoding of ``nonce + ciphertext``. The
    event's existing HMAC signature remains inside the encrypted plaintext,
    so transport confidentiality and event-level authenticity are both
    required before a caller can apply a mutation.
    """

    def __init__(self, *, export_dir: str | Path, import_dir: str | Path, key: bytes | str) -> None:
        super().__init__(export_dir=export_dir, import_dir=import_dir)
        self._aes = _aes_gcm(key)

    @property
    def kind(self) -> str:
        return "encrypted_folder"

    def send(self, events: tuple[SyncEvent, ...]) -> int:
        if not events:
            return 0
        self._export_dir.mkdir(parents=True, exist_ok=True)
        with (self._export_dir / _ENCRYPTED_OUTBOX_NAME).open("a", encoding="ascii") as stream:
            for event in events:
                nonce = os.urandom(12)
                ciphertext = self._aes.encrypt(nonce, encode_event(event).encode("utf-8"), _AAD)
                stream.write(base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii") + "\n")
        return len(events)

    def fetch(self, *, since_seq: int, limit: int = 500) -> tuple[tuple[SyncEvent, ...], int]:
        inbox = self._import_dir / _ENCRYPTED_OUTBOX_NAME
        if not inbox.is_file():
            return (), since_seq
        from cryptography.exceptions import InvalidTag

        events: list[SyncEvent] = []
        watermark = since_seq
        try:
            lines = inbox.read_text(encoding="ascii").splitlines()
        except (OSError, UnicodeError):
            return (), since_seq
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                packed = base64.urlsafe_b64decode(line.encode("ascii"))
                if len(packed) <= 12:
                    continue
                plaintext = self._aes.decrypt(packed[:12], packed[12:], _AAD)
                event = decode_event(plaintext.decode("utf-8"))
            except (binascii.Error, InvalidTag, UnicodeError, ValueError, TypeError):
                continue
            if event.seq <= since_seq:
                continue
            events.append(event)
            watermark = max(watermark, event.seq)
            if len(events) >= limit:
                break
        return tuple(events), watermark


__all__ = ["EncryptedFolderSyncTransport", "FolderSyncTransport"]
