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

## Install (registered, per-browser)

Load `extension/` as an unpacked extension first and copy its generated
extension id. Then preview the registration plan (the default is read-only):

```powershell
python browser/native-host/install.py --extension-id <extension-id> --host-path <installed-host>
```

To write the host manifests and current-user registrations for Chrome, Edge,
and Firefox, add `--install`. Use `--browser chrome`, `--browser edge`, or
`--browser firefox` to limit the targets; repeat the option for multiple
browsers. `--uninstall` removes only HAVEN's current-user registration keys.
The installer generates Chromium and Firefox manifests separately because
their allow-list fields differ. It never needs administrator rights and never
writes a browser profile or extension setting.

## Explicitly not built (future high-authority provider)

Fill/click/submit automation, page-content capture (except an explicit
per-action user gesture), and any incognito observation.
