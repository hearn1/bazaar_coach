# Bazaar Coach

Bazaar Coach is a Windows coaching plugin for *The Bazaar*. It captures your run decisions into a local SQLite database, scores them against build guides, and shows live coaching through an overlay.

## What it captures

- Card/item offers and picks
- Skill offers and picks
- Board state after decisions
- Combat outcomes
- Run metadata such as hero, session, timestamps, and outcome
- Live game context such as day, gold, health, and phase when Mono capture is available

## Requirements

For development:

- Python 3.10+
- Runtime dependencies from `requirements.txt`

For packaging:

- PyInstaller build dependencies from `packaging/pyinstaller/requirements-build.txt`
- Inno Setup 6 for the Windows installer

## Data and settings locations

Installed app:

| Location | Contents |
| --- | --- |
| `%LOCALAPPDATA%\BazaarCoach\` | Database, logs, static cache, refreshed build catalogs |
| `%APPDATA%\BazaarCoach\` | `settings.json` |

Development runs keep mutable data in the repo root.

## Development setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run setup/status checks:

```powershell
python coach.py setup-status
python coach.py setup --refresh-content never
```

Normal startup does not block on CDN content refresh. It initializes local paths, settings, and the database, and reports missing static content as a warning.

Refresh static content when online, especially after major Bazaar patches:

```powershell
python coach.py refresh-content
```

This fetches `data.playthebazaar.com/static`, keeps the previous local cache active if refresh fails, and records endpoint/card diffs in the content manifest.

Refresh build catalogs when updates are available:

```powershell
python coach.py refresh-builds
```

This pulls the latest curator-approved catalogs into the writable data directory. If refresh fails, the bundled catalogs continue to work.

Expected success output:

```text
refresh-builds: 5 updated, 0 unchanged, 0 skipped (errors)
```

## Running in development

Start the full workflow:

```powershell
venv312\Scripts\python.exe coach.py
```

Useful options:

```powershell
venv312\Scripts\python.exe coach.py --no-mono       # skip Frida/Mono subprocess
venv312\Scripts\python.exe coach.py --no-overlay    # headless watcher + Flask only
venv312\Scripts\python.exe coach.py --log "PATH"    # override Player.log autodetect
```

The dashboard is served at:

```text
http://127.0.0.1:5555
```

Each session writes a support log to:

```text
logs\coach_YYYYMMDD_HHMMSS.log
```

## Player.log location

Bazaar Coach auto-detects:

```text
C:\Users\<You>\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log
```

Use `--log "..."` only if your Bazaar log is somewhere else.

## Installed app workflow

The installer creates a Start Menu folder named **Bazaar Coach**.

Use:

```text
Start Menu → Bazaar Coach → Bazaar Coach
```

Normal app behavior:

1. Launch Bazaar Coach.
2. Launch *The Bazaar*.
3. The overlay/dashboard starts and waits quietly if the game is not running yet.
4. Play normally.
5. Session logs are written to `%LOCALAPPDATA%\BazaarCoach\logs\`.

The main installed binary is windowed:

```text
BazaarCoach.exe
```

It is for normal gameplay and does not show console output.

## Support commands

The packaged build also includes:

```text
BazaarCoachCLI.exe
```

Use this for support commands from PowerShell or cmd.exe:

```powershell
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" doctor
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" refresh-builds
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" export-diagnostics
```

Do not run support commands through `BazaarCoach.exe`; it is a windowed GUI binary and does not display stdout/stderr.

Current release note: Start Menu support shortcuts exist, but they may close immediately after the command finishes. Until that is fixed, run `BazaarCoachCLI.exe` from an already-open terminal when you need to read the output.

## Diagnostics

Development:

```powershell
python coach.py doctor
python coach.py export-diagnostics
```

Installed app:

```powershell
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" doctor
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" export-diagnostics
```

The most useful file to share for support is usually the latest session log:

```text
%LOCALAPPDATA%\BazaarCoach\logs\coach_YYYYMMDD_HHMMSS.log
```

## Packaging

Portable build:

```powershell
pip install -r packaging/pyinstaller/requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1
```

Optional explicit Python path:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1 -PythonExe C:\Path\To\python.exe
```

Installer build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\installer\build_installer.ps1
```

If Inno Setup is installed but `ISCC.exe` is not on PATH:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\installer\build_installer.ps1 `
  -InnoSetupCompiler "C:\Path\To\ISCC.exe"
```

The PyInstaller output contains two binaries:

| Binary | Purpose |
| --- | --- |
| `BazaarCoach.exe` | Windowed gameplay app |
| `BazaarCoachCLI.exe` | Console support commands |

## Uninstaller behavior

The uninstaller prompts once:

```text
Remove all Bazaar Coach user data from %APPDATA% and %LOCALAPPDATA%?
```

- **No** removes installed app files and keeps user data.
- **Yes** removes installed app files and deletes both `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`.

## Tests

Tests live in `tests/` and are configured through `pytest.ini`.

```powershell
venv312\Scripts\python.exe -m pytest -q
venv312\Scripts\python.exe -B -m py_compile coach.py first_run.py update_checker.py doctor.py refresh_builds.py settings.py card_cache.py content_manifest.py web/server.py
```

## Querying the database

Installed app database:

```text
%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db
```

Example:

```python
import json
import os
import sqlite3

db_path = os.path.expandvars(r"%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
decisions = conn.execute(
    "SELECT * FROM decisions WHERE run_id=? ORDER BY decision_seq",
    (run["id"],)
).fetchall()

for d in decisions:
    offered = json.loads(d["offered"])
    print(f"#{d['decision_seq']} [{d['game_state']}] {d['decision_type']} — chose {d['chosen_template']}")
    print(f"  Offered {len(offered)}, rejected {len(json.loads(d['rejected']))}")
```

## Database schema

| Table | Purpose |
| --- | --- |
| `runs` | One row per run |
| `decisions` | Picks, offers, rejected cards, and live score fields |
| `combat_results` | Combat outcomes and board state |
| `card_cache` | Local copy of card names/tiers from the game's CDN |

The `decisions` table includes live scoring columns:

- `score_label` — `'optimal'`, `'suboptimal'`, or `'waste'`
- `score_notes` — decision-time explanation text

## Updates and distribution

The app does not require a dedicated hosted website. Update checks are disabled by default and should be configured for GitHub Releases when a repo/release channel exists. The dashboard update check must remain non-blocking and must never call placeholder URLs.

## Architecture

```text
coach.py                   # single entrypoint
  |- watcher.py            # tails Player.log
  |    |- parser.py        # regex → structured events
  |    └── run_state.py    # decisions → db.py
  |         |- board_state.py
  |         |- shop_session.py
  |         └── name_resolver.py
  |- capture_mono.py       # Frida + Mono hooks → snapshots → db.py
  |- web/server.py         # Flask routes
  |    |- web/overlay_state.py
  |    |- web/review_builder.py
  |    |- web/build_helpers.py
  |    |- web/static/index.html
  |    └── web/static/overlay.html
  └── overlay.py           # PyWebView overlay
```

## Roadmap

See `ROADMAP.md` for current open work. Completed items are removed rather than kept as checked-off entries.
