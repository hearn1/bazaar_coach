# Bazaar Coach

Bazaar Coach is a Windows coaching plugin for *The Bazaar*. It captures every run decision into a local SQLite database, scores them against hero build catalogs, and shows live coaching through an in-game overlay.

Hero catalogs ship for Karnok, Mak, Dooley, Vanessa, Pygmalien, Jules, and Stelle.

## Download and install

1. Open the latest release: <https://github.com/hearn1/bazaar_coach/releases>.
2. Under **Assets**, grab the installer — the file named `BazaarCoachSetup-<version>.exe` (e.g. `BazaarCoachSetup-0.2.0-alpha.4.exe`). The `BazaarCoach-Portable-<version>.zip` next to it is the no-installer build; only grab that if you specifically want a portable copy.
3. Run the installer and accept the prompts. No admin rights are required for a per-user install; Windows may show a SmartScreen "Windows protected your PC" warning on first launch — click **More info** → **Run anyway**. See [packaging/installer/README.md](packaging/installer/README.md) for why an unsigned alpha build trips SmartScreen and antivirus.
4. After install, look for a Start Menu folder named **Bazaar Coach** with shortcuts for the main app and the Doctor / support commands.

## How to use it

1. Launch **Bazaar Coach** from the Start Menu.
2. Launch *The Bazaar*.
3. The overlay (the small always-on-top window that floats over the game) and dashboard (a web page at `http://127.0.0.1:5555` you can open in your browser) start and wait quietly if the game is not running yet.
4. Play normally.
5. Session logs land in `%LOCALAPPDATA%\BazaarCoach\logs\`.

Two binaries ship in the install directory:

| Binary | Purpose |
| --- | --- |
| `BazaarCoach.exe` | Windowed gameplay app (no console output) |
| `BazaarCoachCLI.exe` | Console support commands |

## What it does for you

Bazaar Coach watches your run and tracks everything you do so it can score your decisions and surface coaching in the overlay:

- Shop offers, picks, and the cards you passed on
- Skill offers and picks
- Map / event node choices
- Skips (left a shop without buying)
- Sells and inventory moves
- Combat outcomes (PvE wins / losses, PvP wins / losses)
- Run metadata: hero, session, timestamps, outcome
- Live game context: day, hour, gold, health, prestige, PvP record (when the in-game Mono capture is active)

## Troubleshooting and support log

If something goes wrong, the single most useful file to share is the latest session log at:

```
%LOCALAPPDATA%\BazaarCoach\logs\coach_YYYYMMDD_HHMMSS.log
```

Each launch writes a fresh `coach_<timestamp>.log`. Grab the most recent one and attach it when filing an issue at <https://github.com/hearn1/bazaar_coach/issues>.

You can also re-run diagnostics on demand from the install directory:

```powershell
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" doctor
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" export-diagnostics
```

## Uninstall

The uninstaller prompts once:

> Remove all Bazaar Coach user data from `%APPDATA%` and `%LOCALAPPDATA%`?

- **No** — removes installed app files, keeps user data.
- **Yes** — removes installed app files and deletes both `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`.

## Updates

Update checks run in the background and surface in the dashboard / overlay when a new GitHub Release is available. They are non-blocking and never call placeholder URLs.

---

## For developers

Everything below is for contributors and people who want to run Bazaar Coach from source. If you only want to use the app, you're done — skip this section.

### Requirements

Runtime (development):

- Python 3.10+
- Dependencies from `requirements.txt`
- Windows (Frida + PyWebView are Windows-targeted at runtime)

Packaging:

- PyInstaller build deps from `packaging/pyinstaller/requirements-build.txt`
- Inno Setup 6 for the Windows installer

### Data and settings locations

| Location | Contents |
| --- | --- |
| `%LOCALAPPDATA%\BazaarCoach\` | Database, logs, static cache, refreshed build catalogs |
| `%APPDATA%\BazaarCoach\` | `settings.json` |

Development runs keep mutable data in the repo root.

### Development setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run setup / status checks:

```powershell
python coach.py setup-status
python coach.py setup --refresh-content never
```

Normal startup does not block on CDN content refresh. It initializes local paths, settings, and the database, and reports missing static content as a warning.

Refresh static content when online (especially after Bazaar patches):

```powershell
python coach.py refresh-content
```

Refresh build catalogs to pull the latest curator-approved versions:

```powershell
python coach.py refresh-builds
```

If a refresh fails or returns a malformed catalog, the bundled catalogs continue to work.

### Running in development

```powershell
venv312\Scripts\python.exe coach.py
```

Useful flags:

```powershell
venv312\Scripts\python.exe coach.py --no-mono       # skip Frida/Mono subprocess
venv312\Scripts\python.exe coach.py --no-overlay    # headless watcher + Flask only
venv312\Scripts\python.exe coach.py --log "PATH"    # override Player.log autodetect
```

Dashboard: `http://127.0.0.1:5555`.

Session support log: `logs\coach_YYYYMMDD_HHMMSS.log` — the single most useful file for debugging.

### Player.log location

Auto-detected:

```
C:\Users\<You>\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log
```

Use `--log "..."` only if your Bazaar log is elsewhere.

### Diagnostics

```powershell
python coach.py doctor
python coach.py export-diagnostics
```

### Tests

Tests live in `tests/`; `pytest.ini` sets `pythonpath`/`testpaths`.

```powershell
venv312\Scripts\python.exe -m pytest -q
```

### Querying the database

Installed-app database: `%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db`.

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
    (run["id"],),
).fetchall()

for d in decisions:
    offered = json.loads(d["offered"])
    rejected = json.loads(d["rejected"]) if d["rejected"] else []
    print(f"#{d['decision_seq']} [{d['game_state']}] {d['decision_type']} — chose {d['chosen_template']}")
    print(f"  Offered {len(offered)}, rejected {len(rejected)}, score={d['score_label']}")
```

### Database schema

| Table | Purpose |
| --- | --- |
| `runs` | One row per run (hero, session, timestamps, outcome, PvP/PvE counters) |
| `decisions` | Picks, offers, rejected cards, live Mono context, live score |
| `combat_results` | Combat outcomes and player/opponent boards at combat time |
| `card_cache` | Local mirror of card names/tiers from the game's CDN |
| `api_game_states` | Mono snapshots (state, day, hour, gold, health, prestige, victories/defeats) |
| `api_cards` | Per-snapshot card collections (offered, owned, opponent) with template IDs |

The `decisions` table carries live scoring columns:

- `score_label` — `'optimal' | 'good' | 'info' | 'warning' | 'suboptimal' | 'waste'`
- `score_notes` — decision-time explanation text
- `board_snapshot_json` — frozen `BoardState` at this decision (overlay reads this directly)
- `api_game_state_id` — link to the attached Mono snapshot, if any

DB retention is opt-in: set `coach.db_retention_days` ≥ 90 in `settings.json` to prune completed runs older than that on startup. Default `0` disables pruning. In-progress runs are never touched.

### Architecture

```
coach.py                   # single entrypoint
  ├─ watcher.py            # tails Player.log
  │    ├─ parser.py        # regex → events
  │    └─ run_state.py     # decisions → db.py
  │         ├─ board_state.py
  │         ├─ shop_session.py
  │         └─ name_resolver.py
  ├─ capture_mono.py       # Frida + Mono → snapshots → db.py
  │    └─ capture_mono_agent.js  # embedded Frida JS agent
  ├─ web/server.py         # Flask routes
  │    ├─ web/overlay_state.py
  │    ├─ web/review_builder.py
  │    ├─ web/build_helpers.py
  │    ├─ web/static/index.html
  │    └─ web/static/overlay.html
  ├─ overlay.py            # PyWebView overlay
  └─ scorer.py             # LiveScorer
```

`CLAUDE.md` carries the deeper architecture / data-flow / quirk notes for contributors and AI assistants.

### Packaging

Portable build:

```powershell
pip install -r packaging/pyinstaller/requirements-build.txt
powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1
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

For end-to-end release cuts (version bump + build + tag + draft GitHub Release), see `packaging/release/README.md`.

### Roadmap

See `ROADMAP.md` for open work. Completed items are removed rather than kept as checked-off entries.
