# Bazaar Coach — Windows installer

Inno Setup wraps the PyInstaller `dist/BazaarCoach` onedir into a single-file Windows installer.

## Build flow

```
pyinstaller packaging/pyinstaller/BazaarCoach.spec --noconfirm --clean
    → dist/BazaarCoach/                       (onedir portable build)

packaging/installer/build_installer.ps1
    → dist/installer/BazaarCoachSetup-<version>.exe
```

Run the PyInstaller step first; `build_installer.ps1` throws if `dist/BazaarCoach/BazaarCoach.exe` is absent.

## Build entrypoint

```powershell
.\packaging\installer\build_installer.ps1

# Optional overrides:
.\packaging\installer\build_installer.ps1 -AppVersion 0.2.0 -OutputDir C:\out
```

Version is read from `version.py` (`APP_VERSION`) when `-AppVersion` is omitted. Inno Setup 6 (`ISCC.exe`) must be installed or passed via `-InnoSetupCompiler`.

## Output

`dist/installer/BazaarCoachSetup-<version>.exe`

## Install behavior

The Inno Setup script (`BazaarCoach.iss`) declares:

- `PrivilegesRequired=lowest` — **no admin elevation required**. The installer detects whether it's running per-user or per-machine.
- `DefaultDirName={autopf}\Bazaar Coach\{#AppVersion}` — versioned side-by-side install.

In typical (per-user) installs this resolves to:

```
%LOCALAPPDATA%\Programs\Bazaar Coach\<version>\
```

A per-machine install (if launched elevated and the override is accepted) resolves to `C:\Program Files\Bazaar Coach\<version>\`.

After install, an optional post-install action runs `BazaarCoach.exe doctor` to validate the setup.

## Two shipped binaries

| Binary | Purpose |
| --- | --- |
| `BazaarCoach.exe` | Windowed gameplay app (no console output) |
| `BazaarCoachCLI.exe` | Console support commands (`doctor`, `refresh-builds`, `export-diagnostics`) |

## Uninstall

The uninstaller prompts once whether to also remove `%APPDATA%\BazaarCoach` and `%LOCALAPPDATA%\BazaarCoach` (settings, logs, database, cache). Choosing **No** leaves all user data intact.

## AV / SmartScreen warnings

The installer and bundled binaries are **not code-signed** (alpha). Several factors commonly trigger Windows Defender and commercial AV scanners:

- **PyInstaller onedir packing** — PE headers look unusual to heuristic scanners.
- **UPX compression** — used by PyInstaller; a known false-positive trigger.
- **Frida DLL injection** — Frida attaches to `TheBazaar.exe` at runtime to read Mono managed memory (HP, Gold, Day) for the live overlay. This is read-only; we do not modify game memory, bypass anti-cheat, or alter game behavior.
- **Injected JS agent** — Frida loads a small JavaScript agent into the Mono runtime. This pattern is flagged by some behavioral scanners.

Expected experience on a fresh install:

1. SmartScreen may show "Windows protected your PC" on first launch. Click **More info**, then **Run anyway**.
2. Defender or a third-party AV may quarantine `BazaarCoach.exe` or a Frida helper DLL. Restore the file from quarantine and add exclusions for:
   - The install directory: `%LOCALAPPDATA%\Programs\Bazaar Coach\<version>\` (or the elevated `Program Files` path if installed for all users)
   - The user-data directory: `%LOCALAPPDATA%\BazaarCoach\`

These warnings are a consequence of the unsigned-alpha state and Frida's legitimate but uncommon technique. A code-signed release will reduce — but not eliminate — them.
