/*
 * winrt_backend.cpp -- a real Windows/WinRT implementation of haven_bt.h.
 *
 * This is BLE Central v0.1 from native/haven-bt/README.md's sequencing:
 * adapter enumeration, scan/advertisements, connect/disconnect, pair/forget,
 * service discovery, GATT read/write, and notifications -- against the real
 * Windows.Devices.Bluetooth WinRT APIs (BluetoothLEAdvertisementWatcher,
 * BluetoothLEDevice, GattDeviceService/GattCharacteristic), not a fixture.
 *
 * Consolidated into one file for this first real milestone rather than the
 * originally sketched adapter/device/gatt split -- splitting is easy once
 * there is enough real behavior to warrant separate translation units;
 * doing it before that just adds header plumbing.
 *
 * Threading: WinRT delegates (Received, ValueChanged) run on background
 * threads the runtime owns, not the thread that called hb_scan_start()/
 * hb_gatt_subscribe(). Every access to shared context state is behind
 * queue_mutex or device_mutex for exactly that reason -- this is not
 * defensive boilerplate, it is required for correctness the first time two
 * advertisements arrive concurrently.
 *
 * Known v0.1 gaps, deliberately deferred per the README's sequencing:
 *  - hb_device_get_services / the read/write/subscribe helper below
 *    re-enumerate GATT services on every call rather than caching a
 *    per-device characteristic table (that's Performance v0.4's "connection
 *    pool, operation queues" concern).
 *  - hb_device_pair() blocks on PairAsync(), which may show a system
 *    pairing UI; there is no timeout wired through this ABI's error codes
 *    yet.
 *  - RawSignalStrengthInDBm() failures are swallowed into HB_RSSI_UNKNOWN;
 *    no distinct error is surfaced for "adapter doesn't support RSSI" vs.
 *    "no reading yet."
 */

/* sscanf/strncpy are used here deliberately, bounded, the same way
 * fixture_backend.c uses them -- keep the portable CRT functions rather
 * than switching to MSVC-only _s variants just for this platform file. */
#define _CRT_SECURE_NO_WARNINGS

#include "haven_bt.h"

#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Devices.Bluetooth.h>
#include <winrt/Windows.Devices.Bluetooth.Advertisement.h>
#include <winrt/Windows.Devices.Bluetooth.GenericAttributeProfile.h>
#include <winrt/Windows.Devices.Enumeration.h>
#include <winrt/Windows.Devices.Radios.h>
#include <winrt/Windows.Storage.Streams.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace winrt;
using namespace winrt::Windows::Devices::Bluetooth;
using namespace winrt::Windows::Devices::Bluetooth::Advertisement;
using namespace winrt::Windows::Devices::Bluetooth::GenericAttributeProfile;
using namespace winrt::Windows::Devices::Enumeration;
using namespace winrt::Windows::Devices::Radios;
using namespace winrt::Windows::Storage::Streams;

namespace {

std::string guid_to_string(winrt::guid const& g) {
    char buf[HB_MAX_UUID_LEN];
    std::snprintf(
        buf, sizeof(buf), "%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        static_cast<unsigned>(g.Data1), static_cast<unsigned>(g.Data2), static_cast<unsigned>(g.Data3),
        g.Data4[0], g.Data4[1], g.Data4[2], g.Data4[3], g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7]
    );
    return std::string(buf);
}

bool string_to_guid(const char* s, winrt::guid& out) {
    unsigned long d1 = 0;
    unsigned d2 = 0, d3 = 0;
    unsigned b[8] = {};
    if (!s) return false;
    int matched = std::sscanf(
        s, "%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        &d1, &d2, &d3, &b[0], &b[1], &b[2], &b[3], &b[4], &b[5], &b[6], &b[7]
    );
    if (matched != 11) return false;
    out.Data1 = static_cast<uint32_t>(d1);
    out.Data2 = static_cast<uint16_t>(d2);
    out.Data3 = static_cast<uint16_t>(d3);
    for (int i = 0; i < 8; i++) out.Data4[i] = static_cast<uint8_t>(b[i]);
    return true;
}

std::string device_key(hb_device_id device, const char* uuid) {
    return std::to_string(device) + ":" + uuid;
}

std::vector<Radio> bluetooth_radios() {
    std::vector<Radio> result;
    try {
        for (auto const& radio : Radio::GetRadiosAsync().get()) {
            if (radio.Kind() == RadioKind::Bluetooth) {
                result.push_back(radio);
            }
        }
    } catch (...) {
        /* No radio access (permissions, no adapter) -- an empty list is the
         * correct answer, not an error the caller can act on differently. */
    }
    return result;
}

} // namespace

struct hb_context {
    BluetoothLEAdvertisementWatcher watcher{nullptr};
    winrt::event_token received_token{};
    bool scanning = false;

    std::mutex queue_mutex;
    std::deque<hb_event> events;

    std::mutex device_mutex;
    std::unordered_map<hb_device_id, uint64_t> device_addresses;
    std::unordered_map<uint64_t, hb_device_id> address_to_id;
    std::unordered_set<hb_device_id> first_seen;
    hb_device_id next_device_id = 1;

    std::unordered_map<hb_device_id, BluetoothLEDevice> connected_devices;
    std::unordered_map<std::string, winrt::event_token> notify_tokens;
    std::unordered_map<std::string, GattCharacteristic> notify_characteristics;
};

namespace {

void push_event(hb_context* ctx, hb_event ev) {
    std::lock_guard<std::mutex> lock(ctx->queue_mutex);
    ctx->events.push_back(ev);
}

hb_device_id assign_device_id(hb_context* ctx, uint64_t address) {
    std::lock_guard<std::mutex> lock(ctx->device_mutex);
    auto found = ctx->address_to_id.find(address);
    if (found != ctx->address_to_id.end()) {
        return found->second;
    }
    hb_device_id id = ctx->next_device_id++;
    ctx->address_to_id[address] = id;
    ctx->device_addresses[id] = address;
    return id;
}

BluetoothLEDevice connected_device_or_null(hb_context* ctx, hb_device_id device) {
    std::lock_guard<std::mutex> lock(ctx->device_mutex);
    auto it = ctx->connected_devices.find(device);
    return it == ctx->connected_devices.end() ? BluetoothLEDevice{nullptr} : it->second;
}

/* Re-enumerates every call -- see the v0.1 gaps note at the top of this file. */
bool find_characteristic(BluetoothLEDevice const& dev, const char* uuid_str, GattCharacteristic& out) {
    winrt::guid target;
    if (!string_to_guid(uuid_str, target)) return false;
    try {
        auto services_result = dev.GetGattServicesAsync(BluetoothCacheMode::Uncached).get();
        if (services_result.Status() != GattCommunicationStatus::Success) return false;
        for (auto const& service : services_result.Services()) {
            auto chars_result = service.GetCharacteristicsForUuidAsync(target, BluetoothCacheMode::Uncached).get();
            if (chars_result.Status() != GattCommunicationStatus::Success) continue;
            auto chars = chars_result.Characteristics();
            if (chars.Size() > 0) {
                out = chars.GetAt(0);
                return true;
            }
        }
    } catch (...) {
    }
    return false;
}

} // namespace

HB_API hb_context* hb_context_create(void) {
    try {
        winrt::init_apartment(winrt::apartment_type::multi_threaded);
    } catch (...) {
        /* Already initialized on this thread with a compatible apartment --
         * RoInitialize is refcounted; tolerate a repeat call rather than
         * failing context creation over it. */
    }
    hb_context* ctx = new (std::nothrow) hb_context();
    if (!ctx) return nullptr;
    try {
        ctx->watcher = BluetoothLEAdvertisementWatcher();
    } catch (...) {
        delete ctx;
        return nullptr;
    }
    return ctx;
}

HB_API void hb_context_destroy(hb_context* ctx) {
    if (!ctx) return;
    try {
        if (ctx->scanning) {
            ctx->watcher.Stop();
        }
        if (ctx->received_token.value != 0) {
            ctx->watcher.Received(ctx->received_token);
        }
    } catch (...) {
    }
    delete ctx;
}

HB_API int hb_adapter_count(hb_context* ctx) {
    (void)ctx;
    return static_cast<int>(bluetooth_radios().size());
}

HB_API hb_status hb_adapter_get(hb_context* ctx, int index, char* name_out, size_t name_out_len) {
    (void)ctx;
    if (!name_out) return HB_ERROR_INVALID_ARGUMENT;
    auto radios = bluetooth_radios();
    if (index < 0 || static_cast<size_t>(index) >= radios.size()) return HB_ERROR_NOT_FOUND;
    std::string name = winrt::to_string(radios[static_cast<size_t>(index)].Name());
    if (name.size() + 1 > name_out_len) return HB_ERROR_INVALID_ARGUMENT;
    std::memcpy(name_out, name.c_str(), name.size() + 1);
    return HB_OK;
}

HB_API hb_status hb_scan_start(hb_context* ctx) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    if (ctx->scanning) return HB_OK;
    try {
        ctx->watcher.ScanningMode(BluetoothLEScanningMode::Active);
        ctx->received_token = ctx->watcher.Received(
            [ctx](BluetoothLEAdvertisementWatcher const&, BluetoothLEAdvertisementReceivedEventArgs const& args) {
                uint64_t address = args.BluetoothAddress();
                hb_device_id id = assign_device_id(ctx, address);

                bool is_new;
                {
                    std::lock_guard<std::mutex> lock(ctx->device_mutex);
                    is_new = ctx->first_seen.insert(id).second;
                }

                hb_event ev{};
                ev.type = is_new ? HB_EVENT_DEVICE_FOUND : HB_EVENT_DEVICE_UPDATED;
                ev.device = id;
                try {
                    ev.rssi = args.RawSignalStrengthInDBm();
                } catch (...) {
                    ev.rssi = HB_RSSI_UNKNOWN;
                }
                std::string name = winrt::to_string(args.Advertisement().LocalName());
                std::strncpy(ev.name, name.c_str(), HB_MAX_NAME_LEN - 1);
                push_event(ctx, ev);
            }
        );
        ctx->watcher.Start();
        ctx->scanning = true;
        return HB_OK;
    } catch (...) {
        return HB_ERROR_ADAPTER_UNAVAILABLE;
    }
}

HB_API hb_status hb_scan_stop(hb_context* ctx) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    if (!ctx->scanning) return HB_OK;
    try {
        ctx->watcher.Stop();
        if (ctx->received_token.value != 0) {
            ctx->watcher.Received(ctx->received_token);
            ctx->received_token = {};
        }
        ctx->scanning = false;
        return HB_OK;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_device_connect(hb_context* ctx, hb_device_id device) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    uint64_t address;
    {
        std::lock_guard<std::mutex> lock(ctx->device_mutex);
        auto it = ctx->device_addresses.find(device);
        if (it == ctx->device_addresses.end()) return HB_ERROR_NOT_FOUND;
        address = it->second;
    }
    try {
        auto dev = BluetoothLEDevice::FromBluetoothAddressAsync(address).get();
        if (!dev) return HB_ERROR_NOT_FOUND;
        {
            std::lock_guard<std::mutex> lock(ctx->device_mutex);
            /* insert_or_assign, not operator[]: GattCharacteristic and
             * BluetoothLEDevice are WinRT projected types with no default
             * constructor (by design, to prevent an accidental null
             * handle), and operator[] on an unordered_map value-initializes
             * before assigning -- it will not compile for these types. */
            ctx->connected_devices.insert_or_assign(device, dev);
        }
        hb_event ev{};
        ev.type = HB_EVENT_CONNECTED;
        ev.device = device;
        push_event(ctx, ev);
        return HB_OK;
    } catch (...) {
        return HB_ERROR_TIMEOUT;
    }
}

HB_API hb_status hb_device_disconnect(hb_context* ctx, hb_device_id device) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    bool had;
    {
        std::lock_guard<std::mutex> lock(ctx->device_mutex);
        /* BluetoothLEDevice has no explicit Disconnect(): releasing every
         * reference (erasing it here) is what actually drops the GATT
         * connection on this API. */
        had = ctx->connected_devices.erase(device) > 0;
    }
    if (!had) return HB_ERROR_NOT_CONNECTED;
    hb_event ev{};
    ev.type = HB_EVENT_DISCONNECTED;
    ev.device = device;
    push_event(ctx, ev);
    return HB_OK;
}

HB_API hb_status hb_device_pair(hb_context* ctx, hb_device_id device) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    try {
        auto info = DeviceInformation::CreateFromIdAsync(dev.DeviceId()).get();
        auto pairing = info.Pairing();
        if (pairing.IsPaired()) return HB_OK;
        auto result = pairing.PairAsync().get();
        return result.Status() == DevicePairingResultStatus::Paired ? HB_OK : HB_ERROR_PERMISSION_DENIED;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_device_forget(hb_context* ctx, hb_device_id device) {
    if (!ctx) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    try {
        auto info = DeviceInformation::CreateFromIdAsync(dev.DeviceId()).get();
        auto pairing = info.Pairing();
        if (!pairing.IsPaired()) return HB_OK;
        auto result = pairing.UnpairAsync().get();
        return result.Status() == DeviceUnpairingResultStatus::Unpaired ? HB_OK : HB_ERROR_INTERNAL;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_device_get_services(
    hb_context* ctx, hb_device_id device, char* uuids_out, size_t max_count, size_t* uuid_count_out
) {
    if (!ctx || !uuids_out || !uuid_count_out) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    try {
        auto services_result = dev.GetGattServicesAsync(BluetoothCacheMode::Uncached).get();
        if (services_result.Status() != GattCommunicationStatus::Success) return HB_ERROR_INTERNAL;
        size_t count = 0;
        for (auto const& service : services_result.Services()) {
            if (count >= max_count) break;
            auto chars_result = service.GetCharacteristicsAsync(BluetoothCacheMode::Uncached).get();
            if (chars_result.Status() != GattCommunicationStatus::Success) continue;
            for (auto const& characteristic : chars_result.Characteristics()) {
                if (count >= max_count) break;
                std::string uuid = guid_to_string(characteristic.Uuid());
                char* slot = uuids_out + count * HB_MAX_UUID_LEN;
                std::memset(slot, 0, HB_MAX_UUID_LEN);
                std::strncpy(slot, uuid.c_str(), HB_MAX_UUID_LEN - 1);
                count++;
            }
        }
        *uuid_count_out = count;
        return HB_OK;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_gatt_read(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    uint8_t* buf, size_t buf_len, size_t* bytes_read_out
) {
    if (!ctx || !characteristic_uuid || !buf || !bytes_read_out) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    GattCharacteristic characteristic{nullptr};
    if (!find_characteristic(dev, characteristic_uuid, characteristic)) return HB_ERROR_NOT_FOUND;
    try {
        auto result = characteristic.ReadValueAsync(BluetoothCacheMode::Uncached).get();
        if (result.Status() != GattCommunicationStatus::Success) return HB_ERROR_INTERNAL;
        auto value = result.Value();
        uint32_t n = value.Length();
        if (n > buf_len) n = static_cast<uint32_t>(buf_len);
        auto reader = DataReader::FromBuffer(value);
        reader.ReadBytes(winrt::array_view<uint8_t>(buf, buf + n));
        *bytes_read_out = n;
        return HB_OK;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_gatt_write(
    hb_context* ctx, hb_device_id device, const char* characteristic_uuid,
    const uint8_t* buf, size_t buf_len
) {
    if (!ctx || !characteristic_uuid || !buf) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    GattCharacteristic characteristic{nullptr};
    if (!find_characteristic(dev, characteristic_uuid, characteristic)) return HB_ERROR_NOT_FOUND;
    try {
        DataWriter writer;
        writer.WriteBytes(winrt::array_view<const uint8_t>(buf, buf + buf_len));
        auto buffer = writer.DetachBuffer();
        auto status = characteristic.WriteValueAsync(buffer).get();
        return status == GattCommunicationStatus::Success ? HB_OK : HB_ERROR_INTERNAL;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_gatt_subscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid) {
    if (!ctx || !characteristic_uuid) return HB_ERROR_INVALID_ARGUMENT;
    auto dev = connected_device_or_null(ctx, device);
    if (!dev) return HB_ERROR_NOT_CONNECTED;
    GattCharacteristic characteristic{nullptr};
    if (!find_characteristic(dev, characteristic_uuid, characteristic)) return HB_ERROR_NOT_FOUND;
    try {
        auto config_status = characteristic
            .WriteClientCharacteristicConfigurationDescriptorAsync(
                GattClientCharacteristicConfigurationDescriptorValue::Notify
            )
            .get();
        if (config_status != GattCommunicationStatus::Success) return HB_ERROR_INTERNAL;

        std::string key = device_key(device, characteristic_uuid);
        std::string uuid_copy = characteristic_uuid;
        auto token = characteristic.ValueChanged(
            [ctx, device, uuid_copy](GattCharacteristic const&, GattValueChangedEventArgs const& args) {
                hb_event ev{};
                ev.type = HB_EVENT_NOTIFICATION;
                ev.device = device;
                std::strncpy(ev.characteristic_uuid, uuid_copy.c_str(), HB_MAX_UUID_LEN - 1);
                auto value = args.CharacteristicValue();
                uint32_t n = value.Length();
                if (n > HB_MAX_PAYLOAD_LEN) n = HB_MAX_PAYLOAD_LEN;
                auto reader = DataReader::FromBuffer(value);
                reader.ReadBytes(winrt::array_view<uint8_t>(ev.payload, ev.payload + n));
                ev.payload_len = n;
                push_event(ctx, ev);
            }
        );

        std::lock_guard<std::mutex> lock(ctx->device_mutex);
        ctx->notify_tokens[key] = token;
        ctx->notify_characteristics.insert_or_assign(key, characteristic);
        return HB_OK;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API hb_status hb_gatt_unsubscribe(hb_context* ctx, hb_device_id device, const char* characteristic_uuid) {
    if (!ctx || !characteristic_uuid) return HB_ERROR_INVALID_ARGUMENT;
    std::string key = device_key(device, characteristic_uuid);
    GattCharacteristic characteristic{nullptr};
    winrt::event_token token{};
    {
        std::lock_guard<std::mutex> lock(ctx->device_mutex);
        auto ct = ctx->notify_characteristics.find(key);
        auto tt = ctx->notify_tokens.find(key);
        if (ct == ctx->notify_characteristics.end() || tt == ctx->notify_tokens.end()) {
            return HB_ERROR_NOT_FOUND;
        }
        characteristic = ct->second;
        token = tt->second;
        ctx->notify_characteristics.erase(ct);
        ctx->notify_tokens.erase(tt);
    }
    try {
        characteristic.ValueChanged(token);
        characteristic
            .WriteClientCharacteristicConfigurationDescriptorAsync(
                GattClientCharacteristicConfigurationDescriptorValue::None
            )
            .get();
        return HB_OK;
    } catch (...) {
        return HB_ERROR_INTERNAL;
    }
}

HB_API int hb_poll_event(hb_context* ctx, hb_event* event_out) {
    if (!ctx || !event_out) return 0;
    std::lock_guard<std::mutex> lock(ctx->queue_mutex);
    if (ctx->events.empty()) return 0;
    *event_out = ctx->events.front();
    ctx->events.pop_front();
    return 1;
}
