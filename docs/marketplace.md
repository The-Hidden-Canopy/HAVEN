# HAVEN Marketplace

HAVEN Marketplace is the curated product-discovery surface for The Hidden
Canopy portfolio. It is deliberately broader than the existing Export
Consumer catalog and deliberately narrower than a repository browser.

The current implementation is the discovery slice from the Marketplace
Docuseries roadmap:

```text
fetch -> verify -> cache -> list/filter -> inspect state
```

It does not install software, launch arbitrary commands, grant household
authority, or replace the existing model/plugin contracts.

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
future product will not automatically enable its optional HAVEN integration.

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

## Deliberately not built yet

The following remain later roadmap phases:

- product installation and uninstall;
- release artifact downloads, hashes, signatures, and revocations;
- local installed-product identity and launch registration;
- optional integration enablement after standalone installation;
- updates and rollback;
- commerce, entitlements, and publisher payouts; and
- community publishing.

Those features require separate contracts. In particular, remote metadata will
never become an arbitrary shell command, and a signed catalog will never be
treated as proof that downloaded bytes or a local integration are safe by
themselves.
