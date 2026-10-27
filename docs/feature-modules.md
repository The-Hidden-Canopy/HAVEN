# HAVEN Feature Modules

Feature Modules are native/domain extensions that run inside HAVEN under the
same governance boundary as first-party code. They are discovered through the
`haven.features` Python entry-point group, but discovery reads package
metadata only. Importing, building, and attaching a module are explicit
composition-root decisions.

An installed module supplies a plugin with:

- `describe() -> ExtensionDescriptor`, whose class must be `feature`;
- `build() -> module`, where the module exposes `attach(services=...)`.

The attach boundary receives `FeatureServiceCatalog`, a closed mapping of
named application-service operations. It does not receive a raw store,
credential store, installation path, or generic director object. Unknown
operations fail closed. A feature may propose or request work through the
operations the composition root explicitly grants; authority remains in the
existing application services and policy paths.

Example package metadata:

```toml
[project.entry-points."haven.features"]
my_feature = "my_feature.plugin:PLUGIN"
```

HAVEN now ships the **Decision context inspector** (`haven.decision-context`,
versions `1.0.0` and `1.1.0`): a trusted bundled, offline, read-only feature. Its `inspect`
command invokes only `decisions.context` and returns the bounded local decision
view. Decision text cannot grant permissions, create rules or approve work.
Results stay in the local UI. This service boundary for trusted Python is
**not an OS sandbox**.

Version `1.1.0` reduces the default read limit from ten decisions to eight while
retaining the same maximum of twelve and the same service boundary. Both host
releases remain available for actual local update/rollback acceptance.

## Metadata and admission

`inspect_feature_module()` reads bounded `haven-feature.json` from dist-info;
it never loads an entry point. Missing or malformed metadata blocks inspection.
Example static metadata:

```json
{"schema":"haven-feature.v1","entries":{"example":{"extension_id":"example.feature","display_name":"Example","class":"feature","detail":"Declared purpose"}}}
```

Metadata is a declaration, not permission. In-process entry-point loading and
the legacy `build_feature_module` hook default to blocked. Only an internal host
review may set `trusted_in_process=True`; requests, catalogs, retrieved documents
and persisted descriptors must never set it. Isolation for unknown third-party
Python is unavailable. The new lifecycle accepts only host-supplied factories;
catalog fields cannot select executable imports or commands.

## Installation and activation

`FeatureLifecycle` owns the installation's feature registrations. Product
installation, provider configuration and Export Consumer consent stay separate.

1. `catalog(context)` lists host-owned metadata and installation status.
2. `stage(id, version, context=...)` records a pending trusted bundled release.
3. `install(id, context=...)` commits an installation in a disabled state.
4. `enable(id, context=...)` attaches it and checks activation health.
5. `run(id, "inspect", context=..., params={...})` invokes the visible command.

Duplicate install and enable requests return stable status without attaching a
second module. Installation alone does not execute code. Unknown releases report
`untrusted_release`; the catalog explicitly reports third-party execution as
`blocked_isolation_unavailable`.

Every operation uses a server-derived `FeatureContext(principal_id, scope_id)`.
The host authorizer must revalidate current identity, membership and active scope.
A feature binds to the installing principal and scope; other bindings cannot
use or change it. Retained service callbacks recheck the same host policy,
supply the binding themselves, and refuse module-supplied scope or permission
fields. Revocation disables the activation; restored membership requires a
fresh explicit enable. The memory service independently checks each read.

The inspector accepts only `decision_id` and `limit` (1–12). It has no raw
receipt, generic export, credential, mutation or authority service.

## Disable, update and recovery

Disable invalidates handles before detach and unregisters the active command.
Re-enable cannot revive old handles. `close()` revokes handles on shutdown;
the composition root must close the previous manager before replacing it during
an application rebuild. One lifecycle owner operates each installation.

Updates first stage a candidate and run attach/health without making candidate
service handles live. A single atomic state replacement switches the current
version after health succeeds. Failed activation or pointer persistence retains
the prior running version. A successful switch invalidates and detaches the
prior version. Restart discards interrupted staging and preserves the last
committed pointer. Enabled intent is restored only after current policy and
health pass again.

Rollback health-checks the retained previous release before the same atomic
switch. Uninstall invalidates callbacks, detaches, and removes the installed
registration. The bundled release remains available in the catalog; its
installed commands disappear. Missing host releases stay disabled. Corrupt
state requires explicit repair rather than a silent reset.

## Composition

```python
features = FeatureLifecycle(
    data_dir / "features.json",
    releases=bundled_feature_releases(),
    authorize=current_host_feature_policy,
    service_operations={
        "decisions.context": lambda principal_id, scope_id, **params:
            decisions.context(scope_id, principal_id=principal_id, **params),
    },
)
```

The host owns allowed operation names and live identity/scope checks. The local
UI maps explicit stage/install/enable/inspect/disable/uninstall actions to the
manager and displays returned status/results. Export Consumer enablement never
supplies installation or execution consent. Focused tests cover actual bundled
command execution, restart, revocation, stale handles, update failure/recovery,
rollback, uninstall, metadata inspection and private-data export denial.
