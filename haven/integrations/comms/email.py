"""Email provider contracts and stdlib local/credentialed adapters (spec page 32).

Read access and send/mutate access are separate capabilities (spec page 32
decision): a connected mailbox is not permission for an agent to send mail.
The local adapter reads `.eml` files from an explicit user-configured
folder -- bounded snippet only, no full-body indexing by default. The
credentialed adapter uses IMAP over TLS for bounded reads and SMTP over TLS
for sends; the secret is supplied by the credential store only at connection
time and is never part of the provider configuration.
"""

from __future__ import annotations

import email
import imaplib
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as MimeEmailMessage
from email import policy
from email.parser import BytesParser
from email.utils import make_msgid
from pathlib import Path
from typing import Callable

EMAIL_PROVIDER_ID = "haven.email.local"
IMAP_SMTP_PROVIDER_ID = "haven.email.imap_smtp"
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
    body: str = ""


class EmailProviderError(RuntimeError):
    """A provider could not complete an operation; no result is fabricated."""


class CredentialEmailProvider:
    """Read through IMAP and send through SMTP using a stored secret.

    Factories are injectable so the protocol contract can be tested without
    contacting a real mailbox. Production defaults are the stdlib TLS
    clients; callers provide only a secret loader, never a secret field.
    """

    provider_id = IMAP_SMTP_PROVIDER_ID

    def __init__(
        self,
        *,
        imap_host: str,
        imap_port: int,
        smtp_host: str,
        smtp_port: int,
        username: str,
        secret_loader: Callable[[], str],
        mailbox: str = "INBOX",
        imap_factory=imaplib.IMAP4_SSL,
        smtp_factory=smtplib.SMTP_SSL,
    ) -> None:
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._secret_loader = secret_loader
        self._mailbox = mailbox
        self._imap_factory = imap_factory
        self._smtp_factory = smtp_factory

    def capabilities(self) -> EmailCapabilities:
        return EmailCapabilities(
            read=True,
            send=True,
            mutate=True,
            detail="credentialed IMAP read + SMTP send over TLS; network reachability is checked per operation",
        )

    def messages(self, *, limit: int = 100, include_body: bool = False) -> tuple[EmailMessage, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(include_body, bool):
            raise ValueError("include_body must be a boolean")
        client = None
        try:
            client = self._imap_factory(self._imap_host, self._imap_port)
            status, _ = client.login(self._username, self._secret_loader())
            if status != "OK":
                raise EmailProviderError("IMAP login was rejected")
            status, _ = client.select(self._mailbox, readonly=True)
            if status != "OK":
                raise EmailProviderError(f"IMAP mailbox is unavailable: {self._mailbox}")
            status, data = client.search(None, "ALL")
            if status != "OK":
                raise EmailProviderError("IMAP search failed")
            message_ids = (data[0] if data else b"").split()[-limit:]
            rows: list[EmailMessage] = []
            for message_id in reversed(message_ids):
                status, fetched = client.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                raw = b"".join(part[1] for part in fetched if isinstance(part, tuple) and len(part) > 1)
                if raw:
                    rows.append(
                        _parsed_email_message(
                            BytesParser(policy=policy.default).parsebytes(raw),
                            include_body=include_body,
                        )
                    )
            return tuple(rows)
        except EmailProviderError:
            raise
        except (OSError, imaplib.IMAP4.error, email.errors.MessageError) as exc:
            raise EmailProviderError(f"IMAP read failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except (OSError, imaplib.IMAP4.error):
                    pass
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass

    def send(
        self,
        *,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
        cc: tuple[str, ...] = (),
    ) -> str:
        message = MimeEmailMessage()
        message["From"] = self._username
        message["To"] = ", ".join(recipients)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Message-ID"] = make_msgid()
        message.set_content(body)
        try:
            client = self._smtp_factory(self._smtp_host, self._smtp_port)
            try:
                status, _ = client.login(self._username, self._secret_loader())
                if status != 235:
                    raise EmailProviderError("SMTP login was rejected")
                client.send_message(message)
            finally:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    pass
        except EmailProviderError:
            raise
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailProviderError(f"SMTP send failed: {exc}") from exc
        return str(message["Message-ID"] or "")

    def delete(self, message_id: str) -> bool:
        """Delete exactly one message by its RFC 5322 ``Message-ID``.

        IMAP sequence numbers are mailbox-local and can change between reads,
        so the provider resolves the stable message id inside the same
        selected mailbox before marking the message deleted. The final search
        after EXPUNGE is the provider-side verification boundary; callers
        still place this operation behind HAVEN's confirmation ledger.
        """

        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("message_id must be a non-empty string")
        normalized_id = message_id.strip()
        client = None
        try:
            client = self._imap_factory(self._imap_host, self._imap_port)
            status, _ = client.login(self._username, self._secret_loader())
            if status != "OK":
                raise EmailProviderError("IMAP login was rejected")
            status, _ = client.select(self._mailbox, readonly=False)
            if status != "OK":
                raise EmailProviderError(f"IMAP mailbox is unavailable: {self._mailbox}")
            status, data = client.search(None, "HEADER", "Message-ID", normalized_id)
            if status != "OK":
                raise EmailProviderError("IMAP message lookup failed")
            message_ids = (data[0] if data else b"").split()
            if not message_ids:
                return False
            if len(message_ids) > 1:
                raise EmailProviderError("message id matched more than one mailbox message")
            sequence_id = message_ids[0]
            status, _ = client.store(sequence_id, "+FLAGS", "\\Deleted")
            if status != "OK":
                raise EmailProviderError("IMAP message delete was rejected")
            status, _ = client.expunge()
            if status != "OK":
                raise EmailProviderError("IMAP expunge failed")
            status, data = client.search(None, "HEADER", "Message-ID", normalized_id)
            if status != "OK":
                raise EmailProviderError("IMAP delete verification failed")
            if (data[0] if data else b"").split():
                raise EmailProviderError("IMAP message delete could not be verified")
            return True
        except EmailProviderError:
            raise
        except (OSError, imaplib.IMAP4.error, email.errors.MessageError) as exc:
            raise EmailProviderError(f"IMAP delete failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except (OSError, imaplib.IMAP4.error):
                    pass
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass


def _plain_body(parsed) -> str:
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
    return str(body)


def _parsed_email_message(parsed, *, include_body: bool = False) -> EmailMessage:
    body = _plain_body(parsed)
    message_id = str(parsed.get("Message-ID") or "")
    return EmailMessage(
        message_id=message_id,
        sender=str(parsed.get("From") or ""),
        subject=str(parsed.get("Subject") or "(no subject)"),
        at=str(parsed.get("Date") or ""),
        recipients=tuple(item.strip() for item in str(parsed.get("To") or "").split(",") if item.strip()),
        thread_id=str(parsed.get("References") or "").split()[-1] if parsed.get("References") else message_id,
        labels=tuple(item.strip() for item in str(parsed.get("Keywords") or "").split(",") if item.strip()),
        snippet=" ".join(str(body).split())[:SNIPPET_LENGTH],
        body=body if include_body else "",
    )


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

    def messages(self, *, limit: int = 100, include_body: bool = False) -> tuple[EmailMessage, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(include_body, bool):
            raise ValueError("include_body must be a boolean")
        if not self._folder.is_dir():
            return ()
        rows: list[EmailMessage] = []
        for path in sorted(self._folder.glob("*.eml"), reverse=True)[:limit]:
            try:
                parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            except (OSError, email.errors.MessageError):
                continue
            body = _plain_body(parsed)
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
                    body=body if include_body else "",
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

    def messages(self, *, limit: int = 100, include_body: bool = False) -> tuple[EmailMessage, ...]:
        return ()


__all__ = [
    "EMAIL_PROVIDER_ID",
    "IMAP_SMTP_PROVIDER_ID",
    "CredentialEmailProvider",
    "EmailCapabilities",
    "EmailMessage",
    "EmailProviderError",
    "LocalMaildirProvider",
    "SNIPPET_LENGTH",
    "UnconfiguredEmailProvider",
]
