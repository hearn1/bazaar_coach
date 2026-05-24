# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project overview

A coaching plugin for *The Bazaar* (Tempo Storm's PvP autobattler). Captures every run decision into a local SQLite database, scores them against build catalogs, and shows live coaching through an in-game overlay. Hero-aware catalogs ship for Karnok, Mak, Dooley, Vanessa, Pygmalien, Jules, and Stelle. Distributed as a Windows installer; releases at https://github.com/hearn1/bazaar_coach.

Current version: `APP_VERSION` in `version.py`.

## Common commands

```powershell
# Install runtime + test dependencies (Python 3.10+)
pip install -r requirements.txt

# One-command workflow: log watcher + Mono capture + Flask dashboard + PyWebView overlay.
# Decisions are scored live by LiveScorer as they insert.
venv312\Scripts\python.exe coach.py
venv312\Scripts\python.exe coach.py --no-mono       # skip Frida/Mono subprocess
venv312\Scripts\python.exe coach.py --no-overlay    # headless (watcher + Flask only)
venv312\Scripts\python.exe coach.py --log "PATH"    # override Player.log autodetect

# Setup / diagnostics
venv312\Scripts\python.exe coach.py setup-status
venv312\Scripts\python.exe coach.py setup --refresh-content never
venv312\Scripts\python.exe coach.py doctor
venv312\Scripts\python.exe coach.py export-diagnostics

# Content + catalogs
venv312\Scripts\python.exe coach.py refresh-content        # static card data
venv312\Scripts\python.exe coach.py refresh-builds         # latest hero catalogs
venv312\Scripts\python.exe coach.py refresh-images         # card image cache
venv312\Scripts\python.exe coach.py catalog-coverage --hero Karnok

# Watcher in isolation (debugging)
venv312\Scripts\python.exe watcher.py --parse-only         # one-shot parse of existing log
venv312\Scripts\python.exe watcher.py --log "PATH"

# Tests
venv312\Scripts\python.exe -m pytest -q
```

Dashboard: `http://127.0.0.1:5555` (`DEFAULT_WEB_PORT` in `coach.py`). Each session mirrors stdout/stderr to `logs/coach_YYYYMMDD_HHMMSS.log` — the file to share for debugging.

Auto-detected Player.log: `C:\Users\<You>\AppData\LocalLow\Tempo Storm\The Bazaar\Player.log`. Project is Windows-targeted at runtime; `frida`, `watchdog`, `pywebview` are unpinned in `requirements.txt` because they're Windows-venv- or game-build-dependent.

## Architecture

```
coach.py                   # entrypoint - launches the subsystems below
  ├─ watcher.py            # tails Player.log
  │    ├─ parser.py        # regex → event dicts
  │    └─ run_state.py     # state machine → decisions → db.py
  │         ├─ board_state.py    # authoritative inventory tracker
  │         ├─ shop_session.py   # shop-visit state machine
  │         └─ name_resolver.py  # instance_id → human name (lazy retry)
  ├─ capture_mono.py       # Frida + Mono hooks → game-state snapshots → db.py
  ├─ web/server.py         # Flask routes only
  │    ├─ web/overlay_state.py   # /api/overlay/state payload assembly
  │    ├─ web/review_builder.py  # overlay review row construction
  │    ├─ web/build_helpers.py   # catalog loading, archetype scoring, phase notes
  │    ├─ web/static/index.html  # dashboard (self-contained, inline JS)
  │    └─ web/static/overlay.html # overlay UI (self-contained, inline JS)
  └─ overlay.py            # PyWebView always-on-top launcher

Manual diagnostics:
  bridge.py                # correlation report between Pipeline A and Pipeline B (rarely used)
  scorer.py                # LiveScorer scores during the run; CLI prints a manual report
```

## Data flow

- **Pipeline A — Player.log → watcher → run_state.** Authoritative for decisions: offered/chosen/rejected sets, shops, skills, events, skips, sells. BoardState writes `board_snapshot_json` at every `insert_decision`. LiveScorer writes `score_label` immediately after the insert.
- **Pipeline B — capture_mono.py → Frida.** Enrichment: HP, gold, day/hour, prestige, PvP record, and authoritative `template_id` for name resolution via `NameResolver.notify_template()`.
- **Live Mono context.** `RunState._build_live_decision_context` looks up the latest compatible Mono snapshot at decision insert and stores `day/hour/gold/health/health_max/phase_actual/api_game_state_id/offered_names/offered_templates` on the `decisions` row before scoring.
- **scorer.py.** Phase-aware scoring against hero-specific build JSON catalogs. Prefers the writable copy in `app_paths.data_dir()/builds/`, falls back to bundled catalogs. `LiveScorer` scores at decision time; stored scores are authoritative — normal flow never bridges, rescores, or rewrites them.

## Key design decisions

- **Board state.** `BoardState` owned by `RunState`. Snapshots written as `board_snapshot_json` on every decision. Overlay reads the snapshot — no replay, no divergence.
- **Name resolution.** `NameResolver` with in-memory cache + `_UNRESOLVED` sentinel for lazy retry. Single service used by RunState (live) and server.py (per-request). Fallthrough: cache → template_map → api_cards → mark for retry.
- **Scoring.** `LiveScorer` instantiated per run. `score_decision()` called after each `insert_decision`, writes `score_label`/`score_notes` immediately. Run completion does not rescore.
- **Server split.** `web/server.py` is routes only. Business logic lives in `overlay_state.py`, `review_builder.py`, `build_helpers.py`.
- **Shop tracking.** `ShopSession` encapsulates shop visit state (offered/purchased/disposed/rerolls/decisions). RunState delegates via `self._shop`.
- **Snapshot scoping.** `RunState._snapshot_baseline_id` captures the max `api_game_states.id` when a run begins, so prior-run terminal snapshots cannot be stamped onto the new run's decisions. Overlay queries are bounded by `decisions.api_game_state_id` ranges (see `_get_run_mono_state_rows` / `_get_latest_live_snapshot` / `_get_run_end_snapshot` in `web/overlay_state.py`).

## Tech stack

- Python 3.10+, SQLite (`bazaar_runs.db`), Flask + waitress, PyWebView.
- Frida for Mono managed-memory hooks (JS agent embedded in `capture_mono.py`).
- No frontend build step — `index.html` / `overlay.html` are self-contained with inline CSS/JS.
- Fonts: Syne (display), DM Sans (body), IBM Plex Mono (data/labels).

## Features

**Core pipeline.** Log parsing, decision recording, state machine, combat tracking, card cache (playthebazaar.com static data), live Mono context attachment, phase-aware scoring with archetype detection, skip analysis, rejected-set tracking, PvP record from terminal Mono snapshot.

**Multi-hero.** Hero-aware end-to-end for Karnok, Mak, Dooley, Vanessa, Pygmalien, Jules, Stelle. Shared scorer/server/overlay paths resolve the active run's hero catalog, preferring the writable copy from `refresh-builds`, falling back to bundled. To add a new hero: use the fetch+compare workflow in [bazaar-builds](https://github.com/hearn1/bazaar-builds), then hand-edit a new `<hero>_builds.json` here.

**Mono capture.** Frida hooks `HandleMessage` for GameSim/CombatSim/GameStateSync/RunInitialized. ~39 ms median hook latency via direct memory reads (replacing NativeFunction calls). Key optimizations: `readGameSimFast` single-pass reader, `_fastReadPlayerAttrs` with cached dict layout, `_directReadMonoString` (UTF-16 direct read), content-hash SelectionSet cache, vtable→klass double-deref, hint-trusting in `getSnapshotMatches`. Gated by `FAST_GAMESIM_PATH = true`; set false to revert to the safe NativeFunction path.

**Dashboard.** Dark HUD-style UI: run history, stat strip (PvP/PvE/Decisions/Archetype/Flagged), key moments with severity-colored cards, phase-divider timeline with score-colored borders, expandable decision detail, combat grid.

**Overlay.** PyWebView frameless always-on-top window with three tabs — Coach (live archetype detection + item checklist), Review (last 10 decisions with score badges), Run (PvP/PvE record + phase guidance). F8 toggle collapse, drag-to-move, idle-state handling. Header stats from latest in-run Mono snapshot during active runs, EndRun snapshot for completed runs.

**Infrastructure.** Waitress production WSGI, session logging to `logs/`, DB writer queue for non-blocking writes, centralized app/settings/cache paths (`app_paths.py`), schema/settings migrations, content/image refresh commands, diagnostics/export support, pytest coverage under `tests/`, Windows installer via PyInstaller + Inno Setup.

**DB retention.** `coach.db_retention_days` setting (default `0` = disabled). When ≥90, runs with `ended_at` older than the threshold are pruned at startup along with their `decisions` and `combat_results` rows. In-progress runs (`ended_at IS NULL`) are never touched. Implemented as `db.prune_old_runs(retention_days, _now=None)`.

## Known quirks (not blocking)

- Mono can be absent or late. Decisions still insert and score via fallback heuristics; future decisions use live context once snapshots arrive.
- `fast_dict_fail` ~41% — managed dict is genuinely mid-update when hook fires. JS-side `_lastGoodAttrs` cache covers gaps (Gold missing = 0%).
- SelectionSet content-hash cache `selset_hits` may show 0 if no action-card states were seen this run; the cache only triggers in Choice/Loot/LevelUp states.
- `_directReadMonoString` auto-detects chars offset on first call (12 or 16, depending on Mono build).
- `api_game_states.captured_at` is mixed-format: some ISO 8601, some Unix ms. Time-range queries must handle both.
- `combat_results` has no `timestamp` column. Use the ratio-based estimate (`i * total_combats / total_decisions`) when you need combat-count-at-decision.
- Overlay header layout: the run-outcome pill lives in the subtitle line next to the run counter, not in `.header-actions`. The close `×` is corner-pinned absolute (`.header-quit`). When editing `renderHeader()`, keep these in place.
- Mid-run pickup misses Hero / UnlockedSlots / Prestige / Level. `NetMessageGameStateSync` and `NetMessageRunInitialized` are the only messages carrying a full `PlayerSnapshotDTO`; they fire at run init / reconnect / certain transitions. The mid-run `NetMessageGameSim` / `CombatSim` `Player` field resolves to `SimUpdatePlayer` — a per-tick delta with only `CombatantId` + an `Attributes` dict for attrs that *changed this tick*. First deltas usually carry `{Gold, Health, HealthMax}` (cached in `_lastGoodAttrs`); Prestige/Level rarely tick. Recovery is automatic on the next full `GameStateSync`. Grep `logs/coach_*.log` for `player-class fields` and `fast-PlayerAttributes` when debugging similar gaps.

## Capture Mono — technical notes

- Frida agent is a Python raw-string template: `FRIDA_MONO_AGENT = r"""..."""`.
- Hook source must contain `"dynamic-data"` for Python-side `_merge_partial_snapshot` to carry forward player attrs.
- Dict layout cache: `entriesOff=24, countOff=64, entrySize=16, hashOff=0, keyOff=8, valueOff=12, headerAdj=16`. Field offsets from `getFields()` include the 16-byte MonoObject header; subtracted for value-type array entries.

## Catalog curation

Hero catalogs (`<hero>_builds.json` + `builds_schema.json`) live in this repo and ship with the installer. Users run `coach.py refresh-builds` to pull latest catalogs into the writable data dir; malformed refreshed catalogs are ignored in favor of bundled.

The curator toolchain (`bazaar_build_enricher.py`, `probe_*.py`) lives in a separate repo: **[hearn1/bazaar-builds](https://github.com/hearn1/bazaar-builds)**. That repo has the enricher, probe scripts, CI schema validation, and instructions.

Workflow: run the enricher there → review the proposal markdown → hand-edit the appropriate `<hero>_builds.json` here → open a PR.

For manual catalog curation in bazaar-builds, distinguish safe no-op workflow validation from evidence-bearing curation. A run without fetched post evidence can validate the command path, but catalog curation validation requires fetched post evidence (normally `--fetch-posts`) or an evidence-backed empty result after fetch attempts. Apply curator-accepted deltas; otherwise record the evidence-backed no-change decision.

Automated refresh pipeline status: bazaar-builds is in `phase: live_cron` with `dry_run: false`. Weekly scheduled runs default to the `deterministic` classifier. Stats sidecars are persisted via rolling `automated/stats-sync-<hero>` PRs (direct push to `main` is branch-protected). Rolling coach catalog PRs may be opened/updated from `pipeline/<Hero>` branches when generated catalog diffs are non-empty. Hosted LLM classification is not part of the live path — do not require Anthropic/OpenAI credentials for scheduled refreshes. Coach catalog updates remain curator-reviewed PRs unless auto-merge is explicitly enabled later.

See `ROADMAP.md` for open work.
