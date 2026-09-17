#!/usr/bin/env bash
# Compile the HAVEN-BT fixture backend into a real shared library.
#
# Usage: build_fixture.sh [output_path]
#
# This is the deterministic reference backend (src/fixture/fixture_backend.c),
# not a platform backend -- no WinRT/BlueZ/CoreBluetooth calls anywhere in
# it. It exists so CtypesBluetoothLibrary can be tested against a real
# compiled library instead of only a mocked ctypes.CDLL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$SCRIPT_DIR/build/havenbt_fixture.dll}"
mkdir -p "$(dirname "$OUT")"

CC="${CC:-}"
if [ -z "$CC" ]; then
    for candidate in x86_64-w64-mingw32-clang clang cc gcc; do
        if command -v "$candidate" >/dev/null 2>&1; then
            CC="$candidate"
            break
        fi
    done
fi
if [ -z "$CC" ]; then
    echo "no C compiler found on PATH (set \$CC, or install one -- e.g. llvm-mingw on Windows, clang/gcc elsewhere)" >&2
    exit 1
fi

"$CC" -Wall -Wextra -I"$SCRIPT_DIR/include" -shared -o "$OUT" "$SCRIPT_DIR/src/fixture/fixture_backend.c"
echo "built: $OUT"
