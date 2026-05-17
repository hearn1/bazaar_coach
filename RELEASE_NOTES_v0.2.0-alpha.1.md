# Bazaar Coach v0.2.0-alpha.1

First limited Discord alpha release for Bazaar Coach.

## What this includes

- One-command installed app workflow.
- Live overlay and dashboard.
- Local SQLite run capture.
- Live decision scoring against bundled build catalogs.
- Refreshable build catalogs.
- Diagnostics export and doctor command.
- Windows installer with user-data preserve/delete uninstall options.

## Verified in manual testing

- Installed app launches without terminal-window flashing while waiting for The Bazaar.
- Runtime database stays in `%LOCALAPPDATA%\BazaarCoach`, not the install folder.
- Runtime database also stayed out of the install folder after real capture activity.
- Uninstaller “No” preserves user data.
- Uninstaller “Yes” removes user data.
- `BazaarCoachCLI.exe doctor` works from terminal.
- Shutdown no longer logs the SQLite thread-close warning.
- Installer and doctor both report `0.2.0-alpha.1`.

## Known issues

- Start Menu support command shortcuts may close immediately after finishing. Workaround: run `BazaarCoachCLI.exe doctor`, `refresh-builds`, or `export-diagnostics` from an already-open PowerShell/cmd window.
- Some early item/skill names may occasionally appear as raw IDs.
- Image manifest warnings may appear in `doctor`; image support is not part of this alpha release.
