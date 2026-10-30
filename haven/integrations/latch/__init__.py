"""Latch integration: thin read + propose adapter for the WebUI surface."""

from .service import (
    BASE_URL_ENV,
    CANCEL_SUBSCRIPTION_OPERATION,
    DEFAULT_BASE_URL,
    DEMO_ENV,
    DEMO_LABEL,
    SETTINGS_PATH_ENV,
    LatchConfigError,
    LatchHttpError,
    LatchPluginService,
    LatchResponseError,
    LatchUnavailableError,
    default_settings_path,
)

__all__ = [
    "BASE_URL_ENV",
    "CANCEL_SUBSCRIPTION_OPERATION",
    "DEFAULT_BASE_URL",
    "DEMO_ENV",
    "DEMO_LABEL",
    "SETTINGS_PATH_ENV",
    "LatchConfigError",
    "LatchHttpError",
    "LatchPluginService",
    "LatchResponseError",
    "LatchUnavailableError",
    "default_settings_path",
]
