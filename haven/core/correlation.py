"""One correlation id threaded native action -> IPC -> application service ->
authority -> provider -> receipt/history/logging (native product-
consolidation plan, Priority 0 instrumentation).

A `contextvars.ContextVar` rather than an explicit parameter on every
handler/service method: `IpcDispatcher` (and the web `_Handler`) already sit
at the one place a request enters the system, and threading an explicit
`correlation_id` parameter through every intermediate call in
`ComputerActionService`/`WindowActionService`/`BrowserActionService`/the
authority engines would touch dozens of signatures for a value those layers
never need to branch on -- they only need to be able to read it when they
write a receipt or a log line. `ContextVar` is asyncio/thread-safe and
automatically scoped to the one request being handled.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[str | None] = ContextVar("haven_correlation_id", default=None)


def new_id() -> str:
    return f"corr-{uuid.uuid4().hex}"


def current() -> str | None:
    """The correlation id for whatever request is being handled right now,
    or `None` outside any request (a scheduled automation tick, a boot-time
    migration, a test that never bound one)."""

    return _current.get()


@contextmanager
def bind(correlation_id: str | None) -> Iterator[str | None]:
    """Scope `current()` to `correlation_id` for the duration of the `with`
    block, restoring whatever was bound before on exit -- safe to nest."""

    token = _current.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _current.reset(token)


__all__ = ["bind", "current", "new_id"]
