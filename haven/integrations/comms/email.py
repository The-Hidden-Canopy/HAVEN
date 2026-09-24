"""Email provider contracts + a stdlib local-mail adapter (spec page 32).

Read access and send/mutate access are separate capabilities (spec page 32
decision): a connected mailbox is not permission for an agent to send mail.
The local adapter reads `.eml` files from an explicit user-configured
folder -- bounded snippet only, no full-body indexing by default. Send and
mutate stay unavailable until a credentialed provider and credential
storage exist; the capability state is explicit, never fabricated.
"""

from __future__ import annotations

import email
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path

EMAIL_PROVIDER_ID = "haven.email.local"
SNIPPET_LENGTH = 240


@dataclass(frozen=True)
class EmailCapabilities:
    read: bool
    send: bool
    mutate: bool
    detail: str


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    sender: str
    subject: str
    at: object  # timezone-aware datetime
    recipients: tuple[str, ...] = ()
    thread_id: str = ""
    labels: tuple[str, ...] = ()
    snippet: str = ""


class LocalMaildirProvider:
    """Read-only adapter over a folder of .eml files."""

    provider_id = EMAIL_PROVIDER_ID

    def __init__(self, folder: str | Path) -> None:
        self._folder = Path(folder)

    def capabilities(self) -> EmailCapabilities:
        if not self._folder.is_dir():
            return EmailCapabilities(
                read=False,
                send=False,
                mutate=False,
                detail=f"the configured mail folder does not exist: {self._folder}",
            )
        return EmailCapabilities(
            read=True,
            send=False,
            mutate=False,
            detail="read-only local .eml adapter; send/mutate need a credentialed provider",
        )

    def messages(self, *, limit: int = 100) -> tuple[EmailMessage, ...]:
        if not self._folder.is_dir():
            return ()
        rows: list[EmailMessage] = []
        for path in sorted(self._folder.glob("*.eml"), reverse=True)[:limit]:
            try:
                parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            except (OSError, email.errors.MessageError):
                continue
            body = ""
            if parsed.is_multipart():
                for part in parsed.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_content()
                        break
            else:
                try:
                    body = parsed.get_content()
                except Exception:
                    body = ""
            snippet = " ".join(str(body).split())[:SNIPPET_LENGTH]
            at = parsed.get("Date")
            rows.append(
                EmailMessage(
                    message_id=str(parsed.get("Message-ID") or path.stem),
                    sender=str(parsed.get("From") or ""),
                    subject=str(parsed.get("Subject") or "(no subject)"),
                    at=str(at or ""),
                    recipients=tuple(
                        recipient.strip()
                        for recipient in str(parsed.get("To") or "").split(",")
                        if recipient.strip()
                    ),
                    thread_id=str(parsed.get("References") or "").split()[-1]
                    if parsed.get("References")
                    else str(parsed.get("Message-ID") or path.stem),
                    labels=tuple(
                        label.strip()
                        for label in str(parsed.get("X-Labels") or parsed.get("Keywords") or "").split(",")
                        if label.strip()
                    ),
                    snippet=snippet,
                )
            )
        return tuple(rows)


class UnconfiguredEmailProvider:
    """Honest unavailability when no mail source has been configured."""

    provider_id = EMAIL_PROVIDER_ID

    def __init__(self, *, detail: str = "no email provider is configured") -> None:
        self._detail = detail

    def capabilities(self) -> EmailCapabilities:
        return EmailCapabilities(read=False, send=False, mutate=False, detail=self._detail)

    def messages(self, *, limit: int = 100) -> tuple[EmailMessage, ...]:
        return ()


__all__ = [
    "EMAIL_PROVIDER_ID",
    "EmailCapabilities",
    "EmailMessage",
    "LocalMaildirProvider",
    "SNIPPET_LENGTH",
    "UnconfiguredEmailProvider",
]
