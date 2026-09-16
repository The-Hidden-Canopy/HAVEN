"""A real Home Assistant REST client, behind the same adapter contract.

Every other package in this repo is pure and fixture-driven -- `runtime.py`
never imports a network library, and the test suite never opens a socket.
This module is the deliberate exception: it is the only place in Haven that
performs network I/O, and it exists only to implement `HomeAssistantAdapter`
against a real Home Assistant instance's REST API instead of recording
commands locally.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from haven.core.domain import DeviceCommand, DeviceResult


class HomeAssistantConfigError(ValueError):
    """Raised when the client is constructed with an invalid base_url or token."""


class HomeAssistantStateError(RuntimeError):
    """Raised when observing Home Assistant state fails.

    Unlike `execute()` -- where a failure becomes a per-command
    `DeviceResult` for the audit trail -- a state fetch is a batch operation
    with no receipt of its own, so a failure is exceptional and raises. The
    caller (a future polling loop) decides what an unobserved household
    means; Haven's rule is that evidence it could not fetch does not exist,
    which the authority engine already treats as fail-closed.
    """


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HomeAssistantConfigError(f"{name} must be a non-empty string")
    return value.strip()


class LiveHomeAssistantAdapter:
    """Calls Home Assistant's REST service API for one already-authorized command.

    `DeviceCommand.target_device_id` is sent as-is as the HA `entity_id` --
    this adapter does not remap device ids, so a `DeviceManifest`/fixture
    wired to it must already use real HA entity ids. `DeviceCommand.service`
    is split on its first "." into an HA domain and service (e.g.
    "light.turn_on" -> domain "light", service "turn_on"), the same shape
    `runtime.py`'s `_service_for()` and `CapabilityDescriptor.service` already
    produce. Nothing upstream of this class needs to change to use it: it
    satisfies the exact same `execute(command) -> DeviceResult` contract as
    `FixtureHomeAssistant`.

    Known failure modes (an HTTP error status, a connection failure, a
    timeout) are caught and turned into a failed `DeviceResult` with a
    specific `detail`, rather than raised -- `HavenRuntime.run_rule()` also
    catches any exception from an adapter as a last resort, but that path
    only records the exception's type name, which is much less useful for an
    audit receipt than knowing it was, say, an HTTP 401.
    """

    def __init__(self, *, base_url: str, access_token: str, timeout: float = 10.0) -> None:
        self._base_url = _require_text(base_url, name="base_url").rstrip("/")
        self._access_token = _require_text(access_token, name="access_token")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise HomeAssistantConfigError("timeout must be a positive number")
        self._timeout = timeout

    def execute(self, command: DeviceCommand) -> DeviceResult:
        domain, sep, service = command.service.partition(".")
        if not sep or not domain or not service:
            return self._result(command, success=False, detail=f"unroutable_service:{command.service!r}")

        payload = {"entity_id": command.target_device_id, **dict(command.parameters)}
        request = Request(
            f"{self._base_url}/api/services/{domain}/{service}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                status = response.status
        except HTTPError as exc:
            return self._result(command, success=False, detail=f"http_error:{exc.code}:{exc.reason}")
        except URLError as exc:
            return self._result(command, success=False, detail=f"connection_error:{exc.reason}")
        except TimeoutError:
            return self._result(command, success=False, detail="timeout")

        return self._result(command, success=200 <= status < 300, detail=f"http_{status}")

    def fetch_states(self) -> tuple[dict, ...]:
        """GET Home Assistant's /api/states list.

        This is the raw observe side of the integration -- the returned
        entries are Home Assistant's own state dicts, not Haven domain
        values. `state.device_states_from_ha()` is what maps them onto
        `DeviceState` evidence, filtering to entities that have a registered
        `DeviceManifest`.
        """

        request = Request(
            f"{self._base_url}/api/states",
            method="GET",
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HomeAssistantStateError(f"http_error:{exc.code}:{exc.reason}") from exc
        except URLError as exc:
            raise HomeAssistantStateError(f"connection_error:{exc.reason}") from exc
        except TimeoutError as exc:
            raise HomeAssistantStateError("timeout") from exc
        if not isinstance(body, list):
            raise HomeAssistantStateError("unexpected_response:expected a JSON array of states")
        return tuple(body)

    @staticmethod
    def _result(command: DeviceCommand, *, success: bool, detail: str) -> DeviceResult:
        return DeviceResult(
            success=success,
            detail=detail,
            observed_at=command.requested_at,
            source="home_assistant.rest",
        )


__all__ = ["HomeAssistantConfigError", "HomeAssistantStateError", "LiveHomeAssistantAdapter"]
