# HAVEN browser connector (EXPERIMENTAL)

Reference scaffold for the browser-context integration described on spec
page 31. HAVEN Core does **not** depend on any of this to boot: the runtime
seam is `BrowserHub` (`haven/integrations/browser/provider.py`), and with no
connector connected every browser capability reports explicit
unavailability.

## What this is

- `extension/` — a minimal Manifest V3 extension scaffold. It observes tab
  identity/title/URL/activity and pushes snapshots to the native host. It
  does **not** read page bodies, cookies, form fields, or history, and it
  never touches incognito (incognito access is not requested in the
  manifest).
- `native-host/` — a Chrome native-messaging host manifest template plus a
  small Python host that bridges framed native-messaging messages to
  `BrowserHub` (in-process embedding) or to a future loopback transport.

## Install (manual, per-browser)

1. Copy `native-host/haven-browser-host.json` into the browser's native-messaging
   hosts directory, editing `path` to point at `haven-browser-host.py`.
2. Load `extension/` as an unpacked extension.
3. Restart HAVEN Core; the extension connects on browser start.

## Explicitly not built (future high-authority provider)

Fill/click/submit automation, page-content capture (except an explicit
per-action user gesture), and any incognito observation.
