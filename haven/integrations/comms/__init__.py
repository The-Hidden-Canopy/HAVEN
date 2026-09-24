"""Communications integrations: calendar and email provider contracts.

Local, credential-free adapters ship first (ICS calendar files, .eml
folders); credentialed external providers plug into the same contracts
later. Send/mutate stay capability-unavailable until credential storage
lands -- explicit, never fabricated.
"""

from .calendar import (
    ICS_PROVIDER_ID,
    CalendarEvent,
    LocalIcsCalendarProvider,
    parse_ics,
    render_ics,
)
from .email import (
    EMAIL_PROVIDER_ID,
    EmailCapabilities,
    EmailMessage,
    LocalMaildirProvider,
    UnconfiguredEmailProvider,
)

__all__ = [
    "EMAIL_PROVIDER_ID",
    "EmailCapabilities",
    "EmailMessage",
    "ICS_PROVIDER_ID",
    "CalendarEvent",
    "LocalIcsCalendarProvider",
    "LocalMaildirProvider",
    "UnconfiguredEmailProvider",
    "parse_ics",
    "render_ics",
]
