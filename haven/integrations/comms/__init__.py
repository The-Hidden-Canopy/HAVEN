"""Communications integrations: calendar and email provider contracts.

Local, credential-free adapters remain available (ICS calendar files, .eml
folders), while the credentialed IMAP/SMTP adapter uses the same contracts.
Secrets stay in the credential subsystem and capabilities remain explicit.
"""

from .calendar import (
    ICS_PROVIDER_ID,
    CalendarEvent,
    LocalIcsCalendarProvider,
    parse_ics,
    render_ics,
)
from .conversations import (
    CONVERSATION_PROVIDER_ID,
    ConversationCapabilities,
    ConversationMessage,
    ConversationProvider,
    LocalConversationJsonlProvider,
)
from .email import (
    IMAP_SMTP_PROVIDER_ID,
    CredentialEmailProvider,
    EMAIL_PROVIDER_ID,
    EmailCapabilities,
    EmailMessage,
    EmailProviderError,
    LocalMaildirProvider,
    UnconfiguredEmailProvider,
)

__all__ = [
    "EMAIL_PROVIDER_ID",
    "IMAP_SMTP_PROVIDER_ID",
    "CONVERSATION_PROVIDER_ID",
    "ConversationCapabilities",
    "ConversationMessage",
    "ConversationProvider",
    "CredentialEmailProvider",
    "EmailCapabilities",
    "EmailMessage",
    "EmailProviderError",
    "ICS_PROVIDER_ID",
    "CalendarEvent",
    "LocalIcsCalendarProvider",
    "LocalConversationJsonlProvider",
    "LocalMaildirProvider",
    "UnconfiguredEmailProvider",
    "parse_ics",
    "render_ics",
]
