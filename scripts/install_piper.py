"""Install the native Piper TTS runtime for `haven.models.backends.piper_native`.

Run directly: `python scripts/install_piper.py`

Downloads the last release of `rhasspy/piper` (MIT licensed; its
GPL-licensed successor, piper1-gpl, is deliberately not used here -- a
subprocess-invoked GPL tool wouldn't taint HAVEN's Apache-2.0 license, but
the original MIT release does everything HAVEN needs and avoids the
question entirely) and extracts it to `~/.haven/tools/piper/`. This is a
one-time setup step, the same as installing `onnxruntime`/`llama_cpp` for
the other reference backends -- the executable is never vendored inside
this repository.

Windows only today (the release asset this script downloads is
`piper_windows_amd64.zip`); Linux/macOS builds exist upstream
(`piper_linux_*`, `piper_macos_*`) and are a straightforward extension of
`_RELEASE_ASSET` below when needed.
"""

from __future__ import annotations

import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven.models.backends.piper_native import default_piper_root

_REPO = "rhasspy/piper"
_RELEASE_TAG = "2023.11.14-2"
_RELEASE_ASSET = "piper_windows_amd64.zip"
_DOWNLOAD_URL = f"https://github.com/{_REPO}/releases/download/{_RELEASE_TAG}/{_RELEASE_ASSET}"


def main() -> None:
    if platform.system() != "Windows":
        raise SystemExit(
            "this script only installs the Windows build today; see the module "
            "docstring for the Linux/macOS asset names to extend it"
        )

    dest = default_piper_root()
    if (dest / "piper.exe").is_file():
        print(f"piper is already installed at {dest}")
        return

    print(f"Downloading {_RELEASE_ASSET} from {_REPO}@{_RELEASE_TAG}...")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / _RELEASE_ASSET
        with urllib.request.urlopen(_DOWNLOAD_URL, timeout=120) as response, open(archive, "wb") as f:
            shutil.copyfileobj(response, f)

        print(f"Extracting to {dest}...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(Path(tmp) / "extracted")

        # The archive's top-level entry is a "piper/" folder; flatten it so
        # `dest` itself holds piper.exe and its sibling DLLs/data directly.
        extracted_root = Path(tmp) / "extracted" / "piper"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(extracted_root), str(dest))

    exe = dest / "piper.exe"
    if not exe.is_file():
        raise SystemExit(f"install finished but {exe} is missing; the release layout may have changed")
    print(f"Installed piper to {dest}")
    print("Download a voice (e.g. rhasspy/piper-voices on Hugging Face) and register it as a HAVEN model to use it.")


if __name__ == "__main__":
    main()
