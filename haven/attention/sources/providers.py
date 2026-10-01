"""ProviderSource: credential expiry and revocation attention.

This adapter reads only `CredentialMetadata`. It never calls `get_secret()`
and therefore cannot place provider credentials in the attention projection.
Unexpired credentials produce no signal; expired or revoked metadata becomes
an ordinary dismissible provider-repair item routed to Settings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...credentials import CredentialMetadata
from ..domain import (
    AttentionActionRef,
    AttentionItem,
    AttentionKind,
    AttentionSeverity,
    Dismissibility,
    RouteRef,
)

_EXPIRY_NOTICE = timedelta(days=7)


class ProviderSource:
    def __init__(self, *, credentials, identity) -> None:
        self._credentials = credentials
        self._identity = identity

    def collect(
        self, *, now: datetime, visible_scope_ids: tuple[str, ...] | None = None
    ) -> list[AttentionItem]:
        try:
            metadata = self._credentials.list_metadata(include_revoked=True)
        except Exception:
            return []

        items: list[AttentionItem] = []
        for credential in metadata:
            if not isinstance(credential, CredentialMetadata):
                continue
            expired = credential.expires_at is not None and credential.expires_at <= now
            expiring = (
                credential.expires_at is not None
                and now < credential.expires_at <= now + _EXPIRY_NOTICE
            )
            if not credential.revoked and not expired and not expiring:
                continue

            provider = credential.provider
            account = credential.account_label
            if credential.revoked:
                title = f"Reconnect {provider}"
                why_now = f"The {provider} credential for {account} has been revoked."
            elif expired:
                title = f"Reconnect {provider}"
                why_now = f"The {provider} credential for {account} has expired."
            else:
                title = f"Credential expiring: {provider}"
                why_now = (
                    f"The {provider} credential for {account} expires "
                    f"{credential.expires_at.isoformat()} and may need renewal."
                )

            items.append(
                AttentionItem(
                    attention_id=f"expiring:credential:{credential.credential_id}",
                    kind=AttentionKind.EXPIRING,
                    severity=AttentionSeverity.HIGH if (credential.revoked or expired) else AttentionSeverity.NORMAL,
                    title=title,
                    summary=why_now,
                    why_now=why_now,
                    source_domain="providers",
                    source_ref=f"credential:{credential.credential_id}",
                    scope_id=self._identity.personal_scope_id,
                    created_at=credential.created_at,
                    expires_at=credential.expires_at,
                    evidence_refs=(f"credential:{credential.credential_id}",),
                    route=RouteRef(page="settings", subview="providers", action_hint="reconnect", entity_id=provider),
                    available_actions=(
                        AttentionActionRef(action="route", label="Open providers"),
                        AttentionActionRef(action="dismiss", label="Dismiss"),
                    ),
                    dismissibility=Dismissibility.DISMISS,
                )
            )
        return items


__all__ = ["ProviderSource"]
