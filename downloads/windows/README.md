# HAVEN for Windows

Download [HAVENSetup.exe](https://github.com/The-Hidden-Canopy/HAVEN/raw/refs/heads/main/downloads/windows/HAVENSetup.exe), open it, and follow the installer. Then open **HAVEN** from the Start menu. The Python and .NET runtimes are bundled; no developer tools or source checkout are required. There is no account or paid service required to start.

This unsigned preview requires Windows 10 version 1809 or later on an x64 computer. Windows may display a warning or block unsigned software. Optional models, hardware and provider connections need their own configuration; proprietary plugins are not included.

The installer runs for your Windows account and defaults to `%LOCALAPPDATA%\Programs\HAVEN`. Close HAVEN before installing an update. Your selected household data directory (by default `%USERPROFILE%\.haven`) is separate from the app and is retained when you uninstall HAVEN through **Installed apps**.

The package contains the native WinUI desktop client, the public HAVEN Core, its Python runtime, the .NET/Windows App SDK runtime, and upstream license notices. `bundle-manifest.json` in the install folder records the source revision and payload hashes. `SHA256SUMS.txt` beside this file records the installer hash.

Preview `0.1.0-preview.20261030.1` fixes Settings diagnostics when Core reports a fractional uptime. It retains the original light and dark logos.

Real providers, security enrollment and physical Android pairing are separate acceptance steps. Installation does not enroll credentials, enable paid accounts or grant a provider authority.
