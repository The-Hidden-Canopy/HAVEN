"""Timezone and timestamp helpers used by every domain boundary."""

from datetime import datetime, timezone


def require_aware_utc(value: datetime, *, name: str = "timestamp") -> datetime:
    """Reject naive timestamps and normalize accepted timestamps to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)
