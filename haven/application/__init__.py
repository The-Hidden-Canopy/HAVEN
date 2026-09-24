"""Application services hosting the life domains (spec page 17).

The services are the only mutation path for projects and tasks; HTTP/IPC
adapters stay thin over them so validation is never forked. Everything is
scope-keyed and fail-closed: a mutation or read outside the principal's
membership-derived visible scopes is refused, and every mutation is
revision-bound (BuildThread) so a stale suggestion cannot overwrite a
newer user edit.
"""

from .service import ProjectService, TaskService

__all__ = ["ProjectService", "TaskService"]
