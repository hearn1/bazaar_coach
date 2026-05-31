# Bazaar Coach — Developer Setup

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
venv312\Scripts\python.exe coach.py --no-overlay    # headless Mono + Flask only
```

Dashboard: `http://127.0.0.1:5555`.

Session support log: `logs\coach_YYYYMMDD_HHMMSS.log` — the single most useful file for debugging.

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
  ├─ capture_mono.py       # Frida + Mono → MonoEventAdapter → run_state.py → db.py
  │    ├─ capture_mono_agent.js  # embedded Frida JS agent
  │    └─ mono_event_adapter.py  # translates snapshots → RunState events
  │         └─ run_state.py     # decisions → db.py
  │              ├─ board_state.py
  │              ├─ shop_session.py
  │              └─ name_resolver.py
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
