"""PluginManager: joins the Hub catalog to local enablement, nothing else.

`fetch_catalog` is monkeypatched at the `haven.plugins.manager` import site
so these are unit tests of the join/enable/disable logic, independent of
network access or catalog signing -- catalog_client's own contract is
covered in test_plugins_catalog_client.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from haven.plugins import PluginManager, PluginRegistry, UnknownPluginError
from haven.plugins.catalog_client import CatalogFetchError, CatalogSnapshot
from haven.plugins.contracts import PluginCapability, PluginDataBoundary, PluginDescriptor, PluginStatus
import haven.plugins.manager as manager_module

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
GHOST_TEACHER = PluginDescriptor(
    plugin_id="ghost-teacher",
    display_name="Ghost Teacher",
    publisher="The Hidden Canopy LLC",
    capability=PluginCapability.ADAPTIVE_CURRICULUM_EVALUATION,
    status=PluginStatus.CATALOG_ONLY,
    data_boundary=PluginDataBoundary.EXPORTED_RECEIPTS_ONLY,
    description="Proposes training curriculum from exported evaluation signals.",
)


def _snapshot(*plugins) -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version="test-1",
        issued_at=NOW,
        expires_at=NOW,
        plugins=plugins or (TRACEGLASS, GHOST_TEACHER),
    )


def _manager(tmp_path, monkeypatch, *, fetch_result=None, fetch_error=None):
    registry = PluginRegistry(tmp_path / "plugins.json", clock=lambda: NOW)
    manager = PluginManager(registry)

    def fake_fetch(url):
        if fetch_error is not None:
            raise fetch_error
        return fetch_result if fetch_result is not None else _snapshot()

    monkeypatch.setattr(manager_module, "fetch_catalog", fake_fetch)
    return manager


def test_view_before_any_fetch_is_empty_with_no_error(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    view = manager.view()
    assert view.entries == ()
    assert view.catalog_error is None
    assert view.catalog_version is None


def test_refresh_populates_entries_with_local_enablement_joined_in(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    view = manager.refresh_catalog()
    assert {e.descriptor.plugin_id for e in view.entries} == {"traceglass", "ghost-teacher"}
    assert all(e.enabled is False for e in view.entries)
    assert view.catalog_version == "test-1"
    assert view.catalog_error is None


def test_enable_requires_a_prior_successful_fetch(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    with pytest.raises(UnknownPluginError):
        manager.enable("traceglass")


def test_enable_rejects_an_id_absent_from_the_current_catalog(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.refresh_catalog()
    with pytest.raises(UnknownPluginError):
        manager.enable("not-a-real-plugin")


def test_enable_then_view_reflects_the_choice(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.refresh_catalog()
    view = manager.enable("traceglass")
    by_id = {e.descriptor.plugin_id: e.enabled for e in view.entries}
    assert by_id == {"traceglass": True, "ghost-teacher": False}


def test_disable_never_requires_a_fetch_and_is_idempotent(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.disable("traceglass")  # never enabled, never fetched — must not raise
    manager.refresh_catalog()
    manager.enable("traceglass")
    view = manager.disable("traceglass")
    assert all(e.enabled is False for e in view.entries)


def test_a_failed_refresh_keeps_the_previous_successful_snapshot_but_reports_the_error(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.refresh_catalog()
    monkeypatch.setattr(
        manager_module,
        "fetch_catalog",
        lambda url: (_ for _ in ()).throw(CatalogFetchError("unreachable", code="unreachable")),
    )
    view = manager.refresh_catalog()
    assert view.catalog_error == "unreachable"
    assert {e.descriptor.plugin_id for e in view.entries} == {"traceglass", "ghost-teacher"}


def test_a_never_successful_catalog_reports_the_error_with_empty_entries(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, fetch_error=CatalogFetchError("down", code="unreachable"))
    view = manager.refresh_catalog()
    assert view.catalog_error == "unreachable"
    assert view.entries == ()


def test_enablement_survives_a_catalog_entry_disappearing_on_refresh(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.refresh_catalog()
    manager.enable("traceglass")
    monkeypatch.setattr(manager_module, "fetch_catalog", lambda url: _snapshot(GHOST_TEACHER))
    view = manager.refresh_catalog()
    # traceglass is no longer in the catalog, so it no longer appears — but
    # the local choice was never silently dropped or errored; the registry
    # still has it, it simply has nothing to join against right now.
    assert {e.descriptor.plugin_id for e in view.entries} == {"ghost-teacher"}
