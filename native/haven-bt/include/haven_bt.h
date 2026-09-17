/*
 * haven_bt.h -- the stable HAVEN-BT C ABI.
 *
 * HAVEN never sees a WinRT BluetoothLEDevice, a BlueZ org.bluez.Device1
 * object path, or a CoreBluetooth CBPeripheral -- only these opaque types.
 * Each platform backend (Windows/WinRT, Linux/BlueZ, macOS/CoreBluetooth)
 * owns the radio, pairing, and security model of its OS; this header is the
 * only thing HAVEN's Python code (via ctypes) depends on.
 *
 * Design notes:
 *  - No callbacks cross this boundary. Every event a backend produces is
 *    pushed onto an internal queue; the caller drains it with
 *    hb_poll_event(). This avoids callback-lifetime and cross-thread
 *    reentrancy problems at the FFI boundary entirely.
 *  - Every output buffer is caller-allocated with a caller-supplied
 *    capacity. There is no hb_free()/ownership-transfer function in v1 --
 *    a C ABI where the caller and the library might be built with
 *    different allocators must not hand back memory for the caller to
 *    free, so this API never does.
 *  - hb_device_id is an opaque 64-bit handle assigned internally by
 *    whichever backend is running. It is NOT a MAC address: CoreBluetooth's
 *    model is peripheral-UUID based, not MAC-based, so no cross-platform
 *    contract can require one. A backend may retain a native identity
 *    (Windows device ID, BlueZ object path, CoreBluetooth peripheral UUID)
 *    internally, keyed by this handle -- HAVEN never sees that identity.
 *
 * STATUS: this header is a design specification. It has not been compiled
 * or tested against a real toolchain -- see native/haven-bt/README.md.
 */

#ifndef HAVEN_BT_H
#define HAVEN_BT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  define HB_API __declspec(dllexport)
#else
#  define HB_API __attribute__((visibility("default")))
#endif

/* ---- Sizes -------------------------------------------------------- */

#define HB_MAX_NAME_LEN 64          /* advertised device name, NUL-terminated */
#define HB_MAX_UUID_LEN 37          /* standard UUID string form + NUL */
#define HB_MAX_PAYLOAD_LEN 512      /* GATT characteristic values are short */
#define HB_RSSI_UNKNOWN INT32_MIN   /* sentinel: no RSSI reading available */

/* ---- Opaque handles ------------------------------------------------ */

typedef struct hb_context hb_context;
typedef uint64_t hb_device_id; /* 0 is never a valid device id */

/* ---- Status codes --------------------------------------------------- */

typedef enum {
    HB_OK = 0,
    HB_ERROR_INVALID_ARGUMENT = 1,
    HB_ERROR_NOT_FOUND = 2,
    HB_ERROR_NOT_CONNECTED = 3,
    HB_ERROR_TIMEOUT = 4,
    HB_ERROR_PERMISSION_DENIED = 5,
    HB_ERROR_ADAPTER_UNAVAILABLE = 6,
    HB_ERROR_UNSUPPORTED = 7,
    HB_ERROR_INTERNAL = 8
} hb_status;

/* ---- Events ----------------------------------------------------------
 *
 * A native OS event (a WinRT advertisement watcher callback, a BlueZ
 * PropertiesChanged D-Bus signal, a CBCentralManagerDelegate callback) is
 * normalized into one of these and pushed onto the context's event queue.
 * hb_poll_event() is the only way HAVEN observes them.
 */

typedef enum {
    HB_EVENT_DEVICE_FOUND = 0,
    HB_EVENT_DEVICE_UPDATED = 1,
    HB_EVENT_CONNECTED = 2,
    HB_EVENT_DISCONNECTED = 3,
    HB_EVENT_SERVICES_READY = 4,
    HB_EVENT_NOTIFICATION = 5,
    HB_EVENT_PAIRING_REQUEST = 6,
    HB_EVENT_ERROR = 7
} hb_event_type;

typedef struct {
    hb_event_type type;
    hb_device_id device;                          /* 0 for an adapter-level event */
    int32_t rssi;                                 /* DEVICE_FOUND/DEVICE_UPDATED; HB_RSSI_UNKNOWN otherwise */
    char name[HB_MAX_NAME_LEN];                    /* advertised name, empty if none */
    char characteristic_uuid[HB_MAX_UUID_LEN];     /* NOTIFICATION only, else empty */
    uint8_t payload[HB_MAX_PAYLOAD_LEN];           /* NOTIFICATION only */
    size_t payload_len;
    hb_status error;                               /* HB_EVENT_ERROR only */
} hb_event;

/* ---- Context -------------------------------------------------------- */

HB_API hb_context* hb_context_create(void);
HB_API void hb_context_destroy(hb_context* ctx);

/* ---- Adapters --------------------------------------------------------
 *
 * A host may have more than one Bluetooth radio. v1 exposes only enough to
 * enumerate them by name; adapter selection for scan/connect is a v2
 * concern once that is actually needed.
 */

HB_API int hb_adapter_count(hb_context* ctx);
HB_API hb_status hb_adapter_get(hb_context* ctx, int index, char* name_out, size_t name_out_len);

/* ---- Scanning --------------------------------------------------------
 *
 * Starting a scan does not itself produce return values: results arrive as
 * HB_EVENT_DEVICE_FOUND / HB_EVENT_DEVICE_UPDATED events via hb_poll_event().
 */

HB_API hb_status hb_scan_start(hb_context* ctx);
HB_API hb_status hb_scan_stop(hb_context* ctx);

/* ---- Device connection lifecycle -------------------------------------- */

HB_API hb_status hb_device_connect(hb_context* ctx, hb_device_id device);
HB_API hb_status hb_device_disconnect(hb_context* ctx, hb_device_id device);
HB_API hb_status hb_device_pair(hb_context* ctx, hb_device_id device);
HB_API hb_status hb_device_forget(hb_context* ctx, hb_device_id device);

/* ---- GATT --------------------------------------------------------------
 *
 * uuids_out is a caller-allocated buffer of (max_count * HB_MAX_UUID_LEN)
 * bytes, laid out as max_count fixed-width NUL-terminated UUID strings --
 * a flat, fixed-stride buffer is trivial to describe from ctypes without
 * either side owning memory the other must free.
 */

HB_API hb_status hb_device_get_services(
    hb_context* ctx, hb_device_id device,
    char* uuids_out, size_t max_count, size_t* uuid_count_out);

HB_API hb_status hb_gatt_read(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    uint8_t* buf, size_t buf_len, size_t* bytes_read_out);

HB_API hb_status hb_gatt_write(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    const uint8_t* buf, size_t buf_len);

HB_API hb_status hb_gatt_subscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid);
HB_API hb_status hb_gatt_unsubscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid);

/* ---- Events ------------------------------------------------------------
 *
 * Returns 1 and fills *event_out if an event was dequeued, 0 if the queue
 * is currently empty. Never blocks.
 */

HB_API int hb_poll_event(hb_context* ctx, hb_event* event_out);

#ifdef __cplusplus
}
#endif

#endif /* HAVEN_BT_H */
