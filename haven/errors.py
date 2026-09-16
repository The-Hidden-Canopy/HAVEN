"""Errors raised by governed HAVEN operations."""


class HavenError(RuntimeError):
    """Base class for expected HAVEN failures."""


class InvalidTransition(HavenError):
    """Raised when a state transition is not valid for the current state."""


class ScopeViolation(HavenError):
    """Raised when an operation crosses a household boundary."""


class StateConflict(HavenError):
    """Raised when a caller acts on an old state revision."""
