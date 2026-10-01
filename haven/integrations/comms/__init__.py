"""Communications integrations: calendar and email provider contracts.

Local, credential-free adapters remain available (ICS calendar files, .eml
folders), while the credentialed IMAP/SMTP adapter uses the same contracts.
Secrets stay in the credential subsystem and capabilities remain explicit.
"""

from .calendar import (
    CalendarCapabilities,
    ICS_PROVIDER_ID,
    CalendarEvent,
    CalendarProviderError,
    CompositeCalendarProvider,
    LocalIcsCalendarProvider,
    REMOTE_ICS_PROVIDER_ID,
    RemoteIcsCalendarProvider,
    parse_ics,
    render_ics,
    validate_remote_calendar_url,
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
    "REMOTE_ICS_PROVIDER_ID",
    "CalendarCapabilities",
    "CalendarEvent",
    "CalendarProviderError",
    "CompositeCalendarProvider",
    "LocalIcsCalendarProvider",
    "RemoteIcsCalendarProvider",
    "LocalConversationJsonlProvider",
    "LocalMaildirProvider",
    "UnconfiguredEmailProvider",
    "parse_ics",
    "render_ics",
    "validate_remote_calendar_url",
]
