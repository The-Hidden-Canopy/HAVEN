# Paired local feature contract

`haven-feature-ui-1` exposes `haven.feature.catalog` and `haven.feature.call`
through the existing authenticated TLS bridge. The native phone uses
`FeatureClient`; web and desktop retain the authenticated local workflow
dispatcher and the plugins' own result contracts. No phone-selected principal,
household, authorizer, path, import, or command executable is accepted.

The host must deliberately bind the external subject to the installed owner,
grant `actions.request`, and provision an expiring grant for the exact binding,
principal, active scope, feature and operation. A broad external scope alone is
insufficient. `FeatureGateway.grant` is a trusted host API, with no wire route.
The catalog includes only currently granted, reviewed features. Each operation
rechecks local membership, owner policy, license, and the current external
binding. The underlying service independently checks its scoped resources.

Unknown actions and commands fail closed. Mutation intent is persisted before
dispatch, keyed by the binding and request ID with a semantic hash. An exact
repeat never dispatches again: `already_recorded` means inspect current feature
state; an incomplete record means `outcome_uncertain`. Private result bodies are
not replayed from this ledger. Read calls rerun current authorization instead
of returning a prior report. No automatic mutation retry is permitted.

Pass `local_workflows.paired_feature_gateway()` and
`local_workflows.paired_session_gateway()` to the existing `ManagedBridgeHost`.
Their delegates follow the current service graph after a host rebuild. This
does not start a listener, issue a credential, enroll a phone, or establish TLS
trust. Real pairing and device/security acceptance remain separate.

Android provides granted lifecycle actions, Ghost Teacher catalog/session/data
catalog reads, and TraceGlass run selection/inspection. Deeper dataset review,
capture and export controls remain available in the web/native host views.
Foreground stop clears phone projections. Interrupted mutations retain the
existing uncertain-outcome marker and require inspecting the host before retry.
