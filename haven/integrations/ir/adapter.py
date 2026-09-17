"""IR-controlled device integration boundary.

Old TVs, window AC units, fans, projectors, and stereos are often only
reachable through a learned IR code, not a queryable API -- the AC has no
way to report back whether the compressor actually started. `FixtureIrBlaster`
records exactly what would have crossed the integration boundary after
authorization, the same way `FixtureHomeAssistant` does for Home Assistant.
A real IR blaster (Broadlink, ESPHome IR, LIRC) implements the same
`ExecutionAdapter` shape and is registered under its own provider_id in an
`ExecutionProviderRegistry`; Haven does not need to know which one a
household has, or that it is IR at all.
"""

from __future__ import annotations

from haven.core.domain import DeviceCommand, DeviceResult
from haven.execution import ExecutionAdapter

IrBlasterAdapter = ExecutionAdapter


class FixtureIrBlaster:
    """Deterministic local adapter used by tests and demonstrations.

    IR is fire-and-forget: sending a code is not confirmation the device
    responded, which is exactly why the household-control literature around
    IR pairs it with a separate observation (a smart plug's power draw, a
    thermal reading, a camera) to verify the consequence -- that
    verification is not this adapter's job, and `detail` is deliberately
    phrased as "code sent," not "device is now on."
    """

    def __init__(
        self,
        *,
        success: bool = True,
        detail: str = "ir_code_sent",
        source: str = "fixture.ir_blaster",
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


__all__ = ["FixtureIrBlaster", "IrBlasterAdapter"]
