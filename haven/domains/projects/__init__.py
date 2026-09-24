"""Projects domain: bodies of work shared across files, tasks and people."""

from .models import (
    ACTIVE,
    ARCHIVED,
    COMPLETED,
    ON_HOLD,
    OPEN_STATUSES,
    PLANNING,
    PROJECT_STATUSES,
    ProjectRecord,
)
from .store import ProjectStore

__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "COMPLETED",
    "ON_HOLD",
    "OPEN_STATUSES",
    "PLANNING",
    "PROJECT_STATUSES",
    "ProjectRecord",
    "ProjectStore",
]
