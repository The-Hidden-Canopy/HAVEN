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

HAVEN currently ships the loader and contract, not a first-party feature
module. That keeps the extension surface honest while the first feature is
selected and designed against real application-service needs.
