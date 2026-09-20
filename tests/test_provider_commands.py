"""Validation for the provider-neutral execution seam."""

from datetime import datetime, timezone

import pytest

from haven.execution import ProviderCommand, ProviderResult

UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_provider_command_normalizes_capability_and_parameters():
    command = ProviderCommand(
        request_id=" request-1 ",
        provider_id=" local_filesystem ",
        capability=" filesystem.open ",
        target_resource_id=" file:notes ",
        parameters=(("path", "C:/Docs/notes.txt"),),
        requested_at=NOW,
    )

    assert command.request_id == "request-1"
    assert command.provider_id == "local_filesystem"
    assert command.capability == "filesystem.open"
    assert command.target_resource_id == "file:notes"
    assert command.parameters == (("path", "C:/Docs/notes.txt"),)


def test_provider_command_rejects_duplicate_parameters():
    with pytest.raises(ValueError, match="duplicate parameter"):
        ProviderCommand(
            request_id="request-1",
            provider_id="local_filesystem",
            capability="filesystem.open",
            target_resource_id=None,
            parameters=(("path", "a"), ("path", "b")),
            requested_at=NOW,
        )


def test_provider_result_requires_a_real_aware_result():
    result = ProviderResult(
        success=True,
        detail="accepted",
        observed_at=NOW,
        source="local_filesystem",
        metadata=(("operation", "open"),),
    )
    assert result.metadata == (("operation", "open"),)

    with pytest.raises(ValueError, match="timezone-aware"):
        ProviderResult(
            success=True,
            detail="accepted",
            observed_at=datetime(2026, 9, 19, 12, 0),
            source="local_filesystem",
        )
