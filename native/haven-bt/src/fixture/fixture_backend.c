/*
 * fixture_backend.c -- a deterministic, real implementation of haven_bt.h.
 *
 * This is the native-code equivalent of FixtureHomeAssistant/FixtureIrBlaster:
 * not a platform backend (no WinRT/BlueZ/CoreBluetooth calls anywhere in
 * this file), but a real, compilable implementation of the ABI that always
 * behaves the same way, so CtypesBluetoothLibrary can be tested against an
 * actual loaded library instead of only a mocked one.
 *
 * It simulates exactly one device (hb_device_id 1, "Fixture BLE Light",
 * RSSI -42) with one pre-seeded GATT characteristic. hb_gatt_write() stores
 * whatever bytes are written and hb_gatt_read() returns them back -- real
 * in-memory state, not canned responses.
 */

#include "haven_bt.h"

#include <stdlib.h>
#include <string.h>

#define HB_FIXTURE_DEVICE 1
#define HB_FIXTURE_MAX_EVENTS 16
#define HB_FIXTURE_MAX_CHARACTERISTICS 4
#define HB_FIXTURE_CHARACTERISTIC_UUID "0000fff1-0000-1000-8000-00805f9b34fb"

typedef struct {
    char uuid[HB_MAX_UUID_LEN];
    uint8_t value[HB_MAX_PAYLOAD_LEN];
    size_t value_len;
} hb_fixture_characteristic;

struct hb_context {
    hb_event event_queue[HB_FIXTURE_MAX_EVENTS];
    size_t event_head;
    size_t event_count;
    int scanning;
    hb_fixture_characteristic characteristics[HB_FIXTURE_MAX_CHARACTERISTICS];
    size_t characteristic_count;
};

static void push_event(hb_context* ctx, hb_event ev) {
    /* A full queue drops the newest event. A real backend needs a real
     * backpressure policy; this fixture only ever needs to hold a handful. */
    if (ctx->event_count >= HB_FIXTURE_MAX_EVENTS) {
        return;
    }
    size_t tail = (ctx->event_head + ctx->event_count) % HB_FIXTURE_MAX_EVENTS;
    ctx->event_queue[tail] = ev;
    ctx->event_count++;
}

static hb_fixture_characteristic* find_characteristic(hb_context* ctx, const char* uuid) {
    for (size_t i = 0; i < ctx->characteristic_count; i++) {
        if (strcmp(ctx->characteristics[i].uuid, uuid) == 0) {
            return &ctx->characteristics[i];
        }
    }
    return NULL;
}

HB_API hb_context* hb_context_create(void) {
    hb_context* ctx = (hb_context*)calloc(1, sizeof(hb_context));
    if (!ctx) {
        return NULL;
    }
    hb_fixture_characteristic* seed = &ctx->characteristics[0];
    strncpy(seed->uuid, HB_FIXTURE_CHARACTERISTIC_UUID, HB_MAX_UUID_LEN - 1);
    seed->value[0] = 0x00;
    seed->value_len = 1;
    ctx->characteristic_count = 1;
    return ctx;
}

HB_API void hb_context_destroy(hb_context* ctx) {
    free(ctx);
}

HB_API int hb_adapter_count(hb_context* ctx) {
    (void)ctx;
    return 1;
}

HB_API hb_status hb_adapter_get(hb_context* ctx, int index, char* name_out, size_t name_out_len) {
    (void)ctx;
    static const char name[] = "Fixture Adapter";
    if (index != 0) {
        return HB_ERROR_NOT_FOUND;
    }
    if (name_out_len < sizeof(name)) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    memcpy(name_out, name, sizeof(name));
    return HB_OK;
}

HB_API hb_status hb_scan_start(hb_context* ctx) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    ctx->scanning = 1;
    hb_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = HB_EVENT_DEVICE_FOUND;
    ev.device = HB_FIXTURE_DEVICE;
    ev.rssi = -42;
    strncpy(ev.name, "Fixture BLE Light", HB_MAX_NAME_LEN - 1);
    push_event(ctx, ev);
    return HB_OK;
}

HB_API hb_status hb_scan_stop(hb_context* ctx) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    ctx->scanning = 0;
    return HB_OK;
}

HB_API hb_status hb_device_connect(hb_context* ctx, hb_device_id device) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    if (device != HB_FIXTURE_DEVICE) {
        return HB_ERROR_NOT_FOUND;
    }
    hb_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = HB_EVENT_CONNECTED;
    ev.device = device;
    push_event(ctx, ev);
    return HB_OK;
}

HB_API hb_status hb_device_disconnect(hb_context* ctx, hb_device_id device) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    if (device != HB_FIXTURE_DEVICE) {
        return HB_ERROR_NOT_FOUND;
    }
    hb_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = HB_EVENT_DISCONNECTED;
    ev.device = device;
    push_event(ctx, ev);
    return HB_OK;
}

HB_API hb_status hb_device_pair(hb_context* ctx, hb_device_id device) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    return device == HB_FIXTURE_DEVICE ? HB_OK : HB_ERROR_NOT_FOUND;
}

HB_API hb_status hb_device_forget(hb_context* ctx, hb_device_id device) {
    if (!ctx) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    return device == HB_FIXTURE_DEVICE ? HB_OK : HB_ERROR_NOT_FOUND;
}

HB_API hb_status hb_device_get_services(
    hb_context* ctx, hb_device_id device,
    char* uuids_out, size_t max_count, size_t* uuid_count_out
) {
    if (!ctx || !uuids_out || !uuid_count_out) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    if (device != HB_FIXTURE_DEVICE) {
        return HB_ERROR_NOT_FOUND;
    }
    size_t count = ctx->characteristic_count < max_count ? ctx->characteristic_count : max_count;
    for (size_t i = 0; i < count; i++) {
        memset(uuids_out + i * HB_MAX_UUID_LEN, 0, HB_MAX_UUID_LEN);
        strncpy(uuids_out + i * HB_MAX_UUID_LEN, ctx->characteristics[i].uuid, HB_MAX_UUID_LEN - 1);
    }
    *uuid_count_out = count;
    return HB_OK;
}

HB_API hb_status hb_gatt_read(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    uint8_t* buf, size_t buf_len, size_t* bytes_read_out
) {
    if (!ctx || !characteristic_uuid || !buf || !bytes_read_out) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    if (device != HB_FIXTURE_DEVICE) {
        return HB_ERROR_NOT_FOUND;
    }
    hb_fixture_characteristic* c = find_characteristic(ctx, characteristic_uuid);
    if (!c) {
        return HB_ERROR_NOT_FOUND;
    }
    size_t n = c->value_len < buf_len ? c->value_len : buf_len;
    memcpy(buf, c->value, n);
    *bytes_read_out = n;
    return HB_OK;
}

HB_API hb_status hb_gatt_write(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    const uint8_t* buf, size_t buf_len
) {
    if (!ctx || !characteristic_uuid || !buf) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    if (device != HB_FIXTURE_DEVICE) {
        return HB_ERROR_NOT_FOUND;
    }
    hb_fixture_characteristic* c = find_characteristic(ctx, characteristic_uuid);
    if (!c) {
        if (ctx->characteristic_count >= HB_FIXTURE_MAX_CHARACTERISTICS) {
            return HB_ERROR_INTERNAL;
        }
        c = &ctx->characteristics[ctx->characteristic_count++];
        strncpy(c->uuid, characteristic_uuid, HB_MAX_UUID_LEN - 1);
    }
    size_t n = buf_len < HB_MAX_PAYLOAD_LEN ? buf_len : HB_MAX_PAYLOAD_LEN;
    memcpy(c->value, buf, n);
    c->value_len = n;
    return HB_OK;
}

HB_API hb_status hb_gatt_subscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid) {
    if (!ctx || !characteristic_uuid) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    return device == HB_FIXTURE_DEVICE ? HB_OK : HB_ERROR_NOT_FOUND;
}

HB_API hb_status hb_gatt_unsubscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid) {
    if (!ctx || !characteristic_uuid) {
        return HB_ERROR_INVALID_ARGUMENT;
    }
    return device == HB_FIXTURE_DEVICE ? HB_OK : HB_ERROR_NOT_FOUND;
}

HB_API int hb_poll_event(hb_context* ctx, hb_event* event_out) {
    if (!ctx || !event_out || ctx->event_count == 0) {
        return 0;
    }
    *event_out = ctx->event_queue[ctx->event_head];
    ctx->event_head = (ctx->event_head + 1) % HB_FIXTURE_MAX_EVENTS;
    ctx->event_count--;
    return 1;
}
