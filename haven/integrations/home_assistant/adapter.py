"""Home Assistant-shaped integration boundary.

The first milestone has no network client. The fixture records exactly what
would have crossed the integration boundary after authorization.
"""

from __future__ import annotations

from typing import Protocol

from haven.core.domain import DeviceCommand, DeviceResult


class HomeAssistantAdapter(Protocol):
    def execute(self, command: DeviceCommand) -> DeviceResult:
        """Execute one already-authorized command."""


class FixtureHomeAssistant:
    """Deterministic local adapter used by tests and demonstrations."""

    def __init__(
        self,
        *,
        success: bool = True,
        detail: str = "fixture command accepted",
        source: str = "fixture.home_assistant",
    ) -> None:
        self._success = success
        self._detail = detail
        self._source = source
        self._commands: tuple[DeviceCommand, ...] = ()

    @property
    def commands(self) -> tuple[DeviceCommand, ...]:
        return self._commands

    def execute(self, command: DeviceCommand) -> DeviceResult:
        self._commands = (*self._commands, command)
        return DeviceResult(
            success=self._success,
            detail=self._detail,
            observed_at=command.requested_at,
            source=self._source,
        )


__all__ = ["FixtureHomeAssistant", "HomeAssistantAdapter"]
