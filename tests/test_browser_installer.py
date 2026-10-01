"""Pure contract coverage for the browser native-host registration utility."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _installer_module():
    path = Path(__file__).parents[1] / "browser" / "native-host" / "install.py"
    spec = importlib.util.spec_from_file_location("haven_browser_installer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chromium_manifest_uses_extension_origin_and_absolute_host_path(tmp_path: Path) -> None:
    installer = _installer_module()
    manifest = installer.build_manifest(
        extension_id="abcdefghijklmnopabcdefghijklmnop",
        host_path=tmp_path / "haven-browser-host.exe",
        browser="chrome",
    )

    assert manifest["name"] == "haven.browser"
    assert manifest["path"] == str((tmp_path / "haven-browser-host.exe").resolve())
    assert manifest["allowed_origins"] == ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"]
    assert "allowed_extensions" not in manifest


def test_firefox_manifest_uses_allowed_extensions() -> None:
    installer = _installer_module()
    manifest = installer.build_manifest(
        extension_id="haven@example.invalid",
        host_path="host.exe",
        browser="firefox",
    )

    assert manifest["allowed_extensions"] == ["haven@example.invalid"]
    assert "allowed_origins" not in manifest


def test_registration_plan_is_current_user_scoped_and_side_effect_free(tmp_path: Path) -> None:
    installer = _installer_module()
    plan = installer.registration_plan(
        browsers=("chrome", "edge", "firefox"),
        manifest_path=tmp_path / "manifest.json",
    )

    assert [entry.browser for entry in plan] == ["chrome", "edge", "firefox"]
    assert all(entry.registry_key.endswith("\\haven.browser") for entry in plan)
    assert "HKEY_LOCAL_MACHINE" not in " ".join(entry.registry_key for entry in plan)
    assert not (tmp_path / "manifest.json").exists()


def test_extension_uses_runtime_native_messaging_api() -> None:
    source = (Path(__file__).parents[1] / "browser" / "extension" / "background.js").read_text(
        encoding="utf-8"
    )
    assert "chrome.runtime.sendNativeMessage" in source
    assert "chrome.nativeMessaging.sendNativeMessage" not in source
