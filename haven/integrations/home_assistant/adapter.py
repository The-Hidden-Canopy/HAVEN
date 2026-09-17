"""Home Assistant-shaped integration boundary.

The fixture records exactly what would have crossed the integration boundary
after authorization. `HomeAssistantAdapter` is `haven.execution.ExecutionAdapter`
under its original name: Home Assistant is one `ExecutionAdapter` a deployment
can register, not a protocol Haven is specially aware of.
"""

from __future__ import annotations

from haven.core.domain import DeviceCommand, DeviceResult
from haven.execution import ExecutionAdapter

HomeAssistantAdapter = ExecutionAdapter


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
