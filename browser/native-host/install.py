"""Install or preview HAVEN's browser native-messaging registration.

The installer is intentionally explicit about the extension identity and
browser targets. Importing this module has no side effects; registry writes
only occur after ``--install`` and are limited to the current user's native
messaging keys. ``--dry-run`` is the default operational mode.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

HOST_NAME = "haven.browser"
_BROWSERS = ("chrome", "edge", "firefox")
_REGISTRY_ROOT = {
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
    "firefox": r"Software\Mozilla\NativeMessagingHosts",
}


@dataclass(frozen=True)
class Registration:
    browser: str
    registry_key: str
    manifest_path: Path


def _require_extension_id(extension_id: str) -> str:
    value = extension_id.strip()
    if not value:
        raise ValueError("extension_id must be a non-empty string")
    if any(character in value for character in "\r\n\x00"):
        raise ValueError("extension_id contains an invalid character")
    return value


def _local_app_data() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    return Path(configured) if configured else Path.home() / "AppData" / "Local"


def default_manifest_path() -> Path:
    return _local_app_data() / "HAVEN" / "browser" / f"{HOST_NAME}.json"


def build_manifest(*, extension_id: str, host_path: str | Path, browser: str) -> dict:
    """Build one browser-specific native-host manifest without writing it."""

    browser = browser.strip().lower()
    if browser not in _BROWSERS:
        raise ValueError(f"browser must be one of: {', '.join(_BROWSERS)}")
    extension_id = _require_extension_id(extension_id)
    resolved_host = str(Path(host_path).expanduser().resolve())
    manifest = {
        "name": HOST_NAME,
        "description": "HAVEN browser context connector",
        "path": resolved_host,
        "type": "stdio",
    }
    if browser == "firefox":
        manifest["allowed_extensions"] = [extension_id]
    else:
        origin = extension_id if extension_id.endswith("/") else f"{extension_id}/"
        if not origin.startswith("chrome-extension://"):
            origin = f"chrome-extension://{origin}"
        manifest["allowed_origins"] = [origin]
    return manifest


def registration_plan(*, browsers: Iterable[str], manifest_path: str | Path) -> tuple[Registration, ...]:
    """Return the current-user registry plan without touching the registry."""

    path = Path(manifest_path).expanduser().resolve()
    result: list[Registration] = []
    for browser in browsers:
        normalized = browser.strip().lower()
        if normalized not in _BROWSERS:
            raise ValueError(f"browser must be one of: {', '.join(_BROWSERS)}")
        result.append(
            Registration(
                browser=normalized,
                registry_key=rf"{_REGISTRY_ROOT[normalized]}\{HOST_NAME}",
                manifest_path=path,
            )
        )
    return tuple(result)


def write_manifest(path: str | Path, manifest: dict) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


def _registry_module():
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - Windows-only branch
        raise RuntimeError("browser registration requires Windows") from exc
    return winreg


def register(plan: Iterable[Registration]) -> None:
    winreg = _registry_module()
    for entry in plan:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, entry.registry_key) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(entry.manifest_path))


def unregister(plan: Iterable[Registration]) -> None:
    winreg = _registry_module()
    for entry in plan:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, entry.registry_key)
        except FileNotFoundError:
            continue


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register HAVEN's browser native-messaging host")
    parser.add_argument("--extension-id", required=True, help="installed Chrome/Edge extension id or Firefox id")
    parser.add_argument("--host-path", required=True, help="installed native host executable or launcher path")
    parser.add_argument("--manifest-path", default=str(default_manifest_path()))
    parser.add_argument("--browser", choices=(*_BROWSERS, "all"), action="append", default=None)
    parser.add_argument("--install", action="store_true", help="write the manifest and current-user registry keys")
    parser.add_argument("--uninstall", action="store_true", help="remove current-user registry keys")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.install and args.uninstall:
        raise SystemExit("--install and --uninstall are mutually exclusive")
    browsers = args.browser or ["all"]
    if "all" in browsers:
        browsers = list(_BROWSERS)
    plan = registration_plan(browsers=browsers, manifest_path=args.manifest_path)
    if args.uninstall:
        unregister(plan)
        print(json.dumps({"ok": True, "removed": [entry.registry_key for entry in plan]}, indent=2))
        return 0

    manifests = {
        entry.browser: build_manifest(
            extension_id=args.extension_id,
            host_path=args.host_path,
            browser=entry.browser,
        )
        for entry in plan
    }
    if args.install:
        # One manifest is written per browser because Firefox and Chromium use
        # different allow-list fields. Registry values point at the selected
        # browser manifest rather than sharing incompatible JSON.
        manifest_paths: dict[str, str] = {}
        base = Path(args.manifest_path).expanduser().resolve()
        for browser, manifest in manifests.items():
            path = base.with_name(f"{HOST_NAME}-{browser}.json")
            write_manifest(path, manifest)
            manifest_paths[browser] = str(path)
        register(
            Registration(entry.browser, entry.registry_key, Path(manifest_paths[entry.browser]))
            for entry in plan
        )
        print(json.dumps({"ok": True, "manifests": manifest_paths}, indent=2))
        return 0

    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": True,
                "manifests": manifests,
                "registry_keys": [entry.registry_key for entry in plan],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
