# Error reporting and private diagnostics

HAVEN fault schema 1 uses stable `HAVEN.<SUBSYSTEM>.<FAILURE>` codes. The canonical registry is `haven/faults.py`; its subsystem inventory covers every current top-level HAVEN package. Existing service exception names map to specific codes within their operation's subsystem. Unknown failures use a safe fallback rather than serializing exception text.

Every fault contains its code, subsystem, normalized operation, severity, retryability, human explanation, recovery action, correlation ID and redacted confirmed evidence. `probable_causes` contains hypotheses with an explicit confidence label. An exception does not establish the underlying cause. A retryable flag never authorizes an external effect; an uncertain outcome requires durable-state inspection and reconciliation first.

| Family | Confirmed failure examples | Suggested investigation |
| --- | --- | --- |
| AUTHORITY | scope denied, owner required, unauthenticated | Current actor, membership and consent |
| IPC / CORE | authentication failure, unavailable pipe, startup failure, uncertain outcome | Current client/Core versions, connection and durable state |
| MEMORY / STORAGE / RECOVERY | generation conflict, source drift, persistence failure, pending recovery | Current revision, document evidence and preserved checkpoints |
| PROVIDERS / PLUGINS | unavailable provider, execution policy denial | Provider health, static metadata and host support |
| MARKETPLACE | verification or acquisition failure | Signed catalog, host pins and acquisition checkpoint |
| BACKUP / EXPORT | invalid backup, redaction failure | Verified backup manifest or export contract |
| NETWORK / CONFIG | timeout, invalid configuration | Connectivity and documented input fields |
| Other subsystem families | operation failure and existing specific service codes | The named subsystem's current state |

HTTP, native IPC and native UI consume the same fault envelope. The compatibility web UI also displays the code. The native client uses the same registered IPC/config/fallback codes for local transport failures. Codes and human messages never contain exception text, filesystem paths, provider endpoints, credentials or request arguments.

Repeated incidents group by canonical code and normalized operation. The installation's `diagnostics/incidents.json` retains occurrence count, first/last time, first/last correlation references and the last sixteen correlation references. Bounded original text is separate in `diagnostics/private-evidence.json`; native evidence is under the selected desktop state root. These are private installation files, not release assets. They have no public/raw-evidence API. Review and selectively disclose evidence only with the owner's approval of the exact audience and destination.

The support export uses schema 2 and an explicit allowlist: software version/commit, fixed health/count fields, aggregate receipt outcomes and redacted incident summaries. It excludes installation/principal/scope/model IDs, local paths, configuration strings, provider addresses, model assignments, raw receipts, correlation histories and original evidence. Unknown future fields are omitted. The private owner receipt view remains separate at `system.receipts` and `/api/system/receipts`.

## Isolated native state

`HAVEN_DESKTOP_DATA_DIR` selects an absolute native settings/diagnostic directory. If it is absent and `HAVEN_DATA_DIR` is set, native state uses that installation's `desktop` directory. An invalid explicit path fails before any real-profile fallback. The Core passes its selected installation root and desktop root to the native client. Direct standalone native launches without either override retain the Windows local-app-data default. Existing settings are not silently migrated or overwritten.

Tests inject temporary MCP settings paths and isolate Python home/data/model defaults. Packaged UI verification must additionally set fresh home, desktop, model, temp and Core data roots. It must not read an existing installation or use real credentials/memberships. Build caches and SDK first-run setup are separately controlled; application state isolation is not an operating-system sandbox.

When the Core selected the desktop state root, a confirmed installation move reroutes subsequent native settings writes to the new installation. Native writes pause until the authenticated Core confirms that directory. An explicit independent desktop override remains independent.
