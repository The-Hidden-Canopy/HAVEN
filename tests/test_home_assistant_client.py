"""LiveHomeAssistantAdapter, exercised with urlopen mocked out.

This is Haven's first network-touching code, and the project's test suite
otherwise makes a deliberate point of using no network -- so every test here
mocks `haven.integrations.home_assistant.client.urlopen` rather than
reaching a real Home Assistant instance. Nothing that runs `pytest` opens a
socket.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from haven.core.domain import DeviceCommand
from haven.integrations.home_assistant import (
    HomeAssistantConfigError,
    HomeAssistantStateError,
    LiveHomeAssistantAdapter,
)

UTC = timezone.utc
BASE_TIME = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _command(*, service: str = "light.turn_on", parameters=(("brightness_pct", 20),)) -> DeviceCommand:
    return DeviceCommand(
        request_id="request-1",
        target_device_id="light.bedroom_lights",
        service=service,
        parameters=parameters,
        requested_at=BASE_TIME,
    )


def test_rejects_empty_base_url_or_token():
    with pytest.raises(HomeAssistantConfigError):
        LiveHomeAssistantAdapter(base_url="", access_token="token")
    with pytest.raises(HomeAssistantConfigError):
        LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="")


def test_successful_call_builds_the_expected_request_and_result():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123/", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(200)
        result = adapter.execute(_command())

    assert result.success is True
    assert result.detail == "http_200"
    assert result.observed_at == BASE_TIME

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "http://ha.local:8123/api/services/light/turn_on"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert request.get_header("Content-type") == "application/json"
    body = json.loads(request.data.decode("utf-8"))
    assert body == {"entity_id": "light.bedroom_lights", "brightness_pct": 20}


def test_http_error_becomes_a_failed_result_not_an_exception():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = HTTPError("url", 401, "Unauthorized", hdrs=None, fp=None)
        result = adapter.execute(_command())

    assert result.success is False
    assert result.detail == "http_error:401:Unauthorized"


def test_connection_error_becomes_a_failed_result_not_an_exception():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = URLError("connection refused")
        result = adapter.execute(_command())

    assert result.success is False
    assert "connection_error" in result.detail


def test_timeout_becomes_a_failed_result_not_an_exception():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = TimeoutError()
        result = adapter.execute(_command())

    assert result.success is False
    assert result.detail == "timeout"


def test_unroutable_service_is_rejected_before_any_network_call():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        result = adapter.execute(_command(service="noservice"))

    mock_urlopen.assert_not_called()
    assert result.success is False
    assert "unroutable_service" in result.detail


def test_fetch_states_gets_and_parses_the_state_list():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")
    states = [{"entity_id": "light.bedroom_lights", "state": "on", "attributes": {}, "last_changed": "x"}]

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(200, json.dumps(states).encode("utf-8"))
        result = adapter.fetch_states()

    assert result == tuple(states)
    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "http://ha.local:8123/api/states"
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") == "Bearer secret-token"


def test_fetch_states_raises_on_http_error():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = HTTPError("url", 500, "Server Error", hdrs=None, fp=None)
        with pytest.raises(HomeAssistantStateError, match="http_error:500"):
            adapter.fetch_states()


def test_fetch_states_raises_on_connection_error_and_timeout():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = URLError("connection refused")
        with pytest.raises(HomeAssistantStateError, match="connection_error"):
            adapter.fetch_states()
        mock_urlopen.side_effect = TimeoutError()
        with pytest.raises(HomeAssistantStateError, match="timeout"):
            adapter.fetch_states()


def test_fetch_states_rejects_a_non_list_response():
    adapter = LiveHomeAssistantAdapter(base_url="http://ha.local:8123", access_token="secret-token")

    with patch("haven.integrations.home_assistant.client.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(200, json.dumps({"not": "a list"}).encode("utf-8"))
        with pytest.raises(HomeAssistantStateError, match="unexpected_response"):
            adapter.fetch_states()
