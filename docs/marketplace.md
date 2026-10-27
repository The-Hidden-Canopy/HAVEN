# HAVEN Marketplace

HAVEN Marketplace is the curated product-discovery surface for The Hidden
Canopy portfolio. It is deliberately broader than the existing Export
Consumer catalog and deliberately narrower than a repository browser.

The product catalog supports discovery:

```text
fetch -> verify -> cache -> list/filter -> inspect state
```

The separate local product lifecycle can download, install, enable, open,
update, roll back, and remove the free bundled **HAVEN Welcome** content
fixture. It renders an inert welcome card through a fixed host adapter. It
does not install executables, grant household authority, or replace the
existing model/plugin contracts.

## What is separate

The product marketplace and the existing plugin catalog are different
contracts:

| Surface | Contract | Boundary |
| --- | --- | --- |
| Marketplace | `haven-marketplace-catalog.v1` | Product metadata and optional relationships |
| Export Consumers | `haven-plugin-catalog.v1` | Explicitly exported, redacted receipts only |
| Models | `haven-model.json` and the Model Manager | Model manifests, hashes, capabilities, and lifecycle |

The Marketplace cannot create a new extension class or widen any of these
boundaries. A product may be listed without an integration, and installing a
product never automatically enables its optional HAVEN integration.

## Current discovery behavior

The Core exposes:

- `GET /api/marketplace` for the current view;
- `POST /api/marketplace/refresh` for an explicit refresh; and
- IPC methods `marketplace.list` and `marketplace.refresh` for the native
  client.

The native **Marketplace** page supports search and release-state filtering.
Each card keeps product state separate from integration state. The page shows
the catalog's source, signature state, expiry/staleness, and named refresh
errors.

The repository includes a bundled **draft preview** derived from the curated
starter list. It is intentionally marked `bundled_preview` and
`signature_verified: false`. It is useful for exercising navigation and
information architecture, but it is not a release catalog and it has no
install action.

## Remote catalog trust

HAVEN does not invent or silently trust a live Hub key. A deployment that has a
real signed marketplace catalog may configure:

```text
HAVEN_MARKETPLACE_CATALOG_URL=https://example.invalid/api/marketplace/catalog
HAVEN_MARKETPLACE_TRUSTED_KEYS_JSON={"key-id":"base64-ed25519-public-key"}
```

The remote path requires:

- the exact `haven-marketplace-catalog.v1` schema;
- an Ed25519 signature from one of the explicitly configured keys;
- timezone-aware `issued_at` and `expires_at` values;
- valid closed vocabularies and safe product identifiers; and
- no duplicate product IDs.

Only a verified remote payload is written to `marketplace-cache.json`. If the
network is unavailable, HAVEN keeps the last verified catalog and marks it
stale after expiry. A corrupt cache is reported as `cache_invalid`; it is not
silently replaced with an empty catalog. Marketplace failure never prevents
HAVEN from booting. A verified refresh whose `issued_at` moves backward is
treated as a replay and cannot replace the newer accepted catalog.

## Local product acquisition

`LocalProductManager` is configured by the host with exact product/version,
platform, size, digest, source path, dependencies, and an approved adapter.
Neither a catalog nor a client request can register a source path or adapter.
Only `local_content` distributions with a first-party `content_only` product
and an exact numeric version are accepted. Other distribution kinds remain
discovery metadata.

The acquisition sequence is explicit:

```text
available -> downloaded -> installed -> enabled -> observed healthy
```

Each state has a separate status field. Downloading only caches verified
bytes. Installing atomically stages the package and changes the installed
version pointer; it leaves the product disabled. Enabling rechecks its
integrity and dependencies. Opening the product invokes the fixed welcome
card adapter and records observed health for that activation. A restart does
not report observed health until the adapter is opened again.

All mutations and launches consult the host's current authorization callback.
Every acquisition, enable, and open re-verifies the current signed catalog
against the host's current key map, expiration, release state, exact version,
platform, size, and digest pins. Removing a trusted key, withdrawing the
release, or marking it `security_hold` blocks a previously installed/enabled
product. Disabling and removing remain possible under current authorization
even when catalog trust has expired. No permission grant is stored in the
installation registry.

Local packages are small JSON records with a closed field set. They cannot
contain scripts, file lists, archives, imports, shell commands, executable
entry points, or integration enablement. Storage paths reject symlinks,
junctions, reparse points, traversal, and unapproved identifiers. Immutable
version directories and atomic registry replacement preserve the active
installation on corrupt or interrupted updates. Thread and OS file locks
serialize duplicate operations. A cancelled download never commits partial
bytes; a process interruption leaves only inert, unregistered candidates.

Rollback requires an exact previously installed version that remains allowed
by current catalog and host trust. It disables the restored version until an
explicit enable. Uninstall unregisters and disables first, then removes
release and download bytes. `data_policy=preserve` is the default; explicit
`data_policy=delete` removes only the product's dedicated data directory.
Interrupted removal is recorded as `removal_pending` and resumes only while
the requesting principal still passes current host authorization. A revoked
or legacy checkpoint cannot resume deleting saved data; explicit removal
under current authorization is required.
Dependencies are exact and checked without automatic installation or feature
activation.

The bundled fixture catalog is signed with the **published RFC 8032 test
vector** and is additionally pinned to its exact shipped envelope hash.
Artifact digests are independently pinned in host code. This public test key
is confined to the local fixture and is **never** added to remote catalog
trust. There is no operational signing key, credential, or runtime key
generation. The fixture proves the local lifecycle; it is not evidence of a
live release service.

The composition root exposes `GET /api/marketplace/products` and IPC
`marketplace.products`. Explicit actions use
`POST /api/marketplace/products/<action>` and IPC `marketplace.product`,
both through `haven.web.marketplace_lifecycle_api.product_action`.
The authenticated principal comes from the host; a request
body cannot choose its principal. Actions are `download`, `install`,
`update`, `rollback`, `enable`, `disable`, `launch`, `uninstall`, and `cancel`.
Download/install/update/rollback require exact `product_id` and `version`.
Cancellation uses an operation correlation ID owned by the current principal.
The content launch result is rendered as text, never interpreted as HTML.

The native Marketplace page provides these local lifecycle controls and
reports enablement separately from an observed successful open in the current
session. Removal offers an explicit keep-data choice and a separately
confirmed delete-data choice. Current identity and owner membership are
checked for every action. Catalog browsing cannot activate a release.

The product registry and content follow installation data-directory moves
and backups. OS lock files are recreated, rather than backed up. Domain
state is authoritative if history publication fails: the adapter reports
`outcome_uncertain`, and the caller must inspect current product state before
retrying. Download correlation IDs identify duplicate attempts; they confer
no permission to resume after revocation.

## Remaining service gates

The following remain later roadmap phases:

- remote artifact download and executable/archive installation;
- production catalog and artifact signing keys, release service, and
  revocation publication;
- additional product-specific launch adapters reviewed by the host;
- optional integrations through the separate feature lifecycle;
- commerce, entitlements, and publisher payouts; and
- community publishing.

These require configuration and separate approval. In particular, remote metadata will
never become an arbitrary shell command, and a signed catalog will never be
treated as proof that downloaded bytes or a local integration are safe by
themselves.
