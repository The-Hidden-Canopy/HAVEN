"""FixtureIrBlaster: same contract as FixtureHomeAssistant, IR-shaped."""

from datetime import datetime, timezone

from haven.core.domain import DeviceCommand
from haven.integrations.ir import FixtureIrBlaster

BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


def _command() -> DeviceCommand:
    return DeviceCommand(
        request_id="request-1",
        target_device_id="old_window_ac",
        service="ac.power_on",
        parameters=(),
        requested_at=BASE_TIME,
    )


def test_records_the_command_and_returns_a_success_result_by_default():
    blaster = FixtureIrBlaster()

    result = blaster.execute(_command())

    assert result.success is True
    assert result.detail == "ir_code_sent"
    assert result.observed_at == BASE_TIME
    assert len(blaster.commands) == 1
    assert blaster.commands[0].target_device_id == "old_window_ac"


def test_can_be_configured_to_report_failure():
    blaster = FixtureIrBlaster(success=False, detail="ir_transmitter_unresponsive")

    result = blaster.execute(_command())

    assert result.success is False
    assert result.detail == "ir_transmitter_unresponsive"
