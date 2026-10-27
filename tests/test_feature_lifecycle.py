"""Real bundled command execution and adversarial lifecycle transitions."""

from dataclasses import replace
import json

import pytest

from haven.extensions.lifecycle import (
    BundledFeatureRelease, DecisionContextInspector, FeatureContext,
    FeatureLifecycle, FeatureLifecycleError, bundled_feature_releases,
)


FEATURE = "haven.decision-context"
CONTEXT = FeatureContext("resident", "scope-personal")


def _manager(tmp_path, *, allowed=None, releases=None, operations=None):
    allowed = allowed if allowed is not None else {"current": True}
    return FeatureLifecycle(
        tmp_path / "features.json", releases=releases or bundled_feature_releases(),
        authorize=lambda principal, scope, op: allowed["current"] and
        principal == CONTEXT.principal_id and scope == CONTEXT.scope_id,
        service_operations=operations or {"decisions.context": lambda **kw: {
            "decisions": [{"decision_id": "d1", "direction": "Use the offline path"}],
            "scope_id": kw["scope_id"],
        }},
    )


def _install(manager):
    manager.stage(FEATURE, "1.0.0", context=CONTEXT)
    manager.install(FEATURE, context=CONTEXT)


def _run(manager):
    return manager.run(FEATURE, "inspect", context=CONTEXT)


def test_bundled_command_requires_distinct_stage_install_enable_and_returns_real_service_result(tmp_path):
    calls = []
    manager = _manager(tmp_path, operations={"decisions.context": lambda **kw: calls.append(kw) or {"decisions": ["actual result"]}})
    manager.stage(FEATURE, "1.0.0", context=CONTEXT)
    assert calls == []
    with pytest.raises(FeatureLifecycleError, match="commit"):
        manager.enable(FEATURE, context=CONTEXT)
    manager.install(FEATURE, context=CONTEXT)
    with pytest.raises(FeatureLifecycleError, match="disabled"):
        _run(manager)
    assert manager.enable(FEATURE, context=CONTEXT)["status"] == "healthy"
    result = _run(manager)
    assert result["result"] == {"decisions": ["actual result"]}
    assert calls == [{"principal_id": "resident", "scope_id": "scope-personal", "decision_id": None, "limit": 10}]


def test_duplicate_install_and_enable_are_idempotent(tmp_path):
    manager = _manager(tmp_path)
    _install(manager)
    assert manager.stage(FEATURE, "1.0.0", context=CONTEXT)["status"] == "already_installed"
    assert manager.install(FEATURE, context=CONTEXT)["status"] == "already_installed"
    manager.enable(FEATURE, context=CONTEXT)
    activation = manager._active[FEATURE]
    assert manager.enable(FEATURE, context=CONTEXT)["status"] == "already_enabled"
    assert manager._active[FEATURE] is activation


def test_replacing_an_uncommitted_stage_installs_the_requested_version(tmp_path):
    manager = _manager(tmp_path)
    manager.stage(FEATURE, "1.0.0", context=CONTEXT)
    manager.stage(FEATURE, "1.1.0", context=CONTEXT)
    manager.install(FEATURE, context=CONTEXT)
    assert manager.enable(FEATURE, context=CONTEXT)["version"] == "1.1.0"


def test_restart_rechecks_current_authority_and_restores_only_healthy_enabled_feature(tmp_path):
    manager = _manager(tmp_path)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    manager.close()
    restarted = _manager(tmp_path)
    assert _run(restarted)["status"] == "completed"
    restarted.close()
    denied = _manager(tmp_path, allowed={"current": False})
    assert denied._active == {}
    assert denied._records[FEATURE]["enabled"] is False


def test_scope_revocation_defeats_active_command_and_retained_handle(tmp_path):
    allowed = {"current": True}
    calls = []
    manager = _manager(tmp_path, allowed=allowed, operations={"decisions.context": lambda **kw: calls.append(kw) or {}})
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    services = manager._active[FEATURE].module._services
    allowed["current"] = False
    with pytest.raises(FeatureLifecycleError, match="current host policy"):
        services.call("decisions.context")
    assert manager._records[FEATURE]["status"] == "revoked"
    allowed["current"] = True
    with pytest.raises(FeatureLifecycleError, match="no longer active"):
        services.call("decisions.context")
    with pytest.raises(FeatureLifecycleError, match="disabled"):
        _run(manager)
    assert calls == []


def test_revocation_during_read_discards_result_and_disables_activation(tmp_path):
    allowed = {"current": True}

    def read(**kwargs):
        allowed["current"] = False
        return {"decisions": ["must not escape after revocation"]}

    manager = _manager(tmp_path, allowed=allowed, operations={"decisions.context": read})
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    with pytest.raises(FeatureLifecycleError, match="current host policy"):
        _run(manager)
    assert manager._records[FEATURE]["enabled"] is False


def test_wrong_scope_cannot_read_change_or_uninstall_another_binding(tmp_path):
    manager = _manager(tmp_path)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    for action in (lambda: manager.uninstall(FEATURE, context=FeatureContext("other", "scope-other")),
                   lambda: manager.run(FEATURE, "inspect", context=FeatureContext("other", "scope-other"))):
        with pytest.raises(FeatureLifecycleError, match="different principal or scope"):
            action()
    assert _run(manager)["ok"]


def test_disable_detaches_and_invalidates_stale_handles_even_after_reenable(tmp_path):
    manager = _manager(tmp_path)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    module = manager._active[FEATURE].module
    services = module._services
    manager.disable(FEATURE, context=CONTEXT)
    assert module._services is None
    manager.enable(FEATURE, context=CONTEXT)
    with pytest.raises(FeatureLifecycleError, match="no longer active"):
        services.call("decisions.context")


def test_failed_activation_preserves_prior_version_and_command(tmp_path):
    class Broken(DecisionContextInspector):
        def health(self):
            return False

    releases = bundled_feature_releases() + (replace(bundled_feature_releases()[0], version="2.0.0", factory=Broken),)
    manager = _manager(tmp_path, releases=releases)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    prior = manager._active[FEATURE]
    manager.stage(FEATURE, "2.0.0", context=CONTEXT)
    with pytest.raises(FeatureLifecycleError, match="health check failed"):
        manager.update(FEATURE, context=CONTEXT)
    assert manager._active[FEATURE] is prior
    assert _run(manager)["version"] == "1.0.0"
    assert json.loads(manager.path.read_text())["features"][FEATURE]["version"] == "1.0.0"


def test_interrupted_update_keeps_prior_committed_version(tmp_path):
    releases = bundled_feature_releases() + (replace(bundled_feature_releases()[0], version="2.0.0"),)
    manager = _manager(tmp_path, releases=releases)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    manager.stage(FEATURE, "2.0.0", context=CONTEXT)
    manager.close()
    restarted = _manager(tmp_path, releases=releases)
    assert "pending_version" not in restarted._records[FEATURE]
    assert _run(restarted)["version"] == "1.0.0"


def test_atomic_pointer_failure_preserves_prior_active_module(tmp_path, monkeypatch):
    releases = bundled_feature_releases() + (replace(bundled_feature_releases()[0], version="2.0.0"),)
    manager = _manager(tmp_path, releases=releases)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    manager.stage(FEATURE, "2.0.0", context=CONTEXT)
    monkeypatch.setattr("haven.extensions.lifecycle.os.replace", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        manager.update(FEATURE, context=CONTEXT)
    assert _run(manager)["version"] == "1.0.0"
    assert json.loads(manager.path.read_text())["features"][FEATURE]["version"] == "1.0.0"


def test_successful_update_invalidates_old_handle_and_rollback_health_checks(tmp_path):
    releases = bundled_feature_releases() + (replace(bundled_feature_releases()[0], version="2.0.0"),)
    manager = _manager(tmp_path, releases=releases)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    old = manager._active[FEATURE].module._services
    manager.stage(FEATURE, "2.0.0", context=CONTEXT)
    assert manager.update(FEATURE, context=CONTEXT)["version"] == "2.0.0"
    with pytest.raises(FeatureLifecycleError, match="no longer active"):
        old.call("decisions.context")
    assert manager.rollback(FEATURE, context=CONTEXT)["version"] == "1.0.0"
    assert _run(manager)["version"] == "1.0.0"


def test_uninstall_removes_installation_commands_and_stale_handles_across_restart(tmp_path):
    manager = _manager(tmp_path)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    services = manager._active[FEATURE].module._services
    manager.uninstall(FEATURE, context=CONTEXT)
    assert FEATURE not in manager._records
    assert manager.catalog(CONTEXT)["features"][0]["installed"] is False
    with pytest.raises(FeatureLifecycleError, match="no longer active"):
        services.call("decisions.context")
    with pytest.raises(FeatureLifecycleError, match="not installed"):
        _run(_manager(tmp_path))


def test_host_policy_alone_selects_releases_operations_commands_and_scope(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(FeatureLifecycleError, match="host-reviewed"):
        manager.stage("third.party", "1.0", context=CONTEXT)
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    with pytest.raises(FeatureLifecycleError, match="not granted"):
        manager.run(FEATURE, "export", context=CONTEXT)
    services = manager._active[FEATURE].module._services
    with pytest.raises(FeatureLifecycleError, match="widen its scope"):
        services.call("decisions.context", scope_id="another")
    with pytest.raises(ValueError, match="unknown feature service"):
        services.call("actions.execute")
    with pytest.raises(ValueError, match="1 and 12"):
        manager.run(FEATURE, "inspect", context=CONTEXT, params={"limit": 100})


def test_corrupt_state_fails_closed(tmp_path):
    (tmp_path / "features.json").write_text("not json")
    with pytest.raises(FeatureLifecycleError, match="repair is required"):
        _manager(tmp_path)


def test_both_bundled_versions_execute_and_rollback_restores_default_budget(tmp_path):
    limits = []
    manager = _manager(tmp_path, operations={"decisions.context": lambda **kw: limits.append(kw["limit"]) or {"decisions": []}})
    _install(manager)
    manager.enable(FEATURE, context=CONTEXT)
    assert _run(manager)["version"] == "1.0.0"
    manager.stage(FEATURE, "1.1.0", context=CONTEXT)
    manager.update(FEATURE, context=CONTEXT)
    assert _run(manager)["version"] == "1.1.0"
    manager.rollback(FEATURE, context=CONTEXT)
    assert _run(manager)["version"] == "1.0.0"
    assert limits == [10, 8, 10]
