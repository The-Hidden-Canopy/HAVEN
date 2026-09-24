import ctypes, json, sys, time
from ctypes import wintypes

sys.path.insert(0, r"E:\HiddenCanopy\HAVEN")
from haven.ipc import encode_frame, request_message
from haven.ipc.events_pipe import EventPublisher, NamedPipeEventServer
from haven.ipc.named_pipe import installation_pipe_name

pub = EventPublisher(flush_interval=0.05, heartbeat_interval=60.0)
server = NamedPipeEventServer(
    pipe_name=installation_pipe_name("test-events-debug"),
    auth_token="events-secret",
    publisher=pub,
).start()

k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k.CreateFileW.restype = wintypes.HANDLE
k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]

handle = None
for _ in range(40):
    handle = k.CreateFileW(server.pipe_name, 0xC0000000, 0, None, 3, 0, None)
    v = int(getattr(handle, "value", handle) or 0)
    if v not in (0, ctypes.c_void_p(-1).value):
        break
    time.sleep(0.025)
print("connected", flush=True)

frame = encode_frame(request_message("auth", "host.authenticate", {"token": "events-secret"}))
buf = ctypes.create_string_buffer(frame)
w = wintypes.DWORD()
assert k.WriteFile(handle, buf, len(frame), ctypes.byref(w), None)
print("auth written", flush=True)

def read_exact(n):
    buf = (ctypes.c_char * n)()
    r = wintypes.DWORD()
    ok = k.ReadFile(handle, buf, n, ctypes.byref(r), None)
    if not ok:
        print("ReadFile failed, err=", ctypes.get_last_error(), flush=True)
        raise SystemExit(1)
    if r.value != n:
        print(f"short read {r.value}/{n}", flush=True)
        raise SystemExit(1)
    return bytes(buf)

hdr = read_exact(4)
ln = int.from_bytes(hdr, "little")
body = read_exact(ln)
print("auth resp:", body.decode()[:120], flush=True)

# wait for core.connected
pub.publish("core.connected", coalesce=False)  # already published by server; second copy ok
t0 = time.time()
hdr = read_exact(4)
ln = int.from_bytes(hdr, "little")
body = read_exact(ln)
print(f"first frame after {time.time()-t0:.2f}s:", body.decode()[:200], flush=True)
server.stop()
pub.stop()
print("done", flush=True)
