# Bazaar Coach - Roadmap

Active work tracker. This file should contain only work that is still actionable. Completed items are removed rather than kept as checked-off entries. Stable project context and architecture notes live in `CLAUDE.md`.

Status labels:

- `Open`: not yet implemented.
- `Partial`: useful foundation exists, but more work is needed.
- `On Hold`: blocked by an external dependency or prerequisite.

## Triage Queue (2026-05-23)

Prioritized fix order for the open GitHub issues. Ordering principle: infra changes that affect everything else first, then correctness regressions ranked by blast radius, then content/catalog, then tooling. Per-issue root cause + effort lives on each GH issue thread.

1. [#85](https://github.com/hearn1/bazaar_coach/issues/85) — Local vs `.exe` functionality drift. **Infra. Do first** — duplicate `karnok_builds.json` at repo root vs `builds/` means dev and packaged builds load different catalogs, which makes triage of every other bug suspect. Effort: M.
2. [#83](https://github.com/hearn1/bazaar_coach/issues/83) — PvP/PvE/day wrong on overlay header. `_get_latest_live_snapshot` in `web/overlay_state.py` is unscoped to current run/hero, so stale Mono rows from a prior run bleed into the live header. Effort: M.
3. [#84](https://github.com/hearn1/bazaar_coach/issues/84) — Leave Run button missing. Likely the same root cause as #83 (prior run never closed → new run shows stale state with `is_active=true`). Verify after #83; layer in a manual "force end" control only if still needed. Effort: S after #83.
4. [#81](https://github.com/hearn1/bazaar_coach/issues/81) — Universal utility items marked suboptimal when committed. `scorer.score_late_decision` committed-branch never checks `universal_utility_items`/`economy_items`. Surgical scorer fix. Effort: S.
5. [#77](https://github.com/hearn1/bazaar_coach/issues/77) — Missed items not showing in review tab. `_emit_shop_visit_missed_entry` + the acquired-name suppression filter in `web/review_builder.py` are over-suppressing early-run misses. Do after the scorer/state fixes so fixtures are stable. Effort: M.
6. [#82](https://github.com/hearn1/bazaar_coach/issues/82) — Night Vision/Chains/Fairy Circle build display is wrong. Needs live run data to reproduce; likely a catalog content fix that should ride on #85's consolidation. Effort: M.
7. [#86](https://github.com/hearn1/bazaar_coach/issues/86) — Release script. Pure feature. Defer until the bug backlog above ships. Effort: M.

## Release Todo

### P1 — Keep support-command windows open

Status: Open

Observed during installed-app testing on 2026-05-12:

- The installer creates Start Menu shortcuts for Doctor, Refresh Builds, and Export Diagnostics.
- Those shortcuts target `BazaarCoachCLI.exe` directly.
- When launched from the Start Menu or the post-install "Run Doctor" option, the terminal opens and closes immediately after the command finishes, so testers cannot read the output.

Expected:

- Main **Bazaar Coach** shortcut continues to launch `BazaarCoach.exe`.
- Support shortcuts open a visible terminal and keep it open after command completion.
- Post-install **Run Doctor** uses the same stay-open support path.
- Paths are quoted correctly because the install directory contains a space.
- Working directory remains the installed app directory.

Likely fix area:

- `packaging/installer/BazaarCoach.iss`

Implementation direction:

- Use `cmd.exe /K` for support shortcuts and post-install Doctor, or add wrapper `.cmd` files that stay open.
- Keep `BazaarCoachCLI.exe` available for users who run commands from an already-open terminal.

How to test:

1. Build the installer.
2. Install normally.
3. Launch **Bazaar Coach - Doctor** from the Start Menu.
4. Confirm a terminal opens, prints doctor output, and remains open.
5. Repeat for **Refresh Builds** and **Export Diagnostics**.
6. Install again and use the post-install **Run Doctor** option; confirm the output remains readable.

### P2 — Align release version metadata before publishing

Status: Open

Observed during installed-app testing on 2026-05-12:

- The newly built installer was named `BazaarCoachSetup-0.1.0-dev.exe`.
- `doctor` reported `Bazaar Coach 0.1.0-dev`.
- The release being tested is intended for the v0.2 alpha line.

Expected:

- App version, installer filename, install directory, and diagnostic output all match the intended release version before publishing.

Likely fix area:

- Version source used by the app.
- PyInstaller/Inno packaging version inputs.
- Release build process.

How to test:

1. Build portable and installer artifacts.
2. Confirm the installer filename contains the intended version.
3. Install the app.
4. Run `BazaarCoachCLI.exe doctor`.
5. Confirm the reported app version matches the installer version.

## Discord Alpha — Open Issues (v0.2.0-alpha.1)

Findings from manual installer/runtime verification on 2026-05-10 against packaged version `0.2.0-alpha.1`. Items are ordered P1 → P2 within severity.

**Verification passed:** pytest (192 tests), py_compile, portable build + smoke test, installer build, clean install launch, main "Bazaar Coach" Start Menu shortcut, Doctor shortcut target (`cmd /K "...BazaarCoach.exe" doctor`), overlay + dashboard launch, real game capture (first few actions), SQLite rows + session logs created, diagnostics ZIP created, refresh-builds.

**Open:**

### P1-1 — Terminal windows flash repeatedly while waiting for The Bazaar

**Observed:** Launched installed "Bazaar Coach" shortcut before The Bazaar was running. Multiple PowerShell/cmd-like windows repeatedly opened and closed until the game process appeared, then stopped.

**Impact:** Looks broken/scary to Discord testers. Should wait quietly for the game.

**Expected:** No visible terminal windows during the wait-for-game phase. Overlay/dashboard may show "waiting for game." Once The Bazaar is detected, `capture_mono` attaches normally.

**Likely fix area:**
- `coach.py` subprocess launch of `capture_mono` in packaged mode
- Retry loop that re-spawns `capture_mono` while game process is absent
- Windows subprocess creation flags: `CREATE_NO_WINDOW`, `STARTUPINFO` / `STARTF_USESHOWWINDOW`
- PyInstaller packaged-binary subprocess behavior (visible console window)

**How to test:**
1. Install packaged app.
2. Launch "Bazaar Coach" shortcut before The Bazaar is running.
3. Confirm no terminal windows flash — overlay/dashboard should wait quietly.
4. Open The Bazaar and confirm `capture_mono` attaches and decisions flow.

---

### P1-2 — Uninstaller "Yes, remove user data" does not remove user data

**Observed:** After uninstalling and choosing "Yes" to remove user data:
- `Test-Path "$env:LOCALAPPDATA\BazaarCoach"` → `True`
- `Test-Path "$env:APPDATA\BazaarCoach"` → `True`

Leftovers: `%LOCALAPPDATA%\BazaarCoach` contained `bazaar_runs.db`, `logs/`, diagnostics ZIP, `builds/`, `static_cache/`. `%APPDATA%\BazaarCoach` contained `settings.json`.

**Impact:** Uninstaller prompt claims data will be removed but does not remove it. Leaves private gameplay/log data behind when user explicitly chooses deletion.

**Expected:**
- Yes → removes `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`.
- No → preserves both directories.

**Likely fix area:** `packaging/installer/BazaarCoach.iss` — Pascal script / uninstall prompt logic, `{localappdata}` and `{userappdata}` path constants, per-user install privilege behavior.

**How to test:**
1. Install app and generate some data (run the app briefly).
2. Uninstall → choose **No** → confirm app files removed, user data preserved.
3. Reinstall → generate data.
4. Uninstall → choose **Yes** → confirm `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach` are both gone.

---

### P1-3 — Runtime DB leaked into installed PyInstaller `_internal` folder

**Observed:** After uninstalling with "No" (preserve user data), the install folder was not removed because it contained:
```
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db-shm
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db-wal
```

**Impact:** Runtime data written into the install directory. Causes uninstall leftovers, risks split DB state (data spread across two locations), and confuses support/diagnostics.

**Expected:**
- Runtime DB lives only at `%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db`.
- Settings live only at `%APPDATA%\BazaarCoach\settings.json`.
- No `bazaar_runs.db*` under `%LOCALAPPDATA%\Programs\Bazaar Coach\`.

**Likely fix area:**
- `app_paths.py` packaged-mode path detection
- `capture_mono.py` or another subprocess calling `sqlite3.connect("bazaar_runs.db")` with a relative path
- PyInstaller subprocess `cwd` defaulting to `_internal`
- Any fallback path that resolves to CWD when the app-paths detection fails

**How to test:**
1. Install packaged app.
2. Launch app and capture at least a few decisions.
3. Run: `Get-ChildItem "$env:LOCALAPPDATA\Programs\Bazaar Coach" -Recurse -Force -Filter "bazaar_runs.db*" -ErrorAction SilentlyContinue`
4. Expected: no results.
5. Confirm DB exists at `%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db`.

---

### P2/P1-4 — Installed GUI exe support commands silent in terminal

**Observed:** From installed app directory:
```
.\BazaarCoach.exe doctor
.\BazaarCoach.exe refresh-builds
.\BazaarCoach.exe export-diagnostics
```
Printed no output in PowerShell. Commands did execute (`export-diagnostics` created a ZIP; `refresh-builds` appeared to work), but there was no visible confirmation.

**Note:** Start Menu "Doctor" shortcut correctly opens `cmd /K "...BazaarCoach.exe" doctor` and does show output — so there is a working support path for testers.

**Impact:** Direct terminal invocation appears broken even when commands succeed. Testers may not know the supported path is the Start Menu shortcut.

**Expected / options:**
- Provide a console-mode binary (`BazaarCoachCLI.exe`) for `doctor`/`refresh-builds`/`export-diagnostics`.
- Provide `.cmd` wrappers that open a visible window with output and log location hints.
- Or keep GUI exe as-is and update release notes / Discord alpha guide to steer testers to the Start Menu Doctor shortcut and the session log file.

**How to test:**
- Run installed support commands via the intended path (Start Menu shortcut or wrapper).
- Confirm testers can read `doctor` output and locate diagnostic ZIPs.

---

### P2-5 — Shutdown logs SQLite thread-close warning

**Observed log line during normal shutdown:**
```
[DB] close_shared_conn failed: SQLite objects created in a thread can only be used in that same thread. The object was created in thread id 16296 and this is thread id 7256.
```

**Impact:** Likely harmless (data written, shutdown completed cleanly), but looks alarming in diagnostics bundles shared by testers.

**Likely fix area:** `db.py` shared connection lifecycle / writer thread shutdown path — connection being closed from a different thread than it was opened on.

**How to test:**
1. Install app, capture at least one decision, shut down normally.
2. Confirm latest `logs/coach_*.log` has no SQLite thread-close warning on exit.

---

### P2-6 — Some early run item names unresolved

**Observed during real run capture:**
- `ShopPage Bought: ['Flying Squirrel', 'Worry Wart'] | Passed on: ['itm_ikyo_pq']`
- `Decision #3 🛒 Unknown | Inferred from shop select command`

**Impact:** Acceptable alpha caveat if infrequent; worth tracking as name-resolution polish.

**Likely fix area:** `name_resolver.py`, `run_state.py` shop decision paths, Mono template notification timing relative to first shop decisions.

**How to test:**
- Start a new run and capture the first shop decisions.
- Confirm bought/passed/review rows prefer human-readable names and do not regress previously resolved paths.

---

## Open Feature Work

### Multi-Hero Support - On Hold

Goal: add Jules and Stelle hero catalogs while keeping existing Karnok, Mak, Dooley, Vanessa, and Pygmalien behavior stable.

Status: On Hold. Jules and Stelle are not yet purchased.

Relevant files:

- `<hero>_builds.json`
- `scorer.py`
- `web/build_helpers.py`
- `web/overlay_state.py`
- `capture_mono.py`
- `msgpack_decoder.py`

Implementation notes:

- Add one hero at a time.
- Keep build schema compatible with existing `game_phases`, `archetypes`, `scoring_weights`, and `timing_profile` fields.
- Make sure new hero names match the names emitted by Mono capture and stored on `runs.hero`.
- Use the enricher fetch + compare workflow in `bazaar-builds` to populate initial archetypes before writing the catalog here.

How to test:

1. Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
2. Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
3. Verify the overlay Coach tab displays the new hero's archetypes and condition items.

### Automated Builds Refresh Pipeline - On Hold

Goal: eventually let the `bazaar-builds` repo produce reviewed catalog update PRs for this repo.

Status: On Hold. `bazaar-builds` remains in shadow/dry-run mode and must not mutate tracker catalogs or open tracker PRs until manually promoted.

Current constraints:

- Scheduled shadow runs in `bazaar-builds` are allowed to produce review artifacts there.
- Coach catalogs in this repo must not be automatically mutated yet.
- Do not promote to live catalog updates until there is enough healthy shadow history and a classifier/provider strategy has been accepted or explicitly waived.

Before live promotion:

- Require at least two healthy BazaarDB patch windows.
- Require at least 60 calendar days of healthy shadow output.
- Decide or waive the semantic classifier/provider strategy.
- Confirm required secrets, API billing/cost, rollback path, and tracker PR behavior.

How to test when this becomes active:

1. Run selected heroes in `bazaar-builds` with mock/no-LLM mode.
2. Confirm artifacts are produced without tracker catalog mutation.
3. Review shadow artifacts and source-health output.
4. Only after promotion, verify generated tracker PRs are reviewable and reversible.

## Feature Backlog

These are not part of the current release.

- **Cross-Run Analytics Dashboard** — Aggregate win rate by hero/archetype, score by phase, gold curves, and day-of-death.
- **Drill / What-If Mode** — From a completed run, pick a decision and score an alternative.
- **OBS Browser Source** — Minimal transparent view for stream overlays.
- **Catalog Pack Import** — Import community-authored catalog packs.
- **Crash & Auto-Diagnostics Reporter** — Package session log and last decisions on unhandled exception or watcher silence.
- **Opponent Build Inference** — Classify opponent boards after a validation spike proves capture fill rate is high enough.

Dropped:

- Run Export & Share
- Replay Scrubber
- Coaching Diff vs Reference Run
