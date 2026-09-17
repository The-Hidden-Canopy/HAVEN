# haven-bt

A native shared library (`libhavenbt.so` / `havenbt.dll` / `libhavenbt.dylib`)
exposing one stable C ABI (`include/haven_bt.h`) so HAVEN's Python code talks
to real Bluetooth through `ctypes`, with no Python Bluetooth library (no
Bleak) anywhere in the dependency tree.

## What actually exists here

- **`include/haven_bt.h`** -- the full v1 ABI: context lifecycle, adapter
  enumeration, scanning, device connect/pair/forget, GATT read/write/
  subscribe, and a polled (not callback-based) event queue. **This compiles
  clean** (`clang -Wall -Wextra`, zero warnings) -- see the fixture backend
  below.
- **`src/fixture/fixture_backend.c`** -- a real, deterministic
  implementation of the full ABI. Not a platform backend (no WinRT/BlueZ/
  CoreBluetooth anywhere in it) -- the native-code equivalent of
  `FixtureHomeAssistant`: it simulates exactly one device (`hb_device_id 1`,
  "Fixture BLE Light", RSSI -42) with one GATT characteristic that actually
  holds state (`hb_gatt_write` then `hb_gatt_read` round-trips real bytes,
  not canned ones). `build_fixture.sh` compiles it with whatever C compiler
  it finds (`x86_64-w64-mingw32-clang`/`clang`/`cc`/`gcc`).
- **`haven/integrations/bluetooth/native.py`** (Python side) -- a real
  `ctypes` binding against this exact header. `tests/test_bluetooth_native_abi.py`
  mocks `ctypes.CDLL` (proving the marshaling logic), and
  `tests/test_bluetooth_fixture_backend.py` compiles the fixture backend
  above and runs `CtypesBluetoothLibrary` against the **actual resulting
  `.dll`/`.so`** -- real `hb_context_create`, real `hb_scan_start`, a real
  `HB_EVENT_DEVICE_FOUND` event polled back, a real GATT write/read
  round-trip through native memory. This is verified on Windows via
  `llvm-mingw` (no MSVC installed on the machine it was built on); the test
  file skips cleanly wherever no compiler is on `PATH`, so it never blocks
  the rest of the suite.
- **`haven/integrations/bluetooth/provider.py`** -- `BluetoothProvider`,
  implementing HAVEN's `DiscoveryProvider` and `ExecutionAdapter` contracts
  against a small `NativeBluetoothLibrary` protocol that `native.py`'s
  `CtypesBluetoothLibrary` satisfies. Tested against a plain-Python
  `FixtureBluetoothLibrary` for unit-level coverage, against the real
  compiled fixture library in `test_bluetooth_fixture_backend.py`, and --
  manually verified, not as part of the automated suite -- against the real
  Windows/WinRT backend below, discovering genuine nearby BLE devices.

## Windows/WinRT: BLE Central v0.1, real, hardware-verified

**`src/platform/windows/winrt_backend.cpp`** is a real implementation
against `Windows.Devices.Bluetooth`/`.Advertisement`/`.GenericAttributeProfile`
-- not a fixture. It covers the whole v0.1 milestone below: adapter
enumeration (`Windows.Devices.Radios.Radio`), scan via
`BluetoothLEAdvertisementWatcher`, connect/disconnect via `BluetoothLEDevice`,
pair/forget via `DeviceInformation.Pairing`, GATT service/characteristic
discovery, read/write, and notify (`ValueChanged`) -- all through the polled
`hb_poll_event()` queue, exactly like the fixture backend. `build_windows.cmd`
locates Visual Studio via `vswhere.exe`, sets up the MSVC environment, and
links it against `WindowsApp.lib`.

This was verified end to end against **real hardware** on the machine that
wrote it (an Intel Wireless Bluetooth adapter): `hb_scan_start()` through a
real compiled DLL returned genuine nearby BLE advertisements -- real device
names, real RSSI -- through `CtypesBluetoothLibrary` and then through
`BluetoothProvider.discover()`, producing ordinary `DiscoveredDevice` values
indistinguishable from any other provider's. One real bug was caught and
fixed in the process: the Bluetooth radio on that machine was
administratively off (`RadioState.Off`), which `hb_scan_start()` correctly
reported as `HB_ERROR_ADAPTER_UNAVAILABLE` rather than silently returning no
results -- fail-loud, not fail-quiet, matching every other error path in
this ABI.

`tests/test_bluetooth_winrt_backend.py` is the automated half of this: it
compiles and loads the real DLL on every test run (skipped wherever MSVC +
the Windows SDK aren't installed), proving the backend keeps building as the
codebase evolves. It deliberately asserts nothing about scan *results* --
whether a radio is on, what is nearby, what it advertises are facts about a
host machine at a given moment, not about this code, and pinning them would
make CI flaky for reasons that have nothing to do with a real regression.

## Linux/BlueZ and macOS/CoreBluetooth: still not built

- **Linux/BlueZ** needs a D-Bus development environment (`libdbus`,
  `sd-bus`, or similar) and a running `bluetoothd` with a real adapter to
  exercise `Adapter1`/`Device1`/`GattCharacteristic1` against.
- **macOS/CoreBluetooth** needs Xcode (Objective-C++, the CoreBluetooth
  framework) and real Apple hardware -- CoreBluetooth has no meaningful
  simulator story for a real peripheral.

Neither environment exists in the session that wrote the Windows backend.
The Windows precedent is the bar the other two are held to before being
called done: real headers, a real compile, and a real device on real
hardware -- not several hundred lines of D-Bus or Objective-C++ that have
never been run once.

## Sequencing

1. ~~**BLE Central v0.1**~~: done for Windows (above). Still open for
   Linux/BlueZ and macOS/CoreBluetooth.
2. **Enrollment v0.2**: pairing/bonding is implemented
   (`hb_device_pair`/`hb_device_forget` on Windows) but untested against a
   peripheral that actually requires a PIN or passkey; reconnection,
   persisted native identity, and service caching are not built.
3. **Presence v0.3**: RSSI history and advertisement-only observations (no
   connection required) feeding `haven.perception.ObservationProvider` as
   `PresenceState` with confidence -- not built; today `BluetoothProvider`
   only produces `DiscoveredDevice`s, not `PresenceState`.
4. **Performance v0.4**: multiple simultaneous peripherals, a connection
   pool, operation queues, timeouts, MTU awareness -- not built; the Windows
   backend re-enumerates GATT services on every read/write/subscribe call
   rather than caching per-device (see the gaps note at the top of
   `winrt_backend.cpp`).
5. **Bluetooth Classic**, later, only for the specific profiles (RFCOMM,
   media, HID) HAVEN actually needs.

Do not start with raw HCI. Let WinRT, BlueZ, and CoreBluetooth own
radio/controller/security behavior; this ABI's job is only to normalize
their three device/GATT models into one opaque contract.

## The boundary this preserves

HAVEN owns semantics (`DeviceManifest`, `AuthorityEngine`, receipts). This
library owns Bluetooth (adapter/device/GATT), nothing above it. A device
protocol plugin (e.g. a Govee light profile) owns the mapping from a
capability like `brightness=50` to a characteristic UUID and encoded bytes
-- that layer is not built here either; `BluetoothProvider.execute()`
currently expects `DeviceCommand.parameters["bytes"]` to already be the raw
bytes to write, and `CapabilityDescriptor.service` to already be the
characteristic UUID. The OS owns the radio.
