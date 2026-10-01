"""Windows DPAPI binding: encrypt/decrypt secret bytes bound to the current
Windows user account, via `ctypes` against `crypt32.dll`/`kernel32.dll` --
no third-party dependency, the same "ctypes straight against the real OS
API" discipline `haven.integrations.bluetooth.native` already established
for the platform Bluetooth backend.

DPAPI (`CryptProtectData`/`CryptUnprotectData`) ties the ciphertext to the
Windows user profile that encrypted it: the OS, not HAVEN, holds the actual
key material. This is exactly the platform mechanism the native product-
consolidation plan's §5.1 asks for ("use the Windows credential/protected-
data platform for production secrets").
"""

from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DpapiUnavailableError(RuntimeError):
    """Raised on any non-Windows platform -- there is no fallback here on
    purpose; a credential store with a silently-weaker non-Windows path
    would be exactly the kind of security regression this module exists to
    avoid. Callers needing a non-Windows story must inject a different
    encryptor entirely, not expect this one to degrade quietly."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _require_windows() -> None:
    if platform.system() != "Windows":
        raise DpapiUnavailableError("Windows DPAPI is only available on Windows")


def _blob_to_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _bytes_to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    """Build a blob and retain its backing buffer for the native call.

    A ``DATA_BLOB`` stores only a pointer. Returning the buffer alongside it
    keeps that pointer valid until the caller's CryptProtect/CryptUnprotect
    invocation has finished; returning only the struct lets Python reclaim the
    temporary buffer before Windows reads it.
    """

    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(cbData=len(data), pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt32():
    _require_windows()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    for name in ("CryptProtectData", "CryptUnprotectData"):
        function = getattr(library, name)
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
    return library


def _kernel32():
    _require_windows()
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.LocalFree.argtypes = [wintypes.HLOCAL]
    library.LocalFree.restype = wintypes.HLOCAL
    return library


def protect(data: bytes, *, entropy: bytes | None = None) -> bytes:
    """Encrypt `data` for the current Windows user. `entropy`, if given,
    must be supplied again to decrypt -- an extra factor the ciphertext
    alone does not carry, useful for scoping (e.g. per-installation)."""

    crypt32 = _crypt32()
    kernel32 = _kernel32()

    data_in, data_in_buffer = _bytes_to_blob(data)
    data_out = _DataBlob()
    entropy_pair = _bytes_to_blob(entropy) if entropy else None
    entropy_blob = entropy_pair[0] if entropy_pair else None
    entropy_buffer = entropy_pair[1] if entropy_pair else None
    entropy_ptr = ctypes.byref(entropy_blob) if entropy_blob is not None else None

    ok = crypt32.CryptProtectData(
        ctypes.byref(data_in), None, entropy_ptr, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
    )
    if not ok:
        raise OSError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        return _blob_to_bytes(data_out)
    finally:
        kernel32.LocalFree(data_out.pbData)


def unprotect(ciphertext: bytes, *, entropy: bytes | None = None) -> bytes:
    """Decrypt ciphertext produced by `protect()`. Raises `OSError` if the
    ciphertext was not produced by this Windows user account (a different
    machine, a different user, or a restored profile) -- DPAPI itself
    enforces that boundary; this module does not weaken it."""

    crypt32 = _crypt32()
    kernel32 = _kernel32()

    data_in, data_in_buffer = _bytes_to_blob(ciphertext)
    data_out = _DataBlob()
    entropy_pair = _bytes_to_blob(entropy) if entropy else None
    entropy_blob = entropy_pair[0] if entropy_pair else None
    entropy_buffer = entropy_pair[1] if entropy_pair else None
    entropy_ptr = ctypes.byref(entropy_blob) if entropy_blob is not None else None

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, entropy_ptr, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out)
    )
    if not ok:
        raise OSError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
    try:
        return _blob_to_bytes(data_out)
    finally:
        kernel32.LocalFree(data_out.pbData)


__all__ = ["DpapiUnavailableError", "protect", "unprotect"]
