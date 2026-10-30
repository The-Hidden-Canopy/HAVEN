# HAVEN Android development client

This is a small native Android foreground client for the existing HAVEN
external-agent tool contract, plus an HTTPS adapter and an explicitly
synthetic demo host. The app is not the complete HAVEN product. The original
Python HAVEN core executes the demo; Android does not pretend to embed it.

The adapter supports the existing world/room and action tools, plus an opt-in product projection described in [INTEGRATIONS.md](INTEGRATIONS.md).
It routes each request through `TransportBridge`, which resolves the credential,
checks the connection, binding, scope and principal, and delegates actions to
HAVEN's existing authority and confirmation path. The app displays the
returned world projection as room/device cards and shows action and pending
results in Activity. It does not claim an independent device verification
from a reported action status. It does not turn a model response or a client flag into
authority. The adapter's host supplies a credential-hash-to-subject mapping;
request bodies cannot assert their own subject or role. Its subject mapping
is separate from HAVEN's binding store and must be securely maintained by
an eventual production host. For admitted world reads, an optional host-supplied
registry lookup can advertise a short allow-list of parameter-free writable
services for projected device IDs. This is only a request option: it grants
no authority. Without the lookup, the phone shows no action buttons. Action
responses are restricted to result fields and omit the desktop's full state.

Only the synthetic demo host is wired up to start. An opt-in bounded host and
owner-approval pairing seam exist, but are not attached to production or an
authenticated owner UI; see [HOST.md](HOST.md). **There is no live household
provisioning, discovery, Android-to-running-desktop connection, production
listener, certificate enrollment, or production deployment in this change.**
A real host must bind the adapter to its current `external_agent_transport`,
provision and revoke per-device credentials and subject mappings through an
owner-authorized flow, and address authenticated TLS certificate distribution,
listener lifecycle, rate limits, audit/monitoring and security review. Do not
expose this demo server as a production service.

## Build the development APK

Pinned local inputs: JDK 17.0.6, Android platform 37.0 revision 2,
Build Tools 37.0.0 (aapt2, D8, zipalign and apksigner). The app compiles
against that platform, has minimum API 26 and target API 35. The build uses
platform Java APIs only; it does not download Gradle or libraries.

From the repository root in PowerShell:

```powershell
.\native\Haven.Android\build-debug.ps1 `
  -SdkRoot $env:ANDROID_SDK_ROOT `
  -JavaHome $env:JAVA_HOME
```

Output: `native/Haven.Android/build/haven-debug.apk`. The script creates an
ephemeral 30-day development signing identity in its temporary build staging
directory, verifies the APK signature, then deletes the key. Every build has
a different signing identity and therefore cannot upgrade a previous build;
the resulting bytes are not bit-for-bit reproducible. This is a debuggable
APK, not release signing or an artifact for distribution.

## Synthetic demo

Supply an existing TLS certificate and private key; the Android client uses
standard platform certificate and hostname validation, does not trust arbitrary
self-signed certificates, does not follow redirects, and refuses cleartext.
An Android device must be able to reach the chosen host and trust its
certificate. Do not change a device's security settings for this task.

```powershell
python -m haven.android_bridge --cert path\to\certificate.pem --key path\to\private-key.pem --host 127.0.0.1 --port 8443
```

The demo prints a random in-memory token for that run, uses a temporary
synthetic database, and never contacts live devices. The default listen
address is loopback. A deliberately chosen non-loopback `--host` is needed
for a separate client device; the certificate must match the address used.
Enter `https://<host>:8443/v1/tool` and the printed token in Settings, then
save and test. Home shows connection and attention status. Rooms displays
observed evidence and registered phone-supported actions; choose the office
light's turn-off request or the garage cover's close request. The garage
request requires confirmation, which appears as a card in Activity. Activity
is a local request history, not the household audit log; outcomes labeled as
reported or confirmation processed still require checking HAVEN and device
evidence. Demo access is bearer-token
access and the server is a development harness only.

The app has the Haven, Canopy, Ember and Mono palettes, each with System, Light
and Dark appearance modes; see [THEMES.md](THEMES.md). It uses app-private
AES-GCM connection storage backed by Android Keystore, and has a forget action. Settings provides
startup and connection-recovery help. The optional provider-neutral session interface is unavailable until a host injects
an authorized provider; see [session_contract/README.md](session_contract/README.md).
People and Automations remain unavailable; session memory requires provider support. It performs requests
only while the screen is in the foreground, disconnects on stop, and never
automatically retries a mutation. An interrupted mutation is recorded as an
uncertain outcome across restarts until the operator checks HAVEN and dismisses
the warning. Internet is the only requested permission; no microphone,
notification, background service or local-device discovery permission is
requested. A denied connection or scope appears as the returned denial; no
fallback grant is made.

## Tests and limits

Run `python -m pytest -q tests/test_android_pairing.py tests/test_android_host.py tests/test_android_bridge.py tests/test_external_agent_transport.py tests/test_external_agent_actions.py`.
Run the Android-independent presentation classification tests with
`.\native\Haven.Android\test-outcome.ps1 -JavaHome 'E:\Program Files\PyCharm Community Edition 2023.1.2\jbr'`.
The HTTPS test uses pytest and the existing optional `cryptography` dependency,
and creates a disposable test certificate and synthetic credentials. Tests
allow loopback only. They exercise the real demo director, external admission,
evidence projection, capability allow-listing, action response redaction, confirmation, idempotency, invalid credentials and an
actual TLS request, including the demo launcher. The JVM tests check action
labels, unknown evidence and conservative outcome classification.

Compilation and signature verification do not demonstrate device behavior.
Android lifecycle, Android Keystore behavior, TLS trust on a phone, screen
accessibility, background interruption, production provisioning, live
household interoperability and release signing all require separate acceptance.

The Android product screens use Latch and Harbor fixture adapters only in tests; no live product host, pairing UI, or Android Harbor verification channel has been wired.

## Source-only agent and theme handoff

The current source adds a provider-neutral shared-session contract and screen, but no real provider or authorization policy is configured; see [agent contract](session_contract/README.md). The Android theme catalog now reflects all checked desktop presets and modes; see [THEMES.md](THEMES.md). These source changes require a newly packaged development APK to be exercised; device acceptance remains separate.
