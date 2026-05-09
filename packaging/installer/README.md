# Bazaar Tracker — Windows Installer

Inno Setup wraps the PyInstaller `dist/BazaarTracker` onedir into a single-file
Windows installer.

## Build flow

```
pyinstaller packaging/pyinstaller/BazaarTracker.spec --noconfirm --clean
    -> dist/BazaarTracker/          (onedir portable build)

packaging/installer/build_installer.ps1
    -> dist/installer/BazaarTrackerSetup-<version>.exe
```

Run the PyInstaller step first; `build_installer.ps1` will throw if
`dist/BazaarTracker/BazaarTracker.exe` is absent.

## Build entrypoint

```powershell
.\packaging\installer\build_installer.ps1
# Optional overrides:
.\packaging\installer\build_installer.ps1 -AppVersion 0.2.0 -OutputDir C:\out
```

Version is read from `version.py` (`APP_VERSION`) when `-AppVersion` is omitted.
Inno Setup 6 (`ISCC.exe`) must be installed or passed via `-InnoSetupCompiler`.

## Output

`dist/installer/BazaarTrackerSetup-<version>.exe`

## Install behavior

- Requires admin elevation.
- Installs to `Program Files\Bazaar Tracker\<version>` (versioned side-by-side).
- Post-install optionally runs `BazaarTracker.exe doctor` to validate the setup.

## Uninstall

Uninstall prompts whether to also remove `%APPDATA%\BazaarTracker` and
`%LOCALAPPDATA%\BazaarTracker` (settings, logs, database, cache). Choosing No
leaves all user data intact.

## AV / SmartScreen warnings

The installer and bundled binaries are **not code-signed** (alpha). Several
factors commonly trigger Windows Defender and commercial AV scanners:

- **PyInstaller onedir packing** — PE headers look unusual to heuristic scanners.
- **UPX compression** — used by PyInstaller; a known false-positive trigger.
- **Frida DLL injection** — Frida attaches to `TheBazaar.exe` at runtime to read
  Mono managed memory (HP, Gold, Day) for the live overlay. This is read-only;
  we do not modify game memory, bypass anti-cheat, or alter game behavior.
- **Injected JS agent** — Frida loads a small JavaScript agent into the Mono
  runtime. This pattern is flagged by some behavioral scanners.

**Expected experience on a fresh install:**

1. SmartScreen may show "Windows protected your PC" on first launch. Click
   **More info**, then **Run anyway**.
2. Defender or a third-party AV may quarantine `BazaarTracker.exe` or a Frida
   helper DLL. If this happens, restore the file from quarantine and add
   exclusions for:
   - The install directory: `C:\Program Files\Bazaar Tracker\<version>\`
   - The user data directory: `%LOCALAPPDATA%\BazaarTracker\`

These warnings are a consequence of the unsigned-alpha state and Frida's
legitimate but uncommon technique. A code-signed release will reduce (but not
eliminate) them.
