"""`python -m haven.plugins` operator surface: list/enable/disable."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import haven.plugins.__main__ as cli
from haven.plugins.catalog_client import CatalogSnapshot
from haven.plugins.contracts import PluginCapability, PluginDataBoundary, PluginDescriptor, PluginStatus

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)

TRACEGLASS = PluginDescriptor(
    plugin_id="traceglass",
    display_name="TraceGlass",
    publisher="The Hidden Canopy LLC",
    capability=PluginCapability.DECISION_CHAIN_RECONSTRUCTION,
    status=PluginStatus.CATALOG_ONLY,
    data_boundary=PluginDataBoundary.EXPORTED_RECEIPTS_ONLY,
    description="Reconstructs decision chains from exported receipts.",
)


@pytest.fixture(autouse=True)
def fake_catalog(monkeypatch):
    import haven.plugins.manager as manager_module

    snapshot = CatalogSnapshot(catalog_version="test-1", issued_at=NOW, expires_at=NOW, plugins=(TRACEGLASS,))
    monkeypatch.setattr(manager_module, "fetch_catalog", lambda url: snapshot)


def test_list_prints_catalog_and_local_state(tmp_path, capsys):
    exit_code = cli.main(["list", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "traceglass" in out
    assert "disabled" in out


def test_enable_then_list_shows_enabled(tmp_path, capsys):
    assert cli.main(["enable", "traceglass", "--data-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    cli.main(["list", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "enabled" in out


def test_enable_unknown_plugin_id_fails_with_exit_1(tmp_path, capsys):
    exit_code = cli.main(["enable", "not-a-real-plugin", "--data-dir", str(tmp_path)])
    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_disable_is_always_safe(tmp_path, capsys):
    assert cli.main(["disable", "traceglass", "--data-dir", str(tmp_path)]) == 0
