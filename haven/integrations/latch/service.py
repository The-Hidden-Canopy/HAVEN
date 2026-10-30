"""Thin read + propose adapter between HAVEN and a Latch daemon.

The WebUI compatibility surface (spec: Latch plugin, WebUI track) speaks to
Latch over its loopback HTTP API exactly the way the native
``Haven.Desktop/Services/LatchClient.cs`` does:

* fixed Latch API paths only -- this module never proxies arbitrary URLs and
  never moves a plan past PROPOSED. HAVEN may read Latch's state and may
  *propose* a cancellation plan; the plan stays PROPOSED inside Latch until a
  user moves it forward through Latch's own UI (see ``handoff_url``).
* one fresh connection per request (reconnect-per-request): a dropped daemon
  is observed as "disconnected" on the next read, never cached as state.
* short timeouts so a silent daemon cannot stall the HAVEN web surface.

Configuration mirrors the native client: ``HAVEN_LATCH_BASE_URL`` overrides
the ``http://127.0.0.1:8787`` loopback default, ``HAVEN_LATCH_DEMO`` labels
the data as the synthetic demo fixture, and ``HAVEN_LATCH_SETTINGS_PATH``
overrides the shared appearance settings file (normally
``%LOCALAPPDATA%/Haven.Desktop/settings.json``, owned by the native
``ThemeService``) for isolated tests.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from .loopback_transport import validate_loopback_origin, request as loopback_request, LoopbackAddressError, LoopbackUnavailableError
from .identifiers import identifier, segment

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
BASE_URL_ENV = "HAVEN_LATCH_BASE_URL"
DEMO_ENV = "HAVEN_LATCH_DEMO"
SETTINGS_PATH_ENV = "HAVEN_LATCH_SETTINGS_PATH"
DEMO_LABEL = "Synthetic demo"
DEFAULT_TIMEOUT_SECONDS = 2.0

CANCEL_SUBSCRIPTION_OPERATION = "cancel_subscription"

# Appearance choices mirror native ThemeService exactly; this adapter shares
# that settings file, it does not define a second appearance vocabulary.
_APPEARANCE_CHOICES = {
    "theme": ("haven", "canopy", "ember", "mono"),
    "mode": ("light", "dark", "system"),
    "density": ("comfortable", "compact"),
    "motion": ("system", "reduced"),
}
_DEFAULT_APPEARANCE = {
    "theme": "haven",
    "mode": "system",
    "density": "comfortable",
    "motion": "system",
}
_NATIVE_SETTINGS_FILENAME = ("Haven.Desktop", "settings.json")


class LatchConfigError(ValueError):
    """Raised for invalid construction or write parameters."""


class LatchUnavailableError(ConnectionError):
    """Latch did not answer (connection refused, timeout, reset, ...)."""


class LatchHttpError(RuntimeError):
    """Latch answered with a non-success HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"latchd returned HTTP {status}: {detail}")


class LatchResponseError(RuntimeError):
    """Latch answered successfully but not with the JSON object we need."""


def default_settings_path(environ: dict | None = None) -> Path:
    """The shared appearance settings file (native ThemeService's store)."""

    env = os.environ if environ is None else environ
    override = env.get(SETTINGS_PATH_ENV)
    if override:
        return Path(override)
    local_app_data = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base.joinpath(*_NATIVE_SETTINGS_FILENAME)


def _validate_base_url(url: str) -> str:
    try:
        return validate_loopback_origin(url)
    except LoopbackAddressError as exc:
        raise LatchConfigError(str(exc)) from exc


def _demo_from_env(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def _stdlib_transport(method, url, body, timeout):
    try:
        return loopback_request(method, url, body, timeout)
    except LoopbackUnavailableError as exc:
        raise LatchUnavailableError(str(exc)) from exc


class LatchPluginService:
    """Read Latch records and propose cancellations -- nothing more.

    Every method maps to exactly one fixed Latch HTTP call. There is
    deliberately no way to move a plan past PROPOSED anywhere on this class:
    the only mutating call is the idempotent cancellation proposal, whose
    stable per-commitment key collapses double-submits into one durable plan.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        demo: bool | None = None,
        timeout: float | None = None,
        settings_path: str | Path | None = None,
        transport=None,
        environ: dict | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        resolved_base = base_url if base_url is not None else env.get(BASE_URL_ENV)
        self._base_url = _validate_base_url(resolved_base or DEFAULT_BASE_URL)
        if demo is not None:
            self._demo = bool(demo)
        else:
            self._demo = _demo_from_env(env.get(DEMO_ENV))
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
        if not isinstance(self._timeout, (int, float)) or isinstance(self._timeout, bool) or self._timeout <= 0:
            raise LatchConfigError("timeout must be a positive number")
        self._settings_path = (
            Path(settings_path) if settings_path is not None else default_settings_path(env)
        )
        # Tests inject a fake transport; production uses the stdlib one.
        self._transport = transport if transport is not None else _stdlib_transport

    # --- configuration surface -------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_demo(self) -> bool:
        return self._demo

    @property
    def label(self) -> str | None:
        return DEMO_LABEL if self._demo else None

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def describe(self) -> dict:
        """Status/config probe for the web surface; never raises on a down daemon."""

        return {
            "base_url": self._base_url,
            "demo": self._demo,
            "label": self.label,
            "available": self.is_available(),
            "handoff_available": self.is_handoff_available(),
        }

    # --- transport ---------------------------------------------------------

    def _raw(self, method: str, path: str, payload: dict | None = None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        try:
            status, headers, raw = self._transport(
                method, f"{self._base_url}{path}", body, self._timeout
            )
        except LatchUnavailableError:
            raise
        except OSError as exc:
            raise LatchUnavailableError(f"no answer from Latch: {exc}") from exc
        return status, {key.lower(): value for key, value in headers.items()}, raw

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        status, _headers, raw = self._raw(method, path, payload)
        if status < 200 or status >= 300:
            raise LatchHttpError(status, raw.decode("utf-8", errors="replace")[:400])
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LatchResponseError(f"latchd answered non-JSON (HTTP {status})") from exc
        if not isinstance(data, dict):
            raise LatchResponseError(f"latchd answered a non-object body (HTTP {status})")
        return data

    # --- reads (fixed paths only) -------------------------------------------

    def list_commitments(self) -> dict:
        return self._request("GET", "/v1/commitments")

    def get_commitment(self, commitment_id: str) -> dict:
        """Full current commitment detail; the id is path-escaped."""
        if not isinstance(commitment_id, str) or not commitment_id.strip():
            raise LatchConfigError("commitment_id must be a non-empty string")
        return self._request("GET", f"/v1/commitments/{segment(commitment_id)}")

    def list_access(self, stale_only: bool = False) -> dict:
        """Access grants as Latch currently computes them (incl. staleness)."""

        where = {"stale_only": True} if stale_only else {}
        return self._request("GET", f"/v1/access?{urlencode({'where': json.dumps(where)})}")

    def list_stale_access(self) -> dict:
        """Backward-compatible alias: only grants Latch currently flags stale."""

        return self.list_access(stale_only=True)

    def list_review_proposals(self) -> dict:
        return self._request("GET", "/v1/schedules/proposals")

    def list_action_plans(self, commitment_id: str | None = None, status: str | None = None) -> dict:
        params = {}
        if commitment_id:
            params["commitment_id"] = identifier(commitment_id)
        if status:
            params["status"] = status
        query = f"?{urlencode(params)}" if params else ""
        return self._request("GET", f"/v1/actions{query}")

    def get_action_plan(self, plan_id: str) -> dict:
        return self._request("GET", f"/v1/actions/{segment(plan_id)}")

    def get_evidence(self, evidence_id: str, include_content: bool = False) -> dict:
        # latchd treats the mere presence of `content` as the flag.
        path = f"/v1/evidence/{segment(evidence_id)}"
        if include_content:
            path += "?content=1"
        return self._request("GET", path)

    def is_available(self) -> bool:
        try:
            self.list_commitments()
        except (LatchUnavailableError, LatchHttpError, LatchResponseError):
            return False
        return True

    # --- the one mutation: an idempotent proposal ----------------------------

    def propose_cancellation(self, commitment_id: str) -> dict:
        if not isinstance(commitment_id, str) or not commitment_id.strip():
            raise LatchConfigError("commitment_id must be a non-empty string")
        commitment_id = identifier(commitment_id)
        payload = {
            "commitment_id": commitment_id,
            "operation": CANCEL_SUBSCRIPTION_OPERATION,
            "target": {},
            # Stable per commitment: a retried/double-clicked proposal returns
            # the existing durable plan instead of minting a second one.
            "idempotency_key": f"haven:{CANCEL_SUBSCRIPTION_OPERATION}:{commitment_id}",
        }
        return self._request("POST", "/v1/actions/plan", payload)

    # --- handoff to Latch's own UI -------------------------------------------

    def is_handoff_available(self) -> bool:
        """True only when Latch's own HTML UI is actually served at the root.

        An API-only daemon answers JSON (or 404) here; the authorization
        deep link would dead-end, so this probes before the UI enables it.
        Never raises.
        """

        try:
            status, headers, raw = self._raw("GET", "/")
        except LatchUnavailableError:
            return False
        if status < 200 or status >= 300:
            return False
        content_type = headers.get("content-type", "")
        if not content_type.lower().startswith("text/html"):
            return False
        body = raw[:4096].lower()
        return b"<html" in body or b"<!doctype" in body

    def handoff_url(self, plan_id: str | None) -> str | None:
        """Root with exactly a `plan_id` query -- no path, no other parameters."""

        if not isinstance(plan_id, str) or not plan_id.strip():
            return None
        return f"{self._base_url}/?plan_id={segment(plan_id)}"

    # --- shared appearance settings (native ThemeService's file) --------------

    def read_appearance(self) -> dict:
        """Current appearance choices, fail-closed to defaults like ThemeService."""

        raw: dict = {}
        try:
            parsed = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                raw = parsed
        except (OSError, ValueError):
            raw = {}
        appearance = dict(_DEFAULT_APPEARANCE)
        for key, choices in _APPEARANCE_CHOICES.items():
            value = raw.get(key)
            if isinstance(value, str):
                match = next(
                    (choice for choice in choices if choice.lower() == value.lower()), None
                )
                if match is not None:
                    appearance[key] = match
        return appearance

    def write_appearance_mode(self, mode: str) -> dict:
        """Set only the mode; theme/density/motion are preserved semantically.

        The other three choices are carried over (validated against the native
        ThemeService choice-sets, like the native reader) and only `mode`
        changes; the rewrite canonicalizes all four known fields rather than
        preserving the file's raw bytes.
        """

        if not isinstance(mode, str) or mode.lower() not in _APPEARANCE_CHOICES["mode"]:
            raise LatchConfigError(
                f"mode must be one of {sorted(_APPEARANCE_CHOICES['mode'])}"
            )
        appearance = self.read_appearance()
        appearance["mode"] = mode.lower()
        self._write_settings(appearance)
        return appearance

    def write_appearance_preferences(self, changes):
        if not isinstance(changes,dict) or not changes or set(changes)-{"theme","mode"}:
            raise LatchConfigError("unknown appearance preference")
        appearance=self.read_appearance()
        for name,value in changes.items():
            if value not in _APPEARANCE_CHOICES[name]:
                raise LatchConfigError("unknown appearance value")
            appearance[name]=value
        self._write_settings(appearance)
        return appearance

    def _write_settings(self, appearance: dict) -> None:
        directory = self._settings_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                # Same key order the native ThemeService.SaveAsync writes.
                json.dump({key: appearance[key] for key in ("mode", "theme", "density", "motion")}, handle)
            os.replace(tmp_name, self._settings_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
